"""Source-bound historical economics. Scenarios never become spend authority."""
import math
from decimal import Decimal, InvalidOperation
from business import ACTIVE as BUSINESS


def current_recorded_economics(result, source_ref):
    """Observed store aggregates, not costs audited or an ad-level CPA target."""
    if not isinstance(result, dict) or result.get('shopDomain') != BUSINESS.shop:
        return None
    if result.get('chartHint', {}).get('currencyCode') != BUSINESS.currency:
        return None
    names = [x.get('name') for x in result.get('columns', [])]
    required = ['new_or_returning_customer', 'orders', 'net_sales', 'shipping_charges', 'taxes', 'cost_of_goods_sold']
    if names != required:
        return None
    segments = []
    try:
        for values in result.get('rows', []):
            if len(values) != len(required) or values[0] not in ('New', 'Returning'):
                return None
            numbers = [Decimal(str(x)) for x in values[1:]]
            if (not all(x.is_finite() for x in numbers) or numbers[0] <= 0
                    or numbers[0] != numbers[0].to_integral_value()):
                return None
            segments.append(dict(zip(required, [values[0], *[float(x) for x in numbers]])))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not segments or len({x['new_or_returning_customer'] for x in segments}) != len(segments):
        return None
    return {'status': 'recorded_values_observed', 'source_ref': source_ref,
            'observed_date': result.get('observed_date'), 'query': result.get('query'), 'currency': BUSINESS.currency,
            'segments': segments, 'spend_target': None, 'automatic_action_allowed': False,
            'limitations': ['עלויות המוצר הן העלויות הרשומות בחנות; שלמות הכיסוי לא אומתה',
                            'מס שנרשם כאפס אינו הוכחה שאין חבות מע״מ',
                            'אין שיוך של לקוח חדש או חוזר לכל אדסט מתוך הסיכום הזה',
                            'משלוח בפועל, סליקה ורזרבת רווח טרם הותאמו']}


def summarize_economics(profile, source_ref):
    missing = {'status': 'not_collected', 'source_ref': source_ref,
               'spend_target': None, 'automatic_action_allowed': False, 'segments': []}
    if not isinstance(profile, dict) or profile.get('shop') != BUSINESS.shop or profile.get('currency') != BUSINESS.currency:
        return missing
    segments = []
    for key, label in (('New', 'לקוח חדש'), ('Returning', 'הזמנה מלקוח חוזר')):
        row = profile.get('segments', {}).get(key, {})
        amount = row.get('before_ad_contribution_scenario_vat18_included')
        orders = row.get('orders')
        if (isinstance(amount, bool) or not isinstance(amount, (int, float))
                or not math.isfinite(amount) or not isinstance(orders, int) or orders <= 0):
            continue
        segments.append({'segment': key, 'label': label, 'orders': orders,
                         'historical_contribution_scenario': amount,
                         'scope': 'historical_order_before_advertising_not_target',
                         'cost_coverage_gap': row.get('cost_coverage_reconciliation_gap')})
    if not segments:
        return missing
    return {**missing, 'status': 'historical_scenarios_observed',
            'as_of': profile.get('as_of'), 'period': profile.get('period'),
            'currency': BUSINESS.currency, 'segments': segments,
            'assumptions': profile.get('inputs', {}),
            'unresolved': list(profile.get('missing_for_verified_limits', [])),
            'limitation': 'קיים חישוב היסטורי לפני פרסום, המבוסס גם על אומדנים. הוא אינו יעד עלות רכישה, רווח מאומת או תקציב מאושר.'}
