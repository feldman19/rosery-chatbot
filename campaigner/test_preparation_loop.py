from pathlib import Path
import tempfile,unittest,json,hashlib,threading
import test_launch_proposal as fixtures
from unittest.mock import patch
from preparation_loop import PreparationLoop,REVIEWER,encode

class PreparationLoopTests(unittest.TestCase):
 def fixture(self,d,gaps=None,duplicates=False):
  store,old,draft,path=fixtures.ProposalTests().fixture(d)
  loop=PreparationLoop(d,b'x'*32);request=loop.intake.create({'goal':old['goal'],'drive_urls':old['drive_urls']})
  draft['gaps']=gaps or []
  if duplicates:draft['ads'][0]['primary_text_options']=['one','one','two']
  store.save(draft,request);return loop,store,request,path
 def reply(self,loop,verdict='pass',findings=None):
  job=loop.report()['job'];value={'review_id':job['review_id'],'bundle_hash':job['bundle_hash'],'reviewer_id':REVIEWER,'job_id':job['id'],'binding':{k:job[k] for k in ('request_id','request_revision','request_hash','artifact_hash','source_hash')},'verdict':verdict,'findings':findings or [],'limitations':['No live or visual account review performed.']}
  path=loop.bundles/(job['review_id']+'.reply.json');path.write_text(encode(value),encoding='utf-8');loop.attest_review(job['review_id'],hashlib.sha256(path.read_bytes()).hexdigest(),REVIEWER,'test-host-observed-message');return path,value
 def test_expected_binding_rejects_changed_evidence_before_job_creation(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,*_=self.fixture(d);_,_,binding=loop.context();binding['artifact_hash']='0'*64
   with self.assertRaises(ValueError):loop.start(expected_binding=binding)
   self.assertIsNone(loop.report()['job'])
 def test_advertised_handoff_template_constructs_accepted_reply(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,*_=self.fixture(d);binding=loop.report()['current_binding'];self.assertEqual(set(binding),{'request_id','request_revision','request_hash','artifact_hash','source_hash'})
   loop.start(expected_binding=binding);loop.tick();report=loop.report();job=report['job'];handoff=report['handoff']
   bundle=json.loads((loop.bundles/handoff['request_file']).read_text(encoding='utf-8'))
   self.assertIn('bundle_hash',bundle['reply_contract']);self.assertEqual(bundle['reply_contract']['binding'],binding)
   reply=dict(handoff['reply_template']);reply.update(verdict='pass',limitations=['Source/code review only; no live business or visual QA.'])
   self.assertEqual(reply['bundle_hash'],hashlib.sha256((loop.bundles/handoff['request_file']).read_bytes()).hexdigest())
   target=loop.bundles/(job['review_id']+'.reply.json');target.write_text(encode(reply),encoding='utf-8')
   loop.attest_review(job['review_id'],hashlib.sha256(target.read_bytes()).hexdigest(),REVIEWER,'test-contract-actual-host-receipt');loop.tick()
   self.assertEqual(loop.report()['job']['state'],'reviewed')
 def test_real_consumer_durable_idempotent_and_bound_review(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,store,req,path=self.fixture(d,['measurement missing']);first=loop.start();self.assertEqual(loop.start()['id'],first['id'])
   loop.tick();job=loop.report()['job'];self.assertEqual(job['state'],'awaiting_review');self.assertTrue((loop.bundles/(job['review_id']+'.request.json')).is_file())
   recovered=PreparationLoop(d,b'x'*32);self.reply(recovered);recovered.tick()
   result=recovered.report()['job'];self.assertEqual(result['state'],'reviewed_with_gaps');self.assertEqual(result['metrics']['review_rounds'],1)
   recovered.tick();self.assertEqual(recovered.report()['job']['metrics']['review_rounds'],1)
   self.assertFalse(store.report(req)['proposal']['activation_available'])
 def test_repair_changes_hash_and_requires_new_review(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,store,req,path=self.fixture(d,duplicates=True);loop.start();loop.tick();old=loop.report()['job']
   self.reply(loop,'revise',[{'id':'duplicate','severity':'P2','detail':'Duplicate copy options','repair':'deduplicate_copy_options'}]);loop.tick();loop.tick()
   new=loop.report()['job'];self.assertNotEqual(old['artifact_hash'],new['artifact_hash']);self.assertNotEqual(old['review_id'],new['review_id']);self.assertEqual(new['state'],'awaiting_review');self.assertEqual(new['metrics']['repair_rounds'],1)
   self.assertEqual(store.report(req)['proposal']['ads'][0]['primary_text_options'],['one','two'])
   self.reply(loop);loop.tick();self.assertEqual(loop.report()['job']['state'],'reviewed');self.assertEqual(loop.report()['job']['metrics']['review_rounds'],2)
 def test_stale_and_foreign_replies_never_approve(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,store,req,path=self.fixture(d);loop.start();loop.tick();reply,value=self.reply(loop);value['binding']['artifact_hash']='0'*64;reply.write_text(encode(value),encoding='utf-8');loop.tick()
   self.assertNotEqual(loop.report()['job']['state'],'reviewed')
   path.write_bytes(b'changed');self.assertEqual(loop.report()['job']['state'],'stale')
 def test_uncertain_dispatch_not_sent_twice_or_marked_sent(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,*_=self.fixture(d);loop.start();loop.tick();identity=loop.report()['job']['review_id'];loop.dispatch(identity)
   self.assertEqual(loop.report()['job']['state'],'awaiting_review')
   with self.assertRaises(ValueError):loop.dispatch(identity)
   loop.confirm_dispatch(identity);self.assertEqual(loop.report()['job']['state'],'reviewing')
 def test_bound_deadline_and_provenance_not_auto_applied(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,*_=self.fixture(d);loop.start();job=loop.report()['job'];loop.clock=lambda:job['expires_at']+1;loop.tick();self.assertEqual(loop.report()['job']['blocked_reason'],'bounded_run_deadline_exceeded')
   a=loop.correction('No offer','user:launch','proposal_offer');b=loop.correction('No offer','user:launch','proposal_offer');self.assertEqual(a,b)
   with loop.db() as db:rows=db.execute('SELECT payload FROM corrections').fetchall()
   self.assertEqual(len(rows),1);self.assertFalse(json.loads(rows[0][0])['automatically_applied'])
 def test_semantic_revision_is_honest_blocker(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,*_=self.fixture(d);loop.start();loop.tick();self.reply(loop,'revise',[{'id':'offer','severity':'P2','detail':'Need evidence for the offer'}]);loop.tick()
   self.assertEqual(loop.report()['job']['blocked_reason'],'semantic_revision_requires_author_no_connected_model')
 def test_reply_file_without_observed_transport_receipt_is_not_review(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,*_=self.fixture(d);loop.start();loop.tick();path,value=self.reply(loop)
   (loop.bundles/(value['review_id']+'.receipt.json')).unlink();loop.tick()
   self.assertEqual(loop.report()['job']['state'],'awaiting_review');self.assertEqual(loop.report()['job']['metrics']['review_rounds'],0)
   with self.assertRaises(ValueError):loop.attest_review(value['review_id'],hashlib.sha256(path.read_bytes()).hexdigest(),'forged-thread','fake-message-id')
 def test_interrupted_mechanical_repair_recovers_before_or_after_file_write(self):
  for write_first in (False,True):
   with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
    loop,*_=self.fixture(d,duplicates=True);loop.start();loop.tick();self.reply(loop,'revise',[{'id':'duplicate','severity':'P2','detail':'Duplicate options','repair':'deduplicate_copy_options'}])
    save=loop.store.save
    def interrupted(*args,**kwargs):
     if write_first:save(*args,**kwargs)
     raise KeyboardInterrupt()
    with patch.object(loop.store,'save',side_effect=interrupted):
     with self.assertRaises(KeyboardInterrupt):loop.tick()
    recovered=PreparationLoop(d,b'x'*32);recovered.clock=lambda:loop.clock()+181;recovered.tick();recovered.tick()
    job=recovered.report()['job'];self.assertEqual(job['state'],'awaiting_review');self.assertEqual(job['metrics']['repair_rounds'],1);self.assertEqual(job['metrics']['review_rounds'],1)
 def test_external_wait_does_not_consume_active_execution_budget(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,*_=self.fixture(d);loop.start();loop.tick();job=loop.report()['job'];loop.clock=lambda:job['created_at']+3600;self.reply(loop);loop.tick()
   self.assertEqual(loop.report()['job']['state'],'reviewed')
 def test_two_consumers_claim_review_only_once(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,*_=self.fixture(d);loop.start();loop.tick();self.reply(loop);other=PreparationLoop(d,b'x'*32)
   entered=threading.Event();release=threading.Event();review=loop._review;errors=[]
   def paused(*args):entered.set();release.wait(3);return review(*args)
   def worker():
    try:loop.tick()
    except Exception as error:errors.append(error)
   with patch.object(loop,'_review',side_effect=paused):
    thread=threading.Thread(target=worker);thread.start();self.assertTrue(entered.wait(3));other.tick();release.set();thread.join(5)
   self.assertFalse(errors);self.assertEqual(loop.report()['job']['metrics']['review_rounds'],1)
 def test_mechanical_repair_cannot_overwrite_new_author_version(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   loop,store,req,path=self.fixture(d,duplicates=True);loop.start();loop.tick();self.reply(loop,'revise',[{'id':'d','severity':'P2','detail':'Duplicate options','repair':'deduplicate_copy_options'}]);save=loop.store.save
   def competing(proposal,request,**kwargs):
    author=store.report(req)['proposal'];author['ads'][0]['description']='New author version';save(author,request)
    return save(proposal,request,**kwargs)
   with patch.object(loop.store,'save',side_effect=competing):
    with self.assertRaises(ValueError):loop.tick()
   recovered=PreparationLoop(d,b'x'*32);recovered.tick()
   self.assertEqual(store.report(req)['proposal']['ads'][0]['description'],'New author version');self.assertEqual(recovered.report()['job']['state'],'stale')
