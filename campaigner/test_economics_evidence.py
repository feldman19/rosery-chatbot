import json
import unittest
from copy import deepcopy
from pathlib import Path
from economics_evidence import current_recorded_economics
from business_context import load_rosery_dossier


class EconomicsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((Path(__file__).resolve().parent.parent / 'outputs' /
            'rosery-economics-week-2026-10-20.json').read_text(encoding='utf-8'))

    def test_real_aggregate_is_not_spend_authority(self):
        result = current_recorded_economics(self.data, 'test-source')
        self.assertEqual(result['currency'], 'ILS')
        self.assertEqual(len(result['segments']), 2)
        self.assertIsNone(result['spend_target'])
        self.assertFalse(result['automatic_action_allowed'])

    def test_wrong_shop_currency_and_invalid_orders_rejected(self):
        for field, value in [('shopDomain', 'another.myshopify.com'),
                             ('chartHint', {'currencyCode': 'USD'})]:
            data = deepcopy(self.data)
            data[field] = value
            self.assertIsNone(current_recorded_economics(data, 'test-source'))
        for value in ['NaN', '1.5', '0']:
            data = deepcopy(self.data)
            data['rows'][0][1] = value
            self.assertIsNone(current_recorded_economics(data, 'test-source'))

    def test_owner_only_and_no_automatic_target(self):
        self.assertNotIn('economics_evidence', load_rosery_dossier())
        owner = load_rosery_dossier(include_owner_financials=True)
        self.assertIsNone(owner['economics_evidence']['spend_target'])
        self.assertEqual(owner['economics_evidence']['current_recorded']['currency'], 'ILS')


if __name__ == '__main__':
    unittest.main()
