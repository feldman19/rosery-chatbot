"""Restart recovery is local state only; never launches a replacement worker."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from creative_preparation import CreativePreparation

class CreativeRestartTests(unittest.TestCase):
 def test_inflight_current_revision_becomes_interrupted_without_dispatch(self):
  for state in ('queued','collecting','analyzing','validating'):
   with self.subTest(state=state),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
    request={'id':'current','revision':2};path=Path(directory)/'creative-preparation.json'
    path.write_text(json.dumps({'request_id':'current','request_revision':2,'state':state}))
    with patch('creative_preparation.threading.Thread') as worker:
     coordinator=CreativePreparation(directory,intake=SimpleNamespace(latest=lambda:request),workflow=None,materials=None,proposal=None,analyzer=lambda *a,**k:self.fail())
    worker.assert_not_called();self.assertIsNone(coordinator.active)
    self.assertEqual(coordinator.status()['state'],'interrupted');self.assertTrue(coordinator.status()['retry_required'])
    self.assertEqual(coordinator.status()['previous_state'],state)
 def test_other_revision_and_terminal_states_are_not_overwritten(self):
  for previous in ({'request_id':'older','request_revision':1,'state':'analyzing'},{'request_id':'current','request_revision':2,'state':'ready_for_owner'},{'request_id':'current','request_revision':2,'state':'blocked'}):
   with self.subTest(previous=previous),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
    path=Path(directory)/'creative-preparation.json';path.write_text(json.dumps(previous));raw=path.read_bytes()
    CreativePreparation(directory,intake=SimpleNamespace(latest=lambda:{'id':'current','revision':2}),workflow=None,materials=None,proposal=None)
    self.assertEqual(path.read_bytes(),raw)
 def test_request_changes_during_recovery_does_not_write_old_state(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
   path=Path(directory)/'creative-preparation.json';path.write_text(json.dumps({'request_id':'old','request_revision':1,'state':'analyzing'}));raw=path.read_bytes()
   requests=iter([{'id':'old','revision':1},{'id':'new','revision':2}])
   CreativePreparation(directory,intake=SimpleNamespace(latest=lambda:next(requests)),workflow=None,materials=None,proposal=None)
   self.assertEqual(path.read_bytes(),raw)
