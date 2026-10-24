import unittest
from datetime import date
from unittest.mock import patch

from audience_classification import classify_audience
from meta_direct import enrich_audience_frequency, read_active_ads, ACCOUNT
from rule_engine import evaluate_ad
from test_rule_engine import BOOK, ad


class AudienceTests(unittest.TestCase):
    def test_targeting_cases(self):
        cases = [({}, 'prospecting'),
                 ({'age_min': 18, 'age_max': 65, 'targeting_automation': {'advantage_audience': 1}}, 'prospecting'),
                 ({'custom_audiences': [{'id': '1'}]}, 'retargeting'),
                 ({'excluded_custom_audiences': [{'id': '1'}]}, 'prospecting'),
                 ({'product_audience_specs': [{'product_set_id': '1'}]}, 'retargeting'),
                 (None, 'unknown')]
        for targeting, expected in cases:
            with self.subTest(targeting=targeting):
                self.assertEqual(classify_audience(targeting)['audience_type'], expected)

    def test_broad_catalog_uses_real_threshold(self):
        row = ad('1', 300, 2, 900, campaign_name='catalog', objective='PRODUCT_CATALOG_SALES',
                 **classify_audience({}))
        result = evaluate_ad(row, {'days': 3}, BOOK)
        self.assertFalse(result['retargeting'])
        self.assertEqual(2.6, result['thresholds']['fail_below'])
        row.update(classify_audience({'custom_audiences': [{'id': '1'}]}))
        self.assertEqual(5.2, evaluate_ad(row, {'days': 3}, BOOK)['thresholds']['fail_below'])

    def test_unknown_falls_back_to_name_and_explains(self):
        row = ad('1', 300, 2, 900, campaign_name='catalog')
        result = evaluate_ad(row, {'days': 3}, BOOK)
        self.assertTrue(result['audience_fallback'])
        self.assertTrue(result['retargeting'])
        self.assertIn('לפי השם, כי הטירגוט לא נקרא', result['data_does_not_support'])
        row.update(campaign_name='sales', objective='PRODUCT_CATALOG_SALES')
        self.assertFalse(evaluate_ad(row, {'days': 3}, BOOK)['retargeting'])

    def test_inventory_requests_targeting(self):
        with patch('meta_direct.graph', return_value={'data': []}) as graph:
            self.assertTrue(read_active_ads('fake')['verified'])
        self.assertIn('targeting', graph.call_args.args[2]['fields'])

    def test_weekly_frequency_is_not_daily_sum(self):
        active = {'ads': [{'adset': {'id': '1'}}, {'adset': {'id': '2'}}]}
        rows = [{'adset_id': '1', 'frequency': '2.83', 'reach': '100',
                 'date_start': '2026-10-17', 'date_stop': '2026-10-23'}]
        with patch('meta_direct.graph', return_value={'data': rows}) as graph:
            enrich_audience_frequency('fake', active, date(2026, 10, 24), float('inf'))
        self.assertEqual('2.83', active['ads'][0]['adset']['audience_frequency_7d']['frequency'])
        self.assertIsNone(active['ads'][1]['adset']['audience_frequency_7d']['frequency'])
        self.assertEqual('adset', graph.call_args.args[2]['level'])
        self.assertEqual('all_days', graph.call_args.args[2]['time_increment'])

    def test_failed_frequency_stays_unknown(self):
        active = {'ads': [{'adset': {'id': '1'}}]}
        with patch('meta_direct.graph', side_effect=ValueError('unavailable')):
            enrich_audience_frequency('fake', active, date(2026, 10, 24), float('inf'))
        self.assertNotIn('audience_frequency_7d', active['ads'][0]['adset'])

    def test_report_carries_parent_targeting_to_ads_and_adsets(self):
        from test_management_report import ManagementReportTests
        fixture = ManagementReportTests()
        fixture.setUp()
        fixture.configuration(campaign_status='ACTIVE')
        data = fixture.snapshot['sources']['meta']['data']
        group = data['inventory']['adsets'][0]
        group.update(targeting={'excluded_custom_audiences': [{'id': '1'}]},
                     audience_frequency_7d={'frequency': '2.83', 'since': '2026-10-11', 'until': '2026-10-17'})
        for kind in ('ads', 'adsets'):
            row = fixture.report()['windows']['3'][kind][0]
            self.assertEqual('prospecting', row['audience_type'])
            self.assertEqual(2.83, row['frequency_7d'])
            self.assertIsNone(row['buyer_share']['new_share'])
            self.assertIn('utm_content', row['buyer_share']['reason'])
        del group['targeting']
        self.assertEqual('unknown', fixture.report()['windows']['3']['ads'][0]['audience_type'])

    def test_active_audience_survives_newer_inventory_timestamp(self):
        from test_management_report import ManagementReportTests
        fixture = ManagementReportTests()
        fixture.setUp()
        fixture.configuration(campaign_status='ACTIVE')
        data = fixture.snapshot['sources']['meta']['data']
        data['active_ads'] = {
            'verified': True, 'complete': True, 'account_id': ACCOUNT,
            'checked_at': fixture.stamp,
            'ads': [{'id': 'ad1', 'account_id': ACCOUNT,
                     'campaign': {'id': 'sales1'},
                     'adset': {'id': 'set1', 'targeting': {},
                               'audience_frequency_7d': {'frequency': '2.83'}}}]}
        row = fixture.report()['windows']['3']['ads'][0]
        self.assertEqual('prospecting', row['audience_type'])
        self.assertEqual(2.83, row['frequency_7d'])


if __name__ == '__main__':
    unittest.main()
