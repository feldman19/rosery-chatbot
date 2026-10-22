from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from decision_loop import DecisionLoop
from decision_history import save_observation, ACCOUNT


class FreezeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=TemporaryDirectory(dir=Path(__file__).parent);self.loop=DecisionLoop(self.tmp.name)
  self.now=datetime(2026,10,20,12,tzinfo=timezone.utc)
  self.source={'last_success_at':self.now.isoformat(),'data':{'verified':True,'complete':True,
    'account_id':ACCOUNT,'currency':'ILS','today':'2026-10-20','timezone':'Asia/Jerusalem',
    'since':'2026-10-13','until':'2026-10-20','query_settings':{'action_report_time':'impression'},
    'rows':[{'ad_id':'synthetic','date_start':'2026-10-19','date_stop':'2026-10-19',
             'spend':'10','actions':[{'action_type':'purchase','value':'1'}]}]}}
  self.snapshot={'sources':{'meta':self.source}}
  self.output={'account_id':ACCOUNT,'recommendations':[{'action':'INSUFFICIENT_EVIDENCE','rationale':'synthetic fixture only'}]}
 def tearDown(self):self.tmp.cleanup()
 def freeze(self):return self.loop.freeze_recommendations(self.snapshot,self.output,{'synthetic':True},self.now)
 def test_repeat_and_late_source_do_not_rewrite(self):
  first=self.freeze();saved=self.loop.read_frozen()[0]
  self.assertEqual(self.freeze()['id'],first['id']);self.assertEqual(self.freeze()['status'],'already_recorded')
  self.assertEqual(len(self.loop.read_frozen()),1)
  self.source['data']['rows'][0]['spend']='100'
  second=self.freeze();self.assertNotEqual(first['id'],second['id'])
  original=next(x for x in self.loop.read_frozen() if x['id']==first['id'])
  self.assertEqual(saved,original)
 def test_future_reporting_only_never_profit_or_causal_success(self):
  first=self.freeze()
  self.assertEqual(self.loop.evaluate_frozen(first['id'])['status'],'pending')
  later=deepcopy(self.source);later['last_success_at']=(self.now+timedelta(hours=1)).isoformat()
  later['data']['rows'][0]['actions'][0]['value']='2'
  save_observation(self.tmp.name,later)
  result=self.loop.evaluate_frozen(first['id'])
  self.assertEqual(result['status'],'reporting_observed')
  self.assertEqual(len(result['changed_cells']),1)
  for key in ('profit_verified','causal_effect_verified','decision_quality_verified','attribution_maturity_verified','execution_performed'):
   self.assertFalse(result[key])
 def test_wrong_settings_or_past_observation_stays_pending(self):
  first=self.freeze();save_observation(self.tmp.name,self.source)
  later=deepcopy(self.source);later['last_success_at']=(self.now+timedelta(hours=1)).isoformat()
  later['data']['query_settings']['action_report_time']='conversion'
  save_observation(self.tmp.name,later)
  self.assertEqual(self.loop.evaluate_frozen(first['id'])['status'],'pending')
 def test_tamper_detected(self):
  first=self.freeze()
  with self.loop.db() as db:db.execute("UPDATE shadow_assessments SET payload='{}' WHERE id=?",(first['id'],))
  with self.assertRaises((ValueError,KeyError)):self.loop.evaluate_frozen(first['id'])
 def test_legacy_reader_and_preserved_development_receipt(self):
  first=self.freeze();saved=self.loop.read_frozen()[0]
  from decision_loop import encode
  with self.loop.db() as db:
   db.execute('INSERT INTO assessments VALUES(?,?,?,?)',
              (saved['id'],saved['at'],saved['local_day'],encode(saved['payload'])))
   db.execute('DELETE FROM shadow_assessments WHERE id=?',(first['id'],))
  reopened=DecisionLoop(self.tmp.name)
  self.assertEqual(reopened.read_frozen()[0],saved)
  self.assertEqual(reopened.read()['assessment_count'],0)

if __name__=='__main__':unittest.main()
