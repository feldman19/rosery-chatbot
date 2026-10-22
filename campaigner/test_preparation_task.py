import tempfile,unittest
from types import SimpleNamespace
from preparation_task import PreparationTask
from work_intake import WorkIntake
from launch_workflow import LaunchWorkflow

class TaskTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.intake=WorkIntake(self.tmp.name);self.workflow=LaunchWorkflow(self.tmp.name)
  self.calls=[];self.available=False;self.job=None
  self.materials=SimpleNamespace(status=lambda req:{'configured':True,'binding_verified':bool(req),'assets':{'complete':bool(req)}},refresh=lambda req:self.calls.append(('read',req)) or True)
  self.proposal=SimpleNamespace(report=lambda req:{'available':self.available})
  self.loop=SimpleNamespace(report=lambda:{'job':self.job},context=lambda:(None,None,{'request_id':self.intake.latest()['id'],'request_revision':self.intake.latest()['revision']}),start=self.start_review,start_worker=lambda:self.calls.append(('worker',)))
  self.service=self.make()
 def make(self):return PreparationTask(self.tmp.name,intake=self.intake,workflow=self.workflow,materials=self.materials,proposal=self.proposal,loop=self.loop)
 def start_review(self,expected_binding=None):
  r=self.intake.latest();self.job={'id':'job','request_id':r['id'],'request_revision':r['revision'],'state':'awaiting_review'};self.calls.append(('review',));return self.job
 def save(self,key='save_key1',**overrides):
  body={'action':'save','idempotency_key':key,'mode':'new','brief':'פתיחת האתר לקולקציה החדשה והמשך מכירות','drive_urls':['https://drive.google.com/drive/folders/1234567890123456'],'expected_request_id':None,'expected_request_revision':None};body.update(overrides);return self.service.act(body)
 def action(self,action,key='action_key1',**overrides):
  r=self.intake.latest();body={'action':action,'idempotency_key':key,'request_id':r['id'],'request_revision':r['revision']};body.update(overrides);return self.service.act(body)
 def test_candidate_media_bound_hash_and_no_raw_paths(self):
  import io,hashlib
  from test_launch_proposal import png
  from pathlib import Path
  self.save();raw=png();sha=hashlib.sha256(raw).hexdigest();identity='image123456789'
  directory=Path(self.tmp.name)/'drive-assets';directory.mkdir();path=directory/(identity+'-'+sha+'.bin');path.write_bytes(raw)
  self.materials.status=lambda req:{'configured':True,'binding_verified':True,'assets':{'complete':True,'assets':[{'id':identity,'sha256':sha,'name':'photo.png','mime_type':'image/png','content_read':True,'format_check':'decoded','dimensions':{'width':1,'height':1}}]}}
  report=self.service.report();self.assertEqual(report['candidates']['total'],1);self.assertNotIn(str(directory),str(report));self.assertEqual(self.service.media(identity+'~'+sha),(raw,'image/png'))
  path.write_bytes(b'bad')
  with self.assertRaises(ValueError):self.service.media(identity+'~'+sha)
 def test_malformed_candidate_rows_not_exposed(self):
  self.save();self.materials.status=lambda req:{'configured':True,'binding_verified':True,'assets':{'complete':False,'assets':[None,'bad',{'id':'image123456789','sha256':'a'*64,'mime_type':'image/png','content_read':True,'format_check':'decoded','dimensions':{'width':7000,'height':6000}}]}}
  self.assertEqual(self.service.report()['candidates']['items'],[])
 def test_candidate_old_binding_not_exposed(self):
  self.save();self.materials.status=lambda req:{'binding_verified':False,'assets':{'assets':[{'id':'untrusted'}]}}
  self.assertEqual(self.service.report()['candidates']['items'],[])
 def test_full_resolution_candidate_matches_decoder_pixel_limit(self):
  self.save();self.materials.status=lambda req:{'binding_verified':True,'assets':{'complete':True,'assets':[{'id':'image123456789','name':'full-resolution.jpg','sha256':'a'*64,'mime_type':'image/jpeg','content_read':True,'format_check':'decoded','dimensions':{'width':4284,'height':5712}}]}}
  self.assertEqual(self.service.report()['candidates']['total'],1)
 def test_video_is_named_as_unsupported_not_silently_omitted(self):
  self.save();self.materials.status=lambda req:{'binding_verified':True,'assets':{'complete':True,'assets':[{'id':'videoclip123456','name':'Clothing reel.mp4','mime_type':'video/mp4','content_read':True}]}}
  candidates=self.service.report()['candidates']
  self.assertEqual(candidates['items'],[]);self.assertEqual(candidates['unsupported_items'][0]['name'],'Clothing reel.mp4')
  self.assertIn('וידאו',candidates['unsupported_items'][0]['reason']);self.assertFalse(candidates['unsupported_items'][0]['publication_ready'])
 def test_revalidate_uses_matching_saved_projection_and_receipt(self):
  self.save();self.materials.revalidate_cached=lambda req:self.calls.append(('validate',req['id'],req['revision'])) or {'decoded':1,'provider_reads':0}
  result=self.action('revalidate');self.assertEqual(result['validation']['decoded'],1);self.action('revalidate');self.assertEqual(len(self.calls),1);self.assertTrue(result['actions']['can_revalidate'])
 def test_save_restart_idempotent(self):
  first=self.save();self.service=self.make();self.assertEqual(self.save(),first);self.assertEqual(self.intake.latest()['revision'],1)
 def test_explicit_new_same_brief_and_folder_gets_fresh_revision_once(self):
  self.save();prior=self.intake.latest()
  from creative_selection import CreativeSelection
  source={'id':'samefolderimage','sha256':'a'*64}
  selection=CreativeSelection(self.tmp.name);selection.save(prior,[source],[source])
  body={'expected_request_id':prior['id'],'expected_request_revision':prior['revision']}
  result=self.save('fresh_request_key',**body)
  self.assertEqual(result['request']['revision'],prior['revision']+1)
  self.assertEqual(result['request']['goal'],prior['goal']);self.assertEqual(result['request']['drive_urls'],prior['drive_urls'])
  self.assertIsNone(selection.current(result['request']))
  self.assertEqual(self.save('fresh_request_key',**body),result)
  self.assertEqual(self.intake.latest()['revision'],prior['revision']+1)
 def test_import_exact_binding_and_duplicate(self):
  self.save();self.action('import');self.action('import');self.assertEqual(len(self.calls),1)
 def test_stale_import_rejected(self):
  self.save();r=self.intake.latest();self.intake.create({'goal':'חדש עכשיו למכירות','drive_urls':[]})
  with self.assertRaises(ValueError):self.action('import',request_revision=r['revision'])
  self.assertEqual(self.calls,[])
 def test_save_cas_does_not_overwrite(self):
  self.save()
  with self.assertRaises(ValueError):self.save('save_key2',brief='משהו חדש')
  self.assertEqual(self.intake.latest()['revision'],1)
 def test_review_missing_proposal_no_calls(self):
  self.save()
  with self.assertRaises(ValueError):self.action('review')
  self.assertEqual(self.calls,[])
 def test_review_replay_does_not_repeat(self):
  self.save();self.available=True;self.action('review');self.service=self.make();self.action('review');self.assertEqual(len(self.calls),2)
  self.assertFalse(self.service.report()['review_ready'])
 def test_idempotency_cannot_change_body(self):
  self.save()
  with self.assertRaises(ValueError):self.save(brief='שינוי לא מאושר')
 def test_start_does_not_mutate_canonical(self):
  self.save();before=self.intake.latest();self.action('start');self.assertEqual(before,self.intake.latest())
 def test_stale_review_job_not_displayed(self):
  self.save();self.job={'request_id':'other','request_revision':1,'state':'reviewed'};self.assertIsNone(self.service.report()['job'])
 def test_extra_authority_fields_rejected(self):
  self.save()
  with self.assertRaises(ValueError):self.action('review',trusted_reviewer=True)
 def test_unknown_pending_never_replayed(self):
  self.save();self.materials.refresh=lambda req:(_ for _ in ()).throw(RuntimeError('interrupted'))
  with self.assertRaises(ValueError):self.action('import')
  self.materials.refresh=lambda req:self.calls.append('repeat')
  with self.assertRaises(ValueError):self.action('import')
  self.assertEqual(self.calls,[])
 def test_awareness_not_projected_as_sales(self):
  result=self.save(brief='brand awareness');self.assertFalse(result['projection_saved']);self.assertFalse(result['actions']['can_import'])

class UnifiedIntakeTests(unittest.TestCase):
 """Local fake coordinator; these tests never send images or create Meta objects."""
 def setUp(self):
  TaskTests.setUp(self);self.creative_state={};self.dispatched=[]
  def start(request):
   self.assertEqual(request,self.intake.latest())
   self.assertEqual(request['preparation_state'],'projected')
   self.dispatched.append(request.copy())
   self.creative_state.update(state='collecting',request_id=request['id'],request_revision=request['revision'])
   return True
  self.service.creative=SimpleNamespace(start=start,status=lambda:self.creative_state.copy())
 make=TaskTests.make
 start_review=TaskTests.start_review
 def submit(self,**updates):
  body={'goal':'הכנת קמפיין מכירות לקולקציה','drive_urls':['https://drive.google.com/drive/folders/1234567890123456']};body.update(updates)
  return self.service.submit_work(body)
 def test_unified_request_dispatches_bound_preparation_without_second_action(self):
  result=self.submit();self.assertTrue(result['preparation_started_or_queued']);self.assertFalse(result['live_action_allowed'])
  self.assertEqual(len(self.dispatched),1);self.assertEqual(self.dispatched[0],self.intake.latest())
  self.assertFalse(self.service.report()['model_connected'])
 def test_duplicate_submission_does_not_restart_blocked_or_complete_revision(self):
  self.submit();self.creative_state['state']='blocked';self.submit()
  self.creative_state['state']='ready_for_owner';self.submit()
  self.assertEqual(len(self.dispatched),1)
 def test_updated_request_dispatches_new_revision(self):
  self.submit();self.submit(goal='הכנת מכירות לבגדים חדשים')
  self.assertEqual([r['revision'] for r in self.dispatched],[1,2])
 def test_projection_failure_saves_but_never_dispatches(self):
  self.workflow.create=lambda body:(_ for _ in ()).throw(ValueError('fake unavailable'))
  result=self.submit();self.assertTrue(result['saved']);self.assertFalse(result['projection_saved']);self.assertEqual(self.dispatched,[])
 def test_unsupported_goal_and_missing_links_never_dispatch(self):
  result=self.submit(goal='brand awareness')
  self.assertFalse(result['projection_saved'])
  self.assertNotEqual(result['request']['preparation_state'],'failed')
  self.submit(drive_urls=[]);self.assertEqual(self.dispatched,[])

if __name__=='__main__':unittest.main()
