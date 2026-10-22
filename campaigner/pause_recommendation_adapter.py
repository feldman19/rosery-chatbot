"""Host evidence -> conditional pause arithmetic. Never an executable decision."""
import hashlib
import json
from pause_evaluation import evaluate_pause, stamp


def pause_assessment(report,row,economics,dossier,*,now):
    scope={'account_id':report['account_id'],'entity_type':'ad','entity_id':row['id']}
    records=(dossier.get('owner_evidence') or {}).get('pause_scenarios') or []
    matching=[r for r in records if isinstance(r,dict) and r.get('scope')==scope] if isinstance(records,list) else []
    issue=None;scenario=None;source=None
    if not matching:issue='scoped_future_evidence_not_recorded'
    elif len(matching)!=1:issue='ambiguous_scoped_future_evidence'
    else:
        record=matching[0];value=record.get('value');observed=stamp(record.get('observed_at'));expiry=stamp(record.get('valid_until'))
        try:sha=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        except (ValueError,TypeError):sha=None
        if (not isinstance(value,dict) or not record.get('source_ref') or sha is None or record.get('source_digest')!=sha
            or not observed or not expiry or not observed<=now<expiry
            or record.get('review_kind') not in ('deterministic_source_check','documented_human_review')):
            issue='future_evidence_provenance_invalid'
        elif (not isinstance(value.get('horizon'),dict) or any(not isinstance(value.get(k),dict) for k in ('avoidable_spend','foregone_contribution','change_cost'))):
            issue='future_evidence_value_schema_invalid'
        elif ((value.get('avoidable_spend') or {}).get('net_of_budget_redistribution') is not True
              or (value.get('foregone_contribution') or {}).get('includes_substitution') is not True
              or (value.get('change_cost') or {}).get('excludes_foregone_contribution_costs') is not True):
            issue='redistribution_substitution_or_double_count_basis_unresolved'
        else:scenario=value;source={k:record[k] for k in ('source_ref','source_digest','observed_at','valid_until','review_kind')}
    if report.get('currency')!='ILS':issue='report_currency_not_ils';scenario=None
    result=evaluate_pause(report,row,economics,scenario,now=now)
    if issue:result['missing']=list(dict.fromkeys([issue]+result['missing']))
    result['policy_status']='implemented_scenario_only'
    result['evidence_status']='blocked' if result['missing'] else 'conditional'
    result['source_record']=source
    result['is_action_proposal']=False
    result['live_recommendation_allowed']=False;result['execution_allowed']=False
    # A source digest proves binding, not the predictive validity of its bounds.
    # Separate fixable input gaps from capabilities that are not implemented.
    result['decision_readiness']={
        'state':'blocked_by_evidence' if result['missing'] else 'scenario_ready_not_decision',
        'scope':scope,
        'evidence_gaps':list(result['missing']),
        'system_gaps':['prospective_pause_effect_validation_not_implemented',
                       'validated_pause_proposal_generation_not_implemented'],
        'explanation':('חסרות ראיות להשוואת עצירה מול המשך; הרשימה מתייחסת למודעה הזאת בלבד.'
                       if result['missing'] else
                       'אפשר לחשב את התרחיש, אך עדיין לא אומת שהגבולות מנבאים את השפעת העצירה. זו אינה המלצה לעצור.'),
        'next_system_step':'validate_future_spend_and_contribution_effects_against_scoped_observed_outcomes',
        'requires_owner_threshold':False,
        'approval_ready':False,
    }
    return result
