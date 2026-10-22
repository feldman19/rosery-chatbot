import copy
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from pathlib import Path
import sqlite3
import threading
import json
import tempfile
import unittest

from core import Denied
from orchestrator import SandboxCoordinator
from strategy import sample_case


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup);self.key=b'x'*32
        self.app=SandboxCoordinator(self.temp.name,self.key)
        self.now=datetime(2026,10,17,12,tzinfo=timezone.utc)
        fixture=sample_case(self.now)
        self.p,self.e,self.policy=fixture['profile'],fixture['evidence'],fixture['policy']
        self.e=self.app.seal_evidence(self.e)
        self.tenant=self.p['tenant_id'];self.account=self.p['account_id'];self.entity=self.p['entity_id']
        self.app.core.bind_account(self.tenant,self.account)
        state={k:self.e['entity'][k] for k in ('currency','status','budget_type','budget_minor')}
        self.app.core.seed_entity(self.tenant,self.account,self.entity,state)
        self.app.authority.bind_account(self.tenant,self.account)
        self.app.authority.set_mode(self.tenant,self.account,'execute_in_envelope')
        self.grant=self.app.authority.issue_grant(self.tenant,self.account,{
            'currency':'ILS','budget_type':'daily','cap_minor':10000,
            'allowed_actions':['pause','set_budget'],'allowed_entity_ids':[self.entity],
            'expires_at':(self.now+timedelta(days=5)).isoformat(),'ends_at':(self.now+timedelta(days=5)).isoformat(),
            'min_interval_seconds':0,'max_evidence_age_seconds':900})
    def prepare(self):return self.app.prepare(self.p,self.e,self.policy,self.now)
    def current(self):return copy.deepcopy((self.p,self.e,self.policy))
    def dispatch(self,run,**kwargs):
        return self.app.dispatch(self.tenant,run['id'],self.grant['id'],self.current,now=self.now,**kwargs)
    def test_business_to_signed_authority_to_readback_and_outcome(self):
        run=self.prepare();result=self.dispatch(run)
        self.assertEqual(result['state'],'verified');self.assertFalse(result['live_action_allowed'])
        self.assertEqual(result['receipt']['readback']['state']['budget_minor'],7000)
        self.assertEqual(self.app.authority.get(self.tenant,result['intent'])['state'],'verified')
        baseline=dict(started_at='2026-10-16T00:00:00+00:00',ended_at='2026-10-17T00:00:00+00:00',spend_minor=1000,registrations=10,complete=True)
        after={**baseline,'started_at':'2026-10-18T00:00:00+00:00','ended_at':'2026-10-19T00:00:00+00:00','registrations':12}
        outcome=self.app.observe(self.tenant,run['id'],baseline,after,[],now=self.now+timedelta(days=3))
        self.assertTrue(outcome['operational_success']);self.assertFalse(outcome['business_success_verified']);self.assertFalse(outcome['causal_effect_verified'])
    def test_duplicate_prepare_and_restart_keep_same_proposal(self):
        run=self.prepare();again=self.prepare()
        self.assertEqual(run['id'],again['id']);self.assertEqual(run['proposal'],again['proposal'])
        self.app=SandboxCoordinator(self.temp.name,self.key)
        self.assertEqual(self.prepare()['proposal'],run['proposal'])
        self.assertEqual(len(self.app.core.list_proposals(self.tenant)),1)
    def test_policy_or_evidence_change_blocks_dispatch(self):
        run=self.prepare();self.policy['cap_minor']=9000
        self.assertEqual(self.dispatch(run)['state'],'rejected')
        self.assertEqual(self.app.core.get_entity(self.tenant,self.account,self.entity)['state']['budget_minor'],6000)
    def test_references_require_actual_content_artifact(self):
        self.e['provenance']['refs']=['sha256:'+'a'*64]
        with self.assertRaises(Denied):self.prepare()
    def test_real_accounts_never_executable(self):
        self.p['account_id']='393059510164294'
        with self.assertRaisesRegex(Denied,'sandbox_ids'):self.prepare()
    def test_hold_and_missing_measurement_never_create_operations(self):
        self.policy['allowed_actions']=[];self.assertEqual(self.prepare()['state'],'hold')
        self.e['measurement']['verified']=False;self.e=self.app.seal_evidence(self.e)
        self.assertEqual(self.prepare()['state'],'blocked');self.assertEqual(self.app.core.list_proposals(self.tenant),[])
    def test_timeout_keeps_unknown_and_never_redispatches(self):
        run=self.prepare();result=self.dispatch(run,fault='after_success_timeout')
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(self.app.authority.get(self.tenant,result['intent'])['state'],'unknown')
        self.app=SandboxCoordinator(self.temp.name,self.key)
        with self.assertRaises(Denied):self.dispatch(run)
        with closing(sqlite3.connect(self.app.core.db_path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0],1)
    def test_concurrent_workers_write_once(self):
        run=self.prepare()
        def work(_):
            try:return self.dispatch(run)['state']
            except Denied:return 'denied'
        with ThreadPoolExecutor(max_workers=2) as pool:states=list(pool.map(work,range(2)))
        self.assertEqual(states.count('verified'),1)
        with closing(sqlite3.connect(self.app.core.db_path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0],1)
    def test_manual_state_drift_cannot_be_called_verified(self):
        run=self.prepare();state={**run['payload']['candidate']['before'],'budget_minor':6100}
        self.app.core.seed_entity(self.tenant,self.account,self.entity,state)
        self.assertNotEqual(self.dispatch(run)['state'],'verified')
        self.assertEqual(self.app.core.get_entity(self.tenant,self.account,self.entity)['state']['budget_minor'],6100)
    def test_kill_switch_prevents_mutation(self):
        run=self.prepare();self.app.authority.set_kill_switch(self.tenant,self.account,True)
        self.assertNotEqual(self.dispatch(run)['state'],'verified')
        self.assertEqual(self.app.core.get_entity(self.tenant,self.account,self.entity)['state']['budget_minor'],6000)
    def test_outcome_requires_verified_execution_and_completed_future_day(self):
        run=self.prepare();w=dict(started_at='2026-10-18T00:00:00+00:00',ended_at='2026-10-19T00:00:00+00:00',spend_minor=1000,registrations=10,complete=True)
        with self.assertRaises(Denied):self.app.observe(self.tenant,run['id'],w,w,[])
        self.dispatch(run)
        before={**w,'started_at':'2026-10-16T00:00:00+00:00','ended_at':'2026-10-17T00:00:00+00:00'}
        with self.assertRaises(Denied):self.app.observe(self.tenant,run['id'],before,w,[],now=self.now)
    def test_core_digest_is_signed_and_idempotent(self):
        run=self.prepare();proposal=self.app.core.get_proposal(self.tenant,run['proposal'])
        self.assertEqual(proposal['decision_digest'],run['id'])
        self.assertEqual(self.app.core.propose(self.tenant,'sandbox-coordinator',self.account,self.entity,proposal['diff'],run['id'])['id'],proposal['id'])
        with self.assertRaises(Denied):self.app.core.propose(self.tenant,'sandbox-coordinator',self.account,self.entity,{'status':'PAUSED'},run['id'])

    def test_lease_expiry_before_mutation_is_checked_at_actual_gate(self):
        run=self.prepare();times=iter([self.now,self.now,self.now+timedelta(seconds=61),self.now+timedelta(seconds=61)])
        result=self.app.dispatch(self.tenant,run['id'],self.grant['id'],self.current,clock=lambda:next(times))
        self.assertNotEqual(result['state'],'verified')
        self.assertEqual(self.app.core.get_entity(self.tenant,self.account,self.entity)['state']['budget_minor'],6000)

    def test_expiry_after_mutation_cannot_be_called_verified(self):
        run=self.prepare();times=iter([self.now,self.now,self.now,self.now+timedelta(seconds=61)])
        result=self.app.dispatch(self.tenant,run['id'],self.grant['id'],self.current,clock=lambda:next(times))
        self.assertEqual(result['state'],'unknown');self.assertIsNone(result['executed_at'])

    def test_receipt_time_is_completion_not_dispatch_start(self):
        run=self.prepare();times=iter([self.now,self.now,self.now,self.now+timedelta(seconds=2)])
        result=self.app.dispatch(self.tenant,run['id'],self.grant['id'],self.current,clock=lambda:next(times))
        self.assertEqual(result['executed_at'],(self.now+timedelta(seconds=2)).isoformat())

    def test_late_failed_preflight_cannot_overwrite_verified_receipt(self):
        run=self.prepare();started=threading.Event();release=threading.Event()
        def stale():
            started.set();release.wait(5)
            profile,evidence,policy=self.current();policy['cap_minor']=9000
            return profile,evidence,policy
        with ThreadPoolExecutor(max_workers=2) as pool:
            losing=pool.submit(self.app.dispatch,self.tenant,run['id'],self.grant['id'],stale,self.now)
            self.assertTrue(started.wait(5));winner=self.dispatch(run);release.set();losing.result()
        final=self.app.get(self.tenant,run['id'])
        self.assertEqual(final['state'],'verified');self.assertEqual(final['receipt'],winner['receipt']);self.assertEqual(final['executed_at'],winner['executed_at'])

    def test_forged_operational_state_is_not_accepted_as_execution(self):
        run=self.prepare()
        with self.app.db() as db:db.execute("UPDATE runs SET state='verified',executed_at=? WHERE id=?",(self.now.isoformat(),run['id']))
        with self.assertRaisesRegex(Denied,'signature'):self.app.get(self.tenant,run['id'])

    def test_core_approval_covers_decision_digest(self):
        run=self.prepare();self.app.core.approve(self.tenant,'test',run['proposal'])
        with closing(sqlite3.connect(self.app.core.db_path)) as db,db:
            raw=db.execute('SELECT data FROM proposals WHERE id=?',(run['proposal'],)).fetchone()[0]
            payload=json.loads(raw);payload['decision_digest']='f'*64
            db.execute('UPDATE proposals SET data=? WHERE id=?',(json.dumps(payload),run['proposal']))
        self.assertEqual(self.app.core.execute(self.tenant,run['proposal'])['state'],'rejected')

    def test_other_prepare_cannot_deadlock_core_prewrite_guard(self):
        run=self.prepare();other=SandboxCoordinator(self.temp.name,self.key)
        entered=threading.Event();release=threading.Event();core_requested=threading.Event()
        original_get=self.app.get;calls=[0]
        def guarded_get(*args):
            calls[0]+=1
            if calls[0]==2:entered.set();release.wait(5)
            return original_get(*args)
        self.app.get=guarded_get
        original_read=other.core.get_entity
        def other_read(*args):core_requested.set();return original_read(*args)
        other.core.get_entity=other_read
        policy={**self.policy,'budget_step_minor':500}
        def prepare_other():
            try:return other.prepare(self.p,self.e,policy,self.now)['state']
            except Denied:return 'state_drift'
        with ThreadPoolExecutor(max_workers=2) as pool:
            executing=pool.submit(self.dispatch,run)
            self.assertTrue(entered.wait(3))
            preparing=pool.submit(prepare_other)
            self.assertTrue(core_requested.wait(3));release.set()
            self.assertEqual(executing.result(timeout=3)['state'],'verified')
            self.assertIn(preparing.result(timeout=3),('proposed','state_drift'))
