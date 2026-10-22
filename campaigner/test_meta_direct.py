import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from meta_direct import APP_ID,ACCOUNT,LAUNCH,identity,configure,read_reports,graph,read_campaign_inventory,read_active_ads,read_campaign_insights,QUERY_SETTINGS


class MetaTests(unittest.TestCase):
    def creative_read(self,last):
        base={'id':'ad1','status':'ACTIVE','effective_status':'ACTIVE','adset_id':'set1'}
        replies=[{'data':[]},{'data':[]},{'id':LAUNCH,'account_id':ACCOUNT},{'data':[{'id':'set1'}]},{'data':[base]},last]
        with patch('meta_direct.identity',return_value={'currency':'ILS','timezone_name':'Asia/Jerusalem','timezone_offset_hours_utc':3}),patch('meta_direct.get_token',return_value='fake'),patch('meta_direct.graph',side_effect=replies):return read_reports('unused')

    def test_creative_inventory_is_separate_and_account_bound(self):
        r=self.creative_read({'data':[{'id':'ad1','creative':{'id':'creative1'}}]})
        self.assertTrue(r['inventory']['verified']);self.assertTrue(r['creative_inventory']['verified']);self.assertEqual(ACCOUNT,r['creative_inventory']['account_id'])

    def test_optional_creative_failure_never_erases_report(self):
        for last in (OSError(),{'data':[{'id':'other'}]},{'data':[],'paging':{'next':'https://evil.invalid'}}):
            r=self.creative_read(last);self.assertTrue(r['complete']);self.assertTrue(r['inventory']['verified']);self.assertFalse(r['creative_inventory']['verified'])

    def test_fixed_destination(self):
        with self.assertRaises(ValueError):graph('fake','https://evil.example')

    def test_account_inventory_includes_paused_sales_and_uses_fixed_cursor(self):
        row={'id':'123','account_id':ACCOUNT,'objective':'OUTCOME_SALES','status':'PAUSED'}
        replies=[{'data':[row],'paging':{'next':'https://evil.invalid','cursors':{'after':'safe'}}},{'data':[]}]
        with patch('meta_direct.graph',side_effect=replies) as mocked:
            result=read_campaign_inventory('fake')
        self.assertTrue(result['verified']);self.assertEqual(result['campaigns'],[row])
        self.assertEqual(mocked.call_args.args[1],'act_'+ACCOUNT+'/campaigns')
        self.assertEqual(mocked.call_args.args[2]['after'],'safe')

    def test_campaign_inventory_partial_or_cross_account_is_not_verified(self):
        for answer in ({'data':[{'id':'123','account_id':'other'}]}, {'data':[],'paging':{'next':'x'}}, {'data':'invalid'}):
            with patch('meta_direct.graph',return_value=answer):
                result=read_campaign_inventory('fake')
            self.assertFalse(result['verified']);self.assertEqual(result['campaigns'],[])

    def test_current_active_ads_preserve_parent_binding_and_scope(self):
        row={'id':'111','account_id':ACCOUNT,'effective_status':'ACTIVE','campaign':{'id':'222','effective_status':'ACTIVE'},'adset':{'id':'333','effective_status':'ACTIVE'}}
        with patch('meta_direct.graph',return_value={'data':[row]}) as call:
            result=read_active_ads('synthetic')
        self.assertTrue(result['verified']);self.assertEqual(result['ads'],[row])
        self.assertEqual(call.call_args.args[1],'act_'+ACCOUNT+'/ads')

    def test_inactive_or_unbound_active_inventory_not_declared_complete(self):
        for row in ({'id':'111','account_id':ACCOUNT,'effective_status':'PAUSED'}, {'id':'111','account_id':ACCOUNT,'effective_status':'ACTIVE'}, {'id':'111','account_id':'other','effective_status':'ACTIVE'}):
            with patch('meta_direct.graph',return_value={'data':[row]}):
                result=read_active_ads('synthetic')
            self.assertFalse(result['verified']);self.assertEqual(result['ads'],[])

    def test_wrong_app_rejected(self):
        with patch('meta_direct.graph',return_value={'id':'other'}):
            with self.assertRaises(ValueError):identity('fake')

    def test_broad_permissions_never_saved(self):
        replies=[{'id':APP_ID},{'data':[{'permission':p,'status':'granted'} for p in ['ads_read','ads_management']]}]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            with patch('meta_direct.graph',side_effect=replies):
                with self.assertRaises(ValueError):configure(temp,'x'*40)
            self.assertEqual(list(Path(temp).iterdir()),[])

    def test_account_mismatch(self):
        replies=[{'id':APP_ID},{'data':[{'permission':'ads_read','status':'granted'}]},{'account_id':'other'}]
        with patch('meta_direct.graph',side_effect=replies):
            with self.assertRaises(ValueError):identity('fake')

    def test_paging_never_follows_next_url(self):
        from datetime import datetime,timezone,timedelta
        day=datetime.now(timezone(timedelta(hours=3))).date().isoformat()
        row={'ad_id':'1','adset_id':'456','campaign_id':'2','date_start':day,'date_stop':day,'spend':'10'}
        replies=[{'data':[row],'paging':{'next':'https://evil.example/?access_token=fake','cursors':{'after':'cursor'}}},{'data':[]},{'data':[]},OSError()]
        with patch('meta_direct.identity',return_value={'currency':'ILS','timezone_name':'Asia/Jerusalem','timezone_offset_hours_utc':3}),patch('meta_direct.get_token',return_value='fake'),patch('meta_direct.graph',side_effect=replies) as call:
            result=read_reports('unused')
            self.assertTrue(result['complete']);self.assertEqual(len(result['rows']),1)
            self.assertEqual(call.call_args_list[1].args[1],'act_'+ACCOUNT+'/insights')
            self.assertEqual(call.call_args_list[1].args[2]['after'],'cursor')
    def test_campaign_insights_same_daily_settings_and_fixed_cursor(self):
        row={'campaign_id':'123','date_start':'2026-10-17','date_stop':'2026-10-17','spend':'10','actions':[]}
        replies=[{'data':[row],'paging':{'next':'https://evil.invalid','cursors':{'after':'safe'}}},{'data':[]},{'data':[row]}]
        with patch('meta_direct.graph',side_effect=replies) as call:result=read_campaign_insights('synthetic','2026-10-11','2026-10-18')
        self.assertTrue(result['verified']);self.assertTrue(result['complete']);self.assertEqual(result['rows'],[{**row,'metric_sources':{'delivery_present':True,'actions_present':True}}]);self.assertEqual(result['query_settings'],QUERY_SETTINGS);self.assertEqual(call.call_args.args[1],'act_'+ACCOUNT+'/insights');params=call.call_args.args[2];self.assertEqual(params['level'],'campaign');self.assertEqual(params['time_increment'],'1');self.assertEqual(params['use_unified_attribution_setting'],'true');self.assertEqual(call.call_args_list[1].args[2]['after'],'safe')
    def test_campaign_insights_empty_complete_is_preserved(self):
        with patch('meta_direct.graph',return_value={'data':[]}):result=read_campaign_insights('synthetic','2026-10-11','2026-10-18')
        self.assertTrue(result['verified']);self.assertEqual(result['rows'],[])
    def test_campaign_insights_invalid_grain_scope_and_cursor_fail_optional(self):
        row={'campaign_id':'123','date_start':'2026-10-17','date_stop':'2026-10-17','spend':'10'}
        answers=[{'data':[{**row,'account_id':'wrong'}]},{'data':[{**row,'date_stop':'2026-10-18'}]},{'data':[row,row]},{'data':'bad'},{'data':[row],'paging':{'next':'x'}},{'data':[{**row,'date_start':'2026-10-10','date_stop':'2026-10-10'}]}]
        for answer in answers:
            with self.subTest(answer=answer),patch('meta_direct.graph',return_value=answer):result=read_campaign_insights('synthetic','2026-10-11','2026-10-18');self.assertFalse(result['verified']);self.assertFalse(result['complete']);self.assertEqual(result['rows'],[])
    def test_campaign_insights_deadline_no_network(self):
        with patch('meta_direct.graph') as graph,patch('meta_direct.time.monotonic',return_value=100):result=read_campaign_insights('synthetic','2026-10-11','2026-10-18',deadline=99)
        self.assertFalse(result['verified']);graph.assert_not_called()
    def test_campaign_insights_failure_does_not_erase_main_ad_report(self):
        # Existing mocked primary report succeeds, optional later calls exhaust.
        result=self.creative_read({'data':[{'id':'ad1','creative':{'id':'creative1'}}]});self.assertTrue(result['verified']);self.assertTrue(result['complete']);self.assertFalse(result['campaign_insights']['verified'])
