import json,unittest
from urllib.parse import parse_qs
from meta_batch import *

def item(identity='one',edge='adsets',deps=None,**params):
 p={'status':'PAUSED','campaign_id':PARENT,'name':'Synthetic'} if edge=='adsets' else {'name':'Synthetic','object_story_spec':{'page_id':'170018166205299','link_data':{'message':'Copy'}}}
 p.update(params);return {'id':identity,'method':'POST','relative_url':'act_'+ACCOUNT+'/'+edge,'params':p,'depends_on':deps or []}
def success(identity='700001'):return {'code':200,'body':json.dumps({'id':identity})}

class BatchTests(unittest.TestCase):
 def test_split_51_independent_into_50_and_1(self):
  p=plan_batches([item('i'+str(i)) for i in range(51)]);self.assertEqual([len(x) for x in p['waves'][0]],[50,1])
 def test_dependency_waves_and_cycle(self):
  p=plan_batches([item('parent'),item('child',deps=['parent'])]);self.assertEqual(p['waves'],[[['parent']],[['child']]])
  with self.assertRaises(BatchRejected):plan_batches([item('a',deps=['b']),item('b',deps=['a'])])
 def test_external_unowned_and_active_rejected(self):
  for path in ('https://evil.example/ads','/act_'+ACCOUNT+'/ads','act_12345/adsets','123456','act_'+ACCOUNT+'/adimages'):
   row=item();row['relative_url']=path
   with self.subTest(path=path),self.assertRaises(BatchRejected):plan_batches([row])
  with self.assertRaises(BatchRejected):plan_batches([item(status='ACTIVE')])
 def test_missing_paused_and_unknown_fields_rejected(self):
  row=item();row['params'].pop('status')
  with self.assertRaises(BatchRejected):plan_batches([row])
  with self.assertRaises(BatchRejected):plan_batches([item(access_token='never')])
 def test_owned_read_and_pause(self):
  row={'id':'read','method':'GET','relative_url':'700001','params':{'fields':'id,status'},'depends_on':[]}
  self.assertEqual(plan_batches([row],owned_ids=['700001'])['max_batch'],50)
  row.update(method='POST',params={'status':'PAUSED'});plan_batches([row],owned_ids=['700001'])
 def test_partial_error_preserved_and_no_raw_error_output(self):
  values=parse_batch_results([item('a'),item('b')],[success(),{'code':400,'body':json.dumps({'error':{'message':'private secret'}})}])
  self.assertEqual([x['state'] for x in values],['accepted','rejected']);self.assertNotIn('private',str(values));self.assertTrue(values[0]['readback_required'])
 def test_null_5xx_and_malformed_are_unknown(self):
  for response in ([None],[{'code':503,'body':'{}'}],[{'code':200,'body':'not JSON'}],[]):
   self.assertEqual(parse_batch_results([item()],response)[0]['state'],'unknown')
 def test_no_retry_after_timeout_or_restore(self):
  p=plan_batches([item()]);session=BatchSession(p);saved=[];calls=[]
  def send(batch):calls.append(batch);raise TimeoutError('private')
  self.assertEqual(session.run_next(send,saved.append)['state'],'unknown');session.run_next(send,saved.append)
  restored=BatchSession(p,saved[-1]);restored.run_next(send,saved.append);self.assertEqual(len(calls),1)
 def test_checkpoint_before_send_and_bodies_encoded(self):
  events=[];s=BatchSession(plan_batches([item(name='a & b')]))
  def send(batch):events.append('send');self.assertEqual(events[0],'checkpoint');self.assertEqual(parse_qs(batch[0]['body'])['name'],['a & b']);return [success()]
  s.run_next(send,lambda state:events.append('checkpoint'));self.assertEqual(events,['checkpoint','send','checkpoint'])
 def test_rejected_dependency_blocked_independent_continues(self):
  s=BatchSession(plan_batches([item('a'),item('b',deps=['a']),item('c')]))
  s.run_next(lambda b:[{'code':400,'body':'{"error":{}}'},success('700003')],lambda state:None)
  self.assertEqual(s.run_next(lambda b:self.fail('must not send'),lambda state:None)['state'],'finished');self.assertEqual(s.outcomes['b']['state'],'blocked')
 def test_reference_requires_dependency_and_resolves_receipt(self):
  child=item('b',edge='adcreatives',deps=['a'],object_story_spec={'page_id':{'$ref':'a','field':'id'}})
  s=BatchSession(plan_batches([item('a'),child]));s.run_next(lambda b:[success()],lambda state:None);self.assertEqual(s.run_next(lambda b:self.fail('readback required'),lambda state:None)['state'],'awaiting_readback');s.confirm_readback('a','700001')
  def send(batch):self.assertEqual(json.loads(parse_qs(batch[0]['body'])['object_story_spec'][0])['page_id'],'700001');return [success('700002')]
  s.run_next(send,lambda state:None)
  child['depends_on']=[]
  with self.assertRaises(BatchRejected):plan_batches([item('a'),child])
 def test_duplicate_ids_and_bad_restore_rejected(self):
  with self.assertRaises(BatchRejected):plan_batches([item(),item()])
  with self.assertRaises(BatchRejected):BatchSession(plan_batches([item()]),{'fingerprint':'bad','outcomes':{}})
 def test_transitive_rejections_block_reverse_order(self):
  s=BatchSession(plan_batches([item('c',deps=['b']),item('b',deps=['a']),item('a')]))
  s.run_next(lambda b:[{'code':400,'body':'{"error":{}}'}],lambda state:None)
  self.assertEqual(s.run_next(lambda b:self.fail('no retry'),lambda state:None)['state'],'finished');self.assertEqual(s.outcomes['c']['state'],'blocked')
 def test_response_count_limit_and_readback_mismatch(self):
  with self.assertRaises(BatchRejected):parse_batch_results([item(str(i)) for i in range(51)],[])
  s=BatchSession(plan_batches([item()]));s.run_next(lambda b:[success()],lambda state:None)
  with self.assertRaises(BatchRejected):s.confirm_readback('one','700002')
 def test_session_revalidates_plan_not_just_fingerprint(self):
  plan=plan_batches([item()]);plan['items'][0]['relative_url']='https://evil.example/'
  with self.assertRaises(BatchRejected):BatchSession(plan)
 def test_success_does_not_resubmit(self):
  s=BatchSession(plan_batches([item()]));s.run_next(lambda b:[success()],lambda state:None)
  self.assertEqual(s.run_next(lambda b:self.fail('duplicate'),lambda state:None)['state'],'finished')

if __name__=='__main__':unittest.main()
