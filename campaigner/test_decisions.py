import unittest
from datetime import datetime,timezone
from decisions import build_report,derive_economics
from meta_direct import ACCOUNT,LAUNCH


class DecisionTests(unittest.TestCase):
    def source(self):
        return {'sources':{'meta':{'last_success_at':'2026-10-17T12:00:00+00:00','error':None,'data':{'verified':True,'complete':True,'currency':'ILS','account_id':ACCOUNT,'today':'2026-10-17','rows':[
            {'campaign_id':LAUNCH,'date_start':'2026-10-16','spend':'20','actions':[{'action_type':'omni_complete_registration','value':'4'},{'action_type':'complete_registration','value':'4'}]},
            {'campaign_id':LAUNCH,'date_start':'2026-10-17','spend':'90'},
            {'campaign_id':'other','date_start':'2026-10-16','spend':'500'}]}}}}

    def test_full_days_scope_and_no_double_count(self):
        d=build_report(self.source(),{},datetime(2026,10,17,12,1,tzinfo=timezone.utc))
        self.assertEqual(d['windows'][0]['spend'],'20');self.assertEqual(d['windows'][0]['registrations_attributed'],4)
        self.assertEqual(d['windows'][0]['cpl_attributed'],'5')
        self.assertFalse(d['live_action_allowed']);self.assertEqual(d['proposals'],[])
        self.assertNotIn('goal',[b['id'] for b in d['blockers']])

    def test_stale_never_shows_current_budget(self):
        source=self.source();source['sources']['meta']['data']['inventory']={'verified':True,'campaign':{'id':LAUNCH,'account_id':ACCOUNT,'daily_budget':'12000'}}
        d=build_report(source,{},datetime(2026,10,18,tzinfo=timezone.utc))
        self.assertIsNone(d['current']);self.assertFalse(d['inventory_verified'])

    def test_scenarios_are_not_measured_target(self):
        source={'sources':{'shopify':{'last_success_at':datetime.now(timezone.utc).isoformat(),'data':{'shop':'21cc82-3.myshopify.com','currency':'ILS','economics_28d':{'rows':[{'orders':'10','net_sales':'2000','shipping_charges':'100','cost_of_goods_sold':'200','net_sales_with_cost_recorded':'1990','net_sales_without_cost_recorded':'10'}]}}}}}
        e=derive_economics(source)
        self.assertEqual(e['state'],'incomplete_cost_estimate');self.assertIsNone(e['target_cpl'])
        self.assertFalse(e['verified_profit']);self.assertEqual(e['scenarios'],[])
        self.assertNotIn('estimated_contribution_per_order',e)
        self.assertIsNone(e['recommended_daily_budget'])

    def test_wrong_account_cannot_supply_windows(self):
        source=self.source();source['sources']['meta']['data']['account_id']='other'
        self.assertEqual(build_report(source,{})['windows'],[])

    def test_missing_cost_fields_never_become_zero_cost(self):
        source={'sources':{'shopify':{'data':{'economics_28d':{'rows':[{'orders':'10','net_sales':'1000'}]}}}}}
        e=derive_economics(source)
        self.assertEqual(e['scenarios'],[]);self.assertNotIn('estimated_contribution_per_order',e)
