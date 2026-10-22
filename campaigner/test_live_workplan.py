import copy
import unittest
from datetime import datetime, timezone

from live_workplan import build_workplan
from meta_direct import ACCOUNT, LAUNCH
from connections import SHOP


class LiveWorkplanTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.timestamp = '2026-10-17T11:59:00Z'
        self.snapshot = {'checked_at': self.timestamp, 'sources': {
            'meta': {'last_success_at': self.timestamp, 'error': None, 'data': {
                'verified': True, 'complete': True, 'account_id': ACCOUNT,
                'since': '2026-10-10', 'until': '2026-10-17', 'today': '2026-10-17',
                'rows': [{'campaign_id': LAUNCH, 'ad_id': 'ad1', 'date_start': '2026-10-16',
                          'date_stop': '2026-10-16', 'spend': '50', 'impressions': '1000'}],
                'inventory': {'verified': True,
                    'campaign': {'id': LAUNCH, 'account_id': ACCOUNT, 'name': 'Launch',
                                 'objective': 'OUTCOME_LEADS', 'effective_status': 'ACTIVE'},
                    'adsets': [{'id': 'set1', 'name': 'Set', 'effective_status': 'ACTIVE',
                                'promoted_object': {'pixel_id': 'pixel1', 'custom_event_type': 'COMPLETE_REGISTRATION'}}],
                    'ads': [{'id': 'ad1', 'name': 'PL01B', 'adset_id': 'set1', 'effective_status': 'ACTIVE'}]}}},
            'shopify': {'last_success_at': self.timestamp, 'error': None,
                        'data': {'verified': True, 'shop': SHOP, 'table': {'rows': []}}}}}
        self.expectations = {'campaigns': {LAUNCH: {'verified': True, 'source_ref': 'approval:launch',
                              'goal': 'registration', 'expected_event': 'COMPLETE_REGISTRATION', 'expected_pixel_id': 'pixel1'}},
                             'ads': {'ad1': {'verified': True, 'source_ref': 'approval:destination',
                                            'destination_url': 'https://roseryjewelry.com/'}}}

    def run_plan(self, snapshot=None, expectations=None):
        return build_workplan(self.snapshot if snapshot is None else snapshot,
                              self.expectations if expectations is None else expectations, self.now)

    def inventory(self, snapshot):
        return snapshot['sources']['meta']['data']['inventory']

    def test_healthy_configuration_no_fake_action(self):
        result = self.run_plan()
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['analysis_cases'][0]['disposition'], 'retain')
        self.assertFalse(result['live_action_allowed'])
        self.assertEqual(result['campaigns'][0]['result_metric'], 'registration')

    def test_missing_fields_remain_unknown_not_issues(self):
        snapshot = copy.deepcopy(self.snapshot)
        del self.inventory(snapshot)['adsets'][0]['promoted_object']['custom_event_type']
        result = self.run_plan(snapshot)
        self.assertEqual(result['tasks'], [])
        self.assertIn('configuration_field_not_collected', {x['kind'] for x in result['unknowns']})
        self.assertIn('destination_not_collected', {x['kind'] for x in result['unknowns']})

    def test_explicit_event_mismatch_exact_scope_and_rollback(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['adsets'][0]['promoted_object']['custom_event_type'] = 'PURCHASE'
        result = self.run_plan(snapshot)
        task = result['tasks'][0]
        self.assertEqual(task['kind'], 'optimization_event_mismatch')
        self.assertEqual(task['entity']['id'], 'set1')
        self.assertEqual(task['proposal']['before']['promoted_object']['custom_event_type'], 'PURCHASE')
        self.assertEqual(task['proposal']['after']['promoted_object']['pixel_id'], 'pixel1')
        self.assertEqual(task['proposal']['rollback'], task['proposal']['before'])
        self.assertFalse(task['execute_permission'])
        self.assertEqual(result['analysis_cases'][0]['disposition'], 'investigate_measurement')

    def test_pixel_mismatch_only_when_observed(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['adsets'][0]['promoted_object']['pixel_id'] = 'other'
        task = self.run_plan(snapshot)['tasks'][0]
        self.assertEqual(task['kind'], 'pixel_mismatch')
        self.assertEqual(task['proposal']['after']['promoted_object']['pixel_id'], 'pixel1')

    def test_unverified_expectation_is_not_fix_authority(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['adsets'][0]['promoted_object']['pixel_id'] = 'other'
        expectation = copy.deepcopy(self.expectations)
        expectation['campaigns'][LAUNCH]['verified'] = False
        self.assertEqual(self.run_plan(snapshot, expectation)['tasks'], [])

    def test_paused_parent_does_not_generate_budget_waste_or_change(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['campaign']['effective_status'] = 'PAUSED'
        self.inventory(snapshot)['adsets'][0]['promoted_object']['pixel_id'] = 'other'
        snapshot['sources']['meta']['data']['rows'] = []
        result = self.run_plan(snapshot)
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['analysis_cases'][0]['disposition'], 'retain')

    def test_paused_child_not_targeted(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['adsets'][0]['effective_status'] = 'PAUSED'
        self.inventory(snapshot)['adsets'][0]['promoted_object']['pixel_id'] = 'other'
        self.assertEqual(self.run_plan(snapshot)['tasks'], [])

    def test_stale_data_never_creates_live_configuration_fix(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['last_success_at'] = '2026-10-16T11:59:00Z'
        self.inventory(snapshot)['adsets'][0]['promoted_object']['pixel_id'] = 'other'
        result = self.run_plan(snapshot)
        self.assertEqual([x['kind'] for x in result['tasks']], ['report_stale'])
        self.assertEqual(result['analysis_cases'][0]['disposition'], 'insufficient_evidence')

    def test_incomplete_report_not_zero_results(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['complete'] = False
        snapshot['sources']['meta']['data']['rows'] = []
        result = self.run_plan(snapshot)
        self.assertEqual(result['tasks'][0]['kind'], 'report_incomplete')
        self.assertNotIn('inspect_delivery', [x['kind'] for x in result['tasks']])

    def test_partial_insights_do_not_block_independent_verified_config_issue(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['complete'] = False
        self.inventory(snapshot)['adsets'][0]['promoted_object']['pixel_id'] = 'other'
        kinds = {task['kind'] for task in self.run_plan(snapshot)['tasks']}
        self.assertEqual(kinds, {'report_incomplete', 'pixel_mismatch'})

    def test_foreign_parent_child_not_targeted(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['adsets'][0]['campaign_id'] = 'foreign'
        self.inventory(snapshot)['ads'][0]['destination_url'] = None
        self.assertEqual(self.run_plan(snapshot)['tasks'], [])

    def test_zero_full_day_creates_readonly_delivery_investigation_not_failure(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['rows'] = []
        result = self.run_plan(snapshot)
        case = result['analysis_cases'][0]
        self.assertEqual(case['disposition'], 'investigate_delivery')
        task = result['tasks'][0]
        self.assertEqual(task['evidence']['active_during_reported_day'], 'unknown')
        self.assertFalse(task['proposal']['provider_write'])
        self.assertNotIn('budget', task['proposal'])

    def test_missing_day_coverage_is_insufficient_not_zero(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['since'] = '2026-10-17'
        snapshot['sources']['meta']['data']['rows'] = []
        result = self.run_plan(snapshot)
        self.assertEqual(result['analysis_cases'][0]['disposition'], 'insufficient_evidence')
        self.assertEqual(result['tasks'], [])

    def test_destination_missing_proposes_paused_replacement_not_inline_mutation(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['ads'][0]['destination_url'] = None
        task = self.run_plan(snapshot)['tasks'][0]
        self.assertEqual(task['kind'], 'destination_missing')
        self.assertEqual(task['proposal']['status'], 'PAUSED')
        self.assertIsNone(task['proposal']['original_ad_change'])

    def test_unverified_creative_does_not_prove_missing_destination(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['creative_inventory'] = {'verified': False, 'ads': [
            {'id': 'ad1', 'creative': {'link_url': None, 'asset_feed_spec': None, 'object_story_spec': None}}]}
        self.assertEqual(self.run_plan(snapshot)['tasks'], [])

    def test_verified_creative_links_prevent_false_missing(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['creative_inventory'] = {'verified': True, 'ads': [
            {'id': 'ad1', 'creative': {'link_url': None, 'asset_feed_spec': None,
                                      'object_story_spec': {'link_data': {'link': 'https://roseryjewelry.com'}}}}]}
        self.assertEqual(self.run_plan(snapshot)['tasks'], [])

    def test_fresh_verified_empty_creative_fields_can_prove_empty(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['creative_inventory'] = {'verified': True, 'ads': [
            {'id': 'ad1', 'destination_audit_complete': True,
             'creative': {'link_url': None, 'asset_feed_spec': None, 'object_story_spec': None}}]}
        self.assertEqual(self.run_plan(snapshot)['tasks'][0]['kind'], 'destination_missing')

    def test_null_creative_roots_alone_do_not_rule_out_existing_post_links(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['creative_inventory'] = {'verified': True, 'ads': [
            {'id': 'ad1', 'creative': {'link_url': None, 'asset_feed_spec': None, 'object_story_spec': None}}]}
        self.assertEqual(self.run_plan(snapshot)['tasks'], [])

    def test_conflicting_duplicate_inventory_is_unknown_not_last_write_wins(self):
        snapshot = copy.deepcopy(self.snapshot)
        other = copy.deepcopy(self.inventory(snapshot)['adsets'][0])
        other['promoted_object']['pixel_id'] = 'other'
        self.inventory(snapshot)['adsets'].append(other)
        result = self.run_plan(snapshot)
        self.assertEqual(result['tasks'], [])
        self.assertIn('conflicting_inventory_versions', {x['kind'] for x in result['unknowns']})

    def test_duplicate_click_or_same_rows_has_stable_task_ids(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['ads'][0]['destination_url'] = None
        self.inventory(snapshot)['ads'] *= 7
        one, two = self.run_plan(snapshot), self.run_plan(snapshot)
        self.assertEqual(len(one['tasks']), 1)
        self.assertEqual(one['tasks'][0]['id'], two['tasks'][0]['id'])
        self.assertEqual(one['tasks'][0]['fingerprint'], two['tasks'][0]['fingerprint'])

    def test_case_has_evidence_expiry_and_recheck_not_permission(self):
        case = self.run_plan()['analysis_cases'][0]
        self.assertTrue(case['evidence_refs'])
        self.assertTrue(case['expires_at'])
        self.assertTrue(case['recheck_condition'])
        self.assertFalse(case['execute_permission'])

    def test_sales_objective_not_automatic_registration_error(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.inventory(snapshot)['campaign']['objective'] = 'OUTCOME_SALES'
        result = self.run_plan(snapshot, {})
        self.assertEqual(result['campaigns'][0]['objective_family'], 'sales')
        self.assertEqual(result['campaigns'][0]['result_metric'], 'unknown')
        self.assertEqual(result['tasks'], [])

    def test_wrong_account_never_creates_config_fix(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['sources']['meta']['data']['account_id'] = 'other'
        self.inventory(snapshot)['adsets'][0]['promoted_object']['pixel_id'] = 'other'
        result = self.run_plan(snapshot)
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['operator_summary']['blocker'], 'meta_source_not_verified')


if __name__ == '__main__':
    unittest.main()
