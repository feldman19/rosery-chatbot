"""Contribution after costs, next to Meta's ROAS and never merged with it. Read-only.

Two modes chosen by the owner:
- simple: Shopify net sales and recorded cost of goods only;
- detailed: plus cost fields the owner defines (per order, % of revenue, per unit,
  monthly fixed), like custom expenses in Triple Whale.
"Net profit" is shown only in detailed mode and only when fixed costs were entered;
otherwise the result is a contribution. Sales without a recorded cost are left out
of the cost calculation and shown, never treated as zero cost. Shopify records VAT
as zero for this store, so by default net sales include VAT and it is removed here.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from business import ACTIVE as BUSINESS

STORE_TZ = BUSINESS.timezone

FIELD_TYPES = {'per_order': 'לכל הזמנה', 'percent_of_revenue': 'אחוז מההכנסה', 'per_unit': 'לכל יחידה',
               'monthly_fixed': 'קבוע חודשי'}
DEFAULTS = {'mode': 'simple', 'vat_rate': 0.18, 'sales_include_vat': True, 'cogs_include_vat': False, 'fields': []}
SETTING_KEYS = set(DEFAULTS)
FIELD_KEYS = {'name', 'type', 'amount', 'vat_included'}


def _dec(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _out(value, places=0):
    return round(float(value), places) if value is not None else None


def validate_settings(body):
    """Exact shape only; returns a clean copy or raises ValueError with a Hebrew message."""
    if not isinstance(body, dict) or set(body) != SETTING_KEYS:
        raise ValueError('הגדרות עלויות לא תקינות')
    if body['mode'] not in ('simple', 'detailed'):
        raise ValueError('מצב חישוב לא תקין')
    vat = _dec(body['vat_rate'])
    if vat is None or not Decimal(0) <= vat <= Decimal('0.5'):
        raise ValueError('שיעור מע"מ לא תקין')
    if not all(isinstance(body[k], bool) for k in ('sales_include_vat', 'cogs_include_vat')):
        raise ValueError('הגדרת מע"מ לא תקינה')
    fields = body['fields']
    if not isinstance(fields, list) or len(fields) > 30:
        raise ValueError('עד 30 שדות הוצאה')
    clean = []
    for field in fields:
        if not isinstance(field, dict) or set(field) != FIELD_KEYS:
            raise ValueError('שדה הוצאה לא תקין')
        name = field['name'].strip() if isinstance(field['name'], str) else ''
        amount = _dec(field['amount'])
        if not 1 <= len(name) <= 60 or field['type'] not in FIELD_TYPES or amount is None or amount < 0 \
                or not isinstance(field['vat_included'], bool):
            raise ValueError(f'שדה הוצאה לא תקין: {name or "ללא שם"}')
        if field['type'] == 'percent_of_revenue' and amount > 100:
            raise ValueError(f'אחוז גבוה מ-100: {name}')
        clean.append({'name': name, 'type': field['type'], 'amount': float(amount), 'vat_included': field['vat_included']})
    return {'mode': body['mode'], 'vat_rate': float(vat), 'sales_include_vat': body['sales_include_vat'],
            'cogs_include_vat': body['cogs_include_vat'], 'fields': clean}


class ProfitSettings:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            return validate_settings(json.loads(self.path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            return dict(DEFAULTS, fields=[])

    def save(self, body):
        clean = validate_settings(body)
        pending = self.path.with_suffix('.pending')
        pending.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding='utf-8')
        pending.replace(self.path)
        return clean


def _ex_vat(value, include, rate):
    return value / (1 + rate) if value is not None and include else value


def window_profit(row, days, meta_spend, settings):
    rate = Decimal(str(settings['vat_rate']))
    taxes = _dec(row.get('taxes'))
    # When Shopify records tax, net sales already exclude it; when it records none, the owner's setting decides.
    sales_vat = settings['sales_include_vat'] and (taxes is None or taxes == 0)
    recorded = _dec(row.get('net_sales_with_cost_recorded'))
    unrecorded = _dec(row.get('net_sales_without_cost_recorded'))
    net = _ex_vat(_dec(row.get('net_sales')), sales_vat, rate)
    # Shopify's with/without-cost split is not net of returns; use it only as the coverage
    # share, applied to net sales after returns.
    share = recorded / (recorded + unrecorded) if recorded is not None and unrecorded is not None \
        and recorded + unrecorded > 0 else None
    with_cost = net * share if net is not None and share is not None else None
    without_cost = net - with_cost if with_cost is not None else None
    cogs = _dec(row.get('cost_of_goods_sold'))
    cogs = _ex_vat(-cogs if cogs is not None and cogs < 0 else cogs, settings['cogs_include_vat'], rate)
    orders, units = _dec(row.get('orders')), _dec(row.get('net_items_sold'))
    shipping = _ex_vat(_dec(row.get('shipping_charges')), sales_vat, rate)
    missing = []
    if with_cost is None or cogs is None:
        missing.append('עלות סחורה או מכירות עם עלות רשומה')
    gross = with_cost - cogs if with_cost is not None and cogs is not None else None
    margin = gross / with_cost if gross is not None and with_cost else None
    lines, variable, fixed = [], Decimal(0), Decimal(0)
    detailed = settings['mode'] == 'detailed'
    if detailed:
        if shipping is not None and gross is not None:
            gross += shipping  # shipping collected is revenue; its real cost is an owner field
        for field in settings['fields']:
            amount = Decimal(str(field['amount']))
            amount = _ex_vat(amount, field['vat_included'], rate) if field['type'] != 'percent_of_revenue' else amount
            base = {'per_order': orders, 'per_unit': units, 'percent_of_revenue': with_cost,
                    'monthly_fixed': Decimal(days) / Decimal(30)}[field['type']]
            if base is None:
                # A cost that cannot be computed is unknown, so everything after it is unknown too.
                missing.append(f'בסיס לחישוב "{field["name"]}"')
                variable = None
                continue
            cost = amount * base / (100 if field['type'] == 'percent_of_revenue' else 1)
            lines.append({'name': field['name'], 'type': FIELD_TYPES[field['type']], 'cost': _out(cost)})
            if field['type'] == 'monthly_fixed':
                fixed += cost
            elif variable is not None:
                variable += cost
    contribution = gross - variable if gross is not None and variable is not None else None
    spend = _dec(meta_spend)
    if spend is None:
        missing.append('הוצאת מטא לאותה תקופה')
    has_fixed = detailed and any(f['type'] == 'monthly_fixed' for f in settings['fields'])
    after_ads = contribution - spend if contribution is not None and spend is not None else None
    return {'days': days, 'orders': _out(orders), 'revenue_ex_vat': _out(net), 'revenue_with_cost_ex_vat': _out(with_cost),
            'revenue_without_cost_ex_vat': _out(without_cost), 'cogs_ex_vat': _out(cogs),
            'cost_coverage': _out(with_cost / (with_cost + without_cost), 3) if with_cost is not None and without_cost is not None
                             and with_cost + without_cost > 0 else None,
            'gross_margin': _out(margin, 3),
            'revenue_basis': {'net_sales': _out(_dec(row.get('net_sales')), 2),
                              'shipping_charges': _out(_dec(row.get('shipping_charges')), 2),
                              'taxes': _out(taxes, 2), 'discounts': _out(_dec(row.get('discounts')), 2),
                              'returns': _out(_dec(row.get('returns')), 2),
                              'total_sales': _out(_dec(row.get('total_sales')), 2),
                              'shipping_ex_vat': _out(shipping, 2), 'vat_removed': sales_vat},
            'cost_lines': lines, 'contribution': _out(contribution), 'meta_spend': _out(spend),
            'contribution_per_shekel': _out(contribution / spend, 2) if contribution is not None and spend else None,
            'after_ads': _out(after_ads),
            'net_profit': _out(after_ads - fixed) if has_fixed and after_ads is not None else None,
            # The label always matches `contribution`; net profit is a separate value with its own label.
            'label': 'תרומה אחרי עלויות', 'missing': missing}


def products(rows, settings, limit=10):
    rate = Decimal(str(settings['vat_rate']))
    result = []
    for row in rows or []:
        recorded, unrecorded = _dec(row.get('net_sales_with_cost_recorded')), _dec(row.get('net_sales_without_cost_recorded'))
        net = _dec(row.get('net_sales'))
        if recorded is None or unrecorded is None or recorded + unrecorded <= 0:
            continue  # unknown cost coverage: no margin is shown for this product
        if net is not None:
            recorded = net * recorded / (recorded + unrecorded)  # same returns-adjusted share as the totals
        sales = _ex_vat(recorded, settings['sales_include_vat'], rate)
        cogs = _dec(row.get('cost_of_goods_sold'))
        cogs = _ex_vat(-cogs if cogs is not None and cogs < 0 else cogs, settings['cogs_include_vat'], rate)
        if sales is None or cogs is None or sales <= 0:
            continue
        result.append({'product': row.get('product_title'), 'sales_ex_vat': _out(sales), 'cogs_ex_vat': _out(cogs),
                       'gross_margin': _out((sales - cogs) / sales, 3), 'units': _out(_dec(row.get('net_items_sold'))),
                       'without_cost_ex_vat': _out(_ex_vat(_dec(row.get('net_sales_without_cost_recorded')),
                                                           settings['sales_include_vat'], rate))})
    result.sort(key=lambda r: -(r['sales_ex_vat'] - r['cogs_ex_vat']))
    return result[:limit]


def shopify_last_day(checked_at):
    """Last full day ShopifyQL's 'UNTIL yesterday' covered, in the store's timezone."""
    try:
        stamp = datetime.fromisoformat(checked_at)
        return (stamp.astimezone(ZoneInfo(STORE_TZ)).date() - timedelta(days=1)) if stamp.tzinfo else None
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None


def summary(shopify_data, spend_by_window, settings):
    """spend_by_window: {'7': (spend, last_day), '90': (spend, last_day)}; a spend whose period
    does not end on the same day as Shopify's is not used."""
    profit = (shopify_data or {}).get('profit') if isinstance(shopify_data, dict) else None
    if not isinstance(profit, dict) or not isinstance(profit.get('windows'), dict):
        return {'status': 'unavailable', 'reason': 'נתוני הרווחיות משופיפיי עוד לא נקראו.', 'settings': settings}
    last_day = shopify_last_day(profit.get('checked_at'))
    windows = {}
    for key, row in profit['windows'].items():
        if not isinstance(row, dict):
            continue
        spend, spend_last_day = spend_by_window.get(key) or (None, None)
        aligned = spend is not None and last_day is not None and spend_last_day == last_day
        windows[key] = window_profit(row, int(key), spend if aligned else None, settings)
        if spend is not None and not aligned:
            windows[key]['missing'][-1] = 'הוצאת מטא לא מאותה תקופה כמו נתוני שופיפיי (צריך רענון)'
        windows[key]['period_last_day'] = last_day.isoformat() if last_day else None
    return {'status': 'ready', 'checked_at': profit.get('checked_at'), 'settings': settings, 'windows': windows,
            'products': products(profit.get('products_90'), settings),
            'limits': ['התרומה מחושבת לכל החנות. ROAS של מטא מוצג בנפרד ואינו מתמזג איתה.',
                       'מכירות בלי עלות רשומה לא נכללות בחישוב העלויות ומוצגות בנפרד.',
                       'הוצאת מטא כפי שמטא מדווחת; שיוך הזמנות למודעות עוד לא מחובר.',
                       'עלות הסחורה היא העלות הרשומה בשופיפיי, לא עלות מאומתת.'],
            'live_action_allowed': False}
