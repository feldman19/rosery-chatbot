"""Durable tracking-case lifecycle tests with fresh synthetic provider evidence."""
import copy
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from action_desk import ActionDesk
from test_creative_audit import fixture

class ActionCaseLifecycleTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.path=Path(self.temp.name);self.desk=ActionDesk(self.path)
  self.snapshot=fixture();self.freshen()
 def tearDown(self):self.temp.cleanup()
 def freshen(self):
  now=datetime.now(timezone.utc).isoformat();self.snapshot['checked_at']=now;self.snapshot['sources']['meta']['last_success_at']=now
 def sync(self):self.freshen();return self.desk.sync(self.snapshot,{})
 def tracking(self,r):return next(x for x in r['findings'] if x['payload']['key'].startswith('tracking-collision-'))
 def test_repeat_sync_stable_case_and_single_detect_event(self):
  first=self.tracking(self.sync());second=self.tracking(self.sync());self.assertEqual(first['id'],second['id']);self.assertEqual(first['revision'],second['revision'])
  events=[h for h in self.desk.read()['history'] if h['finding_id']==first['id']];self.assertEqual(['detected'],[h['event'] for h in events])
 def test_source_unavailable_never_resolves_existing_case(self):
  initial=self.tracking(self.sync());self.snapshot['sources']['meta']['error']='unavailable'
  later=self.tracking(self.sync());self.assertEqual('open',later['state']);self.assertEqual(initial['id'],later['id'])
  events=[h['event'] for h in self.desk.read()['history'] if h['finding_id']==initial['id']];self.assertNotIn('condition_cleared',events)
 def test_verified_unique_url_fix_resolves_only_collision_condition(self):
  original=self.tracking(self.sync());data=self.snapshot['sources']['meta']['data']
  for ad in data['creative_inventory']['ads']:
   ad['creative']['object_story_spec']['link_data']['link']=ad['creative']['object_story_spec']['link_data']['link'].replace('shared',ad['id'])
  r=self.sync();fixed=self.tracking(r);self.assertEqual(original['id'],fixed['id']);self.assertEqual('resolved',fixed['state'])
  self.assertTrue(any(x['state']=='open' and x['payload']['key']=='measurement' for x in r['findings']))
  self.assertEqual(1,len([h for h in r['history'] if h['finding_id']==fixed['id'] and h['event']=='condition_cleared']))
 def test_review_is_local_not_execution_and_survives_reload(self):
  item=self.tracking(self.sync());result=self.desk.update({'id':item['id'],'expected_revision':item['revision'],'action':'reviewed','note':'Synthetic link inspection only'})
  self.assertFalse(result['live_action_allowed']);self.assertFalse(result['measurement_verified'])
  reloaded=ActionDesk(self.path);saved=self.tracking(reloaded.read());self.assertEqual('reviewed',saved['state']);self.assertEqual('Synthetic link inspection only',saved['note'])
  self.assertIn('prepared_change',saved['payload']);self.assertTrue(saved['payload']['prepared_change']['evidence_refs'])
 def test_unknown_creative_destination_never_clears_collision(self):
  original=self.tracking(self.sync())
  for ad in self.snapshot['sources']['meta']['data']['creative_inventory']['ads']:ad['creative'].pop('object_story_spec')
  later=self.tracking(self.sync());self.assertNotEqual('resolved',later['state']);self.assertEqual(original['id'],later['id'])
if __name__=='__main__':unittest.main()
