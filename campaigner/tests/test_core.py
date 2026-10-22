from contextlib import closing, contextmanager
import concurrent.futures
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from core import Campaigner, Denied

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1],prefix='core-test-')
        self.path=self.tmp.name+'/test.db'
        self.key=b'k'*32
        self.app=Campaigner(self.path,self.key)
        self.app.bind_account('a','account-a')
        self.app.bind_account('b','account-b')
        self.original={'budget_minor':6000,'currency':'ILS','budget_type':'daily','status':'PAUSED'}
        self.app.seed_entity('a','account-a','e',self.original)
        self.addCleanup(self.tmp.cleanup)

    def proposal(self):
        return self.app.propose('a','requester','account-a','e',{'budget_minor':7000})['id']

    def approved(self):
        pid=self.proposal()
        self.app.approve('a','owner',pid)
        return pid

    def count_operations(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT count(*) FROM operations').fetchone()[0]

    def test_success_restart_and_replay(self):
        pid=self.approved()
        self.app=Campaigner(self.path,self.key)
        result=self.app.execute('a',pid)
        self.assertEqual(result['state'],'verified')
        self.assertEqual(self.app.get_entity('a','account-a','e')['state']['budget_minor'],7000)
        with self.assertRaises(Denied): self.app.execute('a',pid)
        self.assertEqual(self.count_operations(),1)
        self.assertTrue(self.app.audit('a'))

    def test_tenant_isolation(self):
        pid=self.approved()
        for fn,args in [(self.app.get_proposal,('b',pid)),(self.app.approve,('b','owner',pid)),(self.app.execute,('b',pid)),(self.app.reconcile,('b',pid)),(self.app.get_entity,('b','account-a','e')),(self.app.propose,('b','x','account-a','e',{'status':'ACTIVE'})),(self.app.bind_account,('b','account-a'))]:
            with self.assertRaises(Denied): fn(*args)
        self.assertEqual(self.app.list_proposals('b'),[])
        self.assertEqual(self.count_operations(),0)

    def test_missing_approval(self):
        with self.assertRaises(Denied): self.app.execute('a',self.proposal())
        self.assertEqual(self.count_operations(),0)

    def test_expired(self):
        pid=self.proposal()
        with patch('core.time.time',return_value=100): self.app.approve('a','owner',pid,1)
        self.assertEqual(self.app.execute('a',pid)['state'],'rejected')
        self.assertEqual(self.count_operations(),0)

    def test_tampered_diff(self):
        pid=self.approved()
        with closing(sqlite3.connect(self.path)) as db:
            p=json.loads(db.execute('SELECT data FROM proposals WHERE id=?',(pid,)).fetchone()[0])
            p['diff']['budget_minor']=8000
            db.execute('UPDATE proposals SET data=? WHERE id=?',(json.dumps(p),pid))
            db.commit()
        self.assertEqual(self.app.execute('a',pid)['state'],'rejected')
        self.assertEqual(self.count_operations(),0)

    def test_rotated_key_denies_old_signature(self):
        pid=self.approved()
        self.app=Campaigner(self.path,b'x'*32)
        self.assertEqual(self.app.execute('a',pid)['state'],'rejected')

    def test_manual_change_invalidates(self):
        pid=self.approved()
        self.app.seed_entity('a','account-a','e',{**self.original,'budget_minor':8000})
        self.assertEqual(self.app.execute('a',pid)['receipt']['reason'],'state_drift')
        self.assertEqual(self.count_operations(),0)

    def test_currency_and_budget_type_drift(self):
        for key,value in [('currency','USD'),('budget_type','total')]:
            pid=self.approved()
            current=self.app.get_entity('a','account-a','e')['state']
            self.app.seed_entity('a','account-a','e',{**current,key:value})
            self.assertEqual(self.app.execute('a',pid)['state'],'rejected')
        self.assertEqual(self.count_operations(),0)

    def test_budget_limit_rechecked_after_restart(self):
        pid=self.approved()
        self.app=Campaigner(self.path,self.key,max_budget_minor=6500)
        self.assertEqual(self.app.execute('a',pid)['state'],'rejected')
        self.assertEqual(self.count_operations(),0)

    def test_external_edit_between_preflight_and_conditional_write(self):
        pid=self.approved()
        real_db=self.app._db
        calls=0
        @contextmanager
        def interleaved():
            nonlocal calls
            calls+=1
            if calls==2:
                other=Campaigner(self.path,self.key)
                other.seed_entity('a','account-a','e',{**self.original,'budget_minor':8500})
            with real_db() as db:
                yield db
        with patch.object(self.app,'_db',interleaved):
            result=self.app.execute('a',pid)
        self.assertEqual(result['receipt']['reason'],'provider_version_conflict')
        self.assertEqual(self.count_operations(),0)
        self.assertEqual(self.app.get_entity('a','account-a','e')['state']['budget_minor'],8500)

    def test_invalid_diff(self):
        for diff in [{},{'currency':'USD'},{'budget_minor':True},{'budget_minor':20001},{'budget_minor':-1},{'budget_minor':1.2},{'status':'DELETE'},{'account_id':'account-b'}]:
            with self.assertRaises(Denied): self.app.propose('a','x','account-a','e',diff)

    def test_concurrent_execution_one_call(self):
        pid=self.approved()
        def run(_):
            try: return Campaigner(self.path,self.key).execute('a',pid)['state']
            except Denied: return 'denied'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(run,range(8)))
        self.assertEqual(results.count('verified'),1)
        self.assertEqual(self.count_operations(),1)

    def test_two_proposals_same_version_only_one_applies(self):
        ids=[self.approved(),self.approved()]
        self.app.execute('a',ids[0])
        self.assertEqual(self.app.execute('a',ids[1])['state'],'rejected')
        self.assertEqual(self.count_operations(),1)

    def test_timeout_success_reconcile_no_retry(self):
        pid=self.approved()
        self.assertEqual(self.app.execute('a',pid,'after_success_timeout')['state'],'unknown')
        with self.assertRaises(Denied): self.app.execute('a',pid)
        self.assertEqual(self.app.reconcile('a',pid)['state'],'verified')
        self.assertEqual(self.count_operations(),1)

    def test_crash_after_submission_restart(self):
        pid=self.approved()
        with self.assertRaises(RuntimeError): self.app.execute('a',pid,'after_success_crash')
        self.app=Campaigner(self.path,self.key)
        self.assertEqual(self.app.get_proposal('a',pid)['state'],'executing')
        self.assertEqual(self.app.reconcile('a',pid)['state'],'verified')
        self.assertEqual(self.count_operations(),1)

    def test_before_submission_unknown_reserves_entity(self):
        pid=self.approved()
        self.app.execute('a',pid,'before_call')
        self.assertEqual(self.app.reconcile('a',pid)['state'],'unknown')
        second=self.approved()
        with self.assertRaises(Denied): self.app.execute('a',second)
        self.assertEqual(self.count_operations(),0)

    def test_reconcile_does_not_hide_later_drift(self):
        pid=self.approved()
        self.app.execute('a',pid,'after_success_timeout')
        self.app.seed_entity('a','account-a','e',{**self.original,'budget_minor':9000})
        result=self.app.reconcile('a',pid)
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(result['receipt']['reason'],'operation_succeeded_but_current_state_drifted')
        self.assertEqual(self.count_operations(),1)

if __name__=='__main__': unittest.main()




