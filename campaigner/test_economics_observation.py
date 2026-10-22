import unittest
from copy import deepcopy
from datetime import datetime,timezone
from economics_observation import recorded_economics
class EconomicsObservationTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,10,21,9,25,tzinfo=timezone.utc)
  self.snapshot={'sources':{'shopify':{'last_success_at':'2026-10-21T09:21:31+00:00','data':{'verified':True,'shop':'21cc82-3.myshopify.com','currency':'ILS','since':'2026-10-14','until':'2026-10-20','economics_28d':{'rows':[dict(orders=458,net_sales='110374.93',shipping_charges=9000,cost_of_goods_sold='18831.30',net_sales_with_cost_recorded='114032.56',net_sales_without_cost_recorded='115.57')]}}}}}
 def test_recorded_numbers_preserved_without_false_coverage_or_dates(self):
  r=recorded_economics(self.snapshot,self.now)
  self.assertEqual(r['values']['cost_of_goods_sold'],'18831.30');self.assertEqual(r['cost_reconciliation']['difference'],'3773.20')
  self.assertEqual(r['freshness'],'fresh');self.assertIsNone(r['period']['since']);self.assertIsNone(r['period']['until']);self.assertFalse(r['period']['bounds_verified']);self.assertFalse(r['decision_authority']);self.assertFalse(r['verified_profit']);self.assertFalse(r['cost_reconciliation']['coverage_verified'])
 def test_wrong_shop_or_currency_never_leaks_metrics(self):
  for field,value in [('shop','other.myshopify.com'),('currency','USD'),('verified',False)]:
   snapshot=deepcopy(self.snapshot);snapshot['sources']['shopify']['data'][field]=value
   self.assertEqual(recorded_economics(snapshot,self.now)['values'],{})
 def test_stale_error_and_invalid_values(self):
  snapshot=deepcopy(self.snapshot);snapshot['sources']['shopify']['error']='provider failed'
  self.assertEqual(recorded_economics(snapshot,self.now)['freshness'],'stale')
  for value in ('NaN',None,True,'not-money'):
   snapshot=deepcopy(self.snapshot);snapshot['sources']['shopify']['data']['economics_28d']['rows'][0]['net_sales']=value
   self.assertEqual(recorded_economics(snapshot,self.now)['values'],{})
 def test_matching_arithmetic_does_not_prove_coverage(self):
  self.snapshot['sources']['shopify']['data']['economics_28d']['rows'][0]['net_sales']='114148.13'
  r=recorded_economics(self.snapshot,self.now);self.assertEqual(r['cost_reconciliation']['difference'],'0.00');self.assertFalse(r['cost_reconciliation']['coverage_verified'])

 def test_malformed_source_stays_unavailable(self):
  for snapshot in (None,{'sources':[]},{'sources':{'shopify':'bad'}},{'sources':{'shopify':{'data':[]}}}):
   self.assertEqual(recorded_economics(snapshot,self.now)['values'],{})
