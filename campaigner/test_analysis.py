import unittest
from analysis import summarize, contribution


class EvidenceTests(unittest.TestCase):
    def test_no_spend_is_untested_not_loser(self):
        report = summarize({'synthetic': True, 'currency': 'ILS', 'window': 'demo',
                            'ads': [{'spend': 0, 'attributed_registrations': 0, 'landing_page_views': 0}]})
        self.assertEqual(report['ads'][0]['assessment'], 'untested')
        self.assertIsNone(report['ads'][0]['cpl'])
        self.assertFalse(report['ads'][0]['scale_ready'])

    def test_live_data_refused(self):
        with self.assertRaises(ValueError):
            summarize({'synthetic': False})

    def test_missing_cost_does_not_invent_profit(self):
        result = contribution({'product_revenue': 300}, {'vat_rate': '.18', 'packaging': 4})
        self.assertIsNone(result['value'])
        self.assertIn('courier', result['missing'])

    def test_refunds_shipping_vat_and_costs(self):
        result = contribution({'product_revenue': 354, 'refunds': 118, 'shipping_revenue': 23.6},
                              {'vat_rate': '.18', 'product_cost': 40, 'packaging': 4,
                               'courier': 25, 'processing': 6})
        self.assertEqual(result['value'], '145.00')
        self.assertFalse(result['verified_profit'])


if __name__ == '__main__':
    unittest.main()
