import unittest
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from owner_action_proposals import build_owner_action_proposals,ACCOUNT
class Tests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,10,20,12,tzinfo=timezone.utc)
  self.report={'account_id':ACCOUNT,'checked_at':self.now.isoformat(),'freshness':{'state':'fresh','meta_last_success_at':self.now.isoformat()},'windows':{'1':{'partial':False,'adsets':[{'id':'a','campaign_id':'c','configured_active':True,'result_type':'purchase','name':'A','spend':100,'purchases':2},{'id':'b','campaign_id':'c','configured_active':True,'result_type':'purchase','spend':100,'purchases':1},{'id':'d','campaign_id':'c','configured_active':True,'result_type':'purchase','spend':50,'purchases':None}]}}}
  self.op={'operation_id':'op','digest':'a'*64,'expires_at':(self.now+timedelta(minutes=10)).isoformat(),'action':'set_budget','state':'prepared','approval_status':'pending','before':{'id':'a','daily_budget':10000},'after':{'id':'a','daily_budget':12000},'manifest':{'entity_id':'a','entity_type':'adset','changes':{'daily_budget':12000}},'blocked_reasons':[],'provider_write_attempted':False,'provider_write_performed':False,'scheduled_at':None}
 def run_engine(self,ops=None,proposal=None):return build_owner_action_proposals(self.report,ops,proposal,now=self.now)
 def test_two_useful_local_actions(self):
  items=self.run_engine()['proposals'];self.assertEqual([x['next_action']['type'] for x in items],['local_reconciliation','local_analysis']);self.assertEqual(items[1]['next_action']['entity_ids'],['b']);self.assertEqual(items[0]['next_action']['entity_ids'],['d'])
 def test_exact_operation_first(self):
  items=self.run_engine({'account_id':ACCOUNT,'proposals':[self.op]})['proposals'];self.assertEqual(len(items),3);self.assertEqual(items[0]['next_action'],{'type':'review_exact_operation','operation_id':'op','digest':'a'*64})
 def test_executed_expired_blocked_never_suggested(self):
  for changes in ({'state':'verified'},{'provider_write_attempted':True},{'expires_at':self.now.isoformat()},{'blocked_reasons':['blocked']},{'approval_status':'approved','execution_ready':False}):
   op={**self.op,**changes};items=self.run_engine({'account_id':ACCOUNT,'proposals':[op]})['proposals'];self.assertFalse(any(x['id']=='operation:op' for x in items))
 def test_attempted_sibling_suppresses_replay(self):
  done={**self.op,'operation_id':'old','state':'verified'};items=self.run_engine({'account_id':ACCOUNT,'proposals':[done,self.op]})['proposals'];self.assertFalse(any(x['id']=='operation:op' for x in items))
 def test_stale_wrong_account(self):
  self.report['freshness']['state']='stale';self.assertEqual(self.run_engine()['proposals'],[])
  self.report['account_id']='other'
  with self.assertRaises(ValueError):self.run_engine()
 def test_rebuilt_checked_time_does_not_change_binding(self):
  first=self.run_engine()['proposals'];self.report['checked_at']=(self.now-timedelta(seconds=1)).isoformat();self.assertEqual(first,self.run_engine()['proposals'])
 def test_changed_scope_changes_binding(self):
  first=self.run_engine()['proposals'][0]['source_binding'];self.report['windows']['1']['adsets'][0]['spend']=90;self.assertNotEqual(first,self.run_engine()['proposals'][0]['source_binding'])
 def test_old_available_creative_not_new_upload(self):
  result=self.run_engine({'account_id':ACCOUNT,'proposals':[]},{'available':True,'proposal':{'id':'old','meta_draft_id':'existing'},'source_binding':{'request_id':'x'}});self.assertFalse(any(x['next_action']['type']=='review_exact_operation' for x in result['proposals']))
 def test_cross_campaign_not_compared(self):
  self.report['windows']['1']['adsets'][1]['campaign_id']='different';self.assertEqual(len(self.run_engine()['proposals']),1)
 def test_zero_results_not_compared_as_cpa(self):
  self.report['windows']['1']['adsets'][1]['purchases']=0;self.assertEqual(len(self.run_engine()['proposals']),1)
 def test_input_unchanged(self):
  before=deepcopy(self.report);self.run_engine();self.assertEqual(before,self.report)
if __name__=='__main__':unittest.main()
