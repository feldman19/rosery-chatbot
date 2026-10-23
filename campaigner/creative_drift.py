"""Advantage+ creative enhancements observed on, although the owner's rule says off.

Read-only toward Meta: it only looks at degrees_of_freedom_spec that the
regular refresh already read. The owner's rule ("disable_text_improvements")
says every Advantage+ enhancement is turned off before publishing; any
enhancement that is on is shown with that quote. Each refresh is compared with
the previous one, so a feature Meta turns on later, a feature Meta adds already
on, or a swapped creative is reported with when it was first seen. A spec Meta
did not return is unknown, never "all off". The owner turns things off in Ads
Manager; "keep as is" only stops showing that exact state again.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import uuid

ACCOUNT = '393059510164294'
RULE_ID = 'disable_text_improvements'
# Owner decision 2026-10-23: "Relevant comments" stays on and is not flagged.
ALLOWED_ON = frozenset({'inline_comment'})
CREATIVE_KEPT = '__creative__'
LABELS = {
    'standard_enhancements': 'שיפורים סטנדרטיים', 'product_browsing': 'עיון במוצרים',
    'music': 'מוזיקה', 'music_generation': 'מוזיקה שנוצרת ב-AI', 'audio': 'אודיו',
    'text_optimizations': 'שיפורי טקסט', 'text_generation': 'טקסט שנוצר ב-AI',
    'text_translation': 'תרגום טקסט', 'description_automation': 'תיאור אוטומטי',
    'image_touchups': 'שיפורי תמונה', 'image_enhancement': 'שיפור תמונה',
    'image_brightness_and_contrast': 'בהירות וניגודיות', 'image_animation': 'אנימציה לתמונה',
    'image_auto_crop': 'חיתוך תמונה אוטומטי', 'image_uncrop': 'הרחבת תמונה',
    'image_background_gen': 'רקע שנוצר ב-AI', 'image_templates': 'תבניות תמונה',
    'add_text_overlay': 'טקסט על התמונה', 'adapt_to_placement': 'התאמה למיקום',
    'enhance_cta': 'שיפור כפתור הפעולה', 'generate_cta': 'כפתור פעולה שנוצר ב-AI',
    'dynamic_cta_text': 'טקסט כפתור משתנה', 'site_extensions': 'קישורים לאתר',
    'product_extensions': 'הרחבות מוצר', 'product_tags': 'תיוג מוצרים',
    'show_summary': 'סיכום אוטומטי', 'video_auto_crop': 'חיתוך וידאו אוטומטי',
    'video_highlight': 'היילייט מהווידאו', 'multi_photo_to_video': 'תמונות לווידאו',
    'advantage_plus_creative': 'Advantage+ קריאייטיב', 'creative_stickers': 'מדבקות',
    'reveal_details_over_time': 'חשיפת פרטים בהדרגה', 'translate_voiceover': 'תרגום קריינות',
    'video_filtering': 'פילטר לווידאו', 'video_uncrop': 'הרחבת וידאו', 'hide_price': 'הסתרת מחיר',
    'media_type_automation': 'בחירת סוג מדיה אוטומטית', 'product_metadata_automation': 'פרטי מוצר אוטומטיים',
    'standard_enhancements_catalog': 'שיפורים סטנדרטיים לקטלוג',
}
# Observations only: a change between two reads does not say who made it.
KINDS = {'turned_on': 'נצפה מופעל אחרי שהיה כבוי', 'new_on': 'שדה חדש שהופיע מופעל',
         'on_at_first_read': 'נצפה מופעל בקריאה הראשונה'}


def label(feature):
    return LABELS.get(feature, feature)


def _features(creative):
    """{feature: OPT_IN|OPT_OUT|None}; None for the whole spec when Meta did not return it."""
    if not isinstance(creative, dict) or 'degrees_of_freedom_spec' not in creative:
        return None
    spec = (creative.get('degrees_of_freedom_spec') or {}).get('creative_features_spec')
    if not isinstance(spec, dict):
        return None
    result = {}
    for name, value in spec.items():
        status = (value or {}).get('enroll_status') if isinstance(value, dict) else None
        result[name] = status if status in ('OPT_IN', 'OPT_OUT') else None
    return result


def ads_manager_url(ad_id):
    return f'https://adsmanager.facebook.com/adsmanager/manage/ads?act={ACCOUNT}&selected_ad_ids={ad_id}'


class CreativeDrift:
    def __init__(self, path):
        self.path = str(path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS seen(ad_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,ad_id TEXT NOT NULL,payload TEXT NOT NULL,at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS kept(ad_id TEXT NOT NULL,creative_id TEXT NOT NULL,feature TEXT NOT NULL,'
                       'at TEXT NOT NULL,PRIMARY KEY(ad_id,creative_id,feature))')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    def import_readbacks(self, documents):
        """Earlier verified readbacks become the starting state for ads not seen yet."""
        rows = []
        def walk(node, source):
            if isinstance(node, dict):
                creative = node.get('creative')
                ad_id = node.get('ad_id') or node.get('id')
                features = _features(creative)
                if isinstance(ad_id, str) and ad_id.isdigit() and features is not None and isinstance(creative.get('id'), str):
                    rows.append((ad_id, {'creative_id': creative['id'], 'features': features,
                                         'first_seen': source, 'last_seen': source, 'source': 'readback'}))
                for value in node.values():
                    walk(value, source)
            elif isinstance(node, list):
                for value in node:
                    walk(value, source)
        for source, document in documents:
            walk(document, source)
        with self._db() as db:
            for ad_id, payload in rows:
                db.execute('INSERT OR IGNORE INTO seen VALUES(?,?)', (ad_id, json.dumps(payload, sort_keys=True)))

    def check(self, data, rulebook, now=None):
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        rule = None
        if isinstance(rulebook, dict) and rulebook.get('status') == 'available' and RULE_ID in (rulebook.get('rules') or {}):
            source = rulebook['rules'][RULE_ID]
            rule = {'rule_id': RULE_ID, 'source': source['source'], 'quote': source['source_excerpt']}
        base = {'live_action_allowed': False, 'provider_writes': 0, 'rule': rule, 'items': [], 'unknown': [],
                'allowed_on': sorted(ALLOWED_ON)}
        active = (data or {}).get('active_ads') if isinstance(data, dict) else None
        if not isinstance(active, dict) or active.get('verified') is not True or active.get('complete') is not True \
                or active.get('account_id') != ACCOUNT or not isinstance(active.get('ads'), list):
            return {**base, 'status': 'unavailable', 'reason': 'רשימת המודעות הפעילות ממטא לא נקראה במלואה.'}
        with self._db() as db:
            seen = {aid: json.loads(raw) for aid, raw in db.execute('SELECT ad_id,payload FROM seen')}
            kept = {(a, c, f) for a, c, f in db.execute('SELECT ad_id,creative_id,feature FROM kept')}
            for ad in active['ads']:
                ad_id = ad.get('id')
                creative = ad.get('creative') or {}
                creative_id = creative.get('id') if isinstance(creative.get('id'), str) else None
                features = _features(creative)
                name = ad.get('name') or ad_id
                if features is None:
                    base['unknown'].append({'ad_id': ad_id, 'name': name})
                    continue
                previous = seen.get(ad_id)
                events = []
                if previous and creative_id and previous.get('creative_id') not in (None, creative_id):
                    events.append({'kind': 'creative_replaced', 'before': previous['creative_id'], 'after': creative_id})
                for feature, status in features.items():
                    if status != 'OPT_IN':
                        continue
                    before = (previous or {}).get('features', {}).get(feature, 'absent') if previous else None
                    if previous is None:
                        kind = 'on_at_first_read'
                    elif before == 'OPT_IN':
                        continue
                    else:
                        kind = 'new_on' if before == 'absent' else 'turned_on'
                    events.append({'kind': kind, 'feature': feature, 'before': before})
                # A kept feature that is seen off again loses its keep: a later re-enable is shown.
                for feature, status in features.items():
                    if status == 'OPT_OUT' and (ad_id, creative_id, feature) in kept:
                        db.execute('DELETE FROM kept WHERE ad_id=? AND creative_id=? AND feature=?', (ad_id, creative_id, feature))
                        kept.discard((ad_id, creative_id, feature))
                for event in events:
                    event.update(creative_id=creative_id, detected_at=stamp)
                    db.execute('INSERT INTO events VALUES(?,?,?,?)',
                               (str(uuid.uuid4()), ad_id, json.dumps(event, sort_keys=True), stamp))
                db.execute('INSERT OR REPLACE INTO seen VALUES(?,?)', (ad_id, json.dumps({
                    'creative_id': creative_id, 'features': features, 'name': name,
                    'first_seen': (previous or {}).get('first_seen', stamp), 'last_seen': stamp,
                    'source': 'live'}, sort_keys=True)))
            history = {}
            for aid, raw in db.execute('SELECT ad_id,payload FROM events ORDER BY at'):
                history.setdefault(aid, []).append(json.loads(raw))
        for ad in active['ads']:
            ad_id = ad.get('id')
            creative = ad.get('creative') or {}
            features = _features(creative)
            if features is None:
                continue
            creative_id = creative.get('id')
            on = [f for f, s in sorted(features.items()) if s == 'OPT_IN' and f not in ALLOWED_ON
                  and (ad_id, creative_id, f) not in kept]
            unclear = [f for f, s in sorted(features.items()) if s is None]
            replaced = [e for e in history.get(ad_id, []) if e['kind'] == 'creative_replaced' and e.get('after') == creative_id
                        and (ad_id, creative_id, CREATIVE_KEPT) not in kept]
            if not on and not replaced and not unclear:
                continue
            first = {}  # the latest time each feature was seen turning on
            for event in history.get(ad_id, []):
                if event.get('feature') in on and event.get('creative_id') == creative_id:
                    first[event['feature']] = event
            base['items'].append({
                'ad_id': ad_id, 'name': ad.get('name') or ad_id, 'adset_name': (ad.get('adset') or {}).get('name'),
                'creative_id': creative_id, 'link': ads_manager_url(ad_id),
                'features': [{'feature': f, 'label': label(f), 'kind': (first.get(f) or {}).get('kind', 'on_at_first_read'),
                              'kind_label': KINDS[(first.get(f) or {}).get('kind', 'on_at_first_read')],
                              'since': (first.get(f) or {}).get('detected_at')} for f in on],
                'creative_replaced': replaced[-1] if replaced else None,
                'unclear': [{'feature': f, 'label': label(f)} for f in unclear]})
        base['items'].sort(key=lambda i: (-len(i['features']), i['name']))
        # Without a verified rulebook the finding is technical: "on", not "against your rule".
        return {**base, 'status': 'ready', 'rule_status': 'verified' if rule else 'unavailable',
                'checked_at': active.get('checked_at'), 'ads_checked': len(active['ads'])}

    def keep(self, ad_id, data, now=None):
        """Owner keeps the current state of one ad; the same state is not shown again."""
        now = now or datetime.now(timezone.utc)
        ad = next((a for a in ((data or {}).get('active_ads') or {}).get('ads') or [] if a.get('id') == ad_id), None)
        features = _features((ad or {}).get('creative'))
        if ad is None or features is None:
            raise ValueError('המודעה לא נמצאה ברשימה הנוכחית. רענן את המסך.')
        creative_id = (ad.get('creative') or {}).get('id')
        on = [f for f, s in features.items() if s == 'OPT_IN' and f not in ALLOWED_ON] + [CREATIVE_KEPT]
        with self._db() as db:
            for feature in on:
                db.execute('INSERT OR IGNORE INTO kept VALUES(?,?,?,?)', (ad_id, creative_id, feature, now.isoformat()))
        return {'ad_id': ad_id, 'kept': [f for f in on if f != CREATIVE_KEPT], 'provider_writes': 0}
