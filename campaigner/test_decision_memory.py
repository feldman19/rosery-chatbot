from datetime import datetime, timezone, timedelta
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from decision_memory import DecisionMemory, current_evidence


NOW=datetime(2026,10,20,12,tzinfo=timezone.utc)


def row(identity, purchases, spend, revenue, name='bracelet', visual=''):
    return {'id':identity,'name':name,'creative_id':'c'+identity,'campaign_name':'sales',
            'spend':spend,'purchases':purchases,'revenue':revenue,
            'format':'image','visual_observation':visual}


def envelope(account='111'):
    return {'account_id':account,'captured_at':'2026-10-19T12:00:00+00:00','currency':'ILS',
            'coverage':'all available; exact start unknown','source_hashes':{'source.json':'a'*64},
            'rows':[row('1',100,1000,4000),row('2',1,1,50),row('3',0,900,0),row('4',None,500,None)],
            'limits':['Observational; attribution is not net profit']}


def live(account='111'):
    return {'cache_restored':{'meta':False},'sources':{'meta':{'last_success_at':NOW.isoformat(),'error':None,
            'data':{'account_id':account,'verified':True,'complete':True,'currency':'ILS',
                    'since':'2026-10-19','until':'2026-10-20','rows':[
                        {'ad_id':'1','date_start':'2026-10-19','date_stop':'2026-10-19','spend':'10',
                         'actions':[{'action_type':'offsite_conversion_fb_pixel_purchase','value':'2'}],
                         'action_values':[{'action_type':'offsite_conversion_fb_pixel_purchase','value':'80'}]}]}}}}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path=Path(self.tmp.name)/'memory.sqlite3'
        self.memory=DecisionMemory(self.path)
        self.memory.bind('business-a','111')
        self.receipt=self.memory.ingest('business-a','111',envelope())

    def tearDown(self):
        self.tmp.cleanup()

    def test_volume_small_sample_lower_efficiency_and_unknown_are_visible(self):
        value=self.memory.history('business-a','111',limit=6)
        examples={r['id']:r for r in value['examples']}
        self.assertEqual(set(examples),{'1','2','3','4'})
        self.assertEqual(examples['1']['purchases'],100)
        self.assertEqual(examples['2']['purchases'],1)
        self.assertIn('sample_not_validated',examples['2']['retrieval_reason'])
        self.assertIsNone(examples['4']['purchases'])
        self.assertIsNone(examples['4']['cpa'])

    def test_selected_ad_and_lexical_match_do_not_claim_comparability(self):
        self.assertEqual(self.memory.history('business-a','111',ad_id='3')['examples'][0]['id'],'3')
        reasons=[r['retrieval_reason'] for r in self.memory.history('business-a','111','bracelet')['examples']]
        self.assertIn('lexical_match_not_comparability_proof',reasons)

    def test_two_businesses_remain_separate_and_binding_cannot_be_stolen(self):
        self.memory.bind('business-b','222')
        self.memory.ingest('business-b','222',envelope('222'))
        with self.assertRaises(ValueError):self.memory.bind('business-b','111')
        with self.assertRaises(ValueError):self.memory.history('business-b','111')
        self.assertEqual(self.memory.context('business-b','222',{},now=NOW)['after']['decisions'],[])

    def test_append_idempotence_persistence_and_tamper_detection(self):
        self.assertEqual(self.memory.ingest('business-a','111',envelope())['snapshot_id'],self.receipt['snapshot_id'])
        reopened=DecisionMemory(self.path)
        self.assertEqual(reopened.history('business-a','111')['rows'],4)
        db=sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM memory_snapshots').fetchone()[0],1)
            raw=json.loads(db.execute('SELECT payload FROM memory_snapshots').fetchone()[0])
            raw['rows'][0]['purchases']=999
            db.execute('UPDATE memory_snapshots SET payload=?',(json.dumps(raw),))
            db.commit()
        finally:db.close()
        with self.assertRaises(ValueError):reopened.history('business-a','111')

    def test_rejects_duplicate_foreign_invalid_and_secret_like_imports(self):
        bad=[]
        p=envelope();p['rows'].append(p['rows'][0]);bad.append(p)
        p=envelope('222');bad.append(p)
        p=envelope();p['rows'][0]['spend']=float('nan');bad.append(p)
        p=envelope();p['rows'][0]['purchases']=-1;bad.append(p)
        p=envelope();p['rows'][0]['name']='access_token=do-not-print';bad.append(p)
        for payload in bad:
            with self.subTest(kind=str(len(payload['rows']))):
                with self.assertRaises(ValueError):self.memory.ingest('business-a','111',payload)

    def test_followup_uses_existing_ledger_and_does_not_claim_execution_or_causality(self):
        d=self.memory.record_decision('business-a','111','Test closeup',['history:source'],'prepare experiment','Compare after a complete window')
        self.memory.record_observation('business-a','111',d['id'],['snapshot:later'],'before_after','More attributed purchases')
        value=DecisionMemory(self.path).context('business-a','111',{},now=NOW)
        self.assertEqual(value['after']['observations'][0]['parent'],d['id'])
        self.assertFalse(value['after']['causal_effect_verified'])
        self.assertFalse(value['after']['decisions'][0]['payload']['live_permission'])
        self.assertFalse(value['recommendation']['execution_allowed'])
        self.memory.bind('business-b','222')
        with self.assertRaises(ValueError):
            self.memory.record_observation('business-b','222',d['id'],['later'],'unknown','Foreign observation')

    def test_current_snapshot_is_not_replaced_by_history(self):
        value=self.memory.context('business-a','111',live(),'bracelet','1',NOW)
        self.assertEqual(value['past']['examples'][0]['purchases'],100)
        self.assertEqual(value['now']['metrics']['purchases'],2)
        self.assertEqual(value['now']['metrics']['spend'],10)
        self.assertFalse(value['now']['measurement_verified'])
        self.assertLess(len(json.dumps(value,ensure_ascii=False)),17000)

    def test_stale_partial_cache_error_and_foreign_current_data_are_not_fresh(self):
        cases=[]
        s=live();s['cache_restored']['meta']=True;cases.append(s)
        s=live();s['sources']['meta']['last_success_at']=(NOW-timedelta(hours=1)).isoformat();cases.append(s)
        s=live();s['sources']['meta']['error']='failed';cases.append(s)
        s=live();s['sources']['meta']['data']['complete']=False;cases.append(s)
        cases.append(live('222'))
        for s in cases:
            result=current_evidence(s,'111',NOW)
            self.assertNotEqual(result['status'],'fresh_connected_snapshot')
            self.assertIsNone(result['metrics'])

    def test_daily_duplicate_invalid_date_negative_missing_and_null_metrics(self):
        for kind in ('duplicate','date','negative','missing'):
            s=live();rows=s['sources']['meta']['data']['rows']
            if kind=='duplicate':rows.append(copy.deepcopy(rows[0]))
            if kind=='date':rows[0]['date_start']='2027-01-01'
            if kind=='negative':rows[0]['spend']='-10'
            if kind=='missing':del rows[0]['spend']
            self.assertEqual(current_evidence(s,'111',NOW)['status'],'invalid_source')
        s=live();s['sources']['meta']['data']['rows'][0]['actions']=[]
        self.assertIsNone(current_evidence(s,'111',NOW)['metrics']['purchases'])

    def test_no_history_has_explicit_gap(self):
        self.memory.bind('business-b','222')
        value=self.memory.context('business-b','222',{},now=NOW)
        self.assertEqual(value['past']['status'],'missing')
        self.assertEqual(value['after']['status'],'no_linked_outcome_evidence')


if __name__=='__main__':unittest.main()
