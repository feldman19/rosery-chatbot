"""Two focused report-to-decision cases, no provider calls."""
import unittest
from test_management_report import ManagementReportTests
from management_report import build_report
from action_recommendations import build_recommendations


class ChangeTimeEvidenceTests(unittest.TestCase):
    def decision(self, present):
        fixture = ManagementReportTests()
        fixture.setUp()
        inventory = fixture.snapshot['sources']['meta']['data']['account_campaigns']
        if present:
            inventory['campaigns'][0]['updated_time'] = '2026-10-17T10:00:00+0300'
        report = build_report(fixture.snapshot, now=fixture.now)
        decision = build_recommendations(report, now=fixture.now)['recommendations'][0]
        return decision

    def test_present_entity_stamp_is_carried_but_not_relevant_change_proof(self):
        result = self.decision(True)
        evidence = result['change_evidence']
        self.assertEqual(evidence['entity_id'], 'sales1')
        self.assertEqual(evidence['source_path'], 'sources.meta.data.account_campaigns.campaigns')
        self.assertEqual(evidence['updated_time'], '2026-10-17T10:00:00+0300')
        self.assertEqual(evidence['status'], 'entity_update_observed')
        self.assertNotIn('entity_update_timestamp', result['missing_data'])
        self.assertIn('relevant_budget_or_creative_change_time', result['missing_data'])
        self.assertIsNone(evidence['relevant_change_time'])
        self.assertFalse(evidence['attribution_maturity_proven'])
        self.assertEqual(result['action'], 'INSUFFICIENT_EVIDENCE')
        self.assertFalse(result['live_change'])

    def test_absent_entity_stamp_remains_explicit_without_inference(self):
        result = self.decision(False)
        self.assertEqual(result['change_evidence']['status'], 'not_collected')
        self.assertIsNone(result['change_evidence']['updated_time'])
        self.assertIn('entity_update_timestamp', result['missing_data'])
        self.assertIn('relevant_budget_or_creative_change_time', result['missing_data'])
        self.assertFalse(result['change_evidence']['attribution_maturity_proven'])
        self.assertFalse(result['live_change'])


if __name__ == '__main__':
    unittest.main()
