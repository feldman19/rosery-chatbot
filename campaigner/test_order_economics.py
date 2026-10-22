import copy
import unittest
from business_context import load_rosery_dossier
from order_economics import order_economics

class OrderEconomicsTests(unittest.TestCase):
    def setUp(self):
        self.d = load_rosery_dossier(include_owner_financials=True)
    def test_current_sources(self):
        result = order_economics(self.d)
        self.assertEqual(result['status'], 'conditional_partial_order_economics')
        self.assertEqual(len(result['segments']), 2)
        new = next(x for x in result['segments'] if x['segment']=='New')
        self.assertEqual(new['orders'],59)
        self.assertAlmostEqual(new['residual_if_sales_include_18pct_vat'],178.50,places=2)
        self.assertIsNone(result['cac_limit'])
        self.assertFalse(result['budget_recommendation_allowed'])
        for item in new['components'].values():
            self.assertTrue(item['source_ref']); self.assertTrue(item['observed_date'])
    def test_missing_costs_never_become_zero(self):
        d=copy.deepcopy(self.d);d['owner_evidence']['current_economics']['rows'][0][5]=None
        result=order_economics(d)
        self.assertEqual(result['status'],'missing_evidence')
        self.assertFalse(result['budget_recommendation_allowed'])
        self.assertIsNone(result['verified_break_even_per_order'])
    def test_zero_tax_does_not_resolve_vat(self):
        result=order_economics(self.d)
        self.assertIn('verified_revenue_and_cost_vat_basis',result['missing'])
        self.assertTrue(all(x['verified_contribution'] is None for x in result['segments']))
    def test_engine_integration(self):
        from action_recommendations import build_recommendations
        report={'account_id':'393059510164294','windows':{'7':{'adsets':[{'id':'test','name':'test'}]}}}
        result=build_recommendations(report,self.d)
        self.assertEqual(len(result['order_economics']['segments']),2)
        missing=result['recommendations'][0]['missing_data']
        self.assertNotIn('approved_current_profit_target',missing)
        self.assertIn('economics:actual_shipping_processing_and_return_costs',missing)
        self.assertFalse(result['live_action_allowed'])

    def test_configured_costs_are_not_actual(self):
        result=order_economics(self.d)
        costs=result['segments'][0]['configured_variable_cost_cases']
        self.assertFalse(costs['actual_costs_verified'])
        self.assertFalse(costs['sales_refunds_deducted_again'])
        self.assertEqual(len(costs['scenarios']),3)
        self.assertEqual(costs['components'][0]['per_order'],21)
        self.assertIsNone(result['cac_limit'])
        self.assertTrue(all(x['vat_basis']=='unknown' for x in costs['components']))
        self.assertTrue(all(x['residual_after_configured_costs']<178.50 for x in costs['scenarios']))

    def test_missing_configuration_is_not_zero(self):
        d=copy.deepcopy(self.d);d['owner_evidence'].pop('configured_costs')
        costs=order_economics(d)['segments'][0]['configured_variable_cost_cases']
        self.assertEqual(costs['scenarios'],[])
        self.assertEqual(costs['status'],'missing_configuration')

    def test_no_financial_evidence(self):
        self.assertEqual(order_economics({})['segments'],[])

if __name__=='__main__':unittest.main()
