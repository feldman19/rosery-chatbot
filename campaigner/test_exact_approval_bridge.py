"""Offline exact-approval bridge tests; local approval is not multi-user auth."""
import copy
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock
from exact_approval_bridge import ExactApprovalBridge
from meta_execution import ACCOUNT,CAMPAIGN

KEY=b'synthetic-host-signing-key-only-123456789'
NOW=datetime(2026,10,17,12,tzinfo=timezone.utc)

def wire(budget='6000',status='ACTIVE'):return {'id':CAMPAIGN,'account_id':ACCOUNT,'daily_budget':budget,'lifetime_budget':'0','status':status,'effective_status':status}

class ExactApprovalBridgeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.directory=Path(self.temp.name);self.now=NOW
  self.transport=Mock();self.transport.read.return_value=wire();self.transport.write.return_value={'success':True}
  self.loader=Mock(return_value='synthetic-secret-token');self.bridge=ExactApprovalBridge(self.directory,KEY,self.loader,self.transport,lambda:self.now)
 def tearDown(self):self.temp.cleanup()
 def preview(self):return self.bridge.preview_action('set_budget',7000)
 def approved(self):
  p=self.preview();self.bridge.approve(p['operation_id'],p['digest']);return p
 def test_preview_is_exact_fresh_manual_not_authority(self):
  p=self.preview();self.assertEqual('prepared',p['status']);self.assertEqual(6000,p['before']['budget_minor']);self.assertEqual(7000,p['after']['budget_minor'])
  self.assertTrue(p['manual_intent']);self.assertFalse(p['is_recommendation']);self.assertFalse(p['execution_ready']);self.transport.write.assert_not_called()
  self.assertNotIn('synthetic-secret-token',json.dumps(p))
 def test_default_kill_and_unapproved_execute_never_posts(self):
  p=self.preview();self.assertFalse(self.bridge.status()['default_action_authorized']);self.assertEqual('blocked',self.bridge.execute(p['operation_id'])['state']);self.transport.write.assert_not_called()
 def test_approve_enables_only_exact_operation_but_does_not_execute(self):
  p=self.preview();r=self.bridge.approve(p['operation_id'],p['digest']);self.assertEqual('approved',r['approval_status']);self.assertTrue(r['execution_ready']);self.transport.write.assert_not_called()
 def test_execute_success_exact_approved_and_released(self):
  p=self.approved();self.transport.read.side_effect=[wire(),wire(),wire('7000')]
  self.assertEqual('verified',self.bridge.execute(p['operation_id'])['state']);self.transport.write.assert_called_once_with('synthetic-secret-token',{'daily_budget':'7000'})
  public=self.bridge.get(p['operation_id']);self.assertEqual('verified',public['operation_state']);self.assertTrue(public['approval_consumed']);self.assertFalse(public['execution_ready']);self.assertTrue(public['provider_write_performed'])
 def test_expired_proposal_cannot_approve_or_execute(self):
  p=self.preview();self.now+=timedelta(minutes=11)
  with self.assertRaisesRegex(ValueError,'expired'):self.bridge.approve(p['operation_id'],p['digest'])
  self.assertEqual('blocked',self.bridge.execute(p['operation_id'])['state']);self.transport.write.assert_not_called()
 def test_digest_tamper_not_approved(self):
  p=self.preview()
  with self.assertRaisesRegex(ValueError,'digest mismatch'):self.bridge.approve(p['operation_id'],'b'*64)
  self.assertFalse(self.bridge.get(p['operation_id'])['exact_action_approved'])
 def test_signed_proposal_tampering_rejected(self):
  p=self.preview()
  with self.bridge.db() as db:
   row=db.execute('SELECT payload FROM envelopes WHERE id=?',(p['operation_id'],)).fetchone();payload=json.loads(row['payload']);payload['candidate']['after']['budget_minor']=99999
   db.execute('UPDATE envelopes SET payload=? WHERE id=?',(json.dumps(payload),p['operation_id']))
  with self.assertRaisesRegex(ValueError,'integrity failed'):self.bridge.approve(p['operation_id'],p['digest'])
  self.assertEqual('blocked',self.bridge.execute(p['operation_id'])['state']);self.transport.write.assert_not_called()
 def test_stop_revokes_approved_operation(self):
  p=self.approved();self.bridge.stop()
  self.assertEqual('revoked',self.bridge.get(p['operation_id'])['approval_status']);self.assertEqual('blocked',self.bridge.execute(p['operation_id'])['state']);self.transport.write.assert_not_called()
 def test_restart_defaults_killed_and_revokes_prior_approval(self):
  p=self.approved();restarted=ExactApprovalBridge(self.directory,KEY,self.loader,self.transport,lambda:self.now)
  self.assertFalse(restarted.status()['default_action_authorized']);self.assertEqual('revoked',restarted.get(p['operation_id'])['approval_status'])
  self.assertEqual('blocked',restarted.execute(p['operation_id'])['state']);self.transport.write.assert_not_called()
 def test_idempotent_verified_replay_no_second_post(self):
  p=self.approved();self.transport.read.side_effect=[wire(),wire(),wire('7000')];first=self.bridge.execute(p['operation_id']);self.assertEqual(first,self.bridge.execute(p['operation_id']));self.assertEqual(1,self.transport.write.call_count)
 def test_timeout_unknown_disables_execute_and_never_claims_no_write(self):
  p=self.approved();self.transport.write.side_effect=TimeoutError('synthetic-secret-token');self.assertEqual('unknown',self.bridge.execute(p['operation_id'])['state'])
  state=self.bridge.get(p['operation_id']);self.assertEqual('unknown',state['operation_state']);self.assertIsNone(state['provider_write_performed']);self.assertFalse(state['execution_ready'])
  self.bridge.execute(p['operation_id']);self.assertEqual(1,self.transport.write.call_count)
 def test_independent_current_state_drift_blocks(self):
  p=self.approved();self.transport.read.return_value=wire('6500');self.assertEqual('blocked',self.bridge.execute(p['operation_id'])['state']);self.transport.write.assert_not_called()
 def test_no_status_network_poll_and_no_secret_output(self):
  self.preview();reads=self.transport.read.call_count;loads=self.loader.call_count
  r=self.bridge.status();self.assertEqual(reads,self.transport.read.call_count);self.assertEqual(loads,self.loader.call_count);self.assertNotIn('synthetic-secret-token',json.dumps(r))
 def test_only_supported_explicit_actions(self):
  for action,budget in [('activate',None),('set_budget',True),('set_budget',0),('pause',7000)]:
   with self.assertRaises(ValueError):self.bridge.preview_action(action,budget)
  self.transport.read.assert_not_called()
 def test_key_required(self):
  with self.assertRaises(ValueError):ExactApprovalBridge(self.directory,b'short')
 def test_default_no_credentials_fails_closed(self):
  bridge=ExactApprovalBridge(self.directory/'unconfigured',KEY,transport=self.transport,clock=lambda:self.now)
  with self.assertRaisesRegex(ValueError,'unavailable'):bridge.preview_action('pause')
  self.transport.write.assert_not_called()
 def test_approve_one_operation_does_not_authorize_other_preview(self):
  first=self.preview();second=self.preview();self.bridge.approve(first['operation_id'],first['digest'])
  self.assertTrue(self.bridge.get(first['operation_id'])['execution_ready']);self.assertFalse(self.bridge.get(second['operation_id'])['execution_ready'])
  self.assertEqual('blocked',self.bridge.execute(second['operation_id'])['state']);self.transport.write.assert_not_called()
 def test_signed_control_tampering_fails_closed(self):
  p=self.approved()
  with self.bridge.db() as db:db.execute("UPDATE envelopes SET payload='{\"generation\":999}' WHERE id='control'")
  self.assertEqual('blocked',self.bridge.execute(p['operation_id'])['state']);self.transport.write.assert_not_called()
 def test_approve_idempotent_does_not_issue_new_nonce(self):
  p=self.approved()
  with self.bridge.db() as db:first=self.bridge._load(db,p['operation_id'])['approval']['instance']
  self.bridge.approve(p['operation_id'],p['digest'])
  with self.bridge.db() as db:second=self.bridge._load(db,p['operation_id'])['approval']['instance']
  self.assertEqual(first,second)
 def test_fresh_explicit_approval_after_stop_only_enables_new_operation(self):
  old=self.approved();self.bridge.stop();new=self.approved()
  self.assertFalse(self.bridge.get(old['operation_id'])['execution_ready']);self.assertTrue(self.bridge.get(new['operation_id'])['execution_ready']);self.transport.write.assert_not_called()
if __name__=='__main__':unittest.main()
