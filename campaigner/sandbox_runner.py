"""Run a complete isolated automation acceptance proof; no network or credentials."""
import copy
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import secrets
import tempfile

from orchestrator import SandboxCoordinator
from strategy import sample_case


def run_case(kind):
    now=datetime(2026,10,17,12,tzinfo=timezone.utc)
    fixture=sample_case(now);profile=fixture['profile'];evidence=fixture['evidence'];policy=fixture['policy']
    if kind=='pause':
        for w in evidence['windows'].values():w['registrations']=0
    elif kind=='hold':policy['allowed_actions']=[]
    elif kind=='blocked':evidence['measurement']['verified']=False
    with tempfile.TemporaryDirectory(prefix='isolated-acceptance-',dir=Path(__file__).parent) as directory:
        coordinator=SandboxCoordinator(directory,secrets.token_bytes(32))
        evidence=coordinator.seal_evidence(evidence)
        tenant,account,entity=[profile[k] for k in ('tenant_id','account_id','entity_id')]
        coordinator.core.bind_account(tenant,account)
        before={k:evidence['entity'][k] for k in ('status','budget_minor','budget_type','currency')}
        coordinator.core.seed_entity(tenant,account,entity,before)
        coordinator.authority.bind_account(tenant,account)
        coordinator.authority.set_mode(tenant,account,'execute_in_envelope')
        grant=coordinator.authority.issue_grant(tenant,account,{
            'currency':'ILS','budget_type':'daily','cap_minor':policy['cap_minor'],
            'allowed_actions':['pause','set_budget'],'allowed_entity_ids':[entity],
            'expires_at':(now+timedelta(days=7)).isoformat(),'ends_at':(now+timedelta(days=7)).isoformat(),
            'min_interval_seconds':0,'max_evidence_age_seconds':900})
        prepared=coordinator.prepare(profile,evidence,policy,now)
        result={'case':kind,'candidate':prepared['payload']['candidate'],'prepared_state':prepared['state'],
                'run_id':prepared['id'],'operation':None,'outcome':None,'synthetic_data':True,'time_simulated':True}
        if prepared['state']=='proposed':
            completed=coordinator.dispatch(tenant,prepared['id'],grant['id'],lambda:copy.deepcopy((profile,evidence,policy)),now=now)
            result['operation']={'state':completed['state'],'receipt':completed['receipt'],'executed_at':completed['executed_at']}
            baseline={'started_at':'2026-10-16T00:00:00+00:00','ended_at':'2026-10-17T00:00:00+00:00',
                      'spend_minor':1000,'registrations':10,'complete':True}
            followup={**baseline,'started_at':'2026-10-18T00:00:00+00:00','ended_at':'2026-10-19T00:00:00+00:00'}
            result['outcome']=coordinator.observe(tenant,prepared['id'],baseline,followup,[],now=now+timedelta(days=3))
        result['final_mock_state']=coordinator.core.get_entity(tenant,account,entity)['state']
        result['live_action_allowed']=False
        return result


def run_all():
    return {'environment':'isolated_sandbox','synthetic_data':True,'time_simulated':True,
            'network_used':False,'credentials_used':False,'live_campaign_changes':False,
            'cases':[run_case(k) for k in ('scale','pause','hold','blocked')],
            'scope':'Business-policy-to-verified-mock-operation acceptance proof; not production profitability proof',
            'live_blockers':['Verified registration and purchase cohort connector','Verified complete cost coverage',
                             'Explicit live operating envelope and ads_management connection',
                             'Live provider executor and reconciliation contract','Hosted user identity and tenant isolation']}


if __name__=='__main__':
    result=run_all()
    destination=Path(__file__).parent.parent/'outputs'/'campaigner-autonomy-acceptance-2026-10-17.json'
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(destination),'cases':[{ 'case':r['case'],'state':r['operation']['state'] if r['operation'] else r['prepared_state']} for r in result['cases']],'live_campaign_changes':False}))
