"""Pure, local registration-to-order reconciliation. No API or campaign writes.

HostContext MUST be constructed by the trusted host after independently checking
connector identity, scopes and source provenance. A record's source_verified flag
is only a declaration; it never authenticates a source. This module cannot prove
API authenticity. Never expose HostContext construction to an untrusted request.

Each record: id, tenant, shop, account, currency, source_id, source_verified,
as_of (aware ISO timestamp), person_hmac (64 hexadecimal characters).
Registrations additionally: event_id, registered_at, ad_id (nullable), linkage
('exact_click', 'view_through', 'unknown'). Orders: ordered_at, optional
registration_id, and financial fields documented in REVENUE_FIELDS/COST_FIELDS.
Amounts are same-currency, cumulative per order, tax-inclusive gross values.
Refund tax is tax returned, not another expense. Missing fields are UNKNOWN.
Duplicate event IDs/orders never inflate counts; conflicting duplicates are
quarantined. Multiple eligible registrations never get a guessed last-click.
Outputs contain pseudonymous identifiers only; HMACs remain sensitive data.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re


REVENUE_FIELDS = ('product_gross', 'shipping_gross', 'tax',
                  'refund_product_gross', 'refund_shipping_gross', 'refund_tax')
COST_FIELDS = ('product_cost', 'shipping_cost', 'packaging_cost', 'payment_fee', 'other_cost')
LINKAGES = {'exact_click', 'view_through', 'unknown'}
RAW_PII_FIELDS = {'email', 'phone', 'name', 'first_name', 'last_name', 'address'}


@dataclass(frozen=True)
class HostContext:
    tenant: str
    shop: str
    account: str
    currency: str
    as_of: str
    verified_source_ids: frozenset[str]


def person_hmac(tenant: str, tenant_secret: bytes, normalized_identity: str) -> str:
    """Hash an identity already normalized consistently by the trusted host.

    Use an independently random >=32-byte secret per tenant. Do not persist the
    input or export the secret. This helper deliberately does not guess email or
    telephone normalization or collapse different identities.
    """
    if not tenant or not isinstance(tenant_secret, bytes) or len(tenant_secret) < 32:
        raise ValueError('tenant and a strong tenant secret are required')
    if not isinstance(normalized_identity, str) or not normalized_identity.strip():
        raise ValueError('normalized identity is required')
    message = json.dumps([tenant, normalized_identity], ensure_ascii=False, separators=(',', ':'))
    return hmac.new(tenant_secret, message.encode('utf-8'), hashlib.sha256).hexdigest()


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('an ISO timestamp is required') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timestamp must include timezone')
    return parsed


def _amount(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError('boolean is not an amount')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('invalid amount') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('amounts must be finite and nonnegative')
    return result


def _validate(record, context, kind):
    if not isinstance(record, dict) or RAW_PII_FIELDS.intersection(record):
        raise ValueError('only normalized records without raw personal fields are accepted')
    for key in ('id', 'tenant', 'shop', 'account', 'currency', 'source_id', 'as_of', 'person_hmac'):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError('required normalized field missing: ' + key)
    for key in ('tenant', 'shop', 'account', 'currency'):
        if record[key] != getattr(context, key):
            raise ValueError('scope or currency mismatch: ' + key)
    if not re.fullmatch('[0-9a-f]{64}', record['person_hmac']):
        raise ValueError('invalid person_hmac')
    as_of = _time(record['as_of'])
    event_time = _time(record['registered_at' if kind == 'registration' else 'ordered_at'])
    if event_time > as_of or as_of > _time(context.as_of):
        raise ValueError('record time exceeds evidence cutoff')
    if kind == 'registration':
        if not isinstance(record.get('event_id'), str) or not record['event_id'] or record.get('linkage') not in LINKAGES:
            raise ValueError('registration event_id and linkage are required')
        if record.get('ad_id') is not None and not isinstance(record['ad_id'], str):
            raise ValueError('ad_id must be a string or null')
    else:
        if record.get('registration_id') is not None and not isinstance(record['registration_id'], str):
            raise ValueError('registration_id must be a string or null')
        for key in REVENUE_FIELDS + COST_FIELDS:
            _amount(record.get(key))
    return record.get('source_verified') is True and record['source_id'] in context.verified_source_ids


def _deduplicate(records, key):
    groups = {}
    for row in records:
        groups.setdefault(row[key], []).append(row)
    accepted, aliases, conflicts = [], {}, []
    duplicate_count = 0
    for identity, group in groups.items():
        # Different import row IDs may describe the same registration event.
        canonical = [{k: v for k, v in row.items() if not (key == 'event_id' and k == 'id')}
                     for row in group]
        if any(row != canonical[0] for row in canonical[1:]):
            conflicts.append(identity)
            continue
        accepted.append(group[0])
        duplicate_count += len(group) - 1
        for row in group:
            aliases[row['id']] = group[0]['id']
    return accepted, aliases, conflicts, duplicate_count


def _economics(order):
    revenue = {key: _amount(order.get(key)) for key in REVENUE_FIELDS}
    costs = {key: _amount(order.get(key)) for key in COST_FIELDS}
    missing_revenue = [key for key, value in revenue.items() if value is None]
    missing_costs = [key for key, value in costs.items() if value is None]
    net = None
    if not missing_revenue:
        r = revenue
        if (r['tax'] > r['product_gross'] + r['shipping_gross'] or
            r['refund_tax'] > r['tax'] or
            r['refund_tax'] > r['refund_product_gross'] + r['refund_shipping_gross'] or
            r['refund_product_gross'] > r['product_gross'] or
            r['refund_shipping_gross'] > r['shipping_gross']):
            raise ValueError('refund or tax values contradict order totals')
        net = (r['product_gross'] + r['shipping_gross'] - r['tax'] -
               r['refund_product_gross'] - r['refund_shipping_gross'] + r['refund_tax'])
    known_cost = sum((x for x in costs.values() if x is not None), Decimal(0))
    return {'net_revenue_ex_tax': str(net) if net is not None else None,
            'known_cost_subtotal': str(known_cost),
            'contribution_before_ads': str(net - known_cost) if net is not None else None,
            'contribution_status': 'calculated' if net is not None and not missing_costs else 'estimate' if net is not None else 'unknown',
            'missing_revenue_fields': missing_revenue, 'missing_cost_fields': missing_costs,
            'cost_coverage': {key: value is not None for key, value in costs.items()},
            'financial_inputs_basis': 'declared_normalized_values',
            'verified_profit': False}


def reconcile_cohort(registrations, orders, *, context: HostContext):
    """Return deterministic cohort accounting with explicit unknowns.

    This is calculated reconciliation, not causal attribution or verified profit.
    Registrations are eligible only before the order and within this supplied
    cohort. An explicit registration_id must match the same person; otherwise a
    single person-matched event is required. Multiple eligible events are unknown.
    Advertising spend/fixed costs are not allocated; contribution is before ads.
    """
    if not isinstance(context, HostContext) or not isinstance(context.verified_source_ids, frozenset):
        raise ValueError('trusted HostContext is required')
    if not all(isinstance(getattr(context, key), str) and getattr(context, key)
               for key in ('tenant', 'shop', 'account', 'currency')):
        raise ValueError('context scope is required')
    if not re.fullmatch('[A-Z]{3}', context.currency):
        raise ValueError('context currency must be an ISO currency code')
    _time(context.as_of)
    declarations = 0
    reg_rows, order_rows = [], []
    for kind, rows, target in [('registration', registrations, reg_rows), ('order', orders, order_rows)]:
        for row in rows:
            trusted = _validate(row, context, kind)
            if trusted:
                target.append(dict(row))
            else:
                declarations += 1
    # Row-ID conflicts are also quarantined before event-level deduplication.
    initial_registrations = list(reg_rows)
    reg_rows, _, reg_id_conflicts, reg_id_duplicates = _deduplicate(reg_rows, 'id')
    reg_rows, aliases, event_conflicts, event_duplicates = _deduplicate(reg_rows, 'event_id')
    order_rows, _, order_conflicts, order_duplicates = _deduplicate(order_rows, 'id')
    conflicted_people = {r['person_hmac'] for r in initial_registrations
                         if r['id'] in reg_id_conflicts or r['event_id'] in event_conflicts}
    reg_by_id = {r['id']: r for r in reg_rows}
    matches = []
    cohorts = {}
    for reg in reg_rows:
        channel = reg['linkage'] if reg.get('ad_id') else 'unknown'
        key = (reg.get('ad_id'), channel)
        cohort = cohorts.setdefault(key, {'ad_id': key[0], 'linkage': channel,
                                          '_people': set(), 'orders': 0, '_contribution': Decimal(0),
                                          '_complete': True, '_unknown_revenue': False})
        cohort['_people'].add(reg['person_hmac'])
    for order in order_rows:
        candidates = [r for r in reg_rows if r['person_hmac'] == order['person_hmac'] and
                      _time(r['registered_at']) <= _time(order['ordered_at'])]
        reference = order.get('registration_id')
        if reference:
            candidate = reg_by_id.get(aliases.get(reference, reference))
            selected = candidate if candidate in candidates else None
            basis = 'explicit_registration_id' if selected else 'invalid_registration_reference'
        else:
            selected = candidates[0] if len(candidates) == 1 and order['person_hmac'] not in conflicted_people else None
            basis = 'person_hmac_unique' if selected else 'ambiguous' if candidates or order['person_hmac'] in conflicted_people else 'unmatched'
        economics = _economics(order)
        linkage = selected['linkage'] if selected and selected.get('ad_id') else 'unknown'
        ad_id = selected.get('ad_id') if selected else None
        matches.append({'order_id': order['id'], 'registration_id': selected['id'] if selected else None,
                        'ad_id': ad_id, 'linkage': linkage, 'match_basis': basis, **economics})
        if selected:
            cohort = cohorts[(ad_id, linkage)]
            cohort['orders'] += 1
            if economics['contribution_before_ads'] is not None:
                cohort['_contribution'] += Decimal(economics['contribution_before_ads'])
            else:
                cohort['_unknown_revenue'] = True
            cohort['_complete'] &= economics['contribution_status'] == 'calculated'
    result_cohorts = []
    for key in sorted(cohorts, key=lambda x: (x[0] or '', x[1])):
        row = cohorts[key]
        result_cohorts.append({'ad_id': row['ad_id'], 'linkage': row['linkage'],
                               'unique_registrants': len(row['_people']), 'orders': row['orders'],
                               'contribution_before_ads': str(row['_contribution']) if row['orders'] and not row['_unknown_revenue'] else None,
                               'contribution_status': 'unknown' if row['_unknown_revenue'] else 'calculated' if row['orders'] and row['_complete'] else 'estimate' if row['orders'] else 'unknown',
                               'verified_profit': False})
    return {'tenant': context.tenant, 'shop': context.shop, 'account': context.account,
            'currency': context.currency, 'as_of': context.as_of,
            'provenance': 'trusted_host_contract', 'classification': 'calculated_reconciliation',
            'declared_untrusted_records_excluded': declarations,
            'unique_registrants': len({r['person_hmac'] for r in reg_rows}),
            'registrations': len(reg_rows), 'orders': len(order_rows), 'matches': matches,
            'cohorts': result_cohorts,
            'duplicates_removed': reg_id_duplicates + event_duplicates + order_duplicates,
            'conflicts': {'registration_ids': reg_id_conflicts, 'event_ids': event_conflicts, 'order_ids': order_conflicts},
            'limitations': ['Host provenance must be independently verified before construction.',
                            'Person HMAC matching does not prove causal lift.',
                            'Contribution excludes advertising and fixed costs; incomplete costs are estimates.']}
