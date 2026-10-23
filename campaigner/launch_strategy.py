"""Where new creatives go: a new dated ad set, or an existing one the owner's rule allows.

Advisory only; the executor re-verifies everything on live data. Grounded in the
owner's rulebook:
- new_creatives_new_dated_adset: new creatives go to a new dated ad set by default;
- existing_adset_reuse: an existing ad set only with fewer than N active ads and the
  same product and angle. The product is matched from ad names; the angle of ads
  already running is not known from data, so reuse is proposed only as a candidate
  with an owner question, never decided silently;
- ads_per_adset: 5–8 ads per ad set (fewer is flagged, more is split).
Missing data is unknown: an ad set whose active-ad count cannot be read is not a
reuse candidate.
"""
from datetime import date
import hashlib

from account_history import CATEGORIES

TARGETING = 'broad_il_adult'


def product_of(*texts):
    joined = ' '.join(t for t in texts if isinstance(t, str)).lower()
    return next((label for label, words in CATEGORIES if any(w in joined for w in words)), None)


def gender_of(audience):
    """men / women from the analysis' audience hypothesis, or None when it names both or neither."""
    text = audience if isinstance(audience, str) else ''
    both = 'צעירים וצעירות' in text or 'בני זוג' in text or 'זוגות' in text
    men = both or any(w in text for w in ('גבר', 'בחורים'))
    women = both or any(w in text for w in ('נשים', 'אישה', 'נשי', 'בחורות', 'צעירות'))
    return 'men' if men and not women else 'women' if women and not men else None


GENDER_LABEL = {'men': 'גברים', 'women': 'נשים', None: 'לבחירתך'}


def _param(rulebook, rule, key):
    return ((rulebook.get('rules') or {}).get(rule) or {}).get('params', {}).get(key)


def account_adsets(report):
    """Active ad sets in the last 7 days with their active-ad count and product, from the verified report."""
    window = (report.get('windows') or {}).get('7') or {}
    sets = {s['id']: s for s in window.get('adsets', []) if isinstance(s, dict) and isinstance(s.get('id'), str)}
    counts, names = {}, {}
    for ad in window.get('ads', []):
        if not isinstance(ad, dict) or ad.get('configured_active') is None:
            continue
        sid = ad.get('adset_id')
        if ad['configured_active'] is True:
            counts[sid] = counts.get(sid, 0) + 1
        names.setdefault(sid, []).append(ad.get('name') or '')
    result = []
    for sid, adset in sets.items():
        if adset.get('configured_active') is not True:
            continue
        result.append({'id': sid, 'name': adset.get('name') or sid, 'campaign_id': adset.get('campaign_id'),
                       'active_ads': counts.get(sid, 0) if sid in names else None,
                       'product': product_of(adset.get('name'), *names.get(sid, []))})
    return result


def plan(assets, analysis_rows, report, rulebook, *, today=None, campaign_id=None):
    """assets: uploaded sources; analysis_rows: CreativeVision assets (same order)."""
    today = today or date.today()
    if not isinstance(rulebook, dict) or rulebook.get('status') != 'available':
        raise ValueError('ספר החוקים אינו מאומת; לא נבנית אסטרטגיה')
    low, high = _param(rulebook, 'ads_per_adset', 'min') or 5, _param(rulebook, 'ads_per_adset', 'max') or 8
    reuse_max = _param(rulebook, 'existing_adset_reuse', 'max_active_ads')
    rows = {r['asset_id']: r for r in analysis_rows}
    groups = {}
    for asset in assets:
        row = rows[asset['id']]
        product = product_of(row.get('observed_product'), asset.get('name')) or row.get('observed_product') or 'לא ידוע'
        angle = (row.get('angle') or '').strip() or 'לא ידוע'
        groups.setdefault((product, angle), []).append(asset['id'])
    # Owner's account rules: "כמה שפחות אד-סטים" and each creative a really different concept, so several
    # angles go together into one new dated ad set (named like the account's own, e.g. "וידאו | תכנים חדשים | 05/10").
    # Hard gender separation (owner rule): one ad set per audience gender, proposed from the analysis and
    # confirmed by the owner; its targeting is then copied exactly from a live ad set of that gender.
    genders = {a['id']: gender_of(rows[a['id']].get('audience_hypothesis')) for a in assets}
    mixed = len(groups) > 1 or len(set(genders.values())) > 1
    if mixed:
        kinds = {a.get('media_kind', 'image') for a in assets}
        kind = 'וידאו' if kinds == {'video'} else 'תמונות' if kinds == {'image'} else 'וידאו ותמונות'
        groups = {}
        for a in assets:
            groups.setdefault((kind, 'תכנים חדשים', genders[a['id']]), []).append(a['id'])
    else:
        groups = {(p, ang, genders[ids[0]]): ids for (p, ang), ids in groups.items()}
    citations = ['new_creatives_new_dated_adset', 'ads_per_adset']
    questions, adsets, assignment = [], [], {}
    existing = [s for s in account_adsets(report) if campaign_id is None or s['campaign_id'] == campaign_id]
    used = set()
    for (product, angle, gender), ids in groups.items():
        chunks = [ids[i:i + high] for i in range(0, len(ids), high)]
        for index, chunk in enumerate(chunks):
            key = 'g_' + hashlib.sha256(f'{product}|{angle}|{gender}|{index}'.encode()).hexdigest()[:10]
            target, rationale, budget_needed = {'mode': 'new'}, [], True
            candidate = None
            if reuse_max and not mixed and len(chunk) < low:
                candidate = next((s for s in existing if s['id'] not in used and s['product'] == product
                                  and s['active_ads'] is not None and s['active_ads'] < reuse_max
                                  and s['active_ads'] + len(chunk) <= high), None)
            if candidate:
                used.add(candidate['id'])
                target = {'mode': 'existing', 'adset_id': candidate['id'], 'rule_cited': 'existing_adset_reuse'}
                budget_needed = False
                if 'existing_adset_reuse' not in citations:
                    citations.append('existing_adset_reuse')
                rationale.append(f'באד-סט "{candidate["name"]}" יש {candidate["active_ads"]} מודעות פעילות (פחות מ-{reuse_max}) '
                                 f'ואותו מוצר ({product}); לפי החוק שלך אפשר להוסיף אליו.')
                questions.append(f'האם הזווית "{angle}" זהה לזווית של המודעות שכבר רצות ב"{candidate["name"]}"? '
                                 'החוק דורש אותה זווית, ואת זה אי אפשר לבדוק מהנתונים.')
            else:
                rationale.append('לפי החוק שלך: קריאייטיבים חדשים נכנסים לאד-סט חדש מתוארך בתוך הקמפיין הרץ.')
                if mixed:
                    rationale.append('כמה זוויות שונות נכנסות יחד לאד-סט אחד, כי החוק שלך הוא "כמה שפחות אד-סטים" וכל קריאייטיב הוא קונספט אחר.')
                if len(chunk) < low:
                    rationale.append(f'יש כאן {len(chunk)} מודעות; החוק שלך הוא {low}–{high} מודעות לאד-סט.')
            label = f'{product} | {angle[:60]}' + (f' | {GENDER_LABEL[gender]}' if mixed and gender else '') + f' | {today.strftime("%d/%m")}'
            if target['mode'] == 'new':
                why = '; '.join(sorted({(rows[i].get('audience_hypothesis') or '')[:140] for i in chunk if rows[i].get('audience_hypothesis')}))
                audience = {'gender': gender, 'reason': f'קהל מוצע: {GENDER_LABEL[gender]}' + (f' (לפי הניתוח: {why})' if why else '') +
                            '. הטירגוט יועתק בדיוק מאד-סט פעיל של אותו מגדר; צריך את האישור שלך.'}
            else:
                audience = None
            adsets.append({'key': key, 'label': label if target['mode'] == 'new' else candidate['name'],
                           'rationale': ' '.join(rationale), 'product': product[:160], 'angle': angle[:160],
                           'targeting_mode': TARGETING if target['mode'] == 'existing' else
                           {'men': 'copy_men', 'women': 'copy_women'}.get(gender, 'choose'), 'target': target, 'budget_minor': None,
                           'budget_required': budget_needed, 'audience': audience})
            for asset_id in chunk:
                assignment[asset_id] = key
    return {'adsets': adsets, 'assignment': assignment, 'citations': citations, 'owner_questions': questions,
            'existing_considered': existing, 'live_action_allowed': False}
