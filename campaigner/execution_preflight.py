"""Pure Meta execution previews and readback assessment. Never sends a request.

Host-supplied verified flags must come from trusted readers/approval verifiers,
not browser JSON. This module cannot authenticate a human or create authority.
"""
import hashlib
import json
import re
from datetime import datetime,timezone
from business import ACTIVE as BUSINESS

ACCOUNT=BUSINESS.meta_account
FIELDS=('status','budget_minor','currency','budget_type')


def digest(binding,before,after):
    raw=json.dumps({'binding':binding,'before':before,'after':after},sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _time(value):
    t=datetime.fromisoformat(value)
    if t.tzinfo is None:raise ValueError('Aware timestamp required')
    return t


def prepare(candidate,binding,current,approval=None,scopes=(),now=None):
    """Build fixed-destination request preview; never an executable capability."""
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Aware now required')
    before={k:candidate.get('before',{}).get(k) for k in FIELDS}
    after={k:candidate.get('after',{}).get(k) for k in FIELDS}
    reasons=[]
    result={'state':'blocked','reasons':reasons,'request_preview':None,'approval_digest':None,
            'dispatch_available':False,'provider_write_performed':False,'execution_ready':False,
            'note':'Preview only; trusted host approval verification and provider executor are separate'}
    identity=candidate.get('entity') or {}
    if binding.get('platform')!='meta' or binding.get('account_id')!=ACCOUNT or binding.get('level')!='campaign':
        reasons.append('Unsupported fixed account or entity level')
    if not isinstance(binding.get('entity_id'),str) or not binding['entity_id'].isdigit():reasons.append('Invalid Meta entity ID')
    if any(not binding.get(k) or identity.get(k)!=binding.get(k) or current.get(k)!=binding.get(k) for k in ('tenant_id','account_id','entity_id')):
        reasons.append('Cross-tenant/account/entity binding mismatch')
    if candidate.get('status')!='propose' or candidate.get('action') not in ('pause','set_budget'):
        reasons.append('No supported candidate')
    refs=candidate.get('evidence_refs')
    if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',r) for r in refs):reasons.append('Missing immutable candidate evidence references')
    if current.get('verified') is not True:reasons.append('Current provider state not verified')
    try:
        if _time(current['checked_at'])>now or now>_time(current['valid_until']):reasons.append('Current provider state is stale')
    except (KeyError,TypeError,ValueError):reasons.append('Missing current-state validity')
    if before!={k:current.get(k) for k in FIELDS}:reasons.append('Current state drifted from exact proposal before-state')
    if not isinstance(before['currency'],str) or not re.fullmatch(r'[A-Z]{3}',before['currency']) or before['currency']!=binding.get('currency') or before['budget_type']!='daily' or binding.get('budget_type')!='daily':
        reasons.append('Currency or budget-type mismatch')
    if before['status']!='ACTIVE' or current.get('parent_active') is not True:reasons.append('Entity or parent is not confirmed active')
    for value in (before['budget_minor'],after['budget_minor']):
        if type(value) is not int or value<=0:reasons.append('Invalid positive integer budget')
    if before['currency']!=after['currency'] or before['budget_type']!=after['budget_type']:
        reasons.append('Candidate changes protected financial fields')
    changes={k:v for k,v in after.items() if before[k]!=v}
    if candidate.get('action')=='pause' and changes!={'status':'PAUSED'}:reasons.append('Pause candidate contains additional or missing changes')
    if candidate.get('action')=='set_budget' and (set(changes)!={'budget_minor'} or after['status']!='ACTIVE'):
        reasons.append('Budget candidate contains additional or missing changes')
    if reasons:return result
    fingerprint=digest({**binding,'candidate_proof':{k:candidate.get(k) for k in ('evidence_refs','next_review_at','rollback_condition','risk')}},before,after);result['approval_digest']=fingerprint
    body={'status':'PAUSED'} if candidate['action']=='pause' else {'daily_budget':str(after['budget_minor'])}
    result['request_preview']={'method':'POST','origin':'https://graph.facebook.com','version':'v26.0',
                               'entity_id':binding['entity_id'],'body':body,'headers_omitted':True}
    result['state']='prepared'
    if approval is None:reasons.append('Exact approval not supplied')
    else:
        if approval.get('verified') is not True or approval.get('digest')!=fingerprint:reasons.append('Approval is not verified and bound to exact preview')
        if any(approval.get(k)!=binding.get(k) for k in ('tenant_id','account_id','entity_id')):reasons.append('Approval identity mismatch')
        try:
            if _time(approval['issued_at'])>now or now>_time(approval['expires_at']):reasons.append('Approval expired or future issued')
        except (KeyError,TypeError,ValueError):reasons.append('Approval validity missing')
    if 'ads_management' not in scopes:reasons.append('Provider write scope unavailable; existing read credential cannot execute')
    if not reasons:result.update(state='ready_for_executor',execution_ready=True)
    return result


def reconcile(candidate,binding,readback,receipt,sent_at):
    """Observed target state alone never proves this operation caused a change."""
    result={'outcome':'unknown','automatic_retry_allowed':False,'business_success_verified':False}
    if binding.get('platform')!='meta' or binding.get('account_id')!=ACCOUNT or binding.get('level')!='campaign':return result
    if any((candidate.get('entity') or {}).get(k)!=binding.get(k) for k in ('tenant_id','account_id','entity_id')):return result
    if readback.get('verified') is not True or any(readback.get(k)!=binding.get(k) for k in ('tenant_id','account_id','entity_id')):return result
    try:
        if _time(readback['checked_at'])<_time(sent_at):return result
    except (KeyError,TypeError,ValueError):return result
    state={k:readback.get(k) for k in FIELDS}
    if state==candidate.get('after'):
        result['observed_target_state']=True
        if receipt.get('verified') is True and receipt.get('operation_id') and receipt.get('operation_id')==readback.get('operation_id'):
            result['outcome']='verified'
    elif state==candidate.get('before') and receipt.get('definitively_rejected') is True:
        result['outcome']='not_applied'
    return result
