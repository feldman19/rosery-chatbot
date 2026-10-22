from pathlib import Path
import tempfile,unittest
from work_intake import WorkIntake


class IntakeTests(unittest.TestCase):
    def test_any_goal_preserves_links_and_restarts_without_approval(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            value={'goal':'brand awareness','drive_urls':['https://drive.google.com/drive/folders/abcdefghijkl']}
            first=WorkIntake(directory).create(value)
            self.assertEqual(WorkIntake(directory).latest(),first)
            self.assertEqual(first['drive_urls'],value['drive_urls']);self.assertFalse(first['execution_approval'])
            self.assertEqual(WorkIntake(directory).create(value)['revision'],1)
            with self.assertRaises(ValueError):WorkIntake(directory).create({**value,'drive_urls':['https://evil.invalid']})
            self.assertEqual(WorkIntake(directory).latest(),first)
