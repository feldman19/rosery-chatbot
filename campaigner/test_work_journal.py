from pathlib import Path
import tempfile
import unittest
from work_journal import WorkJournal


class JournalTests(unittest.TestCase):
    def test_restart_preserves_transitions_without_poll_duplicates(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            journal=WorkJournal(directory)
            state={'goal':'launch','state':'blocked','current_state':'waiting','goal_binding':{'revision':1}}
            self.assertEqual(len(journal.observe(state)),1)
            self.assertEqual(len(journal.observe({**state,'checked_at':'new time'})),1)
            self.assertEqual(len(WorkJournal(directory).observe(state)),1)
            self.assertEqual(len(journal.observe({**state,'state':'ready','current_state':'prepared'})),2)
