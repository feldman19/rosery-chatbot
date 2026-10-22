"""Local evaluator of frozen reporting criteria; never a profitability evaluator."""
from datetime import datetime
import hashlib
import json


def archive_valid(payload):
    body=dict(payload);claimed=body.pop('content_sha256',None)
    canonical=json.dumps(body,sort_keys=True,ensure_ascii=False,allow_nan=False)
    return claimed==hashlib.sha256(canonical.encode()).hexdigest()


def evaluate_record(record, folder):
    plan=record['evaluation_criterion'];baseline=record['baseline']
    result={'status':'pending','reason':'no_suitable_future_observation',
            'frozen_at':record['frozen_at'],'criterion_version':plan['version'],
            'evaluation_scope':'reporting_only','profit_verified':False,
            'causal_effect_verified':False,'decision_quality_verified':False,
            'attribution_maturity_verified':False,'execution_performed':False}
    if plan['version']!='reporting_revision_v1':raise ValueError('Unknown frozen criterion')
    settings=baseline.get('query_settings')
    if not settings or 'action_report_time' not in settings:
        return {**result,'reason':'baseline_query_settings_missing'}
    start,end=plan['since'],plan['until']
    if (not baseline.get('source_since') or not baseline.get('source_until')
        or not baseline['source_since']<=start<=end<=baseline['source_until']):
        return {**result,'reason':'baseline_coverage_missing'}
    candidates=[]
    for path in sorted(folder.glob('*.json')):
        try:
            current=json.loads(path.read_text(encoding='utf-8'))
            stamp=datetime.fromisoformat(current['observed_at'])
            if not stamp.tzinfo or stamp<=datetime.fromisoformat(plan['eligible_after']):continue
            if not archive_valid(current):continue
            if any(current.get(k)!=baseline.get(k) for k in ('account_id','currency','timezone','query_settings')):continue
            if current.get('source_complete') is not True:continue
            if not current['source_since']<=start<=end<=current['source_until']:continue
            if current['archive_excludes_dates_on_or_after']<=end:continue
            candidates.append((stamp,current))
        except (ValueError,TypeError,KeyError,AttributeError,OSError):continue
    if not candidates:return result
    stamp,current=min(candidates,key=lambda x:x[0])
    def cells(source):
        found={}
        for row in source['rows']:
            if not start<=row['date_start']<=end:continue
            key=(row['ad_id'],row['date_start'])
            if row['date_start']!=row['date_stop'] or key in found:raise ValueError('Ambiguous daily grain')
            found[key]={k:row[k] for k in ('spend','actions','action_values') if k in row}
        return found
    try:before,after=cells(baseline),cells(current)
    except (ValueError,TypeError,KeyError):return {**result,'reason':'ambiguous_daily_grain'}
    shared=set(before)&set(after)
    if not shared:return {**result,'reason':'no_matching_daily_cells'}
    changes=[{'ad_id':key[0],'day':key[1],'before':before[key],'after':after[key]}
             for key in sorted(shared) if before[key]!=after[key]]
    return {**result,'status':'reporting_observed','reason':'matched_future_reporting_snapshot',
            'observation_at':current['observed_at'],'observation_sha256':current['content_sha256'],
            'comparison_since':start,'comparison_until':end,'changed_cells':changes,
            'matched_cells':len(shared),'missing_before':len(set(after)-set(before)),
            'missing_after':len(set(before)-set(after)),
            'interpretation':'Reported values only. Missing cells are not zeros; differences do not identify lag, profit or causality.'}
