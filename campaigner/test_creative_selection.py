"""Synthetic selection persistence and bound dispatch; no external calls."""
import threading,unittest
from types import SimpleNamespace
import test_preparation_task as fixtures
from creative_selection import CreativeSelection

class CreativeSelectionTests(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.TaskTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.f.save()
  self.request=self.f.intake.latest();self.rows=[{'id':f'syntheticimage{i:03}','sha256':f'{i:064x}','name':str(i),'mime_type':'image/jpeg','content_read':True,'format_check':'decoded','dimensions':{'width':1080,'height':1350}} for i in range(32)]
  self.f.materials.status=lambda r:{'configured':True,'binding_verified':True,'assets':{'complete':True,'assets':self.rows}}
  self.starts=[];self.f.service.creative=SimpleNamespace(lock=threading.Lock(),active=None,pending=None,status=lambda:{'state':'blocked'},start=lambda r:self.starts.append(r) or True)
  self.selected=[{k:r[k] for k in ('id','sha256')} for r in self.rows[:2]]
 def action(self,key='selection_key_1'):
  return self.f.service.act({'action':'select','idempotency_key':key,'request_id':self.request['id'],'request_revision':self.request['revision'],'selected_sources':self.selected})
 def test_bound_subset_persists_dispatches_and_replay_never_repeats(self):
  result=self.action();self.assertEqual(result['selected_count'],2);self.assertEqual(len(self.starts),1)
  self.assertEqual(self.action(),result);self.assertEqual(len(self.starts),1)
  store=CreativeSelection(self.f.tmp.name);self.assertEqual(store.apply(self.request,self.rows),self.rows[:2])
  self.assertEqual(self.f.service.report()['selected_sources']['items'],self.selected)
 def test_source_hash_change_rejected_after_selection(self):
  self.action();self.rows[0]['sha256']='f'*64
  with self.assertRaises(ValueError):CreativeSelection(self.f.tmp.name).apply(self.request,self.rows)
 def test_new_revision_does_not_inherit_selection(self):
  self.action();new=self.f.intake.create({'goal':'new sales request','drive_urls':self.request['drive_urls']})
  self.assertIsNone(CreativeSelection(self.f.tmp.name).current(new))
 def test_prepared_or_running_reject_selection_before_mutation(self):
  self.f.available=True
  with self.assertRaises(ValueError):self.action()
  self.f.available=False;self.f.service.creative.active=('busy',1)
  with self.assertRaises(ValueError):self.action('selection_key_2')
  self.assertIsNone(CreativeSelection(self.f.tmp.name).current(self.request));self.assertEqual(self.starts,[])
 def test_unknown_duplicate_and_oversized_selections_rejected(self):
  for selected in ([{'id':'unknown','sha256':'a'*64}],self.selected*2,[{k:r[k] for k in ('id','sha256')} for r in self.rows]):
   with self.assertRaises(ValueError):CreativeSelection(self.f.tmp.name).save(self.request,selected,self.rows)
