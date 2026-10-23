"""Which ads paid off over time, and which creative traits went with them.

Source: the daily read-only history read (all history and the last 90 full
days, ad level), else the stored decision-memory snapshot. Purchases an ad has
no reported actions for stay unknown and are left out of rankings, never zero.
Ranking needs ₪200 spend and 3 purchases per ad (owner rules kill_requires_spend
and single_purchase_not_verdict, 3 chosen by the owner 2026-10-23); a trait group
needs 5 ads and 10 purchases. Groups describe what ran together, not what caused
the result: campaigns ran at different times, to different audiences and offers.
"""
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
from pathlib import Path

from management_report import PURCHASE, _event, _number

MIN_SPEND = 200
MIN_PURCHASES = 3
GROUP_MIN_ADS = 5
GROUP_MIN_PURCHASES = 10
STALE_HOURS = 24
TOP, BOTTOM = 10, 5

CATEGORIES = (('שרשראות', ('שרשר', 'necklace')), ('צמידים', ('צמיד', 'bracelet')), ('טבעות', ('טבעת', 'ring')),
              ('עגילים', ('עגיל', 'earring')), ('קפוצ׳ונים', ('קפוצ', 'hood')),
              ('מכנסיים', ('מכנס', 'pants', 'jeans', 'שורט')),
              ('חולצות', ('חולצ', 'טישרט', 'סליב', 'shirt', 'tee')), ('קטלוג', ('קטלוג', 'catalog')))
TESTIMONIAL = re.compile(r'ביקורת|המלצ|לקוח|review|ugc|testimonial', re.I)
PRICE = re.compile(r'₪|ש"ח|ש״ח|שקל|\bnis\b', re.I)
DISCOUNT = re.compile(r'\d+\s?%|הנחה|מבצע|sale|1\s?\+\s?1|קופון|חינם', re.I)
EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿]')
DIMENSIONS = {'format': 'פורמט', 'category': 'מוצר', 'testimonial': 'ביקורת/המלצת לקוח', 'has_discount': 'הנחה או מבצע',
              'has_price': 'מחיר בטקסט', 'emoji': 'אימוג׳י', 'text_length': 'אורך טקסט', 'cta': 'כפתור פעולה'}
VALUE_LABELS = {True: 'כן', False: 'לא', 'video': 'וידאו', 'image': 'תמונה', 'carousel': 'קרוסלה', 'catalog': 'קטלוג',
                'short': 'קצר', 'medium': 'בינוני', 'long': 'ארוך'}


def _float(value):
    number = _number(value)
    return float(number) if number is not None else None


def _texts(creative):
    oss = creative.get('object_story_spec') or {}
    link, video = oss.get('link_data') or {}, oss.get('video_data') or {}
    feed = creative.get('asset_feed_spec') or {}
    parts = [creative.get('body'), creative.get('title'), link.get('message'), link.get('name'),
             video.get('message'), video.get('title')]
    for key in ('bodies', 'titles'):
        parts += [x.get('text') for x in feed.get(key) or [] if isinstance(x, dict)]
    return [p.strip() for p in parts if isinstance(p, str) and p.strip()]


def creative_format(creative):
    oss = creative.get('object_story_spec') or {}
    link, feed = oss.get('link_data') or {}, creative.get('asset_feed_spec') or {}
    if creative.get('product_set_id') or oss.get('template_data') or creative.get('object_type') == 'PRODUCT':
        return 'catalog'
    if creative.get('video_id') or oss.get('video_data') or feed.get('videos') or creative.get('object_type') == 'VIDEO':
        return 'video'
    if link.get('child_attachments'):
        return 'carousel'
    if creative.get('image_hash') or link.get('image_hash') or link.get('picture') or oss.get('photo_data') or feed.get('images'):
        return 'image'
    return None


def traits(name, creative=None, stored_format=None):
    """Each trait with where it came from; None means unknown, not 'no'."""
    name = name or ''
    result = {'category': next((label for label, words in CATEGORIES if any(w in name.lower() for w in words)), None),
              'testimonial': True if TESTIMONIAL.search(name) else None, 'format': None, 'has_price': None,
              'has_discount': None, 'emoji': None, 'text_length': None, 'cta': None, 'hook': None, 'source': 'name'}
    if stored_format in ('video', 'image', 'carousel', 'catalog'):
        result['format'] = stored_format
    if not isinstance(creative, dict):
        return result
    texts = _texts(creative)
    text = '\n'.join(texts)
    result.update(source='creative', format=creative_format(creative) or result['format'],
                  cta=creative.get('call_to_action_type') or (((creative.get('object_story_spec') or {}).get('link_data') or {})
                                                              .get('call_to_action') or {}).get('type'))
    if texts:
        both = name + '\n' + text
        result.update(has_price=bool(PRICE.search(text)), has_discount=bool(DISCOUNT.search(text)),
                      emoji=bool(EMOJI.search(text)), testimonial=bool(TESTIMONIAL.search(both)),
                      text_length='short' if len(text) < 80 else 'medium' if len(text) <= 200 else 'long',
                      hook=texts[0].splitlines()[0][:60])
        result['category'] = result['category'] or next(
            (label for label, words in CATEGORIES if any(w in both.lower() for w in words)), None)
    return result


def _live_rows(window):
    rows = []
    for row in window.get('rows') or []:
        probe = {**row, 'metric_sources': {'actions_present': row.get('actions_present') is True}}
        known = row.get('actions_present') is True
        purchases = _event(probe, 'actions', PURCHASE) if known else None
        revenue = _event(probe, 'action_values', PURCHASE) if known else None
        rows.append({'ad_id': row.get('ad_id'), 'name': row.get('ad_name'), 'campaign_name': row.get('campaign_name'),
                     'spend': _float(row.get('spend')), 'purchases': float(purchases) if purchases is not None else None,
                     'revenue': float(revenue) if revenue is not None else None})
    return rows


def rank(rows, breakeven_cpa=None):
    eligible = [r for r in rows if (r['spend'] or 0) >= MIN_SPEND and r['purchases'] is not None
                and r['purchases'] >= MIN_PURCHASES and r['revenue'] is not None]
    for r in eligible:
        r['roas'] = r['revenue'] / r['spend']
        r['cpa'] = r['spend'] / r['purchases']
        r['below_breakeven'] = r['cpa'] <= breakeven_cpa if breakeven_cpa else None
    ordered = sorted(eligible, key=lambda r: (-r['roas'], -r['purchases']))
    return eligible, ordered[:TOP], sorted(eligible, key=lambda r: (r['roas'], -r['spend']))[:BOTTOM]


def patterns(rows):
    known = [r for r in rows if (r['spend'] or 0) > 0 and r['purchases'] is not None and r['revenue'] is not None]
    spend = sum(r['spend'] for r in known)
    baseline = sum(r['revenue'] for r in known) / spend if spend else None
    result = []
    for dimension in DIMENSIONS:
        groups, unknown = {}, 0
        for r in known:
            value = r['traits'].get(dimension)
            if value is None:
                unknown += 1
                continue
            groups.setdefault(value, []).append(r)
        values = []
        for value, members in groups.items():
            s, p, v = (sum(m[k] for m in members) for k in ('spend', 'purchases', 'revenue'))
            ok = len(members) >= GROUP_MIN_ADS and p >= GROUP_MIN_PURCHASES
            values.append({'value': VALUE_LABELS.get(value, value), 'ads': len(members), 'spend': round(s),
                           'purchases': round(p), 'roas': round(v / s, 2) if ok and s else None, 'enough_data': ok})
        values.sort(key=lambda x: (not x['enough_data'], -(x['roas'] or 0), -x['ads']))
        result.append({'dimension': DIMENSIONS[dimension], 'values': values, 'unknown_ads': unknown})
    return {'baseline_roas': round(baseline, 2) if baseline else None, 'compared_ads': len(known), 'dimensions': result}


def _public(r):
    t = r['traits']
    return {'name': r['name'], 'campaign': r.get('campaign_name'), 'spend': round(r['spend']), 'purchases': round(r['purchases']),
            'roas': round(r['roas'], 2), 'cpa': round(r['cpa']), 'below_breakeven_cpa': r['below_breakeven'],
            'traits': {DIMENSIONS[k]: VALUE_LABELS.get(t[k], t[k]) for k in DIMENSIONS if t.get(k) is not None},
            'hook': t.get('hook')}


class AccountHistory:
    def __init__(self, data_dir, reader=None):
        self.directory = Path(data_dir)
        self.path = self.directory / 'account-history.json'
        self.reader = reader
        self.last_error = None

    def _stored(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return value if value.get('verified') is True and isinstance(value.get('windows'), dict) else None
        except (OSError, ValueError, AttributeError):
            return None

    def refresh_if_stale(self, now=None):
        """Read history from Meta at most once a day. Read-only; failures keep the last copy."""
        now = now or datetime.now(timezone.utc)
        stored = self._stored()
        try:
            fresh = stored and now - datetime.fromisoformat(stored['checked_at']) < timedelta(hours=STALE_HOURS)
        except (KeyError, TypeError, ValueError):
            fresh = False
        if fresh or self.reader is None:
            return False
        try:
            result = self.reader(self.directory)
            if result.get('verified') is not True:
                raise ValueError('Unverified history')
            pending = self.path.with_suffix('.pending')
            pending.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            pending.replace(self.path)
            self.last_error = None
            return True
        except Exception:
            # No exception text: provider errors may contain credentials or URLs.
            self.last_error = 'הקריאה ההיסטורית ממטא נכשלה; נשמרה הגרסה הקודמת.'
            return False

    def spend(self, window):
        """Meta spend for one stored window ('90' or 'all'), or None when it was not read."""
        stored = self._stored()
        rows = ((stored or {}).get('windows') or {}).get(window, {}).get('rows')
        if not isinstance(rows, list):
            return None
        values = [_float(r.get('spend')) for r in rows]
        return sum(values) if all(v is not None for v in values) else None

    def _memory(self):
        try:
            db = sqlite3.connect(f'file:{self.directory / "decision-memory.sqlite3"}?mode=ro', uri=True)
            try:
                found = db.execute('SELECT captured_at,payload FROM memory_snapshots ORDER BY captured_at DESC LIMIT 1').fetchone()
            finally:
                db.close()
        except sqlite3.Error:
            return None
        if not found:
            return None
        payload = json.loads(found[1])
        rows = [{'ad_id': r.get('id'), 'name': r.get('name'), 'campaign_name': r.get('campaign_name'),
                 'spend': _float(r.get('spend')), 'purchases': _float(r.get('purchases')), 'revenue': _float(r.get('revenue')),
                 'stored_format': r.get('format')} for r in payload.get('rows') or []]
        return {'source': 'decision_memory_snapshot', 'checked_at': found[0], 'windows': {'all': rows}, 'creatives': {},
                'limits': payload.get('limits') or []}

    def summary(self, rulebook=None):
        stored = self._stored()
        if stored:
            data = {'source': 'meta_history_read', 'checked_at': stored['checked_at'],
                    'windows': {k: _live_rows(w) for k, w in stored['windows'].items()},
                    'creatives': stored.get('creatives') or {}, 'creatives_failed': stored.get('creatives_failed', 0),
                    'limits': []}
        else:
            data = self._memory()
        if not data:
            return {'status': 'unavailable', 'reason': 'אין עדיין היסטוריית חשבון שמורה.'}
        breakeven = None
        if isinstance(rulebook, dict) and rulebook.get('status') == 'available':
            breakeven = ((rulebook.get('rules') or {}).get('breakeven') or {}).get('params', {}).get('breakeven_cpa')
        windows = {}
        for key, rows in data['windows'].items():
            for r in rows:
                r['traits'] = traits(r['name'], data['creatives'].get(r['ad_id']), r.get('stored_format'))
            eligible, top, bottom = rank(rows, breakeven)
            unknown = [r for r in rows if (r['spend'] or 0) > 0 and r['purchases'] is None]
            total = sum(r['spend'] or 0 for r in rows)
            windows[key] = {
                'label': 'כל ההיסטוריה' if key == 'all' else f'{key} הימים המלאים האחרונים',
                'coverage': {'ads': len(rows), 'spend': round(total), 'ads_purchases_unknown': len(unknown),
                             'spend_purchases_unknown': round(sum(r['spend'] for r in unknown)),
                             'ranked_ads': len(eligible),
                             'creatives_read': sum(1 for r in rows if r['traits']['source'] == 'creative')},
                'top': [_public(r) for r in top], 'bottom': [_public(r) for r in bottom],
                'patterns': patterns(rows)}
        return {'status': 'ready', 'source': data['source'], 'checked_at': data['checked_at'], 'windows': windows,
                'thresholds': {'ad_min_spend': MIN_SPEND, 'ad_min_purchases': MIN_PURCHASES,
                               'group_min_ads': GROUP_MIN_ADS, 'group_min_purchases': GROUP_MIN_PURCHASES,
                               'breakeven_cpa': breakeven},
                'limits': ['ROAS ורכישות הם ייחוס של מטא, לא רווח נטו ולא מכירות תוספתיות.',
                           'קבוצת תכונה מתארת מה רץ יחד; קמפיינים רצו בזמנים, לקהלים ובהצעות שונים, ולכן זה לא מוכיח שהתכונה גרמה לתוצאה.',
                           'מודעה בלי נתון רכישות לא דורגה ולא נספרה כאפס.',
                           'תכונות שחושבו מהשם בלבד חלקיות; תכונה לא ידועה לא נספרת כ"לא".'] + data['limits'][:3],
                'read_error': self.last_error, 'live_action_allowed': False}


def chat_context(summary, budget=7000):
    """Compact block for the advisory chat; trims lists until it fits the budget."""
    if summary.get('status') != 'ready':
        return summary
    block = json.loads(json.dumps(summary, ensure_ascii=False))
    for window in block['windows'].values():
        for dimension in window['patterns']['dimensions']:
            dimension['values'] = [v for v in dimension['values'] if v['enough_data']] + \
                                  [{'value': v['value'], 'ads': v['ads'], 'enough_data': False}
                                   for v in dimension['values'] if not v['enough_data']][:3]
    steps = [('top', 6), ('bottom', 3), ('top', 4), ('bottom', 2)]
    for key, size in steps:
        if len(json.dumps(block, ensure_ascii=False)) <= budget:
            break
        for window in block['windows'].values():
            window[key] = window[key][:size]
            for item in window[key]:
                item.pop('campaign', None)
    if len(json.dumps(block, ensure_ascii=False)) > budget:
        for window in block['windows'].values():
            for item in window['top'] + window['bottom']:
                item.pop('hook', None)
    return block
