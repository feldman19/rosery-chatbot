"""Offline per-order bridge; conditional arithmetic is never an ad spend target."""
from economics_evidence import current_recorded_economics
from business import ACTIVE as BUSINESS

METHOD_SOURCES = [
 'https://help.shopify.com/en/manual/reports-and-analytics/shopify-reports/report-types/default-reports/sales-report',
 'https://openstax.org/books/principles-managerial-accounting/pages/3-1-explain-contribution-margin-and-calculate-contribution-margin-per-unit-contribution-margin-ratio-and-total-contribution-margin']


def order_economics(dossier):
    evidence = dossier.get('owner_evidence', {})
    raw = evidence.get('current_economics')
    observed = (dossier.get('economics_evidence') or {}).get('current_recorded')
    result = {'status': 'missing_evidence', 'segments': [], 'profit_target': None,
              'verified_break_even_per_order': None, 'cac_limit': None,
              'budget_recommendation_allowed': False, 'method_sources': METHOD_SOURCES,
              'method_checked_date': '2026-10-20',
              'missing': ['current_order_economics'],
              'scope': 'store_order_not_incremental_customer_or_adset'}
    if not observed or not raw:
        return result
    checked = current_recorded_economics(raw, observed.get('source_ref'))
    if not checked or not checked.get('observed_date') or not checked.get('source_ref'):
        return result
    source, date = checked['source_ref'], checked['observed_date']
    # Taxes recorded as zero do not resolve the tax-inclusive sales basis.
    result['missing'] = ['verified_revenue_and_cost_vat_basis', 'reconciled_cogs_coverage',
                         'actual_shipping_processing_and_return_costs',
                         'owner_profit_and_fixed_cost_reserve', 'adset_customer_mix_and_incrementality']
    result['status'] = 'conditional_partial_order_economics'
    for row in checked['segments']:
        n = row['orders']
        components = {k: {'value_per_order': row[k]/n, 'source_ref': source,
                          'observed_date': date, 'status': 'recorded_not_cost_audited'}
                      for k in ('net_sales', 'shipping_charges', 'taxes', 'cost_of_goods_sold')}
        components['packaging'] = {'value_per_order': 4, 'status': 'user_supplied_cost_basis_unverified',
            'source_ref': 'outputs/rosery-media-operations-guide-v1-2026-10-15.md', 'observed_date': '2026-10-15'}
        components['vat_rate'] = {'value': .18, 'status': 'documented_operating_assumption',
            'source_ref': 'outputs/rosery-media-operations-guide-v1-2026-10-15.md', 'observed_date': '2026-10-15'}
        sales = (row['net_sales'] + row['shipping_charges'])/n
        cogs = row['cost_of_goods_sold']/n
        result['segments'].append({'segment': row['new_or_returning_customer'], 'orders': int(n),
            'components': components, 'period_query': checked['query'],
            'residual_if_sales_include_18pct_vat': round(sales/1.18-cogs-4, 2),
            'residual_if_sales_already_exclude_vat': round(sales-cogs-4, 2),
            'residual_scope': 'before_courier_processing_returns_ads_fixed_costs_profit_and_cogs_reconciliation',
            'verified_contribution': None, 'verified_break_even': None,
            'configured_variable_cost_cases': configured_variable_costs(dossier, sales, sales/1.18-cogs-4),
            'limitation': 'Two conditional accounting cases, not a confidence interval, CAC ceiling or verified profit. Recorded costs are deducted unchanged; their VAT basis remains unresolved.'})
    return result


def configured_variable_costs(dossier, sales_per_order, residual):
    """Sensitivity cases using saved configuration, not actual paid expenses.

    One transaction per order and fee base=saved net sales+shipping are explicit
    scenario assumptions. No extra sales refund deduction: net_sales already nets
    reversals. Return handling remains unknown. No weighted gateway mix inferred.
    """
    saved = dossier.get('owner_evidence', {}).get('configured_costs') or {}
    out = {'status': 'missing_configuration', 'components': [], 'scenarios': [],
           'actual_costs_verified': False, 'return_handling_cost': None,
           'sales_refunds_deducted_again': False,
           'assumptions': ['One shipment and one gateway transaction per order',
                           'Gateway fee base is net product sales plus shipping, not verified settlements',
                           'Configured amounts deducted unchanged; expense VAT basis unknown'],
           'missing': ['actual_courier_invoices', 'gateway_mix_and_settlement_fees',
                       'historical_rate_applicability', 'expense_vat_basis', 'return_exchange_handling']}
    if saved.get('shop') != BUSINESS.shop or not saved.get('observed_date'):
        return out
    from math import isfinite
    def amount(x):
        return isinstance(x, (int,float)) and not isinstance(x,bool) and isfinite(x) and x>=0
    src = {'source_ref': 'rosery-cost-settings-observed-2026-10-19.json',
           'observed_date': saved['observed_date'], 'vat_basis': 'unknown',
           'status': 'configured_rate_not_actual_cost'}
    shipping = saved.get('shipping', {}).get('amount_ils')
    if not amount(shipping):
        return out
    out['status'] = 'conditional_configured_cost_cases'
    out['components'].append({**src, 'component':'shipping', 'per_order':shipping})
    for gateway, config in saved.get('gateways', {}).items():
        rate, fixed = config.get('percent'), config.get('fixed_ils')
        if not amount(rate) or not amount(fixed) or rate>100:
            out['components'].append({**src,'component':gateway,'status':'amount_not_observed','per_order':None})
            continue
        fee = sales_per_order*rate/100+fixed
        out['components'].append({**src,'component':gateway,'percent':rate,'fixed_ils':fixed,'per_order_scenario':round(fee,2)})
        out['scenarios'].append({'gateway':gateway, 'shipping_scenario':shipping,
            'processing_scenario':round(fee,2), 'deduction_scenario':round(shipping+fee,2),
            'residual_after_configured_costs':round(residual-shipping-fee,2),
            'scope':'conditional_before_return_handling_ads_fixed_costs_profit_not_cac_limit'})
    out['excluded'] = ['Packaging plus labor 10 ILS is not added to packaging 4 ILS; overlap unresolved',
                       'Configured tax allocation 15.25 percent is not deducted in addition to VAT',
                       'Monthly fixed expenses are not assigned to orders without an allocation basis']
    return out
