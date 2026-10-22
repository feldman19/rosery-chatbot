"""Decision contract tests: no provider I/O and no spend authority."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from action_recommendations import build_recommendations, performance_findings, ACCOUNT

class RecommendationsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
        self.report = {'account_id': ACCOUNT, 'currency': 'ILS', 'checked_at': self.now.isoformat(),
                       'freshness': {'state': 'fresh', 'meta_last_success_at':self.now.isoformat()}, 'windows': {}}
        for days in (1, 3, 7):
            self.report['windows'][str(days)] = {'partial': False, 'since': '2026-10-13', 'until': '2026-10-19',
                'adsets': [{'id': 's1', 'name': 'One', 'result_type': 'purchase', 'spend': 100 * days,
                            'purchases': days, 'status': 'ACTIVE', 'effective_status': 'ACTIVE',
                            'configured_active': True, 'daily_budget_minor': 12000}]}
    def decision(self):
        return build_recommendations(self.report, now=self.now)['recommendations'][0]
    def test_hold_preserves_actual_budget_and_is_not_authorization(self):
        item = self.decision()
        self.assertEqual(item['action'], 'INSUFFICIENT_EVIDENCE')
        self.assertEqual(item['before'], item['after'])
        self.assertEqual(item['before']['daily_budget_minor'], 12000)
        self.assertFalse(item['live_change'])
        self.assertEqual([x['cost_per_result'] for x in item['evidence']], [100, 100, 100])
    def test_missing_is_not_zero(self):
        self.report['windows']['1']['adsets'][0]['purchases'] = None
        item = self.decision()
        self.assertIsNone(item['evidence'][0]['results'])
        self.assertIn('אין לפרש', item['rationale'])
        self.report['windows']['1']['adsets'][0]['purchases'] = 0
        item = self.decision()
        self.assertEqual(item['evidence'][0]['results'], 0)
        self.assertIn('ללא תוצאות', item['rationale'])
        self.assertEqual(item['action'], 'INSUFFICIENT_EVIDENCE')
    def test_partial_window_not_usable(self):
        self.report['windows']['1']['partial'] = True
        item = self.decision()
        self.assertIsNone(item['evidence'][0]['spend'])
        self.assertFalse(item['evidence'][0]['complete'])
        self.assertIn('complete_entity_conversion_windows', item['missing_data'])
    def test_old_matching_timestamps_are_not_recent_evidence(self):
        old=(self.now-timedelta(days=1)).isoformat()
        self.report['checked_at']=old
        self.report['freshness']['meta_last_success_at']=old
        item=self.decision()
        self.assertIn('fresh_source_data', item['missing_data'])
        self.assertNotIn('recent_meta_snapshot', item['evidence_assessment']['accepted_facts'])
    def test_stale_or_future_timestamp_blocks_experiment(self):
        for stamp in (self.now-timedelta(minutes=16), self.now+timedelta(minutes=1)):
            self.report['checked_at'] = stamp.isoformat()
            result = build_recommendations(self.report, self.candidate(), now=self.now)
            self.assertFalse(result['fresh'])
            self.assertEqual(result['recommendations'][0]['action'], 'INSUFFICIENT_EVIDENCE')
    def candidate(self):
        return {'business_id': 'rosery', 'experiment_candidates': [
            {'entity_id': 's1', 'hypothesis': 'Distinct documented angle', 'source_ref': 'asset-review:1',
             'evidence_status': 'supported_for_preparation'}]}
    def test_experiment_is_paused_and_does_not_invent_budget(self):
        item = build_recommendations(self.report, self.candidate(), now=self.now)['recommendations'][0]
        self.assertEqual(item['action'], 'INSUFFICIENT_EVIDENCE')
        self.assertIsNone(item['preparation'])
        self.assertEqual(item['before'], item['after'])
    def test_historical_cac_is_not_current_target(self):
        dossier = {'business_id': 'rosery', 'known': [{'fact': 'Old CAC 105.54', 'kind': 'historical_metric'}]}
        result = build_recommendations(self.report, dossier, now=self.now)
        self.assertFalse(result['financial_context_is_target'])
        self.assertEqual(result['recommendations'][0]['action'], 'INSUFFICIENT_EVIDENCE')
        self.assertNotIn('target_cpa', result)
    def test_other_tenant_rejected(self):
        self.report['account_id'] = 'other'
        with self.assertRaises(ValueError): build_recommendations(self.report, now=self.now)
        self.report['account_id'] = ACCOUNT
        with self.assertRaises(ValueError): build_recommendations(self.report, {'business_id': 'other'}, now=self.now)
    def test_historical_paused_ad_is_not_reactivated(self):
        for window in self.report['windows'].values():
            window['adsets'][0].update(status='PAUSED', effective_status='PAUSED', configured_active=False)
        item = self.decision()
        self.assertEqual(item['after']['status'], 'PAUSED')
        self.assertEqual(item['action'], 'INSUFFICIENT_EVIDENCE')
    def test_no_mutation_of_input(self):
        before = deepcopy(self.report)
        self.decision()
        self.assertEqual(before, self.report)
    def test_comparison_only_current_same_campaign_known_results(self):
        rows = self.report['windows']['3']['adsets']
        rows[0]['campaign_id'] = 'c1'
        rows.extend([
            {**rows[0], 'id': 's2', 'spend': 90, 'purchases': 3},
            {**rows[0], 'id': 'missing', 'purchases': None},
            {**rows[0], 'id': 'paused', 'configured_active': False, 'spend': 1},
            {**rows[0], 'id': 'other', 'campaign_id': 'c2', 'spend': 1}])
        findings = {x['entity_id']: x for x in performance_findings(self.report)}
        self.assertNotIn('paused', findings)
        self.assertIsNone(findings['missing']['purchases'])
        self.assertIn('חסר', findings['missing']['title'])
        self.assertIn('30.00', findings['s1']['detail'])
        self.assertIn('לא קביעה של עדיפות', findings['s2']['title'])
        self.assertNotIn('הנמוכה', findings['other']['title'])
        self.assertTrue(all(x['action_allowed'] is False for x in findings.values()))
    def test_stale_does_not_offer_performance_conclusions(self):
        self.report['checked_at'] = (self.now-timedelta(hours=1)).isoformat()
        result = build_recommendations(self.report, now=self.now)
        self.assertEqual(result['performance_findings'], [])
        self.assertEqual(result['decision_engine_status'], 'owner_rulebook_proposals')
        self.assertFalse(any(p['ready_for_approval'] for p in result['rule_engine']['proposals']))
        result = build_recommendations(self.report, now=self.now, rulebook={'status': 'unavailable'})
        self.assertEqual(result['decision_engine_status'], 'financial_action_generation_not_implemented')
        self.assertEqual(result['rule_engine']['status'], 'blocked')

if __name__ == '__main__': unittest.main()
