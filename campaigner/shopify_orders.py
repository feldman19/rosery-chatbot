"""Bounded, read-only ROSERY order reader; no persistence or campaign writes.

Call read_orders(data_dir, tenant_secret, now=None) only after granting the
existing application's authorized read_reports + read_orders scopes. The reader
rechecks shop identity and exact scopes before requesting orders. PII from the
provider exists only transiently in memory and is HMACed before returning; callers
must not persist raw transport responses or exception locals. The email identity
normalization contract is strip + casefold (email_strip_casefold_v1), and must be
used identically for registrations. HMACs are pseudonymous, not anonymous.

Result.orders contains only matchable normalized records for cohort.py. Missing
email orders are listed as safe excluded IDs; all exclusions and incomplete
pagination are explicit. Test/cancelled orders and explicitly host-identified
exchange-fee IDs are excluded, never inferred from order value or app/source.
Other sales channels remain flagged and included; exchange status is otherwise
unknown. Financial totals are provider-observed; incomplete gross/refund
decomposition and all unqueried costs stay None, never implicit zero.

Schema/docs: https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Order
https://shopify.dev/docs/api/admin-graphql/2026-10/queries/orders
The query was validated with Shopify's official validator, version 2026-10.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
import time

from cohort import person_hmac
from connections import QUERY as IDENTITY_QUERY, SHOP
from shopify_direct import get_token, graph


TENANT = 'rosery'
ACCOUNT = '393059510164294'
SOURCE_ID = 'shopify_orders_direct'
AUTHORIZED_SCOPES = frozenset({'read_reports', 'read_orders'})
MAX_PAGES = 20
PAGE_SIZE = 50
READ_BUDGET_SECONDS = 60
QUERY = '''query CampaignerRecentOrders($after: String, $query: String!) {
  orders(first: 50, after: $after, query: $query, sortKey: CREATED_AT) {
    nodes {
      id createdAt currencyCode email test cancelledAt sourceName displayFinancialStatus taxesIncluded
      subtotalPriceSet { shopMoney { amount currencyCode } }
      totalTaxSet { shopMoney { amount currencyCode } }
      totalPriceSet { shopMoney { amount currencyCode } }
      totalRefundedSet { shopMoney { amount currencyCode } }
    }
    pageInfo { hasNextPage endCursor }
  }
}'''


class OrderReadError(ValueError):
    """Safe error codes only; never include provider text, identities or tokens."""


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError):
        raise OrderReadError('invalid_order_time') from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise OrderReadError('invalid_order_time')
    return result


def _money(row, field, currency):
    bag = row.get(field)
    if bag is None:
        return None
    try:
        value = bag['shopMoney']
        if value['currencyCode'] != currency or isinstance(value['amount'], bool):
            raise OrderReadError('order_currency_or_amount_mismatch')
        amount = Decimal(str(value['amount']))
        if not amount.is_finite() or amount < 0:
            raise OrderReadError('invalid_order_amount')
        return str(amount)
    except (KeyError, TypeError, InvalidOperation):
        raise OrderReadError('invalid_order_amount') from None


def _normalize(row, secret, now, since, currency, exchange_ids):
    if not isinstance(row, dict) or not re.fullmatch(r'gid://shopify/Order/[0-9]+', str(row.get('id', ''))):
        raise OrderReadError('invalid_order_id')
    order_id = row['id']
    if row.get('test') is True:
        return None, {'order_id': order_id, 'reason': 'provider_test_order'}
    if row.get('test') is not False:
        raise OrderReadError('missing_test_status')
    if row.get('cancelledAt') is not None:
        _time(row['cancelledAt'])
        return None, {'order_id': order_id, 'reason': 'cancelled_order'}
    if 'cancelledAt' not in row:
        raise OrderReadError('missing_cancellation_status')
    if order_id in exchange_ids:
        return None, {'order_id': order_id, 'reason': 'host_confirmed_exchange_fee'}
    if row.get('displayFinancialStatus') not in {'PAID', 'PARTIALLY_REFUNDED', 'REFUNDED'}:
        return None, {'order_id': order_id, 'reason': 'payment_not_confirmed'}
    created = _time(row.get('createdAt'))
    if not since <= created <= now:
        raise OrderReadError('order_outside_requested_window')
    if row.get('currencyCode') != currency:
        raise OrderReadError('order_currency_mismatch')
    # The email is consumed here and never copied into the normalized output.
    email = row.get('email')
    if not isinstance(email, str) or not email.strip():
        return None, {'order_id': order_id, 'reason': 'identity_unavailable'}
    identity = person_hmac(TENANT, secret, email.strip().casefold())
    subtotal = _money(row, 'subtotalPriceSet', currency)
    tax = _money(row, 'totalTaxSet', currency)
    total = _money(row, 'totalPriceSet', currency)
    refunded = _money(row, 'totalRefundedSet', currency)
    no_refunds = refunded is not None and Decimal(refunded) == 0
    return {'id': order_id, 'tenant': TENANT, 'shop': SHOP, 'account': ACCOUNT,
            'currency': currency, 'source_id': SOURCE_ID, 'source_verified': True,
            'as_of': now.isoformat(), 'person_hmac': identity,
            'ordered_at': created.isoformat(), 'registration_id': None,
            'identity_method': 'email_strip_casefold_v1',
            'product_gross': subtotal if row.get('taxesIncluded') is True else None,
            # Original shipping gross and tax on refunded components cannot be
            # derived reliably from the fetched aggregate totals.
            'shipping_gross': None, 'tax': tax,
            'refund_product_gross': '0' if no_refunds else None,
            'refund_shipping_gross': '0' if no_refunds else None,
            'refund_tax': '0' if no_refunds else None,
            'product_cost': None, 'shipping_cost': None, 'packaging_cost': None,
            'payment_fee': None, 'other_cost': None,
            'provider_totals': {'subtotal_after_discounts_before_returns': subtotal,
                                'tax_before_returns': tax, 'total_before_returns': total,
                                'total_refunded': refunded,
                                'subtotal_includes_tax': row.get('taxesIncluded') is True},
            'sales_channel_classification': 'online_store_web' if row.get('sourceName') == 'web' else 'other_or_unknown',
            'exchange_classification': 'unknown',
            'financial_status': row.get('displayFinancialStatus') if row.get('displayFinancialStatus') in
                                {'PAID', 'PENDING', 'AUTHORIZED', 'PARTIALLY_PAID', 'PARTIALLY_REFUNDED',
                                 'REFUNDED', 'VOIDED', 'EXPIRED'} else 'UNKNOWN',
            'verified_profit': False}, None


def read_orders(data_dir, tenant_secret, now=None, *, known_exchange_order_ids=frozenset(), window_days=60):
    """Read at most 1,000 orders in the last 60 days; return no raw identities.

    now is an aware trusted-host clock, injectable for deterministic tests.
    known_exchange_order_ids must contain independently confirmed full Shopify
    order GIDs; never populate it from guessed value/source/tag classifications.
    No network activity occurs at import time. No cache or files are written.
    """
    if not isinstance(tenant_secret, bytes) or len(tenant_secret) < 32:
        raise OrderReadError('tenant_secret_required')
    if not isinstance(known_exchange_order_ids, frozenset) or any(
            not re.fullmatch(r'gid://shopify/Order/[0-9]+', str(x)) for x in known_exchange_order_ids):
        raise OrderReadError('invalid_exchange_order_ids')
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise OrderReadError('aware_host_clock_required')
    if type(window_days) is not int or not 1 <= window_days <= 60:
        raise OrderReadError('invalid_order_window')
    since = now - timedelta(days=window_days)
    try:
        token = get_token(data_dir)
        identity = graph(token, IDENTITY_QUERY)
        if identity['shop']['myshopifyDomain'] != SHOP:
            raise OrderReadError('shop_identity_mismatch')
        currency = identity['shop']['currencyCode']
        if not re.fullmatch('[A-Z]{3}', currency):
            raise OrderReadError('shop_currency_unavailable')
        scopes = {x['handle'] for x in identity['currentAppInstallation']['accessScopes']}
        if scopes != AUTHORIZED_SCOPES:
            raise OrderReadError('authorized_orders_scope_not_verified')
    except OrderReadError:
        raise
    except Exception:
        raise OrderReadError('orders_identity_or_credentials_unavailable') from None
    after, seen_cursors, seen_orders, conflicts = None, set(), {}, set()
    seen_states = {}
    excluded, pages, complete, error = [], 0, False, None
    deadline = time.monotonic() + READ_BUDGET_SECONDS
    query = "created_at:>='" + since.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') + "' created_at:<='" + now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') + "'"
    for _ in range(MAX_PAGES):
        try:
            if time.monotonic() >= deadline:
                raise OrderReadError('orders_time_budget_exceeded')
            data = graph(token, QUERY, {'after': after, 'query': query})['orders']
            if time.monotonic() >= deadline:
                raise OrderReadError('orders_time_budget_exceeded')
            nodes, page = data['nodes'], data['pageInfo']
            if not isinstance(nodes, list) or len(nodes) > PAGE_SIZE or type(page['hasNextPage']) is not bool:
                raise OrderReadError('invalid_orders_page')
            pages += 1
            for row in nodes:
                normalized, exclusion = _normalize(row, tenant_secret, now, since, currency, known_exchange_order_ids)
                state_id = row['id']
                state = normalized if normalized is not None else exclusion
                if state_id in seen_states and seen_states[state_id] != state:
                    conflicts.add(state_id)
                seen_states[state_id] = state
                if exclusion:
                    excluded.append(exclusion)
                    continue
                order_id = normalized['id']
                if order_id in seen_orders and seen_orders[order_id] != normalized:
                    conflicts.add(order_id)
                else:
                    seen_orders[order_id] = normalized
            if not page['hasNextPage']:
                complete = True
                break
            cursor = page.get('endCursor')
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise OrderReadError('pagination_cursor_invalid')
            seen_cursors.add(cursor)
            after = cursor
        except OrderReadError as exc:
            error = str(exc)
            break
        except Exception:
            error = 'orders_page_unavailable'
            break
    if not complete and error is None:
        error = 'orders_page_limit_reached'
    if conflicts:
        complete = False
        error = error or 'conflicting_order_versions'
        excluded.extend({'order_id': order_id, 'reason': 'conflicting_order_versions'} for order_id in sorted(conflicts))
    normalized_orders = [row for order_id, row in seen_orders.items() if order_id not in conflicts]
    excluded = list({(row['order_id'], row['reason']): row for row in excluded}.values())
    return {'orders': normalized_orders, 'excluded': excluded,
            'coverage': {'since': since.isoformat(), 'until': now.isoformat(), 'complete': complete,
                         'pages_read': pages, 'max_pages': MAX_PAGES, 'error': error,
                         'matching_identity_available': len(normalized_orders)},
            'binding': {'tenant': TENANT, 'shop': SHOP, 'account': ACCOUNT, 'currency': currency,
                        'source_id': SOURCE_ID, 'source_verified': True, 'as_of': now.isoformat(),
                        'verified_by': 'fixed_host_connector_identity_and_exact_scopes', 'scopes': sorted(scopes),
                        'account_binding_basis': 'fixed_rosery_host_configuration'},
            'limits': [f'Only orders created in the last {window_days} days are requested.',
                       'Order data can change during pagination; no atomic snapshot is claimed.',
                       'Only exact email normalization HMAC matching is supported; no identity guessing.',
                       'Non-web channels are retained and flagged; exchanges are unknown unless explicitly confirmed.',
                       'Shipping/refund decomposition and all unqueried costs remain unknown; no verified profit.',
                       'Provider financial status is reported; returned orders are not all confirmed paid acquisitions.'],
            'live_action_allowed': False}
