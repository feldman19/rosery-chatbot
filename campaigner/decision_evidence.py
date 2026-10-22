"""Evidence-envelope assessment. Completeness is shadow-only, not authority.

No LLM verdict, count threshold, historical revenue or bare 'supported' label can
publish a financial recommendation. Every accepted record retains scope/provenance.
"""
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite

REQUIRED={
 'pause':('configuration','measurement','attribution_maturity','relevant_changes','economics','uncertainty','decision_loss','proposal'),
 'scale':('configuration','measurement','attribution_maturity','relevant_changes','economics','uncertainty','decision_loss','marginal_response','proposal'),
 'prepare_experiment':('question','creative_review','duplication_check','preparation_scope'),
}


def _time(value):
    try:
        x=datetime.fromisoformat(value.replace('Z','+00:00'))
        return x if x.tzinfo else None
    except (ValueError,TypeError,AttributeError):return None


def assess_contract(action, identity, records, *, now=None):
    if action not in REQUIRED:raise ValueError('Unsupported decision class')
    now=now or datetime.now(timezone.utc)
    if not now.tzinfo:raise ValueError('Aware evaluation time required')
    if not all(identity.get(k) for k in ('account_id','entity_type','entity_id')):raise ValueError('Exact scope required')
    accepted,missing,rejected={},{},{}
    for name in REQUIRED[action]:
        item=records.get(name)
        if not isinstance(item,dict):missing[name]='not_observed';continue
        reason=None
        if item.get('scope')!=identity:reason='scope_mismatch'
        elif not item.get('source_ref') or not item.get('source_digest'):reason='source_binding_missing'
        elif not _time(item.get('observed_at')) or _time(item['observed_at'])>now:reason='invalid_observation_time'
        elif not _time(item.get('valid_until')) or _time(item['valid_until'])<=now:reason='expired_evidence'
        elif item.get('assessment')!='verified':reason='not_verified'
        elif item.get('value') is None:reason='value_missing'
        elif item.get('review_kind') not in ('deterministic_source_check','documented_human_review'):reason='unqualified_review'
        if reason:rejected[name]=reason
        else:accepted[name]=item
    # Contract completeness is not statistical validation. These objects cannot
    # bypass preview/approval and are never consumed by the live mutation path.
    payload={'action':action,'identity':identity,'records':records}
    return {'action':action,'identity':identity,'accepted':accepted,'missing':missing,'rejected':rejected,
            'contract_satisfied':not missing and not rejected,
            'status':'shadow_envelope_complete' if not missing and not rejected else 'evidence_incomplete',
            'envelope_only':True,'source_content_verified':False,'decision_value_schema_validated':False,
            'evidence_digest':hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
            'live_recommendation_allowed':False,'execution_allowed':False,
            'validation_status':'not_prospectively_validated'}


def report_assessment(report, row, entity_type, economics, *, now=None):
    """Assess what the existing report actually proves; no manufactured receipts."""
    known=[];missing=[]
    source_at=_time(report.get('freshness',{}).get('meta_last_success_at'))
    evaluated=now or datetime.now(timezone.utc)
    built_at=_time(report.get('checked_at'))
    if (report.get('freshness',{}).get('state')=='fresh' and source_at and built_at
        and 0<=(evaluated-source_at).total_seconds()<=900
        and 0<=(evaluated-built_at).total_seconds()<=900):known.append('recent_meta_snapshot')
    else:missing.append('recent_meta_source_timestamp')
    if type(row.get('configured_active')) is bool and row.get('effective_status') in (
        'ACTIVE','PAUSED','CAMPAIGN_PAUSED','ADSET_PAUSED','ARCHIVED','DELETED',
        'PENDING_REVIEW','DISAPPROVED','WITH_ISSUES','PENDING_BILLING_INFO','IN_PROCESS'):
        known.append('observed_configuration')
    else:missing.append('current_configuration')
    change=row.get('change_evidence') or {}
    change_at=_time(change.get('relevant_change_time'))
    if (change.get('entity_id')==row['id'] and change.get('entity_type') in (entity_type,entity_type+'s')
        and change.get('relevant_change_status')=='verified' and change_at and change_at<=evaluated
        and change.get('source_ref')):known.append('relevant_change_time')
    else:missing.append('relevant_change_history')
    # A populated number is observed reporting, not an end-to-end conversion audit.
    count=row.get('purchases')
    if type(count) in (int,float) and isfinite(count) and count>=0:known.append('reported_purchase_count')
    else:missing.append('reported_purchase_count')
    missing.extend(['measurement_integrity','attribution_maturity'])
    missing.extend('economics:'+x for x in economics['missing'])
    # Observed reporting is useful evidence, but does not implement a financial
    # policy. Keep missing source data separate from unfinished product logic.
    scope={'account_id':report.get('account_id'),'entity_type':entity_type,'entity_id':row['id']}
    def record(name,accepted,value):
        return {'name':name,'status':'observed' if accepted else 'unknown','scope':scope,
                'value':value if accepted else None,'source_ref':'management_report',
                'source_observed_at':report.get('freshness',{}).get('meta_last_success_at'),
                'decision_authority':False}
    records=[record('configuration','observed_configuration' in known,{'configured_active':row.get('configured_active'),'effective_status':row.get('effective_status')}),
             record('reported_purchases','reported_purchase_count' in known,count),
             record('relevant_change','relevant_change_time' in known,change),
             record('recorded_order_economics',bool(economics.get('segments')),{'status':economics.get('status'),'segment_count':len(economics.get('segments',[]))})]
    system_gaps=['executable_financial_policy_not_implemented','validated_uncertainty_model_not_implemented','scale_forecast_not_implemented']
    return {'kind':'observation','decision_status':'descriptive_only','entity_id':row['id'],
            'entity_type':entity_type,'accepted_facts':known,'missing_evidence':missing,
            'source_observed_at':report.get('freshness',{}).get('meta_last_success_at'),
            'report_built_at':report.get('checked_at'),'live_recommendation_allowed':False,
            'evidence_records':records,'system_gaps':system_gaps,
            'capabilities':{'describe_reported_results':{'status':'available' if 'recent_meta_snapshot' in known and 'reported_purchase_count' in known else 'source_unavailable'},
                            'financial_pause_or_scale':{'status':'not_implemented','system_gaps':system_gaps},
                            'pause_scenario_comparison':{'status':'implemented_scenario_only','execution_allowed':False},
                            'paused_content_preparation':{'status':'separate_exact_manifest_path','economics_required':False}},
            'explanation':'הנתונים מאפשרים לתאר תוצאות. עדיין לא אומתו תנאי החלטה שמצדיקים שינוי.'}
