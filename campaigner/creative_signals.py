"""Signals beside purchases for the creative analyst: the viewing funnel, a sharper opening, and competitor context.

- Funnel per ad: hook rate (3-second views / impressions), 2-second continuous views, hold rate (ThruPlays / 3-second
  views), link CTR and the viewing curve (25/50/75/100% of plays). Impressions, 3-second views and link clicks are
  already in the stored history read (impressions and the actions list); ThruPlays and the curve come from one more
  read-only insights GET (read_funnel). A field Meta did not return stays unknown, never zero.
  These are in-between measures, not results: outside research found hook and CTR did not predict ROAS across
  brands, so the analyst also checks whether each one goes with ROAS in this account before leaning on it.
- Opening frames: extra stills at 0.1, 1, 1.5, 2 and 3 seconds so the opening tag rests on what happens in the
  first seconds, not on one frame.
- Ad Library: competitor ads (text, page, start date, link) saved from the Meta Ad Library as outside context.
  It never enters the evidence gate and carries no performance numbers.
GET only; nothing here writes to Meta.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import random
import re
import tempfile
import time
import unicodedata
from pathlib import Path

from hypotheses import gate
from management_report import _event

OPENING_TIMES = (100, 1000, 1500, 2000, 3000)
CURVE = ('p25', 'p50', 'p75', 'p100')
VIDEO_FIELDS = {'plays': 'video_play_actions', 'views_2s': 'video_continuous_2_sec_watched_actions',
                'thruplays': 'video_thruplay_watched_actions', 'p25': 'video_p25_watched_actions',
                'p50': 'video_p50_watched_actions', 'p75': 'video_p75_watched_actions', 'p100': 'video_p100_watched_actions',
                'avg_watch_s': 'video_avg_time_watched_actions'}
# metric: (numerator, denominator, Hebrew label, video only)
METRICS = {'hook_rate': ('views_3s', 'impressions', 'hook rate (צפיות 3 שניות מתוך חשיפות)', True),
           'views_2s_rate': ('views_2s', 'impressions', 'צפייה רציפה של 2 שניות מתוך חשיפות', True),
           'hold_rate': ('thruplays', 'views_3s', 'hold rate (צפייה מלאה או 15 שניות מתוך צפיות 3 שניות)', True),
           'link_ctr': ('link_clicks', 'impressions', 'הקלקה על קישור מתוך חשיפות', False)}
MIN_ADS = 5
MIN_RANK_ADS = 20
MIN_IMPRESSIONS = 1000
BOOTSTRAP = 1000
STALE_HOURS = 24


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number >= 0 else None


def _video_value(row, field):
    """Meta video fields are lists of {action_type: video_view, value}. Absent field = unknown."""
    items = row.get(field)
    if not isinstance(items, list):
        return None
    values = [_float(i.get('value')) for i in items if isinstance(i, dict) and i.get('action_type') == 'video_view']
    return values[0] if len(values) == 1 and values[0] is not None else None


def history_metrics(row):
    """Impressions, 3-second views and link clicks from one stored history row; None when not known."""
    probe = {**row, 'metric_sources': {'actions_present': row.get('actions_present') is True}}
    known = row.get('actions_present') is True
    views, clicks = (_event(probe, 'actions', (kind,)) if known else None for kind in ('video_view', 'link_click'))
    return {'impressions': _float(row.get('impressions')), 'views_3s': float(views) if views is not None else None,
            'link_clicks': float(clicks) if clicks is not None else None}


def funnel_metrics(row):
    return {key: _video_value(row, field) for key, field in VIDEO_FIELDS.items()}


def read_funnel(data_dir):
    """Read-only insights GET for the video fields, per stored history window. Uses meta_direct's GET-only path."""
    from meta_direct import ACCOUNT, _all_pages, get_token, identity
    deadline = time.monotonic() + 300
    token = get_token(data_dir)
    account = identity(token)
    offset = float(account['timezone_offset_hours_utc'])
    if not -14 <= offset <= 14:
        raise ValueError('Invalid account timezone offset')
    today = datetime.now(timezone(timedelta(hours=offset))).date()
    ranges = {'all': {'date_preset': 'maximum'},
              '90': {'time_range': json.dumps({'since': (today - timedelta(days=90)).isoformat(),
                                               'until': (today - timedelta(days=1)).isoformat()})}}
    windows = {}
    for key, span in ranges.items():
        request = {'level': 'ad', 'time_increment': 'all_days', 'use_unified_attribution_setting': 'true', 'limit': '500',
                   **span, 'fields': 'ad_id,' + ','.join(VIDEO_FIELDS.values())}
        rows = {}
        for row in _all_pages(token, 'act_' + ACCOUNT + '/insights', request, deadline):
            ad = row.get('ad_id') if isinstance(row, dict) else None
            if not isinstance(ad, str) or not ad.isdigit() or ad in rows:
                raise ValueError('Funnel identity invalid')
            rows[ad] = funnel_metrics(row)
        windows[key] = rows
    return {'verified': True, 'checked_at': datetime.now(timezone.utc).isoformat(), 'windows': windows,
            'fields': sorted(VIDEO_FIELDS.values()), 'live_action_allowed': False}


def refresh_funnel(data_dir, reader=read_funnel, now=None):
    """At most once a day; a failed read keeps the last copy and stores no provider text."""
    path = Path(data_dir) / 'creative-funnel.json'
    now = now or datetime.now(timezone.utc)
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
        if now - datetime.fromisoformat(stored['checked_at']) < timedelta(hours=STALE_HOURS):
            return False
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        result = reader(data_dir)
        if result.get('verified') is not True:
            raise ValueError('Unverified funnel read')
    except Exception:
        return False
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    pending.replace(path)
    return True


# --- funnel analysis -----------------------------------------------------------------------------------------------

def _rate(members, metric):
    num, den = METRICS[metric][:2]
    rows = [m for m in members if m['funnel'].get(num) is not None and m['funnel'].get(den)]
    total = sum(m['funnel'][den] for m in rows)
    return (sum(m['funnel'][num] for m in rows) / total if total else None), rows


def _eligible(ads, metric):
    num, den, _, video = METRICS[metric]
    return [a for a in ads if (not video or (a.get('tags') or {}).get('format') == 'video')
            and a['funnel'].get(num) is not None and (a['funnel'].get(den) or 0) >= (MIN_IMPRESSIONS if den == 'impressions' else 1)]


def _order(ad):
    return ad.get('first_date') or hashlib.sha256(str(ad['ad_id']).encode()).hexdigest()


def compare_rate(ads, tag, value, metric, seed=''):
    """A tag value against the other known values on one funnel rate: interval, sign test, out-of-sample half."""
    pool = [a for a in _eligible(ads, metric) if (a.get('tags') or {}).get(tag) is not None]
    group = [a for a in pool if a['tags'][tag] == value]
    rest = [a for a in pool if a['tags'][tag] != value]
    g, _ = _rate(group, metric)
    r, _ = _rate(rest, metric)
    result = {'tag': tag, 'value': value, 'metric': metric, 'metric_label': METRICS[metric][2],
              'group': {'ads': len(group), 'rate': g}, 'rest': {'ads': len(rest), 'rate': r}}
    if min(len(group), len(rest)) < MIN_ADS or not g or not r:
        return {**result, 'verdict': 'not_enough_data'}
    rng, ratios = random.Random(f'{seed}:{tag}:{value}:{metric}'), []
    for _ in range(BOOTSTRAP):
        a, _ = _rate([rng.choice(group) for _ in group], metric)
        b, _ = _rate([rng.choice(rest) for _ in rest], metric)
        if a is not None and b:
            ratios.append(a / b)
    ratios.sort()
    interval = (ratios[int(0.025 * len(ratios))], ratios[int(0.975 * len(ratios)) - 1]) if len(ratios) >= 0.95 * BOOTSTRAP else None
    num, den = METRICS[metric][:2]
    wins = sum(1 for a in group if a['funnel'][num] / a['funnel'][den] > r)
    losses = sum(1 for a in group if a['funnel'][num] / a['funnel'][den] < r)
    sign_status, p = gate(wins, losses)
    ratio = g / r
    ordered = sorted(group + rest, key=_order)
    member, halves = {id(a) for a in group}, []
    for part in (ordered[:len(ordered) // 2], ordered[len(ordered) // 2:]):
        pg, pr = [a for a in part if id(a) in member], [a for a in part if id(a) not in member]
        x, y = _rate(pg, metric)[0], _rate(pr, metric)[0]
        halves.append(x / y if len(pg) >= 3 and len(pr) >= 3 and x is not None and y else None)
    oos = 'not_checkable' if None in halves else 'held' if all((h > 1) == (ratio > 1) and h != 1 for h in halves) else 'reversed'
    result.update(ratio=round(ratio, 2), interval=[round(x, 2) for x in interval] if interval else None,
                  checks={'sign_test': {'for': wins, 'against': losses, 'p_value': round(p, 4), 'status': sign_status},
                          'out_of_sample': {'status': oos, 'halves': [round(h, 2) if h else None for h in halves]}})
    if interval and oos == 'held' and sign_status == 'supported' and interval[0] > 1:
        return {**result, 'verdict': 'higher'}
    if interval and oos == 'held' and sign_status == 'not_supported' and interval[1] < 1:
        return {**result, 'verdict': 'lower'}
    return {**result, 'verdict': 'unclear'}


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return cov / (vx * vy) ** 0.5 if vx and vy else None


def links_to_roas(ads, min_spend, seed=''):
    """Does each funnel rate go with ROAS here? Rank correlation over ads with enough spend and known results."""
    out = {}
    for metric in METRICS:
        num, den = METRICS[metric][:2]
        rows = [a for a in _eligible(ads, metric) if a['spend'] >= min_spend and a.get('revenue') is not None
                and a.get('purchases') is not None]
        result = {'metric_label': METRICS[metric][2], 'ads': len(rows)}
        if len(rows) < MIN_RANK_ADS:
            out[metric] = {**result, 'verdict': 'not_enough_data'}
            continue
        pairs = [(a['funnel'][num] / a['funnel'][den], a['revenue'] / a['spend']) for a in rows]
        rho = spearman(*zip(*pairs))
        rng, boot = random.Random(f'{seed}:{metric}'), []
        for _ in range(BOOTSTRAP):
            sample = [rng.choice(pairs) for _ in pairs]
            value = spearman(*zip(*sample))
            if value is not None:
                boot.append(value)
        boot.sort()
        interval = (boot[int(0.025 * len(boot))], boot[int(0.975 * len(boot)) - 1]) if len(boot) >= 0.95 * BOOTSTRAP else None
        ordered = sorted(range(len(rows)), key=lambda i: _order(rows[i]))
        halves = [spearman(*zip(*[pairs[i] for i in part])) for part in (ordered[:len(ordered) // 2], ordered[len(ordered) // 2:])]
        held = rho is not None and None not in halves and all((h > 0) == (rho > 0) and h != 0 for h in halves)
        verdict = ('goes_with' if rho > 0 else 'against') if interval and held and (interval[0] > 0 or interval[1] < 0) else 'no_clear_link'
        out[metric] = {**result, 'rho': round(rho, 2) if rho is not None else None,
                       'interval': [round(x, 2) for x in interval] if interval else None,
                       'halves': [round(h, 2) if h is not None else None for h in halves], 'verdict': verdict}
    return out


def drop_off(ads):
    """Pooled viewing curve of video ads: share of plays still watching at 25/50/75/100%, and the steepest step."""
    rows = [a for a in ads if (a.get('tags') or {}).get('format') == 'video' and a['funnel'].get('plays')
            and all(a['funnel'].get(k) is not None for k in CURVE)]
    if len(rows) < MIN_ADS:
        return {'ads': len(rows), 'status': 'not_enough_data'}
    plays = sum(a['funnel']['plays'] for a in rows)
    curve = {k: sum(a['funnel'][k] for a in rows) / plays for k in CURVE}
    steps = list(zip(('התחלה', '25%', '50%', '75%'), ('25%', '50%', '75%', '100%'), [1.0] + [curve[k] for k in CURVE[:-1]],
                     [curve[k] for k in CURVE]))
    worst = max(steps, key=lambda s: (s[2] - s[3]) / s[2] if s[2] else 0)
    return {'ads': len(rows), 'status': 'ready', 'curve': {k: round(v, 3) for k, v in curve.items()},
            'steepest': {'from': worst[0], 'to': worst[1], 'lost_share': round((worst[2] - worst[3]) / worst[2], 3) if worst[2] else None}}


# --- opening frames ------------------------------------------------------------------------------------------------

def opening_frames(path, duration_ms, have=()):
    """Extra JPEG stills in the first 3 seconds, same scaling as video_validation.frames. Returns [(ms, bytes)]."""
    from video_validation import FRAME_EDGE, _run, tool
    times = [ms for ms in OPENING_TIMES if ms < duration_ms and all(abs(ms - h) >= 250 for h in have)]
    out = []
    with tempfile.TemporaryDirectory(prefix='opening-frames-') as temp:
        for index, ms in enumerate(times):
            target = Path(temp) / f'{index}.jpg'
            _run([tool('ffmpeg'), '-v', 'error', '-ss', f'{ms / 1000:.3f}', '-i', str(path), '-frames:v', '1',
                  '-vf', f"scale='if(gt(iw,ih),{FRAME_EDGE},-2)':'if(gt(iw,ih),-2,{FRAME_EDGE})'", '-q:v', '4', str(target)])
            if not target.is_file() or target.stat().st_size == 0:
                raise ValueError('לא הצלחתי להוציא פריים מהסרטון')
            out.append((ms, target.read_bytes()))
    return out


def add_opening_frames(source, directory):
    """Adds opening stills to one CreativeVision video source in place, bound to the same file bytes."""
    if source.get('media_kind') != 'video':
        return source
    path = Path(directory) / 'drive-assets' / (source['asset_id'] + '-' + source['sha256'] + '.bin')
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
        raise ValueError('Video changed')
    extra = opening_frames(path, source['duration_ms'], [ms for ms, _ in source['frames']])
    if hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
        raise ValueError('Video changed during review')
    source['frames'] = sorted(source['frames'] + extra, key=lambda f: f[0])
    return source


# --- Ad Library context --------------------------------------------------------------------------------------------

LIBRARY_URL = re.compile(r'^https://www\.facebook\.com/ads/library/\?id=\d{1,30}$')
ANGLES = {'gift': re.compile(r'מתנה|מתנות|יום הולדת|לאמא|לאהוב|gift', re.I),
          'offer': re.compile(r'\d+\s?%|הנחה|מבצע|1\s?\+\s?1|₪|ש"ח|קופון|sale', re.I),
          'social_proof': re.compile(r'ביקורת|המלצ|לקוחות|מכר|review', re.I),
          'urgency': re.compile(r'רק היום|עד חצות|אחרונ|נגמר|מוגבל', re.I)}


def _clean(text, limit=300):
    text = unicodedata.normalize('NFKC', text if isinstance(text, str) else '')
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C' or c == ' ')
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def save_library(data_dir, ads, query, now=None):
    """Store Ad Library search results as untrusted outside context: bounded text, checked links, no instructions."""
    kept = []
    for ad in ads if isinstance(ads, list) else []:
        if not isinstance(ad, dict):
            continue
        url = ad.get('ad_snapshot_url') if isinstance(ad.get('ad_snapshot_url'), str) else ''
        start = ad.get('ad_delivery_start_time')
        bodies = ad.get('ad_creative_bodies') if isinstance(ad.get('ad_creative_bodies'), list) else []
        kept.append({'id': _clean(str(ad.get('id', '')), 30), 'page_name': _clean(ad.get('page_name'), 120),
                     'title': _clean(ad.get('ad_creative_link_title')), 'body': _clean(' '.join(b for b in bodies if isinstance(b, str))),
                     'start': int(start) if isinstance(start, (int, float)) else None,
                     'stop': int(ad['ad_delivery_stop_time']) if isinstance(ad.get('ad_delivery_stop_time'), (int, float)) else None,
                     'url': url if LIBRARY_URL.match(url) else None})
    record = {'checked_at': (now or datetime.now(timezone.utc)).isoformat(),
              'query': {k: _clean(str(v), 100) for k, v in (query or {}).items()}, 'ads': kept[:200]}
    path = Path(data_dir) / 'creative-library.json'
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    pending.replace(path)
    return record


def library_context(record, now=None):
    """What competitors run and for how long; context only, no performance, never part of the gate."""
    if not isinstance(record, dict) or not record.get('ads'):
        return None
    now = (now or datetime.now(timezone.utc)).timestamp()
    ads = record['ads']
    days = lambda a: ((a['stop'] or now) - a['start']) / 86400 if a.get('start') else None
    pages = {}
    for ad in ads:
        pages.setdefault(ad['page_name'] or 'לא ידוע', []).append(ad)
    angles = {k: sum(1 for a in ads if rx.search(a['title'] + ' ' + a['body'])) for k, rx in ANGLES.items()}
    long_running = sorted((a for a in ads if (days(a) or 0) >= 30 and not a.get('stop')), key=lambda a: -days(a))[:5]
    return {'checked_at': record['checked_at'], 'query': record['query'], 'ads': len(ads), 'pages': len(pages),
            'top_pages': sorted(((p, len(v)) for p, v in pages.items()), key=lambda x: -x[1])[:5],
            'angle_counts': angles,
            'long_running': [{'page': a['page_name'], 'title': a['title'], 'days': round(days(a)), 'url': a['url']} for a in long_running],
            'note': 'הקשר חיצוני בלבד: מה מתחרים מריצים ולכמה זמן, בלי נתוני ביצועים. מודעה שרצה הרבה זמן לא מוכיחה שהיא רווחית, '
                    'וזה לא נכנס לבדיקת הראיות של החשבון.'}
