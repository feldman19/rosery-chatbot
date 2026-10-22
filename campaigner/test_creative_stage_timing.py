"""Deterministic local monotonic stage accounting, never a performance claim."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from creative_preparation import CreativePreparation

class CreativeStageTimingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
  self.request={'id':'test','revision':1};self.clock=10.0
  mock=patch('creative_preparation.time.monotonic',side_effect=lambda:self.clock);mock.start();self.addCleanup(mock.stop)
  self.intake=SimpleNamespace(latest=lambda:self.request)
  self.coordinator=self.make()
 def make(self):return CreativePreparation(self.temp.name,intake=self.intake,workflow=None,materials=None,proposal=None)
 def test_transitions_keep_completed_duration_and_current_elapsed(self):
  c=self.coordinator;c._save('collecting',self.request);self.clock=12.5;c._save('analyzing',self.request);self.clock=16
  status=c.status();self.assertEqual(status['stage_history'][0]['elapsed_ms'],2500);self.assertEqual(status['current_stage_elapsed_ms'],3500)
  c._save('ready_for_owner',self.request);self.clock=100
  status=c.status();self.assertIsNone(status['current_stage_elapsed_ms']);self.assertEqual(status['stage_history'][1]['elapsed_ms'],3500)
 def test_restart_does_not_measure_offline_interval(self):
  c=self.coordinator;c._save('collecting',self.request);self.clock=12;c._save('analyzing',self.request);self.clock=10000
  recovered=self.make().status();self.assertEqual(recovered['state'],'interrupted')
  self.assertEqual(recovered['stage_history'][0]['elapsed_ms'],2000)
  self.assertIsNone(recovered['stage_history'][1]['elapsed_ms']);self.assertFalse(recovered['stage_history'][1]['duration_known'])
 def test_new_revision_resets_history_and_stale_save_cannot_write(self):
  old=dict(self.request);self.coordinator._save('collecting',old);self.request={'id':'test','revision':2};self.clock=20
  self.coordinator._save('collecting',self.request);self.assertEqual(len(self.coordinator.status()['stage_history']),1)
  before=self.coordinator.path.read_bytes();self.assertEqual(self.coordinator._save('blocked',old)['state'],'stale');self.assertEqual(self.coordinator.path.read_bytes(),before)
 def test_repeated_status_same_stage_preserves_clock_and_history_is_bounded(self):
  c=self.coordinator;c._save('collecting',self.request);self.clock=12;c._save('collecting',self.request)
  self.assertEqual(c.status()['current_stage_elapsed_ms'],2000);self.assertEqual(len(c.status()['stage_history']),1)
  for n in range(30):self.clock+=1;c._save('analyzing' if n%2 else 'collecting',self.request)
  self.assertLessEqual(len(c.status()['stage_history']),20)
