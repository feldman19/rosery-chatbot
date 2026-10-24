import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from self_check import SelfCheck,brief_line,read_meta,read_shopify,gateway_reachable

class SelfCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=datetime(2026,10,24,4,29,tzinfo=timezone.utc)
        self.metrics=dict(spend=100,purchases=2,attributed_revenue=500,store_orders=3,store_revenue=600)
        self.direct=dict(self.metrics)
        self.report={'windows':{'1':dict(since='2026-10-23',until='2026-10-23',metrics=self.metrics)}}
        self.snapshot={'last_cycle_at':self.now.isoformat(),'sources':{k:{'last_success_at':self.now.isoformat(),'data':{}} for k in ('meta','shopify')}}
        self.check=SelfCheck(self.tmp.name,meta_reader=lambda *a:self.direct,shopify_reader=lambda *a:self.direct,
            token_checks={'report':lambda d:{'verified':True},'write':lambda d:{'verified':True}},
            gateway=lambda:True,rulebook=lambda:{'status':'available'},clock=lambda:self.now)
    def run_check(self):return self.check.run(self.snapshot,self.report)
    def items(self):return {c['id']:c for c in self.run_check()['checks']}
    def test_match(self):
        self.assertEqual(self.run_check()['status'],'ok')
        self.assertEqual(brief_line(self.check.latest()),'בדיקה עצמית: הכל תקין')
    def test_metrics(self):
        for key in self.metrics:
            identity=('shopify_' if key.startswith('store') else 'meta_')+key
            for value,status in ((self.metrics[key]+20,'fail'),(None,'warn'),(True,'warn'),(float('nan'),'warn')):
                with self.subTest(key=key,value=value):
                    self.direct=dict(self.metrics);self.direct[key]=value
                    self.assertEqual(self.items()[identity]['status'],status)
    def test_tolerance(self):
        self.metrics.update(spend=101,attributed_revenue=505)
        self.assertEqual(self.run_check()['status'],'ok')
        self.metrics['purchases']=2.01
        self.assertEqual(self.items()['meta_purchases']['status'],'fail')
    def test_zero(self):
        self.direct={k:0 for k in self.metrics};self.metrics.update(self.direct)
        self.assertEqual(self.run_check()['status'],'ok')
        self.metrics['spend']=.01
        self.assertEqual(self.items()['meta_spend']['status'],'fail')
    def test_network_warn_no_leaks(self):
        def broken(*a):raise OSError('PRIVATE')
        self.check.meta_reader=broken;self.check.shopify_reader=broken
        self.check.gateway=broken;self.check.rulebook=broken
        self.check.token_checks={'report':broken,'write':broken}
        result=self.run_check()
        self.assertEqual(result['status'],'warn');self.assertNotIn('PRIVATE',json.dumps(result))
    def test_date_binding(self):
        self.report['windows']['1']['since']='2026-10-22'
        self.assertEqual(self.items()['meta_spend']['status'],'warn')
    def test_freshness(self):
        for age,status in ((1799,'ok'),(1800,'fail'),(-1,'fail')):
            self.snapshot['sources']['meta']['last_success_at']=(self.now-timedelta(seconds=age)).isoformat()
            self.assertEqual(self.items()['meta_freshness']['status'],status)
        self.snapshot['sources']['shopify']['last_success_at']=None
        self.assertEqual(self.items()['shopify_freshness']['status'],'warn')
    def test_rejected_tokens(self):
        for name in ('report','write'):
            self.check.token_checks={name:lambda d:{'verified':False}}
            self.assertEqual(self.items()[name]['status'],'fail')
    def test_rulebook(self):
        self.check.rulebook=lambda:{'status':'unavailable'}
        self.assertEqual(self.items()['rulebook']['status'],'fail')
    def test_gateway_unknown(self):
        self.check.gateway=lambda:None
        self.assertEqual(self.items()['phone_gateway']['status'],'warn')
    def test_retention(self):
        for _ in range(33):self.run_check()
        self.assertEqual(len(self.check.history()),30)
    def test_schedule(self):
        self.snapshot['last_cycle_at']=None
        self.assertIsNone(self.check.run_if_due(self.snapshot,self.report))
        self.snapshot['last_cycle_at']=self.now.isoformat()
        self.assertIsNotNone(self.check.run_if_due(self.snapshot,self.report))
        self.assertIsNone(self.check.run_if_due(self.snapshot,self.report))
        self.now+=timedelta(minutes=1)
        self.assertIsNotNone(self.check.run_if_due(self.snapshot,self.report))
        self.assertIsNone(self.check.run_if_due(self.snapshot,self.report))
        self.now+=timedelta(days=1,hours=1)
        self.assertIsNotNone(self.check.run_if_due(self.snapshot,self.report))
    def test_meta_scope(self):
        import meta_direct as meta
        row={'account_id':meta.ACCOUNT,'date_start':'2026-10-23','date_stop':'2026-10-23','spend':'100',
            'actions':[{'action_type':'omni_purchase','value':'2'}],'action_values':[{'action_type':'omni_purchase','value':'500'}]}
        with patch.object(meta,'get_token',return_value='fake'),patch.object(meta,'graph',return_value={'data':[row]}) as graph:
            result=read_meta(self.tmp.name,'2026-10-23',meta.QUERY_SETTINGS)
            self.assertEqual(result['purchases'],2)
            self.assertEqual(graph.call_count,2)
            for call in graph.call_args_list:
                self.assertEqual(call.args[2]['level'],'account')
                self.assertEqual(json.loads(call.args[2]['time_range']),dict(since='2026-10-23',until='2026-10-23'))
            row['account_id']='wrong'
            self.assertRaises(ValueError,read_meta,self.tmp.name,'2026-10-23',meta.QUERY_SETTINGS)
    def test_shopify_scope(self):
        import shopify_direct as shop
        response={'shopifyqlQuery':{'tableData':{'rows':[{'day':'2026-10-23','orders':'3','total_sales':'600'}]}}}
        with patch.object(shop,'get_token',return_value='fake'),patch.object(shop,'graph',return_value=response) as graph:
            self.assertEqual(read_shopify(self.tmp.name,'2026-10-23')['store_orders'],3)
            self.assertIn('SINCE 2026-10-23 UNTIL 2026-10-23',graph.call_args.args[2]['q'])
            self.assertNotIn('mutation',graph.call_args.args[1])
    def test_tcp_only(self):
        with patch.dict('os.environ',{'ROSERY_PHONE_GATEWAY_HOST':'100.64.0.2'}),patch('self_check.socket.create_connection') as connect:
            self.assertTrue(gateway_reachable());connect.return_value.__enter__.return_value.send.assert_not_called()

    def test_meta_missing_and_incomplete_rows(self):
        import meta_direct as meta
        row={'account_id':meta.ACCOUNT,'date_start':'2026-10-23','date_stop':'2026-10-23','spend':'100'}
        with patch.object(meta,'get_token',return_value='fake'),patch.object(meta,'graph',return_value={'data':[row]}) as graph:
            result=read_meta(self.tmp.name,'2026-10-23',meta.QUERY_SETTINGS)
            self.assertIsNone(result['purchases'])
            self.assertIsNone(result['attributed_revenue'])
            graph.return_value={'data':[row],'paging':{'next':'unused'}}
            self.assertRaises(ValueError,read_meta,self.tmp.name,'2026-10-23',meta.QUERY_SETTINGS)
            graph.reset_mock()
            self.assertRaises(ValueError,read_meta,self.tmp.name,'2026-10-23',{})
            graph.assert_not_called()

    def test_missing_connections_and_rulebook_warn(self):
        self.check.token_checks={'report':lambda d:{'verified':False,'state':'missing_credential'},
                                 'write':lambda d:{'verified':False,'configured':False}}
        self.check.rulebook=lambda:{'status':'unavailable','detail':'[Errno 2] missing'}
        items=self.items()
        for key in ('report','write','rulebook'):self.assertEqual(items[key]['status'],'warn')

if __name__=='__main__':unittest.main()
