"""Deterministic evidence summaries. No recommendation to spend is generated here."""
from decimal import Decimal


def summarize(snapshot):
    if snapshot.get('synthetic') is not True:
        raise ValueError('This release accepts synthetic snapshots only')
    rows = []
    for ad in snapshot['ads']:
        spend = Decimal(str(ad['spend']))
        leads = ad['attributed_registrations']
        views = ad['landing_page_views']
        if spend < 0 or not isinstance(leads, int) or leads < 0 or views < 0:
            raise ValueError('Invalid metrics')
        rows.append({**ad, 'cpl': float(spend / leads) if leads else None,
                     'assessment': 'untested' if spend == 0 else 'observe',
                     'scale_ready': False})
    return {'synthetic': True, 'currency': snapshot['currency'],
            'window': snapshot['window'], 'ads': rows,
            'measurement_verified': False,
            'decision': 'hold',
            'reason': 'Synthetic evidence cannot authorize a live campaign change',
            'notes': ['Attributed events are not unique saved contacts',
                      'No platform-attributed revenue is summed across channels']}


def contribution(order, assumptions):
    """All inputs explicit; monetary sums use Decimal. Missing costs block a result."""
    required = ('vat_rate', 'product_cost', 'packaging', 'courier', 'processing')
    missing = [key for key in required if assumptions.get(key) is None]
    if missing:
        return {'value': None, 'missing': missing, 'verified_profit': False}
    d = lambda x: Decimal(str(x))
    revenue = (d(order['product_revenue']) - d(order.get('refunds', 0))
               + d(order.get('shipping_revenue', 0))) / (1 + d(assumptions['vat_rate']))
    value = revenue - sum((d(assumptions[k]) for k in required[1:]), Decimal(0))
    return {'value': str(value.quantize(Decimal('.01'))), 'missing': [],
            'verified_profit': False, 'basis': 'explicit input assumptions, before ads'}
