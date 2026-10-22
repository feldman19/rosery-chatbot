"""Inspect saved MCP responses without merging or blaming the upstream API."""
import json
from decimal import Decimal, InvalidOperation

def number(value):
    if isinstance(value, dict):value=value.get('value')
    if value is None or isinstance(value, bool):return None
    try:
        n=Decimal(str(value))
        return n if n.is_finite() and n>=0 else None
    except (InvalidOperation, ValueError, TypeError):return None

def compare_saved(left, right, left_context=None, right_context=None):
    """Context must come from receipts, never inferred from matching dates."""
    contexts=(left_context or {},right_context or {})
    required=('account_id','level','since','until','timezone','currency',
              'action_report_time','attribution_setting','breakdowns','observed_at')
    missing=[k for k in required if any(c.get(k) is None for c in contexts)]
    mismatch=[k for k in required if k not in missing and contexts[0][k]!=contexts[1][k]]
    # Strict observation equality intentionally does not claim different pulls stable.
    def rows(payload):
        raw=payload.get('ad_entities')
        return json.loads(raw) if isinstance(raw,str) else raw
    a,b=rows(left),rows(right)
    errors=[];maps=[]
    for index,entries in enumerate((a,b)):
        found={}
        if not isinstance(entries,list):errors.append('rows_missing');entries=[]
        for row in entries:
            key=tuple(row.get(k) for k in ('id','date_start','date_stop'))
            if any(k is None for k in key):errors.append('row_identity_missing');continue
            context=contexts[index]
            if context.get('level') in ('account','ad_account') and context.get('account_id') and row['id']!=context['account_id']:
                errors.append('row_account_mismatch')
            if row.get('account_id') and context.get('account_id') and row['account_id']!=context['account_id']:
                errors.append('row_account_mismatch')
            amount=row.get('amount_spent')
            if isinstance(amount,dict) and amount.get('unit') and context.get('currency') and amount['unit']!=context['currency']:
                errors.append('row_currency_mismatch')
            if context.get('since') and context.get('until') and not context['since']<=row['date_start']<=row['date_stop']<=context['until']:
                errors.append('row_window_mismatch')
            if key in found:errors.append('duplicate_identity');continue
            found[key]=row
        maps.append(found)
    if set(maps[0])!=set(maps[1]):errors.append('row_coverage_mismatch')
    conflicts=[];unknown=[]
    for key in maps[0].keys() & maps[1].keys():
        x,y=(number(m[key].get('amount_spent')) for m in maps)
        if x is None or y is None:unknown.append(list(key))
        elif x!=y:conflicts.append({'key':list(key),'left_spend':str(x),'right_spend':str(y)})
    allowed=not(missing or mismatch or errors or conflicts or unknown)
    return {'efficiency_allowed':allowed,'status':'compatible' if allowed else 'blocked',
            'missing_context':missing,'context_mismatch':mismatch,'errors':errors,
            'candidate_conflicts':conflicts,'unknown_spend_rows':unknown,
            'root_cause':'not_established','merged_metrics':None}
