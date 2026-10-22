import tempfile, unittest, uuid
from pathlib import Path
from assistant import HandoffQueue, AssistantDenied
class Recovery(unittest.TestCase):
 def test_retry_is_durable_and_conflict_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'queue.sqlite'; key=str(uuid.uuid4()); q=HandoffQueue(path)
   first=q.issue('Prepare new campaign copy',request_id=key)
   retry=HandoffQueue(path).issue('Prepare new campaign copy',request_id=key)
   self.assertEqual(first['id'],retry['id']); self.assertTrue(retry['replayed'])
   self.assertEqual(len(q.list()),1)
   with self.assertRaises(AssistantDenied):q.issue('Different request',request_id=key)
