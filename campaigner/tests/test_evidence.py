from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from evidence import SnapshotStore,EvidenceDenied,ImportedSource

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1],prefix='evidence-test-')
        self.addCleanup(self.tmp.cleanup)
        self.path=self.tmp.name+'/evidence.db'
        self.store=SnapshotStore(self.path)
        self.store.bind_account('a','meta','account')
        self.now=datetime.now(timezone.utc)
        self.p={'platform':'meta','account_id':'account','captured_at':(self.now-timedelta(hours=1)).isoformat(),'currency':'ILS','account_timezone':'Asia/Jerusalem','window_start':(self.now-timedelta(days=2)).isoformat(),'window_end':(self.now-timedelta(hours=2)).isoformat(),'completeness':'complete','synthetic':True,'provenance':'manual demo fixture','attribution':'7d click,1d view','records':[{'entity_id':'ad1','name':'Demo ad','metrics':{'spend_minor':100,'clicks':1}}]}

    def test_persist_hash_and_normalization(self):
        self.p['captured_at']=datetime.fromisoformat(self.p['captured_at']).astimezone(timezone(timedelta(hours=3))).isoformat()
        result=self.store.ingest('a',self.p)
        loaded=SnapshotStore(self.path).get('a',result['id'],self.now)
        raw=json.dumps(loaded['payload'],sort_keys=True,separators=(',',':'),ensure_ascii=False)
        self.assertEqual(loaded['hash'],hashlib.sha256(raw.encode()).hexdigest())
        self.assertTrue(loaded['payload']['captured_at'].endswith('+00:00'))
        self.assertFalse(loaded['assessment']['current'])
        self.assertFalse(loaded['assessment']['live_action_allowed'])

    def test_tenant_isolation_and_binding(self):
        result=self.store.ingest('a',self.p)
        with self.assertRaises(EvidenceDenied): self.store.get('b',result['id'])
        with self.assertRaises(EvidenceDenied): self.store.ingest('b',self.p)
        with self.assertRaises(EvidenceDenied): self.store.bind_account('b','meta','account')
        self.assertEqual(self.store.list('b'),[])

    def test_unknown_and_credential_fields_rejected(self):
        for field in ('access_token','raw','tenant_id','api_key'):
            p=copy.deepcopy(self.p); p[field]='x'
            with self.assertRaises(EvidenceDenied): self.store.ingest('a',p)
        p=copy.deepcopy(self.p); p['records'][0]['token']='x'
        with self.assertRaises(EvidenceDenied): self.store.ingest('a',p)
        p=copy.deepcopy(self.p); p['provenance']='Bearer abc'
        with self.assertRaises(EvidenceDenied): self.store.ingest('a',p)

    def test_secret_assignments_and_bearer_whitespace(self):
        for text in ('secret=value','secret = value','SECRET   = value','Bearer token','bearer  token'):
            with self.subTest(text=text):
                p=copy.deepcopy(self.p)
                p['provenance']=text
                with self.assertRaisesRegex(EvidenceDenied,'credential_like_text'):
                    self.store.ingest('a',p)

    def test_naive_future_and_bad_window(self):
        for field,value in [('captured_at','2020-01-01T00:00:00'),('captured_at',(self.now+timedelta(days=1)).isoformat()),('window_end',(self.now+timedelta(days=2)).isoformat()),('window_start',self.p['window_end'])]:
            p=copy.deepcopy(self.p); p[field]=value
            with self.assertRaises(EvidenceDenied): self.store.ingest('a',p)

    def test_old_window_new_capture_stays_stale(self):
        self.p['window_start']=(self.now-timedelta(days=40)).isoformat()
        self.p['window_end']=(self.now-timedelta(days=30)).isoformat()
        result=self.store.ingest('a',self.p)
        self.assertEqual(result['assessment']['freshness'],'stale')

    def test_supplied_now_freshness(self):
        result=self.store.ingest('a',self.p)
        self.assertEqual(self.store.get('a',result['id'],self.now)['assessment']['freshness'],'recent_import_not_live')
        self.assertEqual(self.store.get('a',result['id'],self.now+timedelta(days=2))['assessment']['freshness'],'stale')
        self.assertEqual(self.store.get('a',result['id'],self.now-timedelta(days=4))['assessment']['freshness'],'future_relative_to_evaluation')
        with self.assertRaises(EvidenceDenied): self.store.get('a',result['id'],datetime(2020,1,1))

    def test_missing_stays_missing(self):
        del self.p['provenance']; del self.p['attribution']
        self.p['completeness']='unknown'
        result=self.store.ingest('a',self.p)
        self.assertEqual(set(result['assessment']['missing']),{'provenance','attribution'})
        self.assertEqual(result['assessment']['completeness'],'unknown')
        self.assertFalse(result['assessment']['source_verified'])

    def test_bad_metrics_and_duplicate_entities(self):
        for metrics in ({'spend_minor':True},{'clicks':-1},{'revenue_minor':1.2},{'api_key':123}):
            p=copy.deepcopy(self.p); p['records'][0]['metrics']=metrics
            with self.assertRaises(EvidenceDenied): self.store.ingest('a',p)
        self.p['records']*=2
        with self.assertRaises(EvidenceDenied): self.store.ingest('a',self.p)

    def test_invalid_timezone(self):
        self.p['account_timezone']='Invalid/DefinitelyNotAZone'
        with self.assertRaises(EvidenceDenied): self.store.ingest('a',self.p)

    def test_import_adapter_is_read_only(self):
        sid=self.store.ingest('a',self.p)['id']
        source=ImportedSource(self.store,'a',sid)
        self.assertFalse(source.capabilities()['live'])
        self.assertFalse(source.capabilities()['apply_change'])
        self.assertNotIn('metrics',source.get_entities()['entities'][0])
        self.assertEqual(source.get_insights()['id'],sid)
        with self.assertRaises(EvidenceDenied): ImportedSource(self.store,'b',sid).get_insights()

    def test_hash_tamper_detected(self):
        sid=self.store.ingest('a',self.p)['id']
        with closing(sqlite3.connect(self.path)) as db:
            p=copy.deepcopy(self.p); p['currency']='USD'
            db.execute('UPDATE evidence_snapshots SET payload=? WHERE id=?',(json.dumps(p),sid)); db.commit()
        with self.assertRaises(EvidenceDenied): self.store.get('a',sid)

    def test_shopify_and_unique_snapshot_ids(self):
        self.store.bind_account('a','shopify','shop')
        self.p.update(platform='shopify',account_id='shop',account_timezone='UTC')
        del self.p['attribution']
        a=self.store.ingest('a',self.p); b=self.store.ingest('a',self.p)
        self.assertNotEqual(a['id'],b['id'])
        self.assertEqual(a['hash'],b['hash'])
        self.assertNotIn('attribution',a['assessment']['missing'])

if __name__=='__main__': unittest.main()

