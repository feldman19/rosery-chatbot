import copy
import unittest
from datetime import datetime, timezone

from experiment_planner import plan_experiment
from meta_direct import ACCOUNT, LAUNCH


class ExperimentPlannerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.snapshot = {'sources': {'meta': {'last_success_at': '2026-10-17T11:59:00Z', 'error': None,
             'data': {'verified': True, 'complete': True, 'account_id': ACCOUNT,
                      'today': '2026-10-17', 'since': '2026-10-10', 'until': '2026-10-17',
                      'inventory': {'verified': True,
                          'campaign': {'id': LAUNCH, 'account_id': ACCOUNT, 'effective_status': 'ACTIVE'},
                          'adsets': [{'id': 's1', 'effective_status': 'ACTIVE'}],
                          'ads': [{'id': 'a1', 'adset_id': 's1', 'effective_status': 'ACTIVE'},
                                  {'id': 'a2', 'adset_id': 's1', 'effective_status': 'ACTIVE'}]},
                      'creative_inventory': {'verified': True, 'account_id': ACCOUNT, 'campaign_id': LAUNCH,
                          'ads': [self.ad('a1', 'creative1', 'hash1'), self.ad('a2', 'creative2', 'hash2')]},
                      'rows': [self.row('a1', '10', '1'), self.row('a2', '0', '0')]}}}}

    def ad(self, identity, creative_id, image):
        return {'id': identity, 'name': 'A name that claims winner', 'adset_id': 's1', 'effective_status': 'ACTIVE',
                'creative': {'id': creative_id, 'url_tags': 'ad_id='+identity, 'object_story_spec': {'link_data': {
                    'message': 'הקולקציה החדשה בדרך', 'name': 'להרשמה להשקה', 'description': 'עדכון ראשון',
                    'link': 'https://roseryjewelry.com/', 'image_hash': image,
                    'call_to_action': {'type': 'SIGN_UP'}}}}}

    def row(self, identity, spend, registrations, day='2026-10-16'):
        return {'campaign_id': LAUNCH, 'ad_id': identity, 'date_start': day, 'date_stop': day,
                'spend': spend, 'impressions': '100', 'actions': [
                    {'action_type': 'omni_complete_registration', 'value': registrations},
                    {'action_type': 'complete_registration', 'value': registrations},
                    {'action_type': 'landing_page_view', 'value': '10'}]}

    def plan(self, snapshot=None):
        return plan_experiment(snapshot or self.snapshot, self.now)

    def meta(self, snapshot):
        return snapshot['sources']['meta']['data']

    def test_actual_copy_hash_and_single_variable_brief(self):
        brief = self.plan()
        self.assertEqual(brief['state'], 'ready_for_review')
        proposal = brief['proposal']
        self.assertEqual(proposal['variable_to_change'], 'image_only')
        self.assertEqual(proposal['copy_to_hold']['primary_texts'], ['הקולקציה החדשה בדרך'])
        self.assertEqual(proposal['before_image_hash'], 'hash1')
        self.assertEqual(proposal['candidate_image_hash'], 'hash2')
        self.assertEqual(proposal['basis'], 'observed_same_copy_different_image')
        self.assertEqual(brief['observations']['a1']['registrations'], '1')
        self.assertFalse(brief['measurement_verified'])
        self.assertIsNone(brief['budget_change'])
        self.assertIsNone(brief['spend_cap'])
        self.assertFalse(brief['published'])

    def test_same_snapshot_idempotent_changed_creative_changes_version(self):
        first, second = self.plan(), self.plan()
        self.assertEqual(first['version'], second['version'])
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['creative_inventory']['ads'][0]['creative']['object_story_spec']['link_data']['image_hash'] = 'new-hash'
        changed = self.plan(snapshot)
        self.assertEqual(first['brief_id'], changed['brief_id'])
        self.assertNotEqual(first['version'], changed['version'])

    def test_small_volume_does_not_create_winner_or_loser(self):
        brief = self.plan()
        self.assertEqual(brief['observations']['a1']['registrations'], '1')
        self.assertNotIn('winner', brief['proposal'])
        self.assertNotIn('pause', brief['proposal'])
        self.assertFalse(brief['execute_permission'])

    def test_zero_spend_is_not_failed_test(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['rows'] = [self.row('a1', '0', '0'), self.row('a2', '0', '0')]
        brief = self.plan(snapshot)
        self.assertEqual(brief['state'], 'ready_for_review')
        self.assertIsNone(brief['observations']['a2']['attributed_cost_per_registration'])
        self.assertIsNone(brief['budget_change'])

    def test_paused_reference_not_used_for_experiment(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['inventory']['ads'][1]['effective_status'] = 'PAUSED'
        self.meta(snapshot)['creative_inventory']['ads'][1]['effective_status'] = 'PAUSED'
        brief = self.plan(snapshot)
        self.assertEqual(brief['kind'], 'creative_inventory_brief')
        self.assertIsNone(brief['proposal'])
        self.assertEqual(len(brief['inventory']), 2)

    def test_paused_parent_no_activation_proposal(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['inventory']['campaign']['effective_status'] = 'PAUSED'
        brief = self.plan(snapshot)
        self.assertIsNone(brief['proposal'])

    def test_exact_duplicate_grain_deduplicated_not_inflated(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['rows'].append(copy.deepcopy(self.meta(snapshot)['rows'][0]))
        brief = self.plan(snapshot)
        self.assertEqual(brief['observations']['a1']['spend'], '10')
        self.assertEqual(brief['observations']['a1']['registrations'], '1')
        self.assertEqual(brief['report_coverage']['duplicate_rows_removed'], 1)

    def test_conflicting_duplicate_grain_fail_closed_metrics(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['rows'].append(self.row('a1', '99', '1'))
        brief = self.plan(snapshot)
        self.assertEqual(brief['observations'], {})
        self.assertIn('report_metrics_unavailable_or_inconsistent', brief['blockers'])
        self.assertNotEqual(brief['state'], 'ready_for_review')

    def test_invalid_nan_or_negative_metrics_fail_closed(self):
        for spend in ['NaN', '-1', True]:
            snapshot = copy.deepcopy(self.snapshot)
            self.meta(snapshot)['rows'][0]['spend'] = spend
            with self.subTest(spend=spend):
                self.assertEqual(self.plan(snapshot)['observations'], {})

    def test_current_partial_day_excluded(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['rows'].append(self.row('a1', '999', '99', '2026-10-17'))
        brief = self.plan(snapshot)
        self.assertEqual(brief['observations']['a1']['spend'], '10')
        self.assertEqual(brief['report_coverage']['current_partial_rows_excluded'], 1)

    def test_partial_day_only_cannot_be_reported_as_full_day_zero(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['since'] = '2026-10-17'
        self.meta(snapshot)['rows'] = []
        brief = self.plan(snapshot)
        self.assertEqual(brief['observations'], {})
        self.assertIn('report_metrics_unavailable_or_inconsistent', brief['blockers'])

    def test_creative_missing_does_not_guess_from_names(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['creative_inventory']['verified'] = False
        brief = self.plan(snapshot)
        self.assertEqual(brief['inventory'], [])
        self.assertEqual(brief['kind'], 'creative_inventory_brief')
        self.assertIsNone(brief['question'])

    def test_missing_media_hash_requires_inventory_brief(self):
        snapshot = copy.deepcopy(self.snapshot)
        del self.meta(snapshot)['creative_inventory']['ads'][1]['creative']['object_story_spec']['link_data']['image_hash']
        brief = self.plan(snapshot)
        self.assertIsNone(brief['proposal'])
        self.assertEqual(len(brief['inventory']), 2)

    def test_concentration_only_poses_question_holding_baseline_copy(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['creative_inventory']['ads'][1]['creative']['object_story_spec']['link_data']['message'] = 'קופי אחר'
        brief = self.plan(snapshot)
        self.assertEqual(brief['proposal']['basis'], 'observed_spend_distribution_not_creative_quality')
        self.assertEqual(brief['proposal']['copy_to_hold']['primary_texts'], ['הקולקציה החדשה בדרך'])
        self.assertEqual(brief['proposal']['candidate_image_hash'], 'hash2')

    def test_no_spend_distribution_evidence_cannot_pick_different_copy(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['creative_inventory']['ads'][1]['creative']['object_story_spec']['link_data']['message'] = 'קופי אחר'
        self.meta(snapshot)['rows'] = []
        self.assertIsNone(self.plan(snapshot)['proposal'])

    def test_shared_tracking_blocks_experiment_and_references_exact_audit_case(self):
        snapshot = copy.deepcopy(self.snapshot)
        for ad in self.meta(snapshot)['creative_inventory']['ads']:
            ad['creative']['url_tags'] = ''
            ad['creative']['object_story_spec']['link_data']['link'] = 'https://roseryjewelry.com/?utm_content=same&utm_source=facebook'
        brief = self.plan(snapshot)
        self.assertTrue(brief['blocked_by_tracking'])
        self.assertEqual(brief['tracking_configuration_status'], 'collision')
        self.assertTrue(brief['dependency_case_id'].startswith('tracking-collision-'))
        self.assertTrue(brief['dependency_fingerprint'])
        self.assertEqual(brief['state'], 'inventory_only_requires_evidence')
        self.assertEqual(brief['proposal']['destination_to_hold'], 'https://roseryjewelry.com/')

    def test_fixed_url_tag_readback_clears_configuration_only(self):
        snapshot = copy.deepcopy(self.snapshot)
        for ad in self.meta(snapshot)['creative_inventory']['ads']:
            ad['creative']['object_story_spec']['link_data']['link'] = 'https://roseryjewelry.com/launch?discount=NEW&utm_content='+ad['id']
            ad['creative']['url_tags'] = 'ad_id='+ad['id']
        brief = self.plan(snapshot)
        self.assertFalse(brief['blocked_by_tracking'])
        self.assertEqual(brief['tracking_configuration_status'], 'unique_in_read_configuration')
        self.assertFalse(brief['site_parameter_persistence_verified'])
        self.assertFalse(brief['measurement_verified'])
        self.assertFalse(brief['unique_registrations_verified'])
        self.assertEqual(brief['proposal']['destination_to_hold'], 'https://roseryjewelry.com/launch?discount=NEW')
        self.assertEqual(brief['proposal']['tracking_contract']['observed_arms'][0]['url_tags'], 'ad_id=a1')
        self.assertFalse(brief['published'])

    def test_tracking_macros_unknown_not_assumed_runtime_expansion(self):
        snapshot = copy.deepcopy(self.snapshot)
        for ad in self.meta(snapshot)['creative_inventory']['ads']:
            ad['creative']['url_tags'] = 'ad_id={{ad.id}}'
        brief = self.plan(snapshot)
        self.assertTrue(brief['blocked_by_tracking'])
        self.assertEqual(brief['tracking_configuration_status'], 'unknown')
        self.assertTrue(brief['dependency_case_id'].startswith('experiment-tracking-unknown-'))

    def test_unknown_tag_field_is_not_assumed_clean(self):
        snapshot = copy.deepcopy(self.snapshot)
        del self.meta(snapshot)['creative_inventory']['ads'][0]['creative']['url_tags']
        brief = self.plan(snapshot)
        self.assertTrue(brief['blocked_by_tracking'])
        self.assertEqual(brief['tracking_configuration_status'], 'unknown')

    def test_asset_feed_fields_are_actual_arrays(self):
        snapshot = copy.deepcopy(self.snapshot)
        self.meta(snapshot)['creative_inventory']['ads'][0]['creative'] = {'id': 'feedcreative',
            'asset_feed_spec': {'bodies': [{'text': 'טקסט מקור'}], 'titles': [{'text': 'כותרת מקור'}],
                                'images': [{'hash': 'feedhash'}], 'link_urls': [{'website_url': 'https://roseryjewelry.com/'}]}}
        brief = self.plan(snapshot)
        asset = brief['inventory'][0]
        self.assertEqual(asset['copy']['primary_texts'], ['טקסט מקור'])
        self.assertEqual(asset['provider_image_hashes'], ['feedhash'])

    def test_foreign_account_and_stale_source_fail_closed(self):
        for change in ['account', 'stale']:
            snapshot = copy.deepcopy(self.snapshot)
            if change == 'account':
                self.meta(snapshot)['account_id'] = 'other'
            else:
                snapshot['sources']['meta']['last_success_at'] = '2026-10-16T11:59:00Z'
            with self.subTest(change=change):
                self.assertEqual(self.plan(snapshot)['inventory'], [])


if __name__ == '__main__':
    unittest.main()
