"""Offline economics: missing evidence never becomes an automatic spend limit."""
from datetime import datetime,timedelta
from decimal import Decimal

def allowable_cost(contribution, reserve, *, costs_verified=False, window_complete=False):
    value=Decimal(str(contribution))-Decimal(str(reserve))
    return {'scenario_allowable_cost':float(max(Decimal(0),value)),
            'verified_allowable_cost':float(max(Decimal(0),value)) if costs_verified and window_complete else None,
            'automation_allowed':False,
            'missing':[x for x,v in [('verified_costs',costs_verified),('complete_observation_window',window_complete)] if not v]}

def fixed_horizon_cohort(customers, orders, horizon_days, cutoff, *, coverage_complete=False):
    """Pseudonymous customer keys and known first purchases required; no inference from truncated order history.

    order contribution must already exclude refunds/tax/variable costs. Retention
    marketing is deducted separately; acquisition spend is excluded from value.
    Date convention: [first_purchase, first_purchase + horizon_days).
    """
    if horizon_days not in (90,180):raise ValueError('Explicit 90/180-day horizon required')
    parse=lambda x:datetime.fromisoformat(x.replace('Z','+00:00'))
    end=parse(cutoff)
    if end.tzinfo is None:raise ValueError('Aware cutoff required')
    if len({x['customer_key'] for x in customers})!=len(customers):raise ValueError('Duplicate customer')
    mature={}
    for c in customers:
        first=parse(c['first_purchase_at'])
        if first.tzinfo is None or first>end:raise ValueError('Invalid known first purchase')
        if first+timedelta(days=horizon_days)<=end:mature[c['customer_key']]=first
    values={k:Decimal(0) for k in mature};seen=set()
    for o in orders:
        if o['order_id'] in seen:raise ValueError('Duplicate order requires reconciliation')
        seen.add(o['order_id']);k=o['customer_key']
        if k not in mature:continue
        at=parse(o['ordered_at'])
        if mature[k]<=at<mature[k]+timedelta(days=horizon_days):
            values[k]+=Decimal(str(o['contribution_before_ads']))-Decimal(str(o.get('retention_marketing_cost',0)))
    mean=float(sum(values.values())/len(values)) if values else None
    return {'horizon_days':horizon_days,'mature_customers':len(values),'excluded_immature_customers':len(customers)-len(values),
            'observed_contribution_ltv':mean if coverage_complete else None,
            'partial_sample_mean':mean,'coverage_complete':coverage_complete,'automation_allowed':False}
