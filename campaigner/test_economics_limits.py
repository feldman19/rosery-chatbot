import unittest
from economics_limits import allowable_cost,fixed_horizon_cohort

class EconomicsLimitsTest(unittest.TestCase):
 def test_duplicate_order_rejected(self):
  c=[{'customer_key':'old','first_purchase_at':'2026-01-01T00:00:00+00:00'}]
  o={'order_id':'1','customer_key':'old','ordered_at':'2026-01-01T00:00:00+00:00','contribution_before_ads':100}
  with self.assertRaises(ValueError):fixed_horizon_cohort(c,[o,o],90,'2026-04-02T00:00:00+00:00',coverage_complete=True)
 def test_estimates_never_become_verified_limit(self):
  x=allowable_cost(165,30,costs_verified=False,window_complete=True)
  self.assertEqual(x['scenario_allowable_cost'],135);self.assertIsNone(x['verified_allowable_cost'])
 def test_censoring_refunds_boundary_and_retention_cost(self):
  c=[{'customer_key':'old','first_purchase_at':'2026-01-01T00:00:00+00:00'}, {'customer_key':'young','first_purchase_at':'2026-03-01T00:00:00+00:00'}]
  o=[{'order_id':'1','customer_key':'old','ordered_at':'2026-01-01T00:00:00+00:00','contribution_before_ads':100}, {'order_id':'2','customer_key':'old','ordered_at':'2026-02-01T00:00:00+00:00','contribution_before_ads':-20,'retention_marketing_cost':5}, {'order_id':'3','customer_key':'old','ordered_at':'2026-04-01T00:00:00+00:00','contribution_before_ads':999}, {'order_id':'4','customer_key':'young','ordered_at':'2026-03-01T00:00:00+00:00','contribution_before_ads':999}]
  x=fixed_horizon_cohort(c,o,90,'2026-04-02T00:00:00+00:00',coverage_complete=True)
  self.assertEqual(x['observed_contribution_ltv'],75);self.assertEqual(x['mature_customers'],1);self.assertEqual(x['excluded_immature_customers'],1)
  self.assertIsNone(fixed_horizon_cohort(c,o,90,'2026-04-02T00:00:00+00:00')['observed_contribution_ltv'])
if __name__=='__main__':unittest.main()
