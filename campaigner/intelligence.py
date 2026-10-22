"""Evidence-linked read-only portfolio diagnostics, not an autonomous strategy model."""
from decimal import Decimal


def portfolio_report(snapshots):
    """Never combines mismatched currency/window or equates MER with ROAS/profit."""
    reports = []
    groups = {}
    for snapshot in snapshots:
        p = snapshot['payload']
        key = (p['currency'], p['window_start'], p['window_end'], p['synthetic'])
        groups.setdefault(key, []).append(snapshot)
    for (currency, start, end, synthetic), group in groups.items():
        # Multiple versions are not additive. A later import replaces only the same
        # platform/account/window, and original hashes remain in evidence store.
        latest = {}
        for snapshot in sorted(group, key=lambda x: (x['payload']['captured_at'], x['imported_at'], x['id'])):
            p = snapshot['payload']
            latest[(p['platform'], p['account_id'])] = snapshot
        refs = list(latest.values())
        spend = revenue = orders = 0
        spend_present = revenue_present = False
        orders_present = False
        shops = set()
        reasons = set()
        ambiguous_platforms = set()
        for snapshot in refs:
            p, a = snapshot['payload'], snapshot['assessment']
            if not a.get('source_verified'):
                reasons.add('source_not_authenticated')
            if a.get('freshness') != 'recent_import_not_live':
                reasons.add('stale_or_future_evidence')
            if a.get('completeness') != 'complete':
                reasons.add('partial_or_unknown_window')
            if not a.get('timezone_verified'):
                reasons.add('timezone_rules_unverified')
            reasons.add('no_autonomous_live_refresh')
            if p['platform'] == 'shopify':
                shops.add(p['account_id'])
            # No grain field exists in imported evidence yet. Only a single
            # explicitly account-scoped row is safe for account totals. Never
            # add campaign/ad rows to a total or infer completeness from them.
            rows = p['records']
            if len(rows) != 1 or rows[0].get('entity_id') != p['account_id']:
                reasons.add('account_grain_unverified')
                ambiguous_platforms.add(p['platform'])
                continue
            for row in rows:
                metrics = row['metrics']
                if (p['platform'] == 'meta' and 'spend_minor' not in metrics) or (p['platform'] == 'shopify' and 'revenue_minor' not in metrics):
                    ambiguous_platforms.add(p['platform'])
                    reasons.add('incomplete_metric_coverage')
                if p['platform'] == 'meta' and 'spend_minor' in metrics:
                    spend += metrics['spend_minor']; spend_present = True
                if p['platform'] == 'shopify' and 'revenue_minor' in metrics:
                    revenue += metrics['revenue_minor']; revenue_present = True
                    if 'orders' in metrics:
                        orders += metrics['orders']; orders_present = True
        if len(shops) > 1:
            # No account-to-merchant ownership mapping in this prototype.
            reasons.add('multiple_stores_no_mapping')
        reasons.update(('account_store_mapping_unverified', 'contribution_costs_missing', 'measurement_unverified', 'stock_offer_unverified'))
        if 'meta' in ambiguous_platforms:
            spend_present = False
        if 'shopify' in ambiguous_platforms:
            revenue_present = orders_present = False
        if not spend_present: reasons.add('spend_missing')
        if not revenue_present: reasons.add('store_sales_missing')
        # Even a single store is not proof it owns the Meta account. There is
        # currently no authenticated account-store mapping in this component.
        mer = None
        reports.append({'currency': currency, 'window_start': start, 'window_end': end,
                        'synthetic': synthetic, 'spend_minor': spend if spend_present else None,
                        'store_total_sales_minor': revenue if revenue_present and len(shops) == 1 else None,
                        'store_orders': orders if orders_present and len(shops) == 1 else None,
                        'descriptive_mer': mer, 'mer_is_causal_or_profit': False,
                        'decision': 'hold', 'live_action_allowed': False,
                        'blocked_by': sorted(reasons),
                        'evidence': [{'id': s['id'], 'hash': s['hash'], 'platform': s['payload']['platform'],
                                      'account_id': s['payload']['account_id']} for s in refs]})
    return {'reports': reports, 'kind': 'read_only_diagnostics', 'model_connected': False,
            'note': 'Store sales divided by observed Meta spend is descriptive, not causal ROAS; other marketing spend may be missing'}


def contribution_ledger(order, costs):
    """Explicit tax/cost bases; absence or estimates prevent verified margin claims.

    Values are integer minor currency units. Costs must declare already-net amounts;
    this function does not assume a supplier tax rate or recoverable input tax.
    """
    required_order = {'currency', 'product_collected_minor', 'shipping_collected_minor',
                      'refunds_minor', 'tax_remaining_minor'}
    if set(order) != required_order:
        raise ValueError('invalid_order_fields')
    if any(type(order[k]) is not int or order[k] < 0 for k in required_order - {'currency'}):
        raise ValueError('invalid_order_amount')
    required_costs = {'product', 'packaging', 'courier', 'payment', 'other_variable'}
    if set(costs) - required_costs:
        raise ValueError('unknown_cost_category')
    missing, estimated, refs = [], [], []
    total_cost = 0
    for name in sorted(required_costs):
        item = costs.get(name)
        if item is None:
            missing.append(name); continue
        if set(item) != {'net_minor', 'currency', 'basis', 'source_ref'}:
            raise ValueError('invalid_cost_fields')
        if item['currency'] != order['currency'] or type(item['net_minor']) is not int or item['net_minor'] < 0:
            raise ValueError('currency_or_cost_mismatch')
        if item['basis'] not in ('verified', 'estimate') or not isinstance(item['source_ref'], str) or not item['source_ref'].strip():
            raise ValueError('cost_evidence_required')
        total_cost += item['net_minor']; refs.append(item['source_ref'])
        if item['basis'] == 'estimate': estimated.append(name)
    net_collected = order['product_collected_minor'] + order['shipping_collected_minor'] - order['refunds_minor'] - order['tax_remaining_minor']
    return {'currency':order['currency'], 'contribution_before_ads_minor': None if missing else net_collected - total_cost,
            'missing_costs':missing, 'estimated_costs':estimated, 'evidence':refs,
            'declared_cost_inputs_verified':not missing and not estimated,
            'verification_kind':'caller_declared_not_independently_verified',
            'verified_net_profit':False, 'incremental_effect_proven':False,
            'note':'Contribution before ad spend and fixed costs; refunds/tax_remaining must use reconciled accounting inputs'}
