import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from meta_execution import MetaExecutor,FixedMetaTransport,CAMPAIGN,ACCOUNT

TOKEN='synthetic-test-only-token'

def wire(budget='6000',status='ACTIVE'):return {'id':CAMPAIGN,'account_id':ACCOUNT,'daily_budget':budget,'lifetime_budget':'0','status':status}

def approved(operation_id="op"):
 before={'status':'ACTIVE','budget_minor':6000,'currency':'ILS','budget_type':'daily'}
 return {'approval_instance':'approval-'+operation_id+'-instance-001','approved_operation_id':operation_id,'before':before,'after':{**before,'budget_minor':7000},'preflight':{'state':'ready_for_executor','execution_ready':True,'approval_digest':'a'*64,'request_preview':{'method':'POST','origin':'https://graph.facebook.com','version':'v26.0','entity_id':CAMPAIGN,'body':{'daily_budget':'7000'},'headers_omitted':True}}}

class MetaExecutionTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.directory=Path(self.temp.name)
  self.transport=Mock();self.transport.read.side_effect=[wire(),wire(),wire('7000')];self.transport.write.return_value={'success':True}
  self.verifier=Mock(side_effect=lambda operation_id,*_:approved(operation_id));self.loader=Mock(return_value=TOKEN);self.kill=Mock(return_value=False)
  self.executor=MetaExecutor(self.directory,self.loader,self.verifier,self.kill,self.transport)
 def tearDown(self):self.temp.cleanup()
 def test_default_unconfigured_never_networks(self):
  e=MetaExecutor(self.directory/'default',transport=self.transport);self.assertEqual('blocked',e.execute('op')['state']);self.transport.read.assert_not_called();self.transport.write.assert_not_called()
 def test_success_requires_one_post_and_independent_readback(self):
  r=self.executor.execute('op');self.assertEqual('verified',r['state']);self.assertFalse(r['business_success_verified']);self.assertFalse(r['automatic_retry_allowed'])
  self.transport.write.assert_called_once_with(TOKEN,{'daily_budget':'7000'});self.assertEqual(3,self.transport.read.call_count);self.assertNotIn(TOKEN,str(r))
 def test_idempotent_replay_no_network_or_post_after_restart(self):
  first=self.executor.execute('op');restarted=MetaExecutor(self.directory,self.loader,self.verifier,self.kill,self.transport)
  self.assertEqual(first,restarted.execute('op'));self.assertEqual(1,self.transport.write.call_count);self.assertEqual(3,self.transport.read.call_count)
 def test_ambiguous_timeout_no_retry_even_if_target_later_matches(self):
  self.transport.write.side_effect=TimeoutError(TOKEN);r=self.executor.execute('op');self.assertEqual('unknown',r['state'])
  self.transport.read.side_effect=None;self.transport.read.return_value=wire('7000')
  self.assertEqual('unknown',self.executor.reconcile('op')['state']);self.assertEqual('unknown',self.executor.execute('op')['state']);self.assertEqual(1,self.transport.write.call_count)
 def test_stored_success_can_reconcile_readback_failure_later(self):
  self.transport.read.side_effect=[wire(),wire(),TimeoutError(TOKEN)];self.assertEqual('unknown',self.executor.execute('op')['state'])
  self.transport.read.side_effect=None;self.transport.read.return_value=wire('7000')
  self.assertEqual('verified',self.executor.reconcile('op')['state']);self.assertEqual(1,self.transport.write.call_count)
 def test_state_drift_prevents_post(self):
  self.transport.read.side_effect=None;self.transport.read.return_value=wire('6500');self.assertEqual('blocked',self.executor.execute('op')['state']);self.transport.write.assert_not_called()
 def test_scope_not_ready_prevents_post(self):
  result=approved();result['preflight']['execution_ready']=False;self.verifier.side_effect=None;self.verifier.return_value=result
  self.assertEqual('blocked',self.executor.execute('op')['state']);self.transport.write.assert_not_called()
 def test_kill_after_reservation_prevents_post(self):
  self.kill.side_effect=[False,True];self.assertEqual('not_applied',self.executor.execute('op')['state']);self.transport.write.assert_not_called()
 def test_changed_host_authorization_prevents_post(self):
  changed=approved();changed['preflight']['approval_digest']='b'*64;self.verifier.side_effect=[approved(),changed]
  self.assertEqual('not_applied',self.executor.execute('op')['state']);self.transport.write.assert_not_called()
 def test_arbitrary_destination_or_activation_is_rejected(self):
  for change in ({'entity_id':'999'},{'origin':'https://evil.invalid'},{'body':{'status':'ACTIVE'}},{'body':{'daily_budget':'0'}},{'body':{'daily_budget':True}},{'body':{'status':'PAUSED','daily_budget':'7000'}}):
   value=approved();value['preflight']['request_preview'].update(change);self.verifier.side_effect=lambda *_,v=value:v;self.transport.read.side_effect=None;self.transport.read.return_value=wire()
   self.assertEqual('blocked',self.executor.execute('op')['state'])
  self.transport.write.assert_not_called()
 def test_pause_only_is_supported(self):
  value=approved('pause');value['after']={**value['before'],'status':'PAUSED'};value['preflight']['request_preview']['body']={'status':'PAUSED'}
  self.verifier.side_effect=lambda *_:value;self.transport.read.side_effect=[wire(),wire(),wire(status='PAUSED')]
  self.assertEqual('verified',self.executor.execute('pause')['state']);self.transport.write.assert_called_once_with(TOKEN,{'status':'PAUSED'})
 def test_nonaffirmative_response_never_verified(self):
  self.transport.write.return_value={'error':{'message':TOKEN}};self.assertEqual('unknown',self.executor.execute('op')['state']);self.assertEqual(2,self.transport.read.call_count)
 def test_wrong_account_or_readback_mismatch_unknown(self):
  bad=wire('7000');bad['account_id']='other';self.transport.read.side_effect=[wire(),wire(),bad]
  self.assertEqual('unknown',self.executor.execute('op')['state'])
 def test_reservation_is_durable_before_write(self):
  def write(*_):
   restarted=MetaExecutor(self.directory);r=restarted.execute('op');self.assertEqual('unknown',r['state']);self.assertTrue(r['payload_digest']);return {'success':True}
  self.transport.write.side_effect=write;self.assertEqual('verified',self.executor.execute('op')['state'])
 def test_transport_fixed_url_no_redirect_bounded_response(self):
  response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{"success":true}'
  opener=Mock();opener.open.return_value=response
  with patch('meta_execution.urllib.request.build_opener',return_value=opener) as build:
   self.assertEqual({'success':True},FixedMetaTransport().write(TOKEN,{'daily_budget':'7000'}))
   request=opener.open.call_args.args[0];self.assertEqual('POST',request.get_method());self.assertEqual('https://graph.facebook.com/v26.0/'+CAMPAIGN,request.full_url);self.assertEqual(b'daily_budget=7000',request.data)
   self.assertIsNone(build.call_args.args[0].redirect_request(None,None,302,'',{},'https://evil.invalid'));response.read.assert_called_with(1048577)
   response.read.return_value=b'x'*1048577
   with self.assertRaisesRegex(ValueError,'Meta transport result unavailable'):FixedMetaTransport().read(TOKEN)
 def test_transport_rejects_activation_before_network(self):
  with patch('meta_execution.urllib.request.build_opener') as build:
   with self.assertRaises(ValueError):FixedMetaTransport().write(TOKEN,{'status':'ACTIVE'})
   build.assert_not_called()
 def test_concurrent_replay_never_sends_second_post(self):
  entered=threading.Event();release=threading.Event();written=threading.Event();results=[]
  self.transport.read.side_effect=lambda *_:wire('7000' if written.is_set() else '6000')
  def write(*_):
   entered.set()
   if not release.wait(3):raise TimeoutError('test release timeout')
   written.set();return {'success':True}
  self.transport.write.side_effect=write
  worker=threading.Thread(target=lambda:results.append(self.executor.execute('concurrent')))
  worker.start()
  try:
   self.assertTrue(entered.wait(2));replay=self.executor.execute('concurrent');self.assertEqual('unknown',replay['state']);self.assertEqual(1,self.transport.write.call_count)
  finally:release.set();worker.join(3)
  self.assertFalse(worker.is_alive());self.assertEqual('verified',results[0]['state']);self.assertEqual(1,self.transport.write.call_count)
 def test_network_error_strings_are_not_exposed(self):
  response=Mock();response.__enter__=Mock(side_effect=RuntimeError(TOKEN+' https://secret.invalid'));response.__exit__=Mock(return_value=False)
  opener=Mock();opener.open.return_value=response
  with patch('meta_execution.urllib.request.build_opener',return_value=opener):
   with self.assertRaises(ValueError) as error:FixedMetaTransport().read(TOKEN)
   self.assertEqual('Meta transport result unavailable',str(error.exception));self.assertNotIn(TOKEN,str(error.exception))
 def test_second_fresh_read_detects_state_drift_before_any_post(self):
  self.transport.read.side_effect=[wire(),wire('6500')]
  r=self.executor.execute('drift');self.assertEqual('not_applied',r['state']);self.transport.write.assert_not_called();self.assertEqual(1,self.verifier.call_count)
 def test_concurrent_different_id_blocked_by_campaign_reservation(self):
  entered=threading.Event();release=threading.Event();written=threading.Event();results=[]
  self.transport.read.side_effect=lambda *_:wire('7000' if written.is_set() else '6000')
  def write(*_):
   entered.set()
   if not release.wait(3):raise TimeoutError('test release timeout')
   written.set();return {'success':True}
  self.transport.write.side_effect=write;worker=threading.Thread(target=lambda:results.append(self.executor.execute('first')))
  worker.start()
  try:
   self.assertTrue(entered.wait(2));r=self.executor.execute('different');self.assertEqual('blocked',r['state']);self.assertEqual(1,self.transport.write.call_count)
  finally:release.set();worker.join(3)
  self.assertEqual('verified',results[0]['state']);self.assertFalse(worker.is_alive())
 def test_unknown_prior_operation_blocks_other_id_after_restart(self):
  self.transport.write.side_effect=TimeoutError('ambiguous');self.assertEqual('unknown',self.executor.execute('first')['state'])
  self.transport.read.side_effect=None;self.transport.read.return_value=wire()
  restarted=MetaExecutor(self.directory,self.loader,self.verifier,self.kill,self.transport)
  self.assertEqual('blocked',restarted.execute('different')['state']);self.assertEqual(1,self.transport.write.call_count)
 def test_stored_success_reconciliation_closes_reservation_for_new_operation(self):
  self.transport.read.side_effect=[wire(),wire(),TimeoutError('readback unavailable')];self.assertEqual('unknown',self.executor.execute('first')['state'])
  self.transport.read.side_effect=None;self.transport.read.return_value=wire()
  self.assertEqual('blocked',self.executor.execute('different')['state'])
  self.transport.read.return_value=wire('7000');self.assertEqual('verified',self.executor.reconcile('first')['state'])
  next_approval=approved('different');next_approval['before']['budget_minor']=7000;next_approval['after']['budget_minor']=8000;next_approval['preflight']['request_preview']['body']={'daily_budget':'8000'}
  self.verifier.side_effect=lambda *_:next_approval;self.transport.read.side_effect=[wire('7000'),wire('7000'),wire('8000')]
  self.assertEqual('verified',self.executor.execute('different')['state']);self.assertEqual(2,self.transport.write.call_count)
 def test_corrupt_payload_hash_fail_closed_no_retry(self):
  import sqlite3
  self.executor.execute('first')
  with self.executor.db() as db:db.execute("UPDATE operations SET payload='{}' WHERE id='first'")
  with self.assertRaisesRegex(ValueError,'integrity mismatch'):self.executor.execute('first')
  self.assertEqual(1,self.transport.write.call_count)
 def test_mutated_shared_host_result_cannot_change_reserved_body(self):
  shared=approved('shared');calls=[]
  def verifier(*_):
   calls.append(1)
   if len(calls)>1:shared['preflight']['request_preview']['body']['daily_budget']='8000';shared['after']['budget_minor']=8000
   return shared
  self.verifier.side_effect=verifier;self.transport.read.side_effect=[wire(),wire()]
  self.assertEqual('not_applied',self.executor.execute('shared')['state']);self.transport.write.assert_not_called()
 def test_approval_instance_cannot_replay_after_verified_external_revert(self):
  self.assertEqual('verified',self.executor.execute('op')['state'])
  reused=approved('opB');reused['approval_instance']=approved()['approval_instance'];self.verifier.side_effect=lambda *_:reused
  self.transport.read.side_effect=None;self.transport.read.return_value=wire()
  r=self.executor.execute('opB');self.assertEqual('blocked',r['state']);self.assertIn('approval instance',r['reason']);self.assertEqual(1,self.transport.write.call_count)
 def test_approval_consumption_persists_across_restart(self):
  self.executor.execute('op');reused=approved('opB');reused['approval_instance']=approved()['approval_instance'];self.verifier.side_effect=lambda *_:reused
  self.transport.read.side_effect=None;self.transport.read.return_value=wire()
  restarted=MetaExecutor(self.directory,self.loader,self.verifier,self.kill,self.transport)
  self.assertEqual('blocked',restarted.execute('opB')['state']);self.assertEqual(1,self.transport.write.call_count)
 def test_new_approval_instance_allows_same_diff_after_external_revert(self):
  self.executor.execute('op');self.transport.read.side_effect=[wire(),wire(),wire('7000')]
  r=self.executor.execute('opB');self.assertEqual('verified',r['state']);self.assertEqual(2,self.transport.write.call_count)
 def test_approval_must_bind_exact_operation_id(self):
  self.verifier.side_effect=lambda *_:approved('other-operation')
  self.assertEqual('blocked',self.executor.execute('op')['state']);self.transport.write.assert_not_called()
 def test_missing_or_short_approval_instance_blocks(self):
  for instance in (None,'short'):
   value=approved();value['approval_instance']=instance;self.verifier.side_effect=lambda *_,v=value:v;self.transport.read.side_effect=None;self.transport.read.return_value=wire()
   self.assertEqual('blocked',self.executor.execute('op')['state'])
  self.transport.write.assert_not_called()
if __name__=='__main__':unittest.main()

