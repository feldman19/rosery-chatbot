"""Read-only order-level last-visit attribution; no credentials or raw URLs returned."""
from datetime import datetime, time, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from connections import QUERY as IDENTITY, SHOP
from shopify_direct import get_token, graph
from true_profit import DEFAULTS, _dec, window_profit

ORDERS_QUERY = '''query AdSales($after: String, $query: String!) {
  orders(first: 50, after: $after, query: $query, sortKey: CREATED_AT) {
    nodes { id createdAt test cancelledAt displayFinancialStatus currencyCode
      customerJourneySummary { ready customerOrderIndex
        lastVisit { landingPage utmParameters { source campaign content } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}'''
COST_QUERY = '''query AdSalesCosts($q: String!) {
  shopifyqlQuery(query: $q) { tableData { rows } parseErrors }
}'''
COST_FIELDS = ('net_sales', 'taxes', 'shipping_charges', 'cost_of_goods_sold',
               'net_sales_with_cost_recorded', 'net_sales_without_cost_recorded', 'net_items_sold')
LIMITS = ['מכירות בחנות שהגיעו מהמודעה: שיוך לפי UTM בביקור האחרון בלבד; השיוך עלול להחסיר הזמנות.',
          'רכישות מיוחסות ב־Meta כוללות גם ייחוס צפייה; שני המדדים אינם מתחברים ואין כאן הוכחת סיבתיות.',
          'העלויות הן העלויות הרשומות בחנות; עלות חסרה משאירה את הרווח לא ידוע.']


def unavailable(reason):
    return {'status': 'unavailable', 'reason': reason, 'complete': False, 'orders': [],
            'api_version': '2026-10', 'fields_verified': [], 'live_action_allowed': False}


def read_sales(data_dir, now=None):
    """14 full shop-local days. Bounded pagination; errors reveal no provider data."""
    result = unavailable('credentials_or_identity_unavailable')
    try:
        token = get_token(data_dir)
        identity = graph(token, IDENTITY.replace('currencyCode', 'currencyCode ianaTimezone'))
        if identity['shop']['myshopifyDomain'] != SHOP:
            return unavailable('shop_identity_mismatch')
        scopes = {x['handle'] for x in identity['currentAppInstallation']['accessScopes']}
        result['scopes_verified'] = sorted(scopes)
        result['missing_scopes'] = sorted({'read_orders', 'read_reports'} - scopes)
        if 'read_orders' not in scopes:
            result['reason'] = 'read_orders_missing'
            return result
        tz = identity['shop']['ianaTimezone']
        today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz)).date()
        since, until = today - timedelta(days=14), today - timedelta(days=1)
        result.update(since=str(since), until=str(until), timezone=tz,
                      currency=identity['shop']['currencyCode'], checked_at=datetime.now(timezone.utc).isoformat())
        start = datetime.combine(since, time.min, ZoneInfo(tz)).isoformat()
        end = datetime.combine(today, time.min, ZoneInfo(tz)).isoformat()
        query = f"created_at:>='{start}' created_at:<'{end}'"
        after, seen, orders = None, set(), []
        for _ in range(20):
            page = graph(token, ORDERS_QUERY, {'after': after, 'query': query})['orders']
            for row in page['nodes']:
                if row['id'] in seen:
                    raise ValueError('duplicate_order')
                seen.add(row['id'])
                day = datetime.fromisoformat(row['createdAt'].replace('Z', '+00:00')).astimezone(ZoneInfo(tz)).date()
                if not since <= day <= until or row['currencyCode'] != result['currency']:
                    raise ValueError('invalid_order_window_or_currency')
                if row.get('test') is not False or 'cancelledAt' not in row:
                    raise ValueError('unknown_order_status')
                if row['cancelledAt'] is not None or row.get('displayFinancialStatus') not in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED'):
                    continue
                journey = row.get('customerJourneySummary') or {}
                visit = journey.get('lastVisit') or {} if journey.get('ready') is True else {}
                utm = visit.get('utmParameters') or {}
                params = parse_qs(urlsplit(visit.get('landingPage') or '').query)
                index = journey.get('customerOrderIndex') if journey.get('ready') is True else None
                orders.append({'id': row['id'].rsplit('/', 1)[-1], 'day': str(day),
                               'ad_tag': utm.get('content') or next(iter(params.get('utm_content', [])), None),
                               'campaign_tag': utm.get('campaign') or next(iter(params.get('utm_campaign', [])), None),
                               'source': utm.get('source') or next(iter(params.get('utm_source', [])), None),
                               'adset_tag': next(iter(params.get('adset_id', [])), None),
                               'new': index == 1 if type(index) is int and index > 0 else None,
                               'economics': None})
            info = page['pageInfo']
            if info['hasNextPage'] is False:
                result['complete'] = True
                break
            cursor = info.get('endCursor')
            if not cursor or cursor == after:
                raise ValueError('invalid_cursor')
            after = cursor
        result.update(status='ready' if result['complete'] else 'partial', reason=None, orders=orders,
                      fields_verified=['orders', 'customerJourneySummary.ready', 'customerOrderIndex',
                                       'lastVisit.landingPage', 'lastVisit.utmParameters'],
                      customer_number_of_orders='not_requested_order_index_used', costs_status='unavailable')
        if 'read_reports' in scopes:
            try:
                q = f"FROM sales SHOW {', '.join(COST_FIELDS)} GROUP BY order_id SINCE {since} UNTIL {until} LIMIT 10000"
                costs = graph(token, COST_QUERY, {'q': q})['shopifyqlQuery']
                if costs.get('parseErrors') or not costs.get('tableData'):
                    raise ValueError('cost_query_unavailable')
                rows = costs['tableData']['rows']
                if len(rows) >= 10000:
                    raise ValueError('cost_rows_truncated')
                by_id = {}
                for row in rows:
                    key = str(row['order_id']).rsplit('/', 1)[-1]
                    if key in by_id:
                        raise ValueError('duplicate_cost_row')
                    by_id[key] = row
                for order in orders:
                    order['economics'] = by_id.get(order['id'])
                result['costs_status'] = 'query_verified'
                result['fields_verified'].append('shopifyql.order_id_and_cost_fields')
            except Exception:
                result['costs_status'] = 'query_denied_or_unavailable_scope_not_inferred'
        return result
    except Exception:
        result.update(status='unavailable', reason='query_denied_or_unavailable_scope_not_inferred', complete=False, orders=[])
        return result


def entity_sales(source, entities, since, until, currency, tz, settings=None, kind='ads'):
    """Attach one period's aggregates. Unknown source never becomes zero."""
    settings = dict(settings or DEFAULTS, mode='simple', fields=[])
    complete = (source.get('complete') is True and source.get('currency') == currency and
                source.get('timezone') == tz and source.get('since', '9999') <= since and
                source.get('until', '') >= until)
    names = {}
    for entity in entities:
        names.setdefault(entity.get('name'), []).append(str(entity['id']))
    ids = {str(e['id']) for e in entities}
    groups = {key: [] for key in ids}
    unmatched = 0
    for order in source.get('orders', []) if complete else []:
        if not since <= order['day'] <= until:
            continue
        tag = order.get('ad_tag') if kind == 'ads' else order.get('adset_tag')
        key = tag if tag in ids else None
        if kind == 'ads' and key is None and tag and not str(tag).isdigit() and len(names.get(tag, [])) == 1:
            key = names[tag][0]
        if key is None:
            unmatched += 1
        else:
            groups[key].append(order)
    for entity in entities:
        orders = groups[str(entity['id'])]
        count = len(orders) if complete else None
        known = [o for o in orders if o.get('new') is not None]
        share = 100 * sum(o['new'] for o in known) / count if count and len(known) == count else None
        economics = [o.get('economics') for o in orders]
        totals = {field: None for field in COST_FIELDS}
        if complete:
            for field in COST_FIELDS:
                values = [_dec((row or {}).get(field)) for row in economics]
                if all(v is not None for v in values):
                    totals[field] = sum(values)
        profit = window_profit(dict(totals, orders=count), 14, entity.get('spend'), settings)
        # Do not extrapolate the known-cost subset to the whole ad.
        full_cost = complete and all(row is not None and _dec(row.get('net_sales_without_cost_recorded')) == 0
                                     and _dec(row.get('cost_of_goods_sold')) is not None for row in economics)
        after = profit['after_ads'] if full_cost else None
        purchases = _dec(entity.get('purchases'))
        entity['ad_sales'] = {'store_orders': count, 'store_revenue': float(totals['net_sales']) if totals['net_sales'] is not None else None,
                              'revenue_basis': 'net_sales_shopifyql_sale_events_in_period', 'new_customer_percent': share,
                              'unknown_customer_orders': count - len(known) if count is not None else None,
                              'meta_attributed_purchases': float(purchases) if purchases is not None else None,
                              'meta_minus_store_orders': float(purchases) - count if purchases is not None and count is not None else None,
                              'profit_after_product_and_ads': after, 'profit_label': 'רווח מהמודעה אחרי עלות מוצר ופרסום',
                              'status': 'ready' if complete else 'unavailable', 'since': since, 'until': until,
                              'card_line': f'בחנות: {count} הזמנות מהמודעה, {share:.0f}% לקוחות חדשים' if share is not None else
                                           f'בחנות: {count} הזמנות מהמודעה, שיעור לקוחות חדשים לא ידוע' if count is not None else 'מכירות מהמודעה בחנות: לא ידוע',
                              'limits': LIMITS}
    return unmatched


def attach_report(report, source=None, settings=None, tz=None):
    source = source or unavailable('not_collected')
    # Preserve Shopify evidence when Meta does not cover 14 days. These rows
    # carry unknown Meta values, never fabricated spend or purchase zeroes.
    full = report.get('windows', {}).get('14')
    if full is not None:
        for kind, tag in (('ads', 'ad_tag'), ('adsets', 'adset_tag')):
            existing = {str(row['id']) for row in full.get(kind, [])}
            for order in source.get('orders', []):
                identity = order.get(tag)
                if identity and str(identity).isdigit() and identity not in existing:
                    full.setdefault(kind, []).append({'id': identity, 'name': identity,
                                                       'spend': None, 'purchases': None})
                    existing.add(identity)
    for window in report.get('windows', {}).values():
        for kind in ('ads', 'adsets'):
            entity_sales(source, window.get(kind, []), window['since'], window['until'],
                         report.get('currency'), tz, settings, kind)
    report['ad_sales'] = {k: v for k, v in source.items() if k != 'orders'}
    report['ad_sales']['limits'] = LIMITS
    return report
