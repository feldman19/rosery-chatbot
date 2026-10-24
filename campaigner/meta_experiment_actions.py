"""Owner-approved creation, start, stop and cancel of a Meta two-version test (experiment step 3).

Same path as owner_actions.py: preview reads Meta now and returns the exact change with a single-use token
(10 minutes); confirm re-reads Meta and writes only if nothing moved; then Meta is read back and the receipt is
"verified" only when the readback matches. An unknown outcome is recorded as unknown and never retried.
Two separate approvals: "create" links two existing PAUSED ad sets into a Meta split test (no spend), and
"activate" turns both on together. "stop" pauses both; "cancel" deletes a test that has not started (the undo
of create). Status reads are GET only.

The transport is injected. Tests use a fake one; MetaStudyTransport is the real adapter and is NOT exercised
until the owner approves the first live test on the local machine.
"""
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import json
import secrets
import threading
import uuid
from pathlib import Path

from experiments import Arm, Plan
from meta_experiment import (META, Spec, SpecRefused, approval_preview, daily_rows, interpret, meta_verdict,
                             study_path, study_payload, _zone)

PREVIEW_TTL = timedelta(minutes=10)
ADSET_FIELDS = 'id,name,status,effective_status,campaign_id,daily_budget,ads.limit(20){id}'
MIN_LEAD = timedelta(hours=1)


class ExperimentBlocked(ValueError):
    """The step cannot be shown or done; the message is for the owner, in Hebrew."""


def spec_to_dict(spec):
    return {**asdict(spec), 'adset_ids': list(spec.adset_ids), 'warnings': list(spec.warnings), 'rules': list(spec.rules)}


def spec_from_dict(value):
    plan = value['plan']
    plan = Plan(**{**plan, 'a': Arm(**plan['a']), 'b': Arm(**plan['b'])})
    return Spec(**{**value, 'plan': plan, 'adset_ids': tuple(value['adset_ids']), 'warnings': tuple(value['warnings']),
                   'rules': tuple(value['rules'])})


def _stamp(value):
    """Meta returns datetime fields as e.g. 2026-11-01T00:00:00+0200."""
    try:
        return int(datetime.strptime(value, '%Y-%m-%dT%H:%M:%S%z').timestamp())
    except (TypeError, ValueError):
        return None


class ExperimentActions:
    def __init__(self, directory, *, transport=None, credentials=None, clock=None):
        self.path = Path(directory) / 'meta-experiments.json'
        if transport is None:
            transport = MetaStudyTransport()
        self.transport = transport
        if credentials is None:
            from meta_write_connection import load
            credentials = lambda: load(directory)
        self.credentials = credentials
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.previews = {}
        with self.lock:
            state = self._state()
            for r in state['receipts']:
                if r['state'] == 'inflight':
                    r.update(state='unknown', detail='המערכת נסגרה באמצע. לא ידוע אם השינוי נקלט במטא; לבדוק במטא. אין ניסיון חוזר.')
            self._write(state)

    # ---- storage ----
    def _state(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(value, dict) and isinstance(value.get('specs'), dict) and isinstance(value.get('receipts'), list):
                return value
        except (OSError, ValueError):
            pass
        return {'specs': {}, 'receipts': []}

    def _write(self, state):
        pending = self.path.with_suffix('.pending')
        pending.write_text(json.dumps({'specs': state['specs'], 'receipts': state['receipts'][-500:]}, ensure_ascii=False, indent=1), encoding='utf-8')
        pending.replace(self.path)

    def _save_receipt(self, receipt):
        with self.lock:
            state = self._state()
            state['receipts'] = [r for r in state['receipts'] if r['id'] != receipt['id']] + [receipt]
            self._write(state)
        return receipt

    def _set_plan(self, experiment_id, **changes):
        with self.lock:
            state = self._state()
            spec = spec_from_dict(state['specs'][experiment_id])
            plan = spec.plan
            if 'state' in changes and changes['state'] != plan.state:
                plan = plan.transition(changes.pop('state'))
            changes.pop('state', None)
            state['specs'][experiment_id] = spec_to_dict(replace(spec, plan=replace(plan, **changes)))
            self._write(state)

    def register(self, spec):
        """Keep a built spec (meta_experiment.build_spec). One test per account at a time."""
        with self.lock:
            state = self._state()
            busy = [k for k, v in state['specs'].items() if v['plan']['state'] in ('planned', 'prepared', 'running') and k != spec.experiment_id]
            if busy:
                raise ExperimentBlocked('כבר יש ניסוי בהכנה או בריצה. קודם לסיים או לבטל אותו.')
            state['specs'][spec.experiment_id] = spec_to_dict(spec)
            self._write(state)
        return {'experiment_id': spec.experiment_id, 'registered': True, 'live_action_allowed': False}

    def spec(self, experiment_id):
        with self.lock:
            value = self._state()['specs'].get(experiment_id)
        if not value:
            raise ExperimentBlocked('הניסוי לא נמצא')
        return spec_from_dict(value)

    def receipts(self):
        with self.lock:
            return list(reversed(self._state()['receipts'][-30:]))

    # ---- reads shared by preview and confirm ----
    def _adsets(self, token, spec, status):
        found = []
        for key, adset_id in zip('AB', spec.adset_ids):
            adset = self.transport.read(token, adset_id, ADSET_FIELDS)
            label = 'גרסה ' + {'A': 'א', 'B': 'ב'}[key]
            if adset.get('id') != adset_id or adset.get('campaign_id') != spec.campaign_id:
                raise ExperimentBlocked(label + ': קבוצת המודעות לא נמצאה בקמפיין של הניסוי.')
            if adset.get('status') != status:
                raise ExperimentBlocked(f"{label}: המצב במטא הוא {adset.get('status')} ולא כמו שציפינו. רענן ונסה שוב.")
            if str(adset.get('daily_budget')) != str(spec.plan.daily_budget_minor):
                raise ExperimentBlocked(label + ': התקציב היומי במטא שונה מהתכנית. שתי הגרסאות חייבות לקבל אותו תקציב.')
            ads = (adset.get('ads') or {}).get('data')
            if not isinstance(ads, list) or len(ads) != len((spec.plan.a if key == 'A' else spec.plan.b).asset_ids):
                raise ExperimentBlocked(label + ': מספר המודעות בקבוצה לא תואם את התכנית.')
            found.append(adset)
        return found

    def _study(self, token, spec):
        study = self.transport.read(token, spec.plan.meta_study_id, META['readback_fields'])
        if study.get('id') != spec.plan.meta_study_id or study.get('canceled_time'):
            raise ExperimentBlocked('הניסוי במטא בוטל או לא נמצא.')
        return study

    # ---- preview ----
    def preview(self, target):
        if not isinstance(target, str):
            raise ExperimentBlocked('פעולה לא מוכרת')
        kind, _, experiment_id = target.partition(':')
        spec = self.spec(experiment_id)
        token = self.credentials()
        plan = self._plan(kind, token, spec)
        now = self.clock()
        nonce = secrets.token_urlsafe(24)
        with self.lock:
            self.previews = {k: v for k, v in self.previews.items() if v['expires_at'] > now}
            self.previews[nonce] = {**plan, 'target': target, 'experiment_id': experiment_id, 'expires_at': now + PREVIEW_TTL}
        return {'confirm_token': nonce, 'expires_at': (now + PREVIEW_TTL).isoformat(), 'target': target,
                **{k: plan[k] for k in ('title', 'changes', 'warnings')}, 'owner_text': plan.get('owner_text')}

    def _plan(self, kind, token, spec):
        state = spec.plan.state
        if kind == 'create':
            if state != 'planned' or spec.plan.meta_study_id:
                raise ExperimentBlocked('הניסוי כבר הוכן במטא.')
            if datetime.fromtimestamp(spec.start_time, timezone.utc) < self.clock() + MIN_LEAD:
                raise ExperimentBlocked('תאריך ההתחלה כבר עבר או קרוב מדי. צריך לבחור תאריך חדש ולהכין מחדש.')
            self._adsets(token, spec, 'PAUSED')
            text = approval_preview(spec, 'create')
            return {'kind': 'create_study', 'title': text['title'], 'changes': text['changes'], 'owner_text': text,
                    'warnings': text['warnings'], 'write': {'path': study_path(spec), 'payload': study_payload(spec)}}
        if kind == 'activate':
            if state != 'prepared' or not spec.plan.meta_study_id:
                raise ExperimentBlocked('אפשר להפעיל רק ניסוי שהוכן במטא ועוד לא הופעל.')
            study = self._study(token, spec)
            if (_stamp(study.get('end_time')) or 0) <= self.clock().timestamp():
                raise ExperimentBlocked('זמן הניסוי במטא כבר נגמר.')
            self._adsets(token, spec, 'PAUSED')
            text = approval_preview(spec, 'activate')
            return {'kind': 'activate', 'title': text['title'], 'changes': text['changes'], 'owner_text': text,
                    'warnings': text['warnings'], 'write': {'status': 'ACTIVE', 'from': 'PAUSED'}}
        if kind == 'stop':
            if state != 'running':
                raise ExperimentBlocked('אפשר לעצור רק ניסוי שרץ.')
            active = [x for x in spec.adset_ids if self.transport.read(token, x, ADSET_FIELDS).get('status') == 'ACTIVE']
            if not active:
                raise ExperimentBlocked('שתי הגרסאות כבר מושהות במטא.')
            return {'kind': 'stop', 'title': 'לעצור את הניסוי', 'owner_text': None,
                    'changes': [{'field': 'גרסה ' + ('א' if x == spec.adset_ids[0] else 'ב'), 'from': 'פעילה', 'to': 'מושהית'} for x in active],
                    'warnings': ['ניסוי שנעצר באמצע לא נותן תשובה. ההוצאה עד עכשיו נשארת.'],
                    'write': {'status': 'PAUSED', 'from': 'ACTIVE', 'adsets': active}}
        if kind == 'cancel':
            if state != 'prepared' or not spec.plan.meta_study_id:
                raise ExperimentBlocked('אפשר לבטל רק ניסוי שהוכן ועוד לא הופעל.')
            study = self._study(token, spec)
            if (_stamp(study.get('start_time')) or 0) <= self.clock().timestamp():
                raise ExperimentBlocked('הניסוי כבר התחיל; אפשר רק לעצור אותו.')
            return {'kind': 'cancel_study', 'title': 'לבטל את הניסוי לפני שהתחיל', 'owner_text': None,
                    'changes': [{'field': 'הניסוי', 'from': 'מוכן במטא', 'to': 'מבוטל (המודעות נשארות מושהות)'}],
                    'warnings': [], 'write': {'delete': spec.plan.meta_study_id}}
        raise ExperimentBlocked('פעולה לא מוכרת')

    # ---- confirm ----
    def confirm(self, nonce):
        now = self.clock()
        with self.lock:
            plan = self.previews.pop(nonce, None) if isinstance(nonce, str) else None
            if not plan or plan['expires_at'] <= now:
                raise ExperimentBlocked('האישור פג או כבר נוצל. פתח שוב את התצוגה ואשר מחדש.')
            if any(r['state'] == 'inflight' for r in self._state()['receipts']):
                raise ExperimentBlocked('פעולה אחרת עדיין מתבצעת. חכה שתסתיים.')
        experiment_id = plan['experiment_id']
        receipt = {'id': 'exp-' + uuid.uuid4().hex[:16], 'kind': plan['kind'], 'target': plan['target'], 'experiment_id': experiment_id,
                   'title': plan['title'], 'changes': plan['changes'], 'write': plan['write'], 'approved_at': now.isoformat(), 'state': 'checking'}
        token = self.credentials()
        spec = self.spec(experiment_id)
        try:
            again = self._plan(plan['target'].partition(':')[0], token, spec)
            if again['write'] != plan['write']:
                raise ExperimentBlocked('המצב במטא השתנה מאז התצוגה. דבר לא נשלח.')
        except ExperimentBlocked as error:
            receipt.update(state='blocked', detail=str(error))
            self._save_receipt(receipt)
            raise
        receipt['state'] = 'inflight'
        self._save_receipt(receipt)
        done = []
        try:
            result = self._write_change(token, spec, plan, receipt, done)
        except Exception as error:
            from campaign_operations_transport import ProviderRejected
            definite = isinstance(error, ProviderRejected) and not done
            details = getattr(error, 'details', {}) or {}
            message = details.get('error_user_msg') or details.get('message') or ''
            if done:
                detail = ('רק חלק מהשינוי בוצע (' + ', '.join(done) + '). גרסה אחת עלולה לרוץ לבד; כדאי לעצור את הניסוי. אין ניסיון חוזר אוטומטי.')
            elif definite:
                detail = ('מטא דחתה את השינוי. דבר לא השתנה. ' + message).strip()
            else:
                detail = 'לא ידוע אם השינוי נקלט במטא. אין ניסיון חוזר אוטומטי; בדוק במטא או רענן.'
            receipt.update(state='partial' if done else 'rejected' if definite else 'unknown', detail=detail, finished_at=self.clock().isoformat())
            if done and plan['kind'] == 'activate':
                self._set_plan(experiment_id, state='running')  # so "stop" is available at once
            self._save_receipt(receipt)
            return receipt
        receipt['result'] = result
        verified = self._readback(token, spec, plan, result)
        receipt.update(state='verified' if verified else 'not_verified', finished_at=self.clock().isoformat(),
                       detail='בוצע ואומת מול מטא.' if verified else 'מטא קיבלה את הבקשה, אבל הקריאה החוזרת לא הראתה את השינוי. רענן בעוד דקה.')
        if verified:
            if plan['kind'] == 'create_study':
                self._set_plan(experiment_id, state='prepared', meta_study_id=result['study_id'])
            elif plan['kind'] == 'activate':
                self._set_plan(experiment_id, state='running')
            else:
                self._set_plan(experiment_id, state='interrupted')
        elif plan['kind'] == 'create_study':
            receipt['detail'] += ' מזהה הניסוי במטא: ' + result['study_id']
        self._save_receipt(receipt)
        return receipt

    def _write_change(self, token, spec, plan, receipt, done):
        w = plan['write']
        if plan['kind'] == 'create_study':
            created = self.transport.create_study(token, spec.business_id, w['payload'])
            study_id = str(created.get('id') or '')
            if not study_id.isdigit():
                raise ValueError('no study id')
            receipt['result'] = {'study_id': study_id}  # traceable even if the readback fails
            self._save_receipt(receipt)
            return {'study_id': study_id}
        if plan['kind'] == 'cancel_study':
            self.transport.delete_study(token, w['delete'])
            return {}
        targets = spec.adset_ids if plan['kind'] == 'activate' else w['adsets']
        for adset_id in targets:
            self.transport.update(token, adset_id, {'status': w['status']})
            done.append('גרסה ' + ('א' if adset_id == spec.adset_ids[0] else 'ב'))
        return {}

    def _readback(self, token, spec, plan, result):
        try:
            if plan['kind'] == 'create_study':
                study = self.transport.read(token, result['study_id'], META['readback_fields'])
                cells = (study.get('cells') or {}).get('data') if isinstance(study.get('cells'), dict) else study.get('cells')
                return (study.get('type') == META['study_type'] and _stamp(study.get('start_time')) == spec.start_time
                        and _stamp(study.get('end_time')) == spec.end_time and isinstance(cells, list) and len(cells) == 2
                        and all(float(c.get(META['cell_share'])) == 50 and c.get('ad_entities_count') == 1 for c in cells))
            if plan['kind'] == 'cancel_study':
                from campaign_operations_transport import ProviderRejected
                try:
                    study = self.transport.read(token, plan['write']['delete'], META['readback_fields'])
                except ProviderRejected:
                    return True  # gone
                return bool(study.get('canceled_time'))
            targets = spec.adset_ids if plan['kind'] == 'activate' else plan['write']['adsets']
            return all(self.transport.read(token, x, ADSET_FIELDS).get('status') == plan['write']['status'] for x in targets)
        except Exception:
            return False

    # ---- monitor (GET only) ----
    def status(self, experiment_id, today=None):
        """Daily status for the owner: spend and purchases per version, chance, days left, and the answer."""
        spec = self.spec(experiment_id)
        if spec.plan.state not in ('prepared', 'running', 'interrupted') or not spec.plan.meta_study_id:
            return {'experiment_id': experiment_id, 'state': spec.plan.state, 'text': 'הניסוי עוד לא הוכן במטא.', 'live_action_allowed': False}
        token = self.credentials()
        local = self.clock().astimezone(_zone(spec.timezone_name))
        today = today or local.date()
        as_of = min(today - timedelta(days=1), date.fromisoformat(spec.plan.start_date) + timedelta(days=spec.plan.max_days - 1)).isoformat()
        study = self.transport.read(token, spec.plan.meta_study_id, META['result_fields'])
        daily = {}
        if as_of >= spec.plan.start_date:
            for key, adset_id in zip('AB', spec.adset_ids):
                rows = daily_rows(self.transport.insights(token, adset_id, spec.plan.start_date, as_of), spec.plan.start_date, as_of)
                if rows is None:
                    daily = {}
                    break
                daily[key] = rows
        if spec.plan.state == 'interrupted':
            answer = {'state': 'interrupted', 'winner': None, 'text': 'הניסוי נעצר באמצע, אז אין תשובה.', 'next_step': 'אפשר לתכנן ניסוי חדש'}
        elif as_of < spec.plan.start_date:
            answer = {'state': spec.plan.state, 'winner': None, 'text': 'הניסוי עוד לא התחיל לאסוף נתונים.', 'next_step': 'לחכות'}
        else:
            answer = interpret(spec, daily, as_of, meta_verdict(study))
        left = max(0, spec.plan.max_days - (today - date.fromisoformat(spec.plan.start_date)).days)
        return {'experiment_id': experiment_id, 'study_id': spec.plan.meta_study_id, 'as_of': as_of, 'days_left': left,
                'meta_end_time': study.get('end_time'), 'canceled': bool(study.get('canceled_time')),
                **answer, 'live_action_allowed': False}


class MetaStudyTransport:
    """Real adapter over CampaignTransport (fixed paths, no retries). Not exercised in the cloud; the first
    live use is the owner-approved canary on the local machine."""

    def __init__(self, base=None):
        if base is None:
            from campaign_operations_transport import CampaignTransport
            base = CampaignTransport()
        self.base = base

    @staticmethod
    def _digits(value):
        if not isinstance(value, str) or not value.isdigit() or not 5 <= len(value) <= 30:
            raise ValueError('Unsupported Meta node')
        return value

    def read(self, token, node, fields):
        return self.base.read(token, node, fields)

    def update(self, token, node, params):
        if set(params) != {'status'} or params['status'] not in ('ACTIVE', 'PAUSED'):
            raise ValueError('Only status changes')
        return self.base.update(token, node, params)

    def create_study(self, token, business_id, payload):
        return self.base._request(token, self._digits(business_id) + '/' + META['study_edge'], 'POST', payload)

    def delete_study(self, token, study_id):
        return self.base._request(token, self._digits(study_id), 'DELETE', {})

    def insights(self, token, adset_id, since, until):
        params = {'fields': 'date_start,date_stop,spend,actions', 'time_increment': '1', 'level': 'adset',
                  'time_range': json.dumps({'since': since, 'until': until}), 'use_unified_attribution_setting': 'true', 'limit': '100'}
        rows = self.base._request(token, self._digits(adset_id) + '/insights', 'GET', params)
        if (rows.get('paging') or {}).get('next'):
            return None  # more than one page for ≤30 days is unexpected; unknown, not partial
        return rows.get('data')
