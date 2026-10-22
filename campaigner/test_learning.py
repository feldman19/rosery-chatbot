from pathlib import Path
import tempfile
import unittest
from learning import LearningLedger, LearningDenied


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent,prefix='campaigner-test-')
        self.addCleanup(self.tmp.cleanup)
        self.ledger=LearningLedger(Path(self.tmp.name)/'learning.db')

    def test_predeclared_decision_and_observation_are_separate(self):
        d=self.ledger.decide('a','Hypothesis',['snapshot1'],'hold','Review next complete day')
        o=self.ledger.observe('a',d['id'],['snapshot2'],'before_after','More purchases observed')
        self.assertFalse(d['payload']['live_permission'])
        self.assertFalse(o['payload']['causal_effect_verified'])
        self.assertEqual([r['kind'] for r in self.ledger.list('a')],['decision','observation'])
        self.assertEqual(self.ledger.list('b'),[])

    def test_cross_tenant_and_retrospective_unknown_decision_denied(self):
        d=self.ledger.decide('a','Hypothesis',['s1'],'hold','Review after window')
        with self.assertRaises(LearningDenied): self.ledger.observe('b',d['id'],['s2'],'randomized','Claim success')
        with self.assertRaises(LearningDenied): self.ledger.observe('a','unknown',['s2'],'randomized','Claim success')

    def test_claimed_randomization_not_automatically_causal(self):
        d=self.ledger.decide('a','Hypothesis',['s1'],'hold','Review design')
        o=self.ledger.observe('a',d['id'],['s2'],'randomized','Self-reported randomized result')
        self.assertFalse(o['payload']['causal_effect_verified'])

    def test_missing_evidence_or_plan_denied(self):
        with self.assertRaises(LearningDenied): self.ledger.decide('a','H',[],'hold','review')
        with self.assertRaises(LearningDenied): self.ledger.decide('a','H',['s'],'hold','')

    def test_orphan_observations_denied(self):
        for parent in ('', None, ' '):
            with self.assertRaises(LearningDenied):
                self.ledger.observe('a',parent,['s'],'unknown','No decision')
