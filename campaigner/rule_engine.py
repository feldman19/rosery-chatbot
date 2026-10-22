"""Apply the owner's rulebook to full-day ad results and produce exact proposals.

Pure function: no API, no credentials, no execution. Every PAUSE/SCALE output is
a proposal that still needs the owner's explicit approval and a readback after
execution. Missing data stays missing; it is never treated as zero.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite
import re

from rulebook import ACCOUNT, cite, param

PAUSE, SCALE, HOLD, SUCCESS, KEEP_TESTING, UNTESTED, NO_DATA = (
    'PAUSE', 'SCALE', 'HOLD', 'SUCCESS', 'KEEP_TESTING', 'UNTESTED', 'INSUFFICIENT_DATA')
LABELS = {PAUSE: 'להשהות', SCALE: 'ווינר — להגדיל תקציב', HOLD: 'להחזיק', SUCCESS: 'הצלחה — להשאיר',
          KEEP_TESTING: 'להמשיך לבדוק', UNTESTED: 'עוד לא נבדקה', NO_DATA: 'חסרים נתונים'}
PRIORITY = {PAUSE: 0, SCALE: 1, NO_DATA: 2, KEEP_TESTING: 3, HOLD: 4, SUCCESS: 5, UNTESTED: 6}
RETARGETING = re.compile(r'retarget|ריטרגט|רימרקט|remarket|\bdpa\b|catalog|קטלוג', re.I)


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None


def _stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except ValueError:
        return None


def _money(value):
    return f'₪{value:,.0f}' if value is not None else 'לא ידוע'


def _ratio_text(value):
    return f'{value:.2f}' if value is not None else 'לא ידוע'


def ads_manager_url(rulebook, ad_ids):
    return param(rulebook, 'ads_manager_link', 'url') + ','.join(ad_ids)


def thresholds(rulebook, *, retargeting, click_only_attribution):
    ladder = rulebook['rules']['roas_ladder']['params']
    fail, hold, winner = ladder['fail_below'], ladder['hold_up_to'], ladder['winner_above']
    applied = ['roas_ladder']
    if not click_only_attribution:
        fail = max(fail, param(rulebook, 'default_attribution_threshold', 'fail_below'))
        applied.append('default_attribution_threshold')
    if retargeting:
        multiplier = param(rulebook, 'retargeting_double_threshold', 'multiplier')
        fail, hold, winner = fail * multiplier, hold * multiplier, winner * multiplier
        applied.append('retargeting_double_threshold')
    return {'fail_below': round(fail, 4), 'hold_up_to': round(hold, 4), 'winner_above': round(winner, 4)}, applied


def _rescue(rulebook, funnel):
    """True/False when the rescue signals are known, None when the data is missing."""
    p = rulebook['rules']['rescue_signals']['params']
    atc, checkout, ctr = (_number(funnel.get(k)) for k in ('add_to_cart', 'initiate_checkout', 'link_ctr'))
    intent = None if atc is None or checkout is None else atc >= p['min_add_to_cart'] and checkout >= p['min_checkout']
    clicks = None if ctr is None else ctr >= p['min_link_ctr']
    if intent or clicks:
        return True
    return None if intent is None or clicks is None else False


def _recent_change(row, now, days):
    change = row.get('change_evidence') or {}
    updated = _stamp(change.get('updated_time')) if change.get('status') == 'entity_update_observed' else None
    if updated is None:
        return None
    return now - updated < timedelta(days=days)


def evaluate_ad(row, window, rulebook, *, attribution_click_only=False):
    """One ad, one complete 3-day window. Returns a decision dict (no execution)."""
    citations = ['complete_days_only', 'pause_by_ad_only', 'state_what_data_supports']
    spend, purchases, revenue = (_number(row.get(k)) for k in ('spend', 'purchases', 'attributed_revenue'))
    funnel = row.get('funnel') or {}
    days = window.get('days') or param(rulebook, 'ad_min_full_days', 'ad_days')
    campaign_text = ' '.join(str(row.get(k) or '') for k in ('campaign_name', 'adset_name'))
    retargeting = row.get('objective') == 'PRODUCT_CATALOG_SALES' or bool(RETARGETING.search(campaign_text))
    limits, applied = thresholds(rulebook, retargeting=retargeting, click_only_attribution=attribution_click_only)
    citations += applied
    roas = revenue / spend if spend and revenue is not None else (0.0 if spend and purchases == 0 else None)
    cpa = spend / purchases if spend is not None and purchases else None
    avg_daily = spend / days if spend is not None else None
    numbers = {'spend': spend, 'purchases': purchases, 'attributed_revenue': revenue, 'roas': roas, 'cpa': cpa,
               'avg_daily_spend': avg_daily, 'add_to_cart': _number(funnel.get('add_to_cart')),
               'initiate_checkout': _number(funnel.get('initiate_checkout')), 'link_ctr': _number(funnel.get('link_ctr'))}
    supports, not_supported = [], [
        'ROAS של מטא הוא ייחוס, לא רווח ולא תוספתיות.',
        'ההחלטה מבוססת על 3 ימים מלאים בלבד (ללא היום הנוכחי).']
    if not attribution_click_only:
        not_supported.append('הנתונים כוללים ייחוס צפייה (ברירת מחדל), לכן סף הכישלון הועלה ל-2.6.')
    if retargeting:
        not_supported.append('זוהה ריטרגטינג/קטלוג לפי שם או יעד הקמפיין — הספים הוכפלו. לוודא שהזיהוי נכון.')
    action, title, why = NO_DATA, 'חסרים נתונים', ''
    min_spend = param(rulebook, 'roas_ladder', 'min_spend')
    rescue = rulebook['rules']['rescue_signals']['params']
    gate = param(rulebook, 'spend_gate', 'min_daily_spend')
    if spend is None or purchases is None or (purchases > 0 and revenue is None):
        why = 'חסרים נתוני הוצאה, רכישות או הכנסה למודעה בחלון. נתון חסר אינו אפס — אין החלטה.'
    elif spend < min_spend and avg_daily < gate:
        action, citations = UNTESTED, citations + ['spend_gate']
        title = 'הוצאה נמוכה — המודעה עוד לא נבדקה'
        why = (f'המודעה הוציאה בממוצע {_money(avg_daily)} ליום, מתחת לשער ההוצאה של {_money(gate)} ליום. '
               'זה לא כישלון — מטא פשוט לא נותנת לה תקציב. לא מכבים מודעה רדומה על סמך תוצאות.')
    elif spend < min_spend:
        action, citations = KEEP_TESTING, citations + ['kill_requires_spend']
        if purchases == 0 and spend >= rescue['watch_from']:
            signal = _rescue(rulebook, funnel)
            citations.append('rescue_signals')
            if signal:
                title = 'אין רכישה עדיין, אבל יש סימנים טובים'
                why = (f'הוציאה {_money(spend)} בלי רכישה, אבל יש סימני כוונה (הוספות לסל/צ׳קאאוט או CTR גבוה). '
                       f'לפי החוק ממשיכים עד {_money(rescue["extended_limit"])}.')
            else:
                title = 'מתקרבת לסף בלי רכישה'
                why = (f'הוציאה {_money(spend)} בלי רכישה. ' +
                       ('אין סימני כוונה מספיקים. ' if signal is False else 'נתוני הוספה לסל/CTR חסרים. ') +
                       f'אם תגיע ל-{_money(min_spend)} בלי שיפור — תוצע השהיה.')
        else:
            title = 'עוד לא הגיעה לסף ההחלטה'
            why = f'הוציאה {_money(spend)} מתוך {_money(min_spend)} שנדרשים לפני החלטה. ממשיכים לתת לה לרוץ.'
            if roas is not None and roas > limits['winner_above']:
                why += ' התחלה מבטיחה, אבל עוד מוקדם לסקייל.'
    elif roas < limits['fail_below']:
        citations += ['kill_requires_spend']
        signal = _rescue(rulebook, funnel) if purchases == 0 else False
        if purchases == 0:
            citations.append('rescue_signals')
        if purchases == 0 and spend < rescue['extended_limit'] and signal:
            action, title = KEEP_TESTING, 'נכשלת ברכישות, אבל יש סימני כוונה — הארכה עד 300 ₪'
            why = (f'הוציאה {_money(spend)} בלי רכישה, אבל יש 2+ הוספות לסל וצ׳קאאוט או Link CTR מעל 1.5%. '
                   f'לפי החוק ממשיכים עד {_money(rescue["extended_limit"])} ואז מחליטים.')
        else:
            action, title = PAUSE, 'מפסידה כסף — להשהות'
            why = (f'הוציאה {_money(spend)} ב-3 ימים מלאים עם ROAS {_ratio_text(roas)}, '
                   f'מתחת לסף הכישלון ({limits["fail_below"]:.2f}).')
            if purchases == 0 and spend < rescue['extended_limit'] and signal is None:
                not_supported.append('נתוני הוספה לסל/צ׳קאאוט/CTR חסרים, ולכן לא ניתן לבדוק אם המודעה זכאית להארכה עד 300 ₪. '
                                     'לבדוק במנהל המודעות לפני האישור.')
            supports.append(f'ההוצאה ({_money(spend)}) עברה את סף ההחלטה ({_money(min_spend)}).')
    elif roas <= limits['hold_up_to']:
        action, title = HOLD, 'לא הצלחה — להחזיק בלי שינוי'
        why = (f'ROAS {_ratio_text(roas)} — בין {limits["fail_below"]:.2f} ל-{limits["hold_up_to"]:.2f}. '
               'לא מכבים ולא מגדילים.')
    elif roas <= limits['winner_above']:
        action, title = SUCCESS, 'הצלחה — להשאיר כמו שהיא'
        why = f'ROAS {_ratio_text(roas)} מעל {limits["hold_up_to"]:.2f}. מודעה טובה; עוד לא ברמת ווינר לסקייל.'
    elif purchases < param(rulebook, 'single_purchase_not_verdict', 'min_purchases_for_scale'):
        action, citations = SUCCESS, citations + ['single_purchase_not_verdict']
        title = 'נראית מצוין, אבל רכישה אחת אינה פסק דין'
        why = f'ROAS {_ratio_text(roas)} מרכישה בודדת. מחכים לרכישה נוספת לפני הגדלת תקציב.'
    else:
        action, citations = SCALE, citations + ['scale_step', 'scale_in_place', 'scale_stabilization']
        title = 'ווינר — להגדיל את תקציב האד-סט ב-20%'
        why = (f'ROAS {_ratio_text(roas)} מעל סף הווינר ({limits["winner_above"]:.2f}) עם {purchases:g} רכישות '
               f'ו-{_money(spend)} הוצאה. מגדילים במקום (באותו אד-סט), ב-20% בלבד.')
        supports.append(f'{purchases:g} רכישות ב-3 ימים מלאים.')
    if cpa is not None:
        breakeven = param(rulebook, 'breakeven', 'breakeven_cpa')
        (supports if cpa <= breakeven else not_supported).append(
            f'עלות לרכישה {_money(cpa)} מול נקודת איזון של כ-{_money(breakeven)}.')
        citations.append('breakeven')
    return {'id': 'ad:' + str(row.get('id')), 'entity_type': 'ad', 'entity_id': row.get('id'),
            'entity_name': row.get('name') or row.get('id'), 'adset_id': row.get('adset_id'),
            'adset_name': row.get('adset_name'), 'campaign_id': row.get('campaign_id'),
            'campaign_name': row.get('campaign_name'), 'configured_active': row.get('configured_active'),
            'action': action, 'label': LABELS[action], 'title': title, 'why': why,
            'numbers': numbers, 'thresholds': limits, 'retargeting': retargeting,
            'window': {'since': window.get('since'), 'until': window.get('until'), 'days': days},
            'data_supports': supports, 'data_does_not_support': not_supported,
            'rules': [cite(rulebook, rule) for rule in dict.fromkeys(citations)],
            'ads_manager_url': ads_manager_url(rulebook, [str(row.get('id'))]),
            'priority': PRIORITY[action]}


def evaluate_report(report, rulebook, *, now=None, attribution_click_only=False):
    """Evaluate every ad in the 3-day window; group winners into ad-set budget proposals."""
    now = now or datetime.now(timezone.utc)
    result = {'status': 'blocked', 'account_id': ACCOUNT, 'decisions': [], 'proposals': [], 'account_alerts': [],
              'blocked_by': [], 'live_action_allowed': False, 'requires_owner_approval': True,
              'rulebook': {'status': rulebook.get('status'), 'source_ref': rulebook.get('source_ref'),
                           'version': rulebook.get('version')}}
    if rulebook.get('status') != 'available':
        result['blocked_by'].append('ספר החוקים לא זמין או שקובץ מקור השתנה')
        return result
    if not isinstance(report, dict) or report.get('account_id') != ACCOUNT:
        result['blocked_by'].append('הדוח לא שייך לחשבון של ROSERY')
        return result
    window = (report.get('windows') or {}).get(str(param(rulebook, 'roas_ladder', 'window_days'))) or {}
    if window.get('partial') is not False:
        result['blocked_by'].append('חלון 3 הימים אינו מלא — לא מחליטים על יום חלקי')
        return result
    fresh = (report.get('freshness') or {}).get('state') == 'fresh'
    if not fresh:
        result['blocked_by'].append('הנתונים אינם עדכניים — אפשר לקרוא את ההמלצות, אבל לא לאשר עד רענון')
    rows = [r for r in window.get('ads', []) if isinstance(r, dict) and isinstance(r.get('id'), str)
            and r.get('result_type') in ('purchase', None) and r.get('configured_active') is not False]
    decisions = [evaluate_ad(row, window, rulebook, attribution_click_only=attribution_click_only) for row in rows]
    by_adset = {}
    for decision in decisions:
        by_adset.setdefault(decision['adset_id'], []).append(decision)
    adsets = {r.get('id'): r for r in window.get('adsets', []) if isinstance(r, dict)}
    seven = {r.get('id'): r for r in ((report.get('windows') or {}).get('7') or {}).get('ads', []) if isinstance(r, dict)}
    active_in_adset = {}
    for row in list(seven.values()) + rows:
        if row.get('configured_active') is True:
            active_in_adset.setdefault(row.get('adset_id'), set()).add(row.get('id'))
    proposals = []
    for decision in decisions:
        if decision['action'] != PAUSE:
            continue
        blockers = [] if fresh else ['data_not_fresh']
        if decision['configured_active'] is not True:
            blockers.append('current_status_unknown')
        remaining = active_in_adset.get(decision['adset_id'], set()) - {
            d['entity_id'] for d in by_adset.get(decision['adset_id'], []) if d['action'] == PAUSE}
        warnings = []
        if decision['adset_id'] and not remaining:
            warnings.append('השהיית המודעה תשאיר את האד-סט בלי מודעות פעילות. לפי החוק לא מחשיכים אד-סט עובד — '
                            'להכין קריאייטיב חלופי לפני ההשהיה.')
        proposals.append({'id': 'pause:' + decision['entity_id'], 'kind': 'pause_ad', 'entity_type': 'ad',
                          'entity_id': decision['entity_id'], 'entity_name': decision['entity_name'],
                          'before': {'status': 'ACTIVE'}, 'after': {'status': 'PAUSED'},
                          'reason': decision['why'], 'warnings': warnings, 'blockers': blockers,
                          'ready_for_approval': not blockers, 'ads_manager_url': decision['ads_manager_url'],
                          'rules': [cite(rulebook, r) for r in ('pause_by_ad_only', 'kill_requires_spend',
                                                               'approval_required', 'readback_required')]
                                   + ([cite(rulebook, 'never_darken_working_adset')] if warnings else []),
                          'rollback': 'להפעיל מחדש את אותה מודעה (ACTIVE). אין מחיקה.'})
    step = param(rulebook, 'scale_step', 'step')
    stabilization = param(rulebook, 'scale_stabilization', 'days')
    for adset_id, items in by_adset.items():
        winners = [d for d in items if d['action'] == SCALE]
        if not winners:
            continue
        adset = adsets.get(adset_id) or {}
        budget = adset.get('daily_budget_minor') if adset.get('budget_known') else None
        blockers = [] if fresh else ['data_not_fresh']
        if not adset_id:
            blockers.append('adset_unknown')
        if budget is None:
            blockers.append('adset_daily_budget_unknown')
        recent = _recent_change(adset, now, stabilization)
        if recent:
            blockers.append('changed_within_stabilization_window')
        warnings = [] if recent is False else ['לא ידוע מתי שונה האד-סט לאחרונה. לוודא שלא היה שינוי תקציב ב-3 הימים האחרונים.']
        after = int(round(budget * (1 + step))) if budget is not None else None
        ids = [d['entity_id'] for d in winners]
        proposals.append({'id': 'scale:' + str(adset_id), 'kind': 'scale_adset_budget', 'entity_type': 'adset',
                          'entity_id': adset_id, 'entity_name': winners[0]['adset_name'] or adset_id,
                          'before': {'daily_budget_minor': budget}, 'after': {'daily_budget_minor': after},
                          'change': '+20%', 'winning_ads': ids,
                          'reason': f'{len(ids)} מודעות ווינר באד-סט: ' + ', '.join(d['entity_name'] for d in winners),
                          'warnings': warnings, 'blockers': blockers, 'ready_for_approval': not blockers,
                          'ads_manager_url': ads_manager_url(rulebook, ids),
                          'rules': [cite(rulebook, r) for r in ('scale_step', 'scale_in_place', 'scale_stabilization',
                                                               'approval_required', 'readback_required')],
                          'rollback': 'להחזיר את התקציב היומי לערך הקודם.'})
    result['account_alerts'] = account_alerts(report, rulebook)
    result.update(status='ready' if fresh else 'stale_data', decisions=sorted(
        decisions, key=lambda d: (d['priority'], -(d['numbers']['spend'] or 0))), proposals=proposals,
        window={'since': window.get('since'), 'until': window.get('until')})
    return result


def account_alerts(report, rulebook):
    """Structure checks from the account rules; informational, never an action."""
    alerts = []
    window = (report.get('windows') or {}).get('7') or {}
    ads = [r for r in window.get('ads', []) if isinstance(r, dict)]
    limits = rulebook['rules']['ads_per_adset']['params']
    spending = {}
    for row in ads:
        if (_number(row.get('spend')) or 0) > 0 and row.get('configured_active') is True:
            spending.setdefault((row.get('adset_id'), row.get('adset_name')), []).append(row)
    for (adset_id, name), rows in spending.items():
        if len(rows) > limits['max']:
            alerts.append({'kind': 'too_many_ads', 'entity_id': adset_id,
                           'text': f'באד-סט "{name or adset_id}" יש {len(rows)} מודעות פעילות שמוציאות. '
                                   f'החוק: לגזום ל-{limits["min"]}–{limits["max"]} שלוקחות הוצאה.',
                           'rule': cite(rulebook, 'ads_per_adset')})
    campaigns = {}
    for row in ads:
        text = ' '.join(str(row.get(k) or '') for k in ('campaign_name', 'adset_name'))
        if row.get('objective') == 'PRODUCT_CATALOG_SALES' or RETARGETING.search(text):
            spend = _number(row.get('spend'))
            if spend:
                campaigns.setdefault((row.get('campaign_id'), row.get('campaign_name')), []).append((spend, row))
    share = param(rulebook, 'catalog_single_ad_risk', 'share')
    for (campaign_id, name), items in campaigns.items():
        total = sum(spend for spend, _ in items)
        top_spend, top = max(items, key=lambda item: item[0])
        if total > 0 and len(items) > 0 and top_spend / total >= share:
            alerts.append({'kind': 'catalog_single_ad_dependency', 'entity_id': campaign_id,
                           'text': f'בקמפיין "{name or campaign_id}" מודעה אחת ({top.get("name")}) לוקחת '
                                   f'{top_spend / total:.0%} מההוצאה ב-7 ימים. סיכון ידוע — כדאי להכין חלופה.',
                           'rule': cite(rulebook, 'catalog_single_ad_risk')})
    return alerts
