from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import unittest
from autonomy import AutonomyStore,AutonomyDenied

class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(dir=Path(__file__).resolve().parents[1],prefix='autonomy-test-')
        self.addCleanup(self.tmp.cleanup)
        self.path=self.tmp.name+'/auto.db'; self.key=b'a'*32
        self.app=AutonomyStore(self.path,self.key)
        self.app.bind_account('t','a')
        self.now=datetime(2026,10,16,12,tzinfo=timezone.utc)
        self.refs=['sha256:'+'a'*64]
        self.scope={'currency':'ILS','budget_type':'daily','cap_minor':10000,'allowed_actions':['set_budget','pause'],'allowed_entity_ids':['e1','e2','e3'],'expires_at':(self.now+timedelta(days=10)).isoformat(),'ends_at':(self.now+timedelta(days=5)).isoformat(),'min_interval_seconds':0,'max_evidence_age_seconds':3600}
        self.g=self.app.issue_grant('t','a',self.scope)
        self.ctx={'currency':'ILS','budget_type':'daily','evidence_refs':self.refs,'evidence_at':self.now.isoformat(),'data_quality_ok':True,'stock_ok':True,'offer_ok':True}
    def intent(self,entity='e1',amount=6000,action='set_budget',grant=None,at=None):
        self.app.set_mode('t','a','execute_in_envelope')
        return self.app.create_intent('t','a',(grant or self.g)['id'],entity,action,amount,self.refs,at or self.now)['id']
    def claim(self,jid,**kw): return self.app.claim('t',jid,'w',self.ctx,now=self.now,**kw)
    def test_default_observe_disables_intents(self):
        with self.assertRaises(AutonomyDenied): self.app.create_intent('t','a',self.g['id'],'e1','set_budget',100,self.refs,self.now)
    def test_propose_cannot_claim(self):
        jid=self.intent(); self.app.set_mode('t','a','propose')
        with self.assertRaises(AutonomyDenied): self.claim(jid)
    def test_kill_switch_blocks(self):
        jid=self.intent(); self.app.set_kill_switch('t','a',True)
        with self.assertRaises(AutonomyDenied): self.claim(jid)
    def test_scope_entity_action_and_pause_amount(self):
        for entity,action,amount in [('foreign','set_budget',100),('e1','delete',0),('e1','pause',10)]:
            with self.assertRaises(AutonomyDenied): self.intent(entity,amount,action)
    def test_tenant_isolation(self):
        jid=self.intent()
        with self.assertRaises(AutonomyDenied): self.app.get('other',jid)
        with self.assertRaises(AutonomyDenied): self.app.claim('other',jid,'w',self.ctx,self.now)
        with self.assertRaises(AutonomyDenied): self.app.bind_account('other','a')
        self.assertEqual(self.app.list('other'),[])
    def test_scope_mismatches(self):
        jid=self.intent()
        for key,value in [('currency','USD'),('budget_type','total')]:
            with self.assertRaises(AutonomyDenied): self.app.claim('t',jid,'w',{**self.ctx,key:value},self.now)
    def test_all_boolean_gates_strict(self):
        jid=self.intent()
        for field in ('stock_ok','offer_ok','data_quality_ok'):
            for bad in (False,1,'true',None):
                with self.assertRaises(AutonomyDenied): self.app.claim('t',jid,'w',{**self.ctx,field:bad},self.now)
    def test_missing_evidence_and_hashes(self):
        self.app.set_mode('t','a','propose')
        for refs in ([],['plain source'],['sha256:'+'x'*64]):
            with self.assertRaises(AutonomyDenied): self.app.create_intent('t','a',self.g['id'],'e1','pause',0,refs,self.now)
    def test_evidence_refs_changed(self):
        jid=self.intent()
        with self.assertRaises(AutonomyDenied): self.app.claim('t',jid,'w',{**self.ctx,'evidence_refs':['sha256:'+'b'*64]},self.now)
    def test_evidence_timestamp_cannot_be_freshened(self):
        jid=self.intent(at=self.now-timedelta(days=2))
        with self.assertRaisesRegex(AutonomyDenied,'evidence_time_changed'): self.claim(jid)
    def test_stale_and_future_evidence(self):
        for offset in (-7200,60):
            at=self.now+timedelta(seconds=offset); jid=self.intent(at=at)
            with self.assertRaises(AutonomyDenied): self.app.claim('t',jid,'w',{**self.ctx,'evidence_at':at.isoformat()},self.now)
    def test_grant_expiry_and_end(self):
        for field in ('expires_at','ends_at'):
            g=self.app.issue_grant('t','a',{**self.scope,field:self.now.isoformat()})
            jid=self.intent(grant=g)
            with self.assertRaises(AutonomyDenied): self.claim(jid)
    def test_aggregate_concurrent_reservations(self):
        ids=[self.intent('e1'),self.intent('e2')]
        def go(jid):
            try: return self.app.claim('t',jid,'w',self.ctx,self.now)['state']
            except AutonomyDenied: return 'denied'
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(go,ids))
        self.assertEqual(results.count('claimed'),1)
    def test_same_job_claim_once(self):
        jid=self.intent()
        def go(_):
            try: return self.claim(jid)['state']
            except AutonomyDenied: return 'denied'
        with ThreadPoolExecutor(max_workers=6) as pool: results=list(pool.map(go,range(6)))
        self.assertEqual(results.count('claimed'),1)
    def test_grant_overlap_cannot_double_cap(self):
        self.claim(self.intent('e1'))
        g=self.app.issue_grant('t','a',self.scope)
        with self.assertRaisesRegex(AutonomyDenied,'aggregate_cap'): self.claim(self.intent('e2',grant=g))
    def test_unknown_blocks_entity_and_retains_exposure(self):
        jid=self.intent(); c=self.claim(jid)
        self.app.complete('t',jid,c['claim_token'],'unknown',now=self.now)
        with self.assertRaises(AutonomyDenied): self.claim(jid)
        with self.assertRaisesRegex(AutonomyDenied,'entity_reserved'): self.claim(self.intent('e1',0,'pause'))
        with self.assertRaisesRegex(AutonomyDenied,'aggregate_cap'): self.claim(self.intent('e2'))
    def test_lease_expiration_restart_unknown(self):
        jid=self.intent(); self.claim(jid,lease_seconds=1)
        self.app=AutonomyStore(self.path,self.key)
        self.assertEqual(self.app.expire_leases(self.now+timedelta(seconds=2)),1)
        self.assertEqual(self.app.get('t',jid)['state'],'unknown')
        with self.assertRaises(AutonomyDenied): self.claim(jid)
    def test_late_success_does_not_clear_unknown(self):
        jid=self.intent(); c=self.claim(jid,lease_seconds=1)
        result=self.app.complete('t',jid,c['claim_token'],'verified','mock:receipt',self.now+timedelta(seconds=2))
        self.assertEqual(result['state'],'unknown')
    def test_claim_token_required_and_no_replay(self):
        jid=self.intent(); c=self.claim(jid)
        with self.assertRaises(AutonomyDenied): self.app.complete('t',jid,'wrong','verified','proof',self.now)
        self.app.complete('t',jid,c['claim_token'],'verified','proof',self.now)
        with self.assertRaises(AutonomyDenied): self.app.complete('t',jid,c['claim_token'],'verified','proof',self.now)
    def test_not_applied_requires_proof_and_releases(self):
        jid=self.intent(); c=self.claim(jid)
        with self.assertRaises(AutonomyDenied): self.app.complete('t',jid,c['claim_token'],'not_applied',now=self.now)
        self.app.complete('t',jid,c['claim_token'],'not_applied','trusted-host-proof',self.now)
        self.assertEqual(self.claim(self.intent('e2'))['state'],'claimed')
    def test_verified_exposure_retained(self):
        jid=self.intent(); c=self.claim(jid)
        self.app.complete('t',jid,c['claim_token'],'verified','mock-proof',self.now)
        with self.assertRaisesRegex(AutonomyDenied,'aggregate_cap'): self.claim(self.intent('e2'))
    def test_cadence(self):
        g=self.app.issue_grant('t','a',{**self.scope,'min_interval_seconds':60})
        self.claim(self.intent('e1',100,grant=g))
        with self.assertRaisesRegex(AutonomyDenied,'cadence_block'): self.claim(self.intent('e2',100,grant=g))
    def test_total_bucket_never_resets(self):
        g=self.app.issue_grant('t','a',{**self.scope,'budget_type':'total'})
        self.ctx['budget_type']='total'
        jid=self.intent(grant=g); c=self.claim(jid)
        self.app.complete('t',jid,c['claim_token'],'verified','proof',self.now)
        later=self.now+timedelta(days=1)
        jid=self.intent('e2',grant=g,at=later)
        with self.assertRaisesRegex(AutonomyDenied,'aggregate_cap'): self.app.claim('t',jid,'w',{**self.ctx,'evidence_at':later.isoformat()},later)
    def test_unknown_daily_exposure_crosses_midnight(self):
        jid=self.intent(); c=self.claim(jid)
        self.app.complete('t',jid,c['claim_token'],'unknown',now=self.now)
        later=self.now+timedelta(days=1)
        other=self.intent('e2',at=later)
        with self.assertRaisesRegex(AutonomyDenied,'aggregate_cap'): self.app.claim('t',other,'w',{**self.ctx,'evidence_at':later.isoformat()},later)
    def test_grant_and_intent_tampering(self):
        jid=self.intent()
        with closing(sqlite3.connect(self.path)) as db:
            raw=db.execute('SELECT payload FROM auto_jobs WHERE id=?',(jid,)).fetchone()[0]
            p=json.loads(raw); p['amount_minor']=0
            db.execute('UPDATE auto_jobs SET payload=? WHERE id=?',(json.dumps(p),jid)); db.commit()
        with self.assertRaisesRegex(AutonomyDenied,'intent_changed'): self.claim(jid)
        with closing(sqlite3.connect(self.path)) as db:
            raw=db.execute('SELECT payload FROM auto_grants WHERE id=?',(self.g['id'],)).fetchone()[0]
            p=json.loads(raw); p['cap_minor']=999999
            db.execute('UPDATE auto_grants SET payload=? WHERE id=?',(json.dumps(p),self.g['id'])); db.commit()
        with self.assertRaisesRegex(AutonomyDenied,'invalid_grant_signature'): self.intent('e2')
    def test_rotation_denies_old_grants(self):
        self.app=AutonomyStore(self.path,b'x'*32)
        with self.assertRaises(AutonomyDenied): self.intent()
    def test_lease_bounded_by_grant(self):
        g=self.app.issue_grant('t','a',{**self.scope,'expires_at':(self.now+timedelta(seconds=3)).isoformat()})
        c=self.claim(self.intent(grant=g),lease_seconds=60)
        self.assertEqual(c['lease_end'],g['expires_at'])
    def test_no_tokens_in_public_job(self):
        jid=self.intent(); self.claim(jid)
        self.assertNotIn('claim_token',self.app.get('t',jid))
        self.assertFalse(self.app.get('t',jid)['live'])

if __name__=='__main__': unittest.main()
