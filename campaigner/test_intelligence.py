from copy import deepcopy
import unittest
from intelligence import portfolio_report, contribution_ledger


def snapshot(platform, account, metrics, capture='2026-10-16T12:00:00Z', currency='ILS', synthetic=False):
    return {'id':account+capture,'hash':'h-'+account+capture,'imported_at':capture,
            'payload':{'platform':platform,'account_id':account,'currency':currency,
                       'window_start':'2026-10-14T21:00:00Z','window_end':'2026-10-15T21:00:00Z',
                       'synthetic':synthetic,'captured_at':capture,'records':[{'entity_id':account,'metrics':metrics}]},
            'assessment':{'source_verified':False,'freshness':'recent_import_not_live',
                          'completeness':'complete','timezone_verified':False}}


class IntelligenceTests(unittest.TestCase):
    def test_no_summing_reimports(self):
        old=snapshot('meta','a',{'spend_minor':1000})
        new=snapshot('meta','a',{'spend_minor':2000},capture='2026-10-16T13:00:00Z')
        shop=snapshot('shopify','s',{'revenue_minor':8000,'orders':2})
        report=portfolio_report([old,new,shop])['reports'][0]
        self.assertEqual(report['spend_minor'],2000)
        self.assertIsNone(report['descriptive_mer'])
        self.assertEqual(len(report['evidence']),2)
        self.assertFalse(report['live_action_allowed'])
        self.assertIn('contribution_costs_missing',report['blocked_by'])

    def test_platform_attributed_value_not_added_to_store_sales(self):
        report=portfolio_report([snapshot('meta','a',{'spend_minor':1000,'revenue_minor':7000}),snapshot('shopify','s',{'revenue_minor':8000})])['reports'][0]
        self.assertEqual(report['store_total_sales_minor'],8000)

    def test_currencies_windows_and_synthetic_not_mixed(self):
        a=snapshot('meta','a',{'spend_minor':1000})
        b=snapshot('shopify','s',{'revenue_minor':8000},currency='USD')
        c=snapshot('meta','a',{'spend_minor':1000},synthetic=True)
        self.assertEqual(len(portfolio_report([a,b,c])['reports']),3)
        b=deepcopy(a); b['payload']['window_end']='2026-10-16T21:00:00Z'
        self.assertEqual(len(portfolio_report([a,b])['reports']),2)

    def test_multiple_stores_do_not_invent_mapping(self):
        r=portfolio_report([snapshot('meta','a',{'spend_minor':1000}),snapshot('shopify','s',{'revenue_minor':5000}),snapshot('shopify','s2',{'revenue_minor':5000})])['reports'][0]
        self.assertIsNone(r['descriptive_mer'])
        self.assertIn('multiple_stores_no_mapping',r['blocked_by'])

    def test_missing_spend_not_zero_efficiency(self):
        r=portfolio_report([snapshot('shopify','s',{'revenue_minor':5000})])['reports'][0]
        self.assertIsNone(r['spend_minor']); self.assertIsNone(r['descriptive_mer'])

    def test_stale_partial_evidence_blocks(self):
        s=snapshot('meta','a',{'spend_minor':1000})
        s['assessment'].update(freshness='stale',completeness='partial')
        r=portfolio_report([s])['reports'][0]
        self.assertIn('stale_or_future_evidence',r['blocked_by'])
        self.assertIn('partial_or_unknown_window',r['blocked_by'])

    def test_contribution_is_not_net_profit_and_missing_not_invented(self):
        order={'currency':'ILS','product_collected_minor':30000,'shipping_collected_minor':2000,'refunds_minor':1000,'tax_remaining_minor':4000}
        self.assertIsNone(contribution_ledger(order,{})['contribution_before_ads_minor'])
        costs={k:{'net_minor':100,'currency':'ILS','basis':'verified','source_ref':'source-'+k} for k in ('product','packaging','courier','payment','other_variable')}
        r=contribution_ledger(order,costs)
        self.assertEqual(r['contribution_before_ads_minor'],26500)
        self.assertTrue(r['declared_cost_inputs_verified']); self.assertFalse(r['verified_net_profit'])
        costs['courier']['basis']='estimate'
        self.assertFalse(contribution_ledger(order,costs)['declared_cost_inputs_verified'])
        costs['courier']['currency']='USD'
        with self.assertRaises(ValueError): contribution_ledger(order,costs)

    def test_total_plus_child_is_not_added(self):
        s=snapshot('meta','a',{'spend_minor':100})
        s['payload']['records'].append({'entity_id':'child','metrics':{'spend_minor':100}})
        r=portfolio_report([s])['reports'][0]
        self.assertIsNone(r['spend_minor'])
        self.assertIn('account_grain_unverified',r['blocked_by'])

    def test_missing_order_count_stays_missing(self):
        r=portfolio_report([snapshot('shopify','s',{'revenue_minor':100})])['reports'][0]
        self.assertIsNone(r['store_orders'])

    def test_single_ad_not_mislabeled_account_total(self):
        s=snapshot('meta','a',{'spend_minor':100})
        s['payload']['records'][0]['entity_id']='ad1'
        self.assertIsNone(portfolio_report([s])['reports'][0]['spend_minor'])

    def test_valid_and_ambiguous_accounts_not_partial_total(self):
        good=snapshot('meta','a',{'spend_minor':100})
        bad=snapshot('meta','b',{'spend_minor':200})
        bad['payload']['records'][0]['entity_id']='ad1'
        self.assertIsNone(portfolio_report([good,bad])['reports'][0]['spend_minor'])

    def test_missing_account_metric_does_not_make_partial_total(self):
        a=snapshot('meta','a',{'spend_minor':100})
        b=snapshot('meta','b',{'clicks':4})
        r=portfolio_report([a,b])['reports'][0]
        self.assertIsNone(r['spend_minor'])
        self.assertIn('incomplete_metric_coverage',r['blocked_by'])
