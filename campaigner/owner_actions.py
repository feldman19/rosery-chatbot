"""Owner-approved Meta changes from the simple screen ("אשר ובצע").

Three changes only: pause one ad, raise one ad set's daily budget by the rulebook step (+20%),
and turn off Advantage+ creative enhancements on one ad. Owner decision 2026-10-23: each change
runs only after the owner presses the button and sees the exact change.

Two steps, both from the owner: preview reads Meta now and returns the exact change with a
single-use token (10 minutes); confirm re-reads Meta and writes only if the state is still the
one shown. After writing, Meta is read back; a receipt is "verified" only when the readback
matches. A write whose outcome is unknown is recorded as unknown and never retried. Undo is a new
preview of the reverse change, so it also needs the owner's confirm.

Enhancements live on the creative, which Meta does not let you edit: turning them off makes a
copy of the creative with every feature OPT_OUT (except "Relevant comments", which the owner keeps)
and puts it on the same ad. The ad id and its statistics stay; Meta reviews the ad again. Undo
puts the previous creative back. Catalog and flexible (asset_feed_spec) creatives are not copied
here; those stay a link to Ads Manager.
"""
from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import threading
import uuid
from pathlib import Path

from creative_drift import ALLOWED_ON, label as feature_label

PREVIEW_TTL = timedelta(minutes=10)
MAX_STEP = 0.2
AD_FIELDS = 'id,name,status,effective_status,adset_id'
ADSET_FIELDS = 'id,name,status,daily_budget'
CREATIVE_FIELDS = 'id,name,object_story_spec,asset_feed_spec,product_set_id,url_tags,degrees_of_freedom_spec'
STATUS_LABEL = {'ACTIVE': 'פעילה', 'PAUSED': 'מושהית'}


class ActionBlocked(ValueError):
    """The change cannot be shown or done; the message is for the owner, in Hebrew."""


def _ils(minor):
    return '₪' + format(int(minor) / 100, ',.2f').rstrip('0').rstrip('.')


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{5,30}', value):
        raise ActionBlocked('מזהה לא תקין')
    return value


def _text_feed(feed):
    """Copy of a text-options asset_feed_spec (several texts/headlines on one image or video), None when absent,
    False for anything else (more media, catalog rules, ...), which is not copied here."""
    if not feed:
        return None
    lists = ('bodies', 'titles', 'descriptions')
    if not isinstance(feed, dict) or not set(feed) <= set(lists) | {'optimization_type'} or feed.get('optimization_type', 'DEGREES_OF_FREEDOM') != 'DEGREES_OF_FREEDOM':
        return False
    copy = {'optimization_type': 'DEGREES_OF_FREEDOM'}
    for key in lists:
        items = feed.get(key)
        if items is None:
            continue
        if not isinstance(items, list) or not all(isinstance(i, dict) and isinstance(i.get('text'), str) for i in items):
            return False
        copy[key] = [{'text': i['text']} for i in items]
    return copy


class OwnerActions:
    def __init__(self, directory, *, report, transport=None, credentials=None, clock=None):
        self.path = Path(directory) / 'owner-actions.json'
        self.report = report
        if transport is None:
            from campaign_operations_transport import CampaignTransport
            transport = CampaignTransport()
        self.transport = transport
        if credentials is None:
            from meta_write_connection import load
            credentials = lambda: load(directory)
        self.credentials = credentials
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.previews = {}
        with self.lock:
            receipts = self._receipts()
            changed = False
            for r in receipts:
                if r['state'] == 'inflight':
                    r.update(state='unknown', detail='המערכת נסגרה באמצע הביצוע. לא ידוע אם השינוי נקלט במטא; לבדוק במטא. אין ניסיון חוזר אוטומטי.')
                    changed = True
            if changed:
                self._write(receipts)

    # ---- storage ----
    def _receipts(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return value['receipts'] if isinstance(value, dict) and isinstance(value.get('receipts'), list) else []
        except (OSError, ValueError):
            return []

    def _write(self, receipts):
        pending = self.path.with_suffix('.pending')
        pending.write_text(json.dumps({'receipts': receipts[-500:]}, ensure_ascii=False, indent=1), encoding='utf-8')
        pending.replace(self.path)

    def _save(self, receipt):
        with self.lock:
            receipts = [r for r in self._receipts() if r['id'] != receipt['id']] + [receipt]
            self._write(receipts)
        return receipt

    def verified_changes(self):
        """{proposal_id: finished_at} for verified pause/scale changes not undone; for result tracking."""
        with self.lock:
            return {r['target']: r['finished_at'] for r in self._receipts() if r['state'] == 'verified' and not r.get('undone_by')
                    and r['kind'] in ('pause_ad', 'scale_adset_budget')}

    def status(self):
        with self.lock:
            receipts = self._receipts()
        return {'receipts': list(reversed(receipts)), 'provider_writes_need_owner_confirm': True}

    # ---- preview ----
    def preview(self, target):
        if not isinstance(target, str):
            raise ActionBlocked('פעולה לא מוכרת')
        kind, _, ref = target.partition(':')
        token = self.credentials()
        if kind == 'pause':
            plan = self._plan_pause(token, _id(ref))
        elif kind == 'scale':
            plan = self._plan_scale(token, _id(ref))
        elif kind == 'drift':
            plan = self._plan_drift(token, _id(ref))
        elif kind == 'undo':
            plan = self._plan_undo(token, ref)
        else:
            raise ActionBlocked('פעולה לא מוכרת')
        now = self.clock()
        nonce = secrets.token_urlsafe(24)
        with self.lock:
            self.previews = {k: v for k, v in self.previews.items() if v['expires_at'] > now}
            self.previews[nonce] = {**plan, 'target': target, 'expires_at': now + PREVIEW_TTL}
        return {'confirm_token': nonce, 'expires_at': (now + PREVIEW_TTL).isoformat(), 'target': target,
                **{k: plan[k] for k in ('title', 'changes', 'warnings', 'entity_name', 'undo_note')}}

    def _proposal(self, proposal_id):
        report = self.report() or {}
        for p in ((report.get('recommendations') or {}).get('rule_engine') or {}).get('proposals') or []:
            if p.get('id') == proposal_id:
                if not p.get('ready_for_approval') or p.get('blockers'):
                    raise ActionBlocked('ההצעה לא מוכנה לביצוע (' + ', '.join(p.get('blockers') or ['לא מוכנה']) + '). צריך לרענן נתונים.')
                return p
        raise ActionBlocked('ההצעה כבר לא קיימת בנתונים העדכניים. רענן ונסה שוב.')

    def _plan_pause(self, token, ad_id):
        p = self._proposal('pause:' + ad_id)
        ad = self.transport.read(token, ad_id, AD_FIELDS)
        if ad.get('status') != 'ACTIVE':
            raise ActionBlocked('המודעה כבר לא פעילה במטא (' + str(ad.get('status')) + '). אין מה לעצור.')
        return {'kind': 'pause_ad', 'entity_id': ad_id, 'entity_name': ad.get('name') or p.get('entity_name'),
                'title': 'לעצור את המודעה', 'expect': {'status': 'ACTIVE'}, 'write': {'status': 'PAUSED'},
                'changes': [{'field': 'סטטוס', 'from': STATUS_LABEL['ACTIVE'], 'to': STATUS_LABEL['PAUSED']}],
                'warnings': list(p.get('warnings') or []), 'undo_note': 'אפשר להפעיל מחדש בלחיצה.'}

    def _plan_scale(self, token, adset_id):
        p = self._proposal('scale:' + adset_id)
        before = (p.get('before') or {}).get('daily_budget_minor')
        after = (p.get('after') or {}).get('daily_budget_minor')
        if not isinstance(before, int) or not isinstance(after, int) or not before < after <= round(before * (1 + MAX_STEP)) + 1:
            raise ActionBlocked('ההגדלה חורגת מ-20% או שהתקציב לא ידוע. לא מבצעים.')
        adset = self.transport.read(token, adset_id, ADSET_FIELDS)
        if str(adset.get('daily_budget')) != str(before):
            raise ActionBlocked('התקציב במטא השתנה מאז הניתוח (עכשיו ' + _ils(adset.get('daily_budget') or 0) + '). רענן ונסה שוב.')
        return {'kind': 'scale_adset_budget', 'entity_id': adset_id, 'entity_name': adset.get('name') or p.get('entity_name'),
                'title': 'להגדיל תקציב יומי ב-20%', 'expect': {'daily_budget': str(before)}, 'write': {'daily_budget': str(after)},
                'changes': [{'field': 'תקציב יומי', 'from': _ils(before), 'to': _ils(after)}],
                'warnings': list(p.get('warnings') or []), 'undo_note': 'אפשר להחזיר לתקציב הקודם בלחיצה.'}

    def _plan_drift(self, token, ad_id):
        report = self.report() or {}
        item = next((i for i in (report.get('creative_drift') or {}).get('items') or [] if i.get('ad_id') == ad_id), None)
        if not item:
            raise ActionBlocked('לא נמצאו שיפורים מופעלים במודעה הזו בנתונים העדכניים.')
        ad = self.transport.read(token, ad_id, AD_FIELDS + ',creative{' + CREATIVE_FIELDS + '}')
        creative = ad.get('creative') or {}
        if creative.get('id') != item.get('creative_id'):
            raise ActionBlocked('הקריאייטיב של המודעה הוחלף מאז הקריאה האחרונה. רענן ונסה שוב.')
        feed = _text_feed(creative.get('asset_feed_spec'))
        if creative.get('product_set_id') or not isinstance(creative.get('object_story_spec'), dict) or feed is False:
            raise ActionBlocked('במודעת קטלוג או מודעה גמישה עם כמה תמונות/סרטונים אי אפשר עדיין לכבות מכאן. כבה במטא.')
        spec = (creative.get('degrees_of_freedom_spec') or {}).get('creative_features_spec') or {}
        on = sorted(f for f, v in spec.items() if isinstance(v, dict) and v.get('enroll_status') == 'OPT_IN' and f not in ALLOWED_ON)
        if not on:
            raise ActionBlocked('כל השיפורים כבר כבויים במודעה הזו.')
        features = {f: ({'enroll_status': v.get('enroll_status')} if f in ALLOWED_ON and isinstance(v, dict) else {'enroll_status': 'OPT_OUT'})
                    for f, v in spec.items()}
        new = {'name': ((creative.get('name') or 'creative') + ' | ללא שיפורים')[:200],
               'object_story_spec': creative['object_story_spec'],
               'degrees_of_freedom_spec': {'creative_features_spec': features}}
        if feed:
            new['asset_feed_spec'] = feed
        if creative.get('url_tags'):
            new['url_tags'] = creative['url_tags']
        return {'kind': 'disable_enhancements', 'entity_id': ad_id, 'entity_name': ad.get('name') or item.get('name'),
                'title': 'לכבות שיפורי Advantage+', 'expect': {'creative_id': creative['id']}, 'write': {'new_creative': new},
                'changes': [{'field': feature_label(f), 'from': 'מופעל', 'to': 'כבוי'} for f in on],
                'warnings': ['מטא לא מאפשרת לערוך קריאייטיב קיים, אז נוצר עותק זהה בלי השיפורים ומוחלף באותה מודעה. '
                             'המודעה והנתונים שלה נשארים, ומטא תבדוק את המודעה שוב (בדרך כלל דקות עד שעות).'],
                'undo_note': 'ביטול מחזיר את הקריאייטיב הקודם.'}

    def _plan_undo(self, token, receipt_id):
        with self.lock:
            r = next((x for x in self._receipts() if x['id'] == receipt_id), None)
        if not r or r['state'] != 'verified' or r.get('undone_by'):
            raise ActionBlocked('אפשר לבטל רק פעולה שבוצעה ואומתה ועוד לא בוטלה.')
        if r['kind'] == 'pause_ad':
            ad = self.transport.read(token, r['entity_id'], AD_FIELDS)
            if ad.get('status') != 'PAUSED':
                raise ActionBlocked('המודעה כבר לא מושהית במטא.')
            return {'kind': 'resume_ad', 'entity_id': r['entity_id'], 'entity_name': r['entity_name'], 'undo_of': r['id'],
                    'title': 'להפעיל מחדש את המודעה', 'expect': {'status': 'PAUSED'}, 'write': {'status': 'ACTIVE'},
                    'changes': [{'field': 'סטטוס', 'from': STATUS_LABEL['PAUSED'], 'to': STATUS_LABEL['ACTIVE']}],
                    'warnings': [], 'undo_note': ''}
        if r['kind'] == 'scale_adset_budget':
            adset = self.transport.read(token, r['entity_id'], ADSET_FIELDS)
            if str(adset.get('daily_budget')) != r['write']['daily_budget']:
                raise ActionBlocked('התקציב במטא השתנה מאז ההגדלה. לא מחזירים אוטומטית.')
            return {'kind': 'restore_adset_budget', 'entity_id': r['entity_id'], 'entity_name': r['entity_name'], 'undo_of': r['id'],
                    'title': 'להחזיר את התקציב הקודם', 'expect': {'daily_budget': r['write']['daily_budget']},
                    'write': {'daily_budget': r['expect']['daily_budget']},
                    'changes': [{'field': 'תקציב יומי', 'from': _ils(r['write']['daily_budget']), 'to': _ils(r['expect']['daily_budget'])}],
                    'warnings': [], 'undo_note': ''}
        if r['kind'] == 'disable_enhancements':
            ad = self.transport.read(token, r['entity_id'], AD_FIELDS + ',creative{id}')
            if (ad.get('creative') or {}).get('id') != r['result']['creative_id']:
                raise ActionBlocked('הקריאייטיב של המודעה הוחלף שוב מאז. לא מחזירים אוטומטית.')
            return {'kind': 'restore_creative', 'entity_id': r['entity_id'], 'entity_name': r['entity_name'], 'undo_of': r['id'],
                    'title': 'להחזיר את הקריאייטיב הקודם (עם השיפורים)', 'expect': {'creative_id': r['result']['creative_id']},
                    'write': {'creative_id': r['expect']['creative_id']},
                    'changes': [{'field': 'שיפורי Advantage+', 'from': 'כבויים', 'to': 'כמו שהיו קודם'}],
                    'warnings': ['מטא תבדוק את המודעה שוב.'], 'undo_note': ''}
        raise ActionBlocked('אין ביטול לפעולה הזו.')

    # ---- confirm ----
    def confirm(self, nonce):
        now = self.clock()
        with self.lock:
            plan = self.previews.pop(nonce, None) if isinstance(nonce, str) else None
            if not plan or plan['expires_at'] <= now:
                raise ActionBlocked('האישור פג או כבר נוצל. פתח שוב את התצוגה ואשר מחדש.')
            if any(r['state'] == 'inflight' for r in self._receipts()):
                raise ActionBlocked('פעולה אחרת עדיין מתבצעת. חכה שתסתיים.')
            receipt = {'id': 'act-' + uuid.uuid4().hex[:16], 'kind': plan['kind'], 'target': plan['target'],
                       'entity_id': plan['entity_id'], 'entity_name': plan['entity_name'], 'title': plan['title'],
                       'changes': plan['changes'], 'expect': plan['expect'], 'write': plan['write'],
                       'undo_of': plan.get('undo_of'), 'approved_at': now.isoformat(), 'state': 'checking'}
        token = self.credentials()
        try:
            self._check_unchanged(token, plan)
        except ActionBlocked as error:
            receipt.update(state='blocked', detail=str(error))
            self._save(receipt)
            raise
        receipt['state'] = 'inflight'
        self._save(receipt)
        try:
            result = self._write_change(token, plan, receipt)
        except Exception as error:
            from campaign_operations_transport import ProviderRejected
            # Each step is one POST: a definite rejection of the last one leaves the ad as it was
            # (a creative copy made before a rejected swap is unused and changes nothing).
            definite = isinstance(error, ProviderRejected)
            details = getattr(error, 'details', {}) or {}
            message = details.get('error_user_msg') or details.get('message') or ''
            receipt.update(state='rejected' if definite else 'unknown',
                           detail=('מטא דחתה את השינוי. דבר לא השתנה. ' + message).strip() if definite else
                           'לא ידוע אם השינוי נקלט במטא. אין ניסיון חוזר אוטומטי; בדוק במטא או רענן.',
                           finished_at=self.clock().isoformat())
            self._save(receipt)
            return receipt
        receipt['result'] = {**(receipt.get('result') or {}), **result}
        verified = self._readback(token, plan, receipt)
        receipt.update(state='verified' if verified else 'not_verified', finished_at=self.clock().isoformat(),
                       detail='בוצע ואומת מול מטא.' if verified else 'מטא קיבלה את הבקשה, אבל הקריאה החוזרת לא הראתה את השינוי עדיין. רענן בעוד דקה.')
        self._save(receipt)
        if verified and plan.get('undo_of'):
            with self.lock:
                receipts = self._receipts()
                for r in receipts:
                    if r['id'] == plan['undo_of']:
                        r['undone_by'] = receipt['id']
                self._write(receipts)
        return receipt

    def _check_unchanged(self, token, plan):
        exp = plan['expect']
        if 'status' in exp:
            if self.transport.read(token, plan['entity_id'], AD_FIELDS).get('status') != exp['status']:
                raise ActionBlocked('המצב במטא השתנה מאז התצוגה. דבר לא נשלח.')
        elif 'daily_budget' in exp:
            if str(self.transport.read(token, plan['entity_id'], ADSET_FIELDS).get('daily_budget')) != exp['daily_budget']:
                raise ActionBlocked('התקציב במטא השתנה מאז התצוגה. דבר לא נשלח.')
        elif 'creative_id' in exp:
            ad = self.transport.read(token, plan['entity_id'], AD_FIELDS + ',creative{id}')
            if (ad.get('creative') or {}).get('id') != exp['creative_id']:
                raise ActionBlocked('הקריאייטיב במטא השתנה מאז התצוגה. דבר לא נשלח.')

    def _write_change(self, token, plan, receipt):
        w = plan['write']
        if 'status' in w or 'daily_budget' in w:
            self.transport.update(token, plan['entity_id'], w)
            return {}
        if 'new_creative' in w:
            created = self.transport.create(token, 'adcreatives', w['new_creative'])
            creative_id = _id(str(created.get('id')))
            # Saved before the swap: if the swap's outcome is unknown, the copy is still traceable.
            receipt['result'] = {'creative_id': creative_id}
            self._save(receipt)
            self.transport.swap_creative(token, plan['entity_id'], creative_id)
            return {'creative_id': creative_id}
        if 'creative_id' in w:
            self.transport.swap_creative(token, plan['entity_id'], w['creative_id'])
            return {'creative_id': w['creative_id']}
        raise ActionBlocked('שינוי לא נתמך')

    def _readback(self, token, plan, receipt):
        w = plan['write']
        try:
            if 'status' in w:
                return self.transport.read(token, plan['entity_id'], AD_FIELDS).get('status') == w['status']
            if 'daily_budget' in w:
                return str(self.transport.read(token, plan['entity_id'], ADSET_FIELDS).get('daily_budget')) == w['daily_budget']
            ad = self.transport.read(token, plan['entity_id'], AD_FIELDS + ',creative{id,degrees_of_freedom_spec}')
            creative = ad.get('creative') or {}
            if creative.get('id') != receipt['result']['creative_id']:
                return False
            if 'new_creative' in w:
                spec = (creative.get('degrees_of_freedom_spec') or {}).get('creative_features_spec') or {}
                return not any(isinstance(v, dict) and v.get('enroll_status') == 'OPT_IN' and f not in ALLOWED_ON for f, v in spec.items())
            return True
        except Exception:
            return False
