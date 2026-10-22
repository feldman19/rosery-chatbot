import copy
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from execution_preflight import prepare,reconcile,ACCOUNT
from strategy import sample_case,evaluate

class ExecutionPreflightTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,10,17,12,tzinfo=timezone.utc);case=sample_case(self.now)
  case['profile'].update(tenant_id='synthetic-tenant',account_id=ACCOUNT,entity_id='123456789')
  case['evidence']['entity'].update({k:case['profile'][k] for k in ('tenant_id','account_id','entity_id')})
  self.candidate=evaluate(**case)
  self.binding={**case['profile'],'platform':'meta','level':'campaign','budget_type':'daily'}
  self.current={**self.candidate['entity'],**self.candidate['before'],'verified':True,'parent_active':True,'checked_at':self.now.isoformat(),'valid_until':(self.now+timedelta(minutes=10)).isoformat()}
  self.approval={**self.candidate['entity'],'verified':True,'digest':prepare(self.candidate,self.binding,self.current,now=self.now)['approval_digest'],'issued_at':self.now.isoformat(),'expires_at':(self.now+timedelta(minutes=5)).isoformat()}
 def prepare(self,**kw):return prepare(self.candidate,self.binding,self.current,approval=kw.get('approval',self.approval),scopes=kw.get('scopes',['ads_management']),now=self.now)
 def test_preview_no_network_or_dispatch(self):
  r=self.prepare();self.assertEqual('ready_for_executor',r['state']);self.assertTrue(r['execution_ready']);self.assertFalse(r['dispatch_available']);self.assertFalse(r['provider_write_performed'])
  self.assertEqual({'daily_budget':'7000'},r['request_preview']['body']);self.assertEqual('https://graph.facebook.com',r['request_preview']['origin'])
 def test_existing_read_scope_cannot_execute(self):
  r=self.prepare(scopes=['ads_read']);self.assertEqual('prepared',r['state']);self.assertFalse(r['execution_ready'])
 def test_missing_approval_only_preview(self):self.assertFalse(self.prepare(approval=None)['execution_ready'])
 def test_exact_diff_approval(self):
  self.candidate['after']['budget_minor']=7500;self.assertFalse(self.prepare()['execution_ready'])
 def test_evidence_change_invalidates_approval(self):
  self.candidate['evidence_refs']=['sha256:'+'b'*64];self.assertFalse(self.prepare()['execution_ready'])
 def test_current_drift_no_request_preview(self):
  self.current['budget_minor']=8000;self.assertIsNone(self.prepare()['request_preview'])
 def test_tenant_entity_account_mismatch_blocks(self):
  for key in ('tenant_id','account_id','entity_id'):
   saved=self.current[key];self.current[key]='other';self.assertEqual('blocked',self.prepare()['state']);self.current[key]=saved
 def test_arbitrary_endpoint_and_level_blocks(self):
  self.binding['entity_id']='https://evil.invalid';self.assertEqual('blocked',self.prepare()['state'])
  self.binding['entity_id']='123456789';self.binding['level']='ad';self.assertEqual('blocked',self.prepare()['state'])
 def test_stale_or_parent_inactive_blocks(self):
  self.current['valid_until']=(self.now-timedelta(seconds=1)).isoformat();self.assertEqual('blocked',self.prepare()['state'])
  self.current['valid_until']=(self.now+timedelta(minutes=1)).isoformat();self.current['parent_active']=False;self.assertEqual('blocked',self.prepare()['state'])
 def test_expired_approval_no_ready(self):
  self.approval['expires_at']=(self.now-timedelta(seconds=1)).isoformat();self.assertFalse(self.prepare()['execution_ready'])
 def test_no_reactivation_or_additional_pause_diff(self):
  self.candidate['action']='pause';self.candidate['after']['status']='PAUSED';self.assertEqual('blocked',self.prepare()['state'])
 def test_lifetime_currency_or_boolean_budget_block(self):
  for key,value in [('budget_type','lifetime'),('currency','USD'),('budget_minor',True)]:
   saved=self.candidate['after'][key];self.candidate['after'][key]=value;self.assertEqual('blocked',self.prepare()['state']);self.candidate['after'][key]=saved
 def test_target_state_without_operation_proof_unknown(self):
  rb={**self.current,**self.candidate['after']};r=reconcile(self.candidate,self.binding,rb,{},self.now.isoformat())
  self.assertEqual('unknown',r['outcome']);self.assertTrue(r['observed_target_state']);self.assertFalse(r['automatic_retry_allowed'])
 def test_matching_receipt_and_readback_operation_verified(self):
  rb={**self.current,**self.candidate['after'],'operation_id':'synthetic-op'};receipt={'verified':True,'operation_id':'synthetic-op'}
  self.assertEqual('verified',reconcile(self.candidate,self.binding,rb,receipt,self.now.isoformat())['outcome'])
 def test_definite_reject_with_unchanged_state(self):
  r=reconcile(self.candidate,self.binding,self.current,{'definitively_rejected':True},self.now.isoformat())
  self.assertEqual('not_applied',r['outcome']);self.assertFalse(r['automatic_retry_allowed'])
 def test_readback_before_dispatch_or_wrong_entity_unknown(self):
  rb={**self.current,**self.candidate['after'],'operation_id':'synthetic-op'};receipt={'verified':True,'operation_id':'synthetic-op'}
  rb['checked_at']=(self.now-timedelta(seconds=1)).isoformat();self.assertEqual('unknown',reconcile(self.candidate,self.binding,rb,receipt,self.now.isoformat())['outcome'])
 def test_inputs_unchanged(self):
  saved=copy.deepcopy((self.candidate,self.binding,self.current,self.approval));self.prepare();self.assertEqual(saved,(self.candidate,self.binding,self.current,self.approval))
if __name__=='__main__':unittest.main()
