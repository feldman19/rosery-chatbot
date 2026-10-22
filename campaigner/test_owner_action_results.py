"""Pure local action receipts: no provider I/O or advertising authority."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from owner_action_proposals import build_owner_action_proposals, ACCOUNT
from owner_action_results import execute_local, run_analysis

class OwnerActionResultsTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,10,20,12,tzinfo=timezone.utc)
  self.report={'account_id':ACCOUNT,'checked_at':self.now.isoformat(),'freshness':{'state':'fresh','meta_last_success_at':self.now.isoformat()},'windows':{}}
  for days in ('1','3','7'):
   self.report['windows'][days]={'partial':False,'since':'2026-10-19','until':'2026-10-19','adsets':[{'id':'s','campaign_id':'c','configured_active':True,'result_type':'purchase','spend':100,'purchases':None}], 'ads':[{'id':'a','name':'One','adset_id':'s','spend':100,'purchases':None,'configured_active':True}]}
  self.cards=build_owner_action_proposals(self.report,now=self.now)['proposals']
  self.card=self.cards[0]
  self.body={'id':self.card['id'],'source_binding':deepcopy(self.card['source_binding'])}
 def test_stale_binding_rejected_without_receipt(self):
  with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
   root=Path(directory)/'receipts';body=deepcopy(self.body);body['source_binding']['snapshot_sha256']='0'*64
   with self.assertRaises(ValueError):execute_local(root,self.report,self.cards,body)
   self.assertFalse(root.exists())
 def test_regenerated_card_rejects_changed_scope(self):
  self.report['windows']['1']['ads'][0]['spend']=101
  current=build_owner_action_proposals(self.report,now=self.now)['proposals']
  with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
   with self.assertRaises(ValueError):execute_local(Path(directory),self.report,current,self.body)
 def test_repeat_receipt_is_identical_and_single_file(self):
  with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
   root=Path(directory);first=execute_local(root,self.report,self.cards,self.body);second=execute_local(root,self.report,self.cards,self.body)
   self.assertEqual(first,second);self.assertEqual(len(list(root.glob('*.json'))),1)
   self.assertEqual(first['provider_writes'],0);self.assertFalse(first['campaign_changes'])
 def test_cannot_execute_operation_even_if_card_is_exact(self):
  operation={**self.card,'next_action':{'type':'execute_approved_operation','operation_id':'op','digest':'a'*64}}
  with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
   with self.assertRaises(ValueError):execute_local(Path(directory),self.report,[operation],self.body)
   self.assertEqual(list(Path(directory).iterdir()),[])
 def test_no_ad_data_not_completed(self):
  for window in self.report['windows'].values():window['ads']=[]
  result=run_analysis(self.report,self.card)
  self.assertEqual(result['state'],'blocked');self.assertIn('לא הושלם',result['conclusion'])
 def test_partial_day_not_filled_from_other_days(self):
  self.report['windows']['1']['partial']=True
  result=run_analysis(self.report,self.card)
  self.assertEqual(result['state'],'blocked');self.assertEqual([x['days'] for x in result['periods']],[3,7])
 def test_missing_not_zero_or_repaired(self):
  result=run_analysis(self.report,self.card)
  self.assertEqual(result['state'],'completed');self.assertIsNone(result['periods'][0]['ads'][0]['purchases']);self.assertIsNone(result['periods'][0]['ads'][0]['cpa'])
  self.assertEqual(result['scope'],'saved_report_analysis_not_live_measurement_repair')
 def test_exact_body_no_extra_fields(self):
  with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
   with self.assertRaises(ValueError):execute_local(Path(directory),self.report,self.cards,{**self.body,'action':'activate'})
 def test_unrelated_ads_never_included(self):
  self.report['windows']['1']['ads'].append({'id':'foreign','adset_id':'other','spend':999,'purchases':5})
  result=run_analysis(self.report,self.card)
  self.assertEqual([x['id'] for x in result['periods'][0]['ads']],['a'])

if __name__=='__main__':unittest.main()

