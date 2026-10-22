"""Pure synthetic report tests; no provider I/O."""
from datetime import datetime,timedelta,timezone
from copy import deepcopy
import unittest
from management_report import build_report,ACCOUNT,SHOP

class ManagementReportTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,18,12,tzinfo=timezone.utc);self.stamp=self.now.isoformat();days=[(self.now.date()-timedelta(days=x)).isoformat() for x in range(1,8)]
        rows=[{'ad_id':'ad1','campaign_id':'sales1','campaign_name':'Sales historical name','date_start':day,'date_stop':day,'spend':'10','actions':[{'action_type':'offsite_conversion.fb_pixel_purchase','value':'1'},{'action_type':'omni_purchase','value':'3'},{'action_type':'offsite_conversion.fb_pixel_complete_registration','value':'2'},{'action_type':'omni_complete_registration','value':'4'},{'action_type':'complete_registration','value':'4'},{'action_type':'landing_page_view','value':'5'}],'action_values':[{'action_type':'offsite_conversion.fb_pixel_purchase','value':'50'},{'action_type':'omni_purchase','value':'150'}]} for day in days]
        self.snapshot={'collection_active':True,'sources':{'meta':{'last_success_at':self.stamp,'data':{'verified':True,'complete':True,'account_id':ACCOUNT,'currency':'ILS','timezone':'Asia/Jerusalem','today':'2026-10-18','since':'2026-10-11','until':'2026-10-18','rows':rows,'account_campaigns':{'verified':True,'complete':True,'account_id':ACCOUNT,'checked_at':self.stamp,'campaigns':[{'id':'sales1','account_id':ACCOUNT,'name':'Current sales name','status':'PAUSED','effective_status':'PAUSED','objective':'OUTCOME_SALES','daily_budget':'12000','lifetime_budget':'0'}]}}},'shopify':{'last_success_at':self.stamp,'data':{'verified':True,'shop':SHOP,'currency':'ILS','since':'2026-10-11','until':'2026-10-17','table':{'columns':[{'name':x} for x in ('day','orders','total_sales')],'rows':[{'day':day,'orders':'3','total_sales':'30'} for day in days]}}}}}
    def report(self):return build_report(self.snapshot,now=self.now)
    def test_canonical_actions_do_not_sum_overlapping_purchase_or_registration(self):
        result=self.report();metrics=result['windows']['7']['metrics'];self.assertEqual(metrics['spend'],70);self.assertEqual(metrics['purchases'],7);self.assertEqual(metrics['registrations'],14);self.assertEqual(metrics['attributed_revenue'],350);self.assertEqual(metrics['roas'],5);self.assertEqual(metrics['landing_page_views'],35);self.assertIsNone(metrics['cost_per_registration']);self.assertEqual(result['freshness']['state'],'fresh')
    def test_partial_current_day_excluded(self):
        row=deepcopy(self.snapshot['sources']['meta']['data']['rows'][0]);row.update(date_start='2026-10-18',date_stop='2026-10-18',spend='999');self.snapshot['sources']['meta']['data']['rows'].append(row)
        report=self.report();self.assertEqual(report['windows']['1']['until'],'2026-10-17');self.assertEqual(report['windows']['1']['metrics']['spend'],10);self.assertFalse(report['windows']['1']['partial'])
    def test_exact_and_conflicting_daily_duplicates(self):
        rows=self.snapshot['sources']['meta']['data']['rows'];rows.append(deepcopy(rows[0]));self.assertEqual(self.report()['windows']['7']['metrics']['spend'],70);rows[-1]['spend']='11';self.assertIsNone(self.report()['windows']['1']['metrics']['spend'])
    def test_ranged_row_cannot_be_a_full_day(self):
        self.snapshot['sources']['meta']['data']['rows'][0]['date_stop']='2026-10-18';self.assertIsNone(self.report()['windows']['1']['metrics']['spend'])
    def test_complete_empty_reporting_is_zero_but_missing_actions_unknown(self):
        meta=self.snapshot['sources']['meta']['data'];meta['rows']=[];metrics=self.report()['windows']['7']['metrics'];self.assertEqual(metrics['spend'],0);self.assertEqual(metrics['purchases'],0);self.assertIsNone(metrics['roas']);meta['rows']=[{'ad_id':'ad1','campaign_id':'sales1','date_start':'2026-10-17','date_stop':'2026-10-17','spend':'0'}];self.assertIsNone(self.report()['windows']['1']['metrics']['purchases']);meta['rows'][0].update(actions=[],action_values=[]);self.assertEqual(self.report()['windows']['1']['metrics']['purchases'],0)
    def test_incomplete_reporting_is_never_zero(self):
        meta=self.snapshot['sources']['meta']['data'];meta.update(complete=False,rows=[]);self.assertIsNone(self.report()['windows']['7']['metrics']['spend'])
    def test_missing_actions_explains_null_without_partial_purchase_kpi(self):
        del self.snapshot['sources']['meta']['data']['rows'][0]['actions'];window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['purchases']);self.assertTrue(any('שדה הפעולות חסר ב־1 שורות' in note for note in window['notes']));self.assertNotIn('known_purchases',window['metrics'])
    def test_purchase_value_missing_does_not_become_zero_revenue(self):
        self.snapshot['sources']['meta']['data']['rows'][0]['action_values']=[];metrics=self.report()['windows']['1']['metrics'];self.assertEqual(metrics['purchases'],1);self.assertIsNone(metrics['attributed_revenue']);self.assertIsNone(metrics['roas'])
    def test_omni_fallback_without_pixel_event(self):
        row=self.snapshot['sources']['meta']['data']['rows'][0];row['actions']=[{'action_type':'omni_purchase','value':'3'}];row['action_values']=[{'action_type':'omni_purchase','value':'90'}];metrics=self.report()['windows']['1']['metrics'];self.assertEqual(metrics['purchases'],3);self.assertEqual(metrics['attributed_revenue'],90)
    def test_duplicate_action_types_are_not_added(self):
        row=self.snapshot['sources']['meta']['data']['rows'][0];row['actions'].append({'action_type':'landing_page_view','value':'5'});self.assertEqual(self.report()['windows']['1']['metrics']['landing_page_views'],5);row['actions'][-1]['value']='7';self.assertIsNone(self.report()['windows']['1']['metrics']['landing_page_views'])
    def test_shopify_totals_are_all_store_separate_from_meta(self):
        metrics=self.report()['windows']['7']['metrics'];self.assertEqual(metrics['store_orders'],21);self.assertEqual(metrics['store_revenue'],210);self.assertEqual(metrics['attributed_revenue'],350);self.assertNotIn('profit',metrics)
    def test_shopify_missing_day_and_mismatched_coverage_not_filled(self):
        store=self.snapshot['sources']['shopify']['data'];store['table']['rows'].pop(0);self.assertIsNone(self.report()['windows']['1']['metrics']['store_orders']);store['until']='2026-10-14';self.assertIsNone(self.report()['windows']['7']['metrics']['store_revenue'])
    def test_shopify_duplicate_dates_cannot_double_count(self):
        rows=self.snapshot['sources']['shopify']['data']['table']['rows'];rows.append(deepcopy(rows[0]));self.assertIsNone(self.report()['windows']['1']['metrics']['store_orders'])
    def test_shopify_currency_scope_and_missing_columns(self):
        store=self.snapshot['sources']['shopify']['data'];store['currency']='USD';self.assertIsNone(self.report()['windows']['1']['metrics']['store_revenue']);store['currency']='ILS';store['table']['columns']=[];self.assertIsNone(self.report()['windows']['1']['metrics']['store_revenue'])
    def test_shopify_refund_day_can_have_negative_total_sales(self):
        self.snapshot['sources']['shopify']['data']['table']['rows'][0]['total_sales']='-40';self.assertEqual(self.report()['windows']['1']['metrics']['store_revenue'],-40)
    def test_current_inventory_not_historical_status_or_old_ad_average(self):
        campaign=self.report()['windows']['7']['campaigns'][0];self.assertEqual(campaign['name'],'Current sales name');self.assertFalse(campaign['configured_active']);self.assertEqual(campaign['spend'],70);self.assertEqual(campaign['daily_budget_minor'],12000);self.assertEqual(campaign['status_scope'],'current_configuration_not_period_history')
    def test_stale_and_disabled_collector_not_called_live(self):
        self.snapshot['sources']['meta']['last_success_at']=(self.now-timedelta(minutes=16)).isoformat();self.assertEqual(self.report()['freshness']['state'],'stale');self.snapshot['sources']['meta']['last_success_at']=self.stamp;self.snapshot['collection_active']=False;self.assertEqual(self.report()['freshness']['state'],'stale')
    def test_midnight_account_date_rollover_requires_refresh(self):
        self.now=datetime(2026,10,18,22,tzinfo=timezone.utc);report=self.report();self.assertEqual(report['windows']['1']['until'],'2026-10-18');self.assertIsNone(report['windows']['1']['metrics']['spend'])
    def test_verified_registration_objective_gets_cpr_without_mixed_spend(self):
        data=self.snapshot['sources']['meta']['data'];data['inventory']={'verified':True,'campaign':{'id':'sales1','account_id':ACCOUNT,'objective':'OUTCOME_LEADS'},'adsets':[{'promoted_object':{'custom_event_type':'COMPLETE_REGISTRATION'}}]};data['account_campaigns']['campaigns'][0]['objective']='OUTCOME_LEADS';result=self.report();self.assertEqual(result['windows']['7']['metrics']['cost_per_registration'],5);self.assertEqual(result['windows']['7']['campaigns'][0]['result_type'],'registration')
    def test_wrong_account_and_invalid_numbers_fail_closed(self):
        self.snapshot['sources']['meta']['data']['account_id']='other';self.assertIsNone(self.report()['windows']['7']['metrics']['spend']);self.snapshot['sources']['meta']['data']['account_id']=ACCOUNT;self.snapshot['sources']['meta']['data']['rows'][0]['spend']='NaN';self.assertIsNone(self.report()['windows']['1']['metrics']['spend'])
    def test_action_summary_is_not_execution(self):
        result=build_report(self.snapshot,{'available':True},{'proposals':[{'approval_status':'pending','state':'prepared','expires_at':(self.now+timedelta(minutes=5)).isoformat(),'blocked_reasons':[]}],'jobs':[{'state':'unknown'}]},self.now);self.assertEqual(result['action_summary']['pending_approval_count'],1);self.assertEqual(result['action_summary']['jobs_requiring_attention'],1);self.assertFalse(result['action_summary']['live_action_performed_by_report'])
    def hierarchy(self):
        rows=self.snapshot['sources']['meta']['data']['rows']
        for row in rows:row.update(adset_id='set1',adset_name='Original group',ad_name='Original ad')
        second=[]
        for row in rows:
            item=deepcopy(row);item.update(ad_id='ad2',ad_name='Second ad',adset_id='set2',adset_name='Second group',spend='20')
            for event in item['actions']:
                if event['action_type']=='offsite_conversion.fb_pixel_purchase':event['value']='2'
                if event['action_type']=='offsite_conversion.fb_pixel_complete_registration':event['value']='3'
            second.append(item)
        rows.extend(second)
    def test_drilldown_hierarchy_sums_and_parent_ids(self):
        self.hierarchy();window=self.report()['windows']['7'];self.assertEqual(len(window['ads']),2);self.assertEqual(len(window['adsets']),2)
        for key in ('spend','purchases','registrations','attributed_revenue','landing_page_views'):
            self.assertEqual(sum(row[key] for row in window['ads']),window['metrics'][key]);self.assertEqual(sum(row[key] for row in window['adsets']),window['metrics'][key]);self.assertEqual(sum(row[key] for row in window['campaigns']),window['metrics'][key])
        ad=next(row for row in window['ads'] if row['id']=='ad2');self.assertEqual(ad['campaign_id'],'sales1');self.assertEqual(ad['adset_id'],'set2');self.assertEqual(ad['name'],'Second ad');self.assertTrue(ad['hierarchy_complete']);self.assertIsNone(ad['status'])
    def test_partial_observations_do_not_replace_null_totals(self):
        self.hierarchy();del self.snapshot['sources']['meta']['data']['rows'][-1]['actions'];window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['purchases']);self.assertIsNone(window['campaigns'][0]['purchases']);observed=window['observed_results']['purchases'];self.assertEqual(observed['value'],19);self.assertEqual(observed['known_rows'],13);self.assertEqual(observed['missing_rows'],1);self.assertFalse(observed['complete'])
        first=next(row for row in window['ads'] if row['id']=='ad1');second=next(row for row in window['ads'] if row['id']=='ad2');self.assertEqual(first['purchases'],7);self.assertIsNone(second['purchases']);self.assertEqual(second['observed_results']['purchases']['value'],12)
    def test_all_missing_observations_are_unknown_not_zero(self):
        for row in self.snapshot['sources']['meta']['data']['rows']:row.pop('actions')
        observed=self.report()['windows']['7']['observed_results']['purchases'];self.assertIsNone(observed['value']);self.assertEqual(observed['known_rows'],0);self.assertEqual(observed['missing_rows'],7);self.assertFalse(observed['complete'])
    def test_complete_empty_observations_have_zero_without_invented_rows(self):
        self.snapshot['sources']['meta']['data']['rows']=[];window=self.report()['windows']['7'];self.assertEqual(window['observed_results']['purchases'],{'value':0.0,'known_rows':0,'missing_rows':0,'complete':True,'semantics':'attributed_events_in_known_rows_not_unique_people_or_orders'});self.assertEqual(window['ads'],[]);self.assertEqual(window['adsets'],[])
    def test_invalid_coverage_has_no_partial_observation_or_drilldown(self):
        self.hierarchy();self.snapshot['sources']['meta']['data']['complete']=False;window=self.report()['windows']['7'];self.assertIsNone(window['observed_results']);self.assertEqual(window['ads'],[]);self.assertEqual(window['adsets'],[])
    def test_missing_parent_id_is_explicit_not_fabricated(self):
        self.hierarchy();self.snapshot['sources']['meta']['data']['rows'][0].pop('adset_id');window=self.report()['windows']['7'];ad=next(row for row in window['ads'] if row['id']=='ad1');self.assertFalse(ad['hierarchy_complete']);self.assertIsNone(ad['adset_id']);self.assertIsNone(ad['adset_name']);self.assertTrue(any('חסר מזהה קבוצת מודעות ב־1' in note for note in window['notes']));self.assertEqual(window['metrics']['spend'],210)
    def test_conflicting_parent_history_not_silently_assigned(self):
        self.hierarchy();self.snapshot['sources']['meta']['data']['rows'][0]['campaign_id']='other';window=self.report()['windows']['7'];ad=next(row for row in window['ads'] if row['id']=='ad1');self.assertIsNone(ad['campaign_id']);self.assertFalse(ad['hierarchy_complete']);self.assertEqual(ad['parent_ids_observed']['campaign_ids'],['other','sales1'])
    def test_current_ad_metadata_keeps_historical_metrics_separate(self):
        self.hierarchy();data=self.snapshot['sources']['meta']['data'];data['checked_at']=self.stamp;data['inventory']={'verified':True,'campaign':{'id':'sales1','account_id':ACCOUNT},'adsets':[{'id':'set1','name':'Current group','status':'PAUSED','effective_status':'PAUSED','daily_budget':'12000','lifetime_budget':'0'}],'ads':[{'id':'ad1','adset_id':'set1','name':'Current ad','status':'PAUSED','effective_status':'PAUSED'}]}
        window=self.report()['windows']['7'];ad=next(row for row in window['ads'] if row['id']=='ad1');self.assertEqual(ad['name'],'Current ad');self.assertFalse(ad['configured_active']);self.assertEqual(ad['spend'],70);self.assertEqual(ad['status_scope'],'current_configuration_not_period_history');group=next(row for row in window['adsets'] if row['id']=='set1');self.assertEqual(group['daily_budget_minor'],12000);self.assertEqual(group['inventory_observed_at'],self.stamp)
    def test_observed_alias_policy_and_duplicate_rows_remain_single(self):
        self.hierarchy();rows=self.snapshot['sources']['meta']['data']['rows'];rows.append(deepcopy(rows[0]));window=self.report()['windows']['7'];self.assertEqual(window['observed_results']['purchases']['value'],21);self.assertEqual(window['observed_results']['purchases']['known_rows'],14);self.assertEqual(window['observed_results']['registrations']['value'],35);self.assertTrue(window['observed_results']['purchases']['complete'])
    def test_explicit_wrong_inventory_ownership_cannot_lend_status_or_budget(self):
        self.hierarchy();data=self.snapshot['sources']['meta']['data'];data['checked_at']=self.stamp;data['inventory']={'verified':True,'campaign':{'id':'sales1','account_id':ACCOUNT},'adsets':[{'id':'set1','account_id':'other','name':'Wrong account name','status':'ACTIVE','daily_budget':'99999','lifetime_budget':'0'}],'ads':[{'id':'ad1','campaign_id':'other-campaign','adset_id':'set1','name':'Wrong parent name','status':'ACTIVE'}]}
        window=self.report()['windows']['7'];ad=next(row for row in window['ads'] if row['id']=='ad1');group=next(row for row in window['adsets'] if row['id']=='set1');self.assertIsNone(ad['status']);self.assertEqual(ad['name'],'Original ad');self.assertIsNone(group['daily_budget_minor']);self.assertFalse(group['budget_known']);self.assertEqual(group['name'],'Original group')
    def test_extreme_non_json_safe_event_amounts_remain_unknown(self):
        self.hierarchy();row=self.snapshot['sources']['meta']['data']['rows'][0];row['spend']='1e99999999';row['actions'][0]['value']='1e99999999';window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['spend']);self.assertIsNone(window['metrics']['purchases']);self.assertFalse(window['observed_results']['purchases']['complete']);self.assertEqual(window['observed_results']['purchases']['missing_rows'],1)
    def configuration(self,campaign_status='PAUSED',set_status='ACTIVE',ad_status='ACTIVE'):
        self.hierarchy();data=self.snapshot['sources']['meta']['data'];data['checked_at']=self.stamp;data['account_campaigns']['campaigns'][0]['status']=campaign_status
        data['inventory']={'verified':True,'campaign':{'id':'sales1','account_id':ACCOUNT,'status':campaign_status},'adsets':[{'id':'set1','status':set_status,'name':'Current set','daily_budget':'12000','lifetime_budget':'0'}],'ads':[{'id':'ad1','adset_id':'set1','status':ad_status,'name':'Current ad'}]}
    def test_active_child_keeps_own_switch_but_explains_paused_parent(self):
        self.configuration();window=self.report()['windows']['7'];ad=next(row for row in window['ads'] if row['id']=='ad1');self.assertTrue(ad['configured_active']);self.assertEqual(ad['status'],'ACTIVE');self.assertEqual(ad['configured_delivery_blockers'],['campaign_paused']);self.assertEqual(ad['configured_delivery_blockers_observed_at']['campaign_paused'],self.stamp);self.assertEqual(window['campaigns'][0]['configured_delivery_blockers'],['campaign_paused']);self.assertEqual(ad['configured_delivery_blockers_scope'],'verified_current_configuration_not_actual_delivery')
    def test_campaign_pause_explained_even_when_ad_status_unknown(self):
        self.hierarchy();ad=self.report()['windows']['7']['ads'][0];self.assertIsNone(ad['status']);self.assertEqual(ad['configured_delivery_blockers'],['campaign_paused'])
    def test_all_verified_pause_switches_are_separate(self):
        self.configuration(set_status='PAUSED',ad_status='PAUSED');window=self.report()['windows']['7'];ad=next(row for row in window['ads'] if row['id']=='ad1');group=next(row for row in window['adsets'] if row['id']=='set1');self.assertEqual(ad['configured_delivery_blockers'],['campaign_paused','adset_paused','ad_paused']);self.assertEqual(group['configured_delivery_blockers'],['campaign_paused','adset_paused'])
    def test_all_active_never_claims_actual_delivery(self):
        self.configuration(campaign_status='ACTIVE');ad=next(row for row in self.report()['windows']['7']['ads'] if row['id']=='ad1');self.assertEqual(ad['configured_delivery_blockers'],[]);self.assertNotIn('can_deliver',ad);self.assertNotIn('delivering',ad)
    def test_unverified_or_wrong_account_parent_cannot_add_blocker(self):
        self.configuration();data=self.snapshot['sources']['meta']['data'];data['account_campaigns']['campaigns'][0]['account_id']='wrong';data['inventory']['campaign']['account_id']='wrong';window=self.report()['windows']['7'];ad=next(row for row in window['ads'] if row['id']=='ad1');self.assertEqual(ad['configured_delivery_blockers'],[]);self.assertIsNone(ad['status']);self.assertIsNone(ad['configured_active'])
    def test_effective_reason_does_not_invent_missing_parent_switch(self):
        self.configuration();data=self.snapshot['sources']['meta']['data'];data['account_campaigns']['campaigns'][0].pop('status');data['inventory']['campaign'].pop('status');data['inventory']['ads'][0]['effective_status']='CAMPAIGN_PAUSED';ad=next(row for row in self.report()['windows']['7']['ads'] if row['id']=='ad1');self.assertEqual(ad['configured_delivery_blockers'],[]);self.assertEqual(ad['effective_status'],'CAMPAIGN_PAUSED')
    def test_blocker_observation_age_not_replaced_with_report_time(self):
        self.configuration();data=self.snapshot['sources']['meta']['data'];old=(self.now-timedelta(days=1)).isoformat();data['account_campaigns']['checked_at']=old;ad=next(row for row in self.report()['windows']['7']['ads'] if row['id']=='ad1');self.assertEqual(ad['configured_delivery_blockers_observed_at']['campaign_paused'],old)
    def test_collection_reports_refresh_without_advancing_source_timestamps(self):
        old=(self.now-timedelta(minutes=20)).isoformat();self.snapshot.update(refreshing=True,generation=12,last_cycle_at=old)
        for source in self.snapshot['sources'].values():source['last_success_at']=old
        result=self.report();self.assertEqual(result['collection'],{'refreshing':True,'active':True,'last_cycle_at':old,'generation':12});self.assertEqual(result['freshness']['meta_last_success_at'],old);self.assertEqual(result['freshness']['shopify_last_success_at'],old);self.assertEqual(result['freshness']['state'],'stale');self.assertNotEqual(result['checked_at'],old)
    def test_collection_cannot_turn_missing_data_into_fresh(self):
        self.snapshot.update(refreshing=True,generation=2,last_cycle_at=self.stamp);self.snapshot['sources']={};result=self.report();self.assertNotEqual(result['freshness']['state'],'fresh');self.assertIsNone(result['windows']['7']['metrics']['spend']);self.assertTrue(result['collection']['refreshing']);self.assertIsNone(result['freshness']['meta_last_success_at'])
    def test_collection_fields_reject_bool_generations_and_raw_error_payloads(self):
        self.snapshot.update(refreshing='true',collection_active=1,generation=True,last_cycle_at={'private_error':'must not be copied'});result=self.report();self.assertEqual(result['collection'],{'refreshing':False,'active':False,'last_cycle_at':None,'generation':None});self.assertNotIn('private_error',str(result['collection']))
    def test_stale_shopify_with_fresh_meta_is_stale_not_partial(self):
        self.snapshot['sources']['shopify']['last_success_at']=(self.now-timedelta(minutes=16)).isoformat();freshness=self.report()['freshness'];self.assertEqual(freshness['state'],'stale');self.assertEqual(freshness['source_states'],{'meta':'fresh','shopify':'stale'})
    def test_missing_identity_and_fresh_sparse_coverage_remain_partial(self):
        self.snapshot['sources']['shopify']['data']['shop']='wrong';freshness=self.report()['freshness'];self.assertEqual(freshness['state'],'partial');self.assertEqual(freshness['source_states']['shopify'],'unavailable');self.snapshot['sources']['shopify']['data']['shop']=SHOP;self.snapshot['sources']['shopify']['data']['table']['rows'].pop();freshness=self.report()['freshness'];self.assertEqual(freshness['state'],'partial');self.assertEqual(freshness['source_states']['shopify'],'partial')
    def campaign_comparison(self):
        data=self.snapshot['sources']['meta']['data'];settings={'time_increment':'1','use_unified_attribution_setting':True,'action_report_time':'default_unchanged'};data['query_settings']=deepcopy(settings);data['checked_at']=self.stamp
        rows=[{k:deepcopy(v) for k,v in row.items() if k in ('campaign_id','campaign_name','date_start','date_stop','spend','actions','action_values')} for row in data['rows']]
        data['campaign_insights']={'verified':True,'complete':True,'account_id':ACCOUNT,'since':data['since'],'until':data['until'],'checked_at':self.stamp,'query_settings':deepcopy(settings),'rows':rows};return data['campaign_insights']
    def test_independent_campaign_totals_fill_summary_not_ad_rows(self):
        self.hierarchy();data=self.snapshot['sources']['meta']['data'];data['rows']=[r for r in data['rows'] if r['ad_id']=='ad1'];self.campaign_comparison();del data['rows'][0]['actions'];window=self.report()['windows']['7'];self.assertEqual(window['metrics']['purchases'],7);self.assertEqual(window['metrics_source'],'campaign_insights');self.assertEqual(window['campaigns'][0]['purchases'],7);self.assertIsNone(window['ads'][0]['purchases']);self.assertEqual(window['observed_results']['purchases']['value'],6);self.assertFalse(window['observed_results']['purchases']['complete']);self.assertEqual(window['observed_results_source'],'ad_insights')
    def test_independent_campaign_zero_rows_only_when_both_reports_empty(self):
        data=self.snapshot['sources']['meta']['data'];data['rows']=[];self.campaign_comparison();window=self.report()['windows']['7'];self.assertEqual(window['metrics']['purchases'],0);self.assertEqual(window['metrics_source'],'campaign_insights')
    def test_comparison_wrong_identity_settings_or_timestamps_never_fills_missing(self):
        for field,value in [('account_id','wrong'),('complete',False),('query_settings',{}),('checked_at',(self.now-timedelta(hours=1)).isoformat())]:
            with self.subTest(field=field):
                source=self.campaign_comparison();source[field]=value;data=self.snapshot['sources']['meta']['data'];data['rows'][0].pop('actions',None);window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['purchases']);self.assertEqual(window['metrics_source'],'ad_insights');data['rows'][0]['actions']=deepcopy(self.snapshot['sources']['meta']['data']['rows'][1]['actions'])
    def test_comparison_daily_spend_conflict_stays_unknown(self):
        source=self.campaign_comparison();source['rows'][0]['spend']='11';self.snapshot['sources']['meta']['data']['rows'][0].pop('actions');window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['spend']);self.assertIsNone(window['metrics']['purchases']);self.assertEqual(window['metrics_source'],'conflict');self.assertTrue(any('הוצאות דוח הקמפיין שונות' in note for note in window['notes']))
    def test_complete_count_conflict_is_not_silently_chosen(self):
        source=self.campaign_comparison();source['rows'][0]['actions'][0]['value']='2';window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['purchases']);self.assertIsNone(window['campaigns'][0]['purchases']);self.assertEqual(window['observed_results']['purchases']['value'],7);self.assertEqual(window['metrics_sources']['purchases'],'conflict')
    def test_partial_ad_lower_bound_above_campaign_count_is_conflict(self):
        source=self.campaign_comparison();data=self.snapshot['sources']['meta']['data'];first=data['rows'][0];first['actions'][0]['value']='2';unknown=deepcopy(first);unknown['ad_id']='another';unknown['spend']='0';unknown.pop('actions');unknown.pop('action_values');data['rows'].append(unknown);window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['purchases']);self.assertEqual(window['metrics_sources']['purchases'],'conflict')
    def test_missing_campaign_day_or_duplicate_or_cross_scope_rejected(self):
        for change in ('missing','duplicate','scope','range'):
            with self.subTest(change=change):
                source=self.campaign_comparison();data=self.snapshot['sources']['meta']['data'];data['rows'][0].pop('actions',None)
                if change=='missing':source['rows'].pop(0)
                elif change=='duplicate':source['rows'].append(deepcopy(source['rows'][0]))
                elif change=='scope':source['rows'][0]['account_id']='wrong'
                else:source['rows'][0]['date_stop']='2026-10-18'
                window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['purchases']);self.assertEqual(window['metrics_source'],'ad_insights');data['rows'][0]['actions']=deepcopy(data['rows'][1]['actions'])
    def test_campaign_query_partial_day_does_not_enter_summary(self):
        source=self.campaign_comparison();partial=deepcopy(source['rows'][0]);partial.update(date_start='2026-10-18',date_stop='2026-10-18',spend='999');source['rows'].append(partial);window=self.report()['windows']['7'];self.assertEqual(window['metrics']['spend'],70);self.assertEqual(window['metrics']['purchases'],7)
    def test_campaign_query_missing_actions_is_not_inferred_zero(self):
        source=self.campaign_comparison();source['rows'][0].pop('actions');self.snapshot['sources']['meta']['data']['rows'][0].pop('actions');window=self.report()['windows']['7'];self.assertIsNone(window['metrics']['purchases']);self.assertNotEqual(window['metrics_sources']['purchases'],'campaign_insights')

class OmittedActionsTests(unittest.TestCase):
    """Meta omits `actions` for zero-action ad days (21.10 gap: new ads with tiny spend)."""
    def test_confirmed_actions_row_without_actions_is_zero_not_unknown(self):
        from management_report import _aggregate
        sold={'spend':'10','actions':[{'action_type':'omni_purchase','value':'2'}],'action_values':[{'action_type':'omni_purchase','value':'500'}],'metric_sources':{'delivery_present':True,'actions_present':True}}
        quiet={'spend':'1.5','metric_sources':{'delivery_present':True,'actions_present':True}}
        values=_aggregate([sold,quiet])
        self.assertEqual(values['purchases'],2);self.assertEqual(values['attributed_revenue'],500)
    def test_missing_actions_stream_row_stays_unknown(self):
        from management_report import _aggregate
        lost={'spend':'1.5','metric_sources':{'delivery_present':True,'actions_present':False}}
        self.assertIsNone(_aggregate([lost])['purchases'])
        self.assertIsNone(_aggregate([{'spend':'1.5'}])['purchases'])
    def test_purchases_without_values_keep_revenue_unknown(self):
        from management_report import _aggregate
        row={'spend':'10','actions':[{'action_type':'omni_purchase','value':'1'}],'metric_sources':{'delivery_present':True,'actions_present':True}}
        self.assertIsNone(_aggregate([row])['attributed_revenue'])

if __name__=='__main__':unittest.main()
