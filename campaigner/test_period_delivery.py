import unittest,json,time,copy
from datetime import datetime,timezone,timedelta
from period_delivery import read_period_delivery,validate_period_delivery,apply_period_delivery,digest
from meta_direct import ACCOUNT,QUERY_SETTINGS

class PeriodTests(unittest.TestCase):
 def fixture(self):
  now=datetime.now(timezone.utc);today=now.date()
  def graph(token,path,p):
   span=json.loads(p['time_range']);row={'campaign_id':'10','date_start':span['since'],'date_stop':span['until'],'spend':'30','impressions':'100'}
   if p['level']=='ad':row.update(ad_id='20',adset_id='30')
   return {'data':[row]}
  value=read_period_delivery(graph,'test',ACCOUNT,today,time.monotonic()+10,QUERY_SETTINGS)
  return value,today-timedelta(days=7),today-timedelta(days=1),datetime.now(timezone.utc)
 def test_exact_contract_and_six_requests(self):
  v,s,e,n=self.fixture();self.assertIsNotNone(validate_period_delivery(v,s,e,n,ACCOUNT,QUERY_SETTINGS))
  self.assertEqual(set(v['windows']),{'1','3','7'})
 def test_wrong_window_scope_params_dates_duplicates_and_stale_fail(self):
  original,s,e,n=self.fixture()
  for mutation in ('account','params','date','duplicate','old','negative','parent'):
   v=copy.deepcopy(original);r=v['windows']['7']['ad']
   if mutation=='account':v['account_id']='other'
   if mutation=='params':r['request']['action_report_time']='conversion'
   if mutation=='date':r['rows'][0]['date_start']=e.isoformat()
   if mutation=='duplicate':r['rows']*=2
   if mutation=='old':v['checked_at']=(n-timedelta(hours=2)).isoformat()
   if mutation=='negative':r['rows'][0]['spend']='-1'
   if mutation=='parent':r['rows'][0]['adset_id']=''
   r['rows_sha256']=digest(r['rows'])
   with self.subTest(mutation=mutation):self.assertIsNone(validate_period_delivery(v,s,e,n,ACCOUNT,QUERY_SETTINGS))
 def test_preserves_unknown_actions_and_blocks_actual_daily_conflict(self):
  v,s,e,n=self.fixture();data={'period_delivery':v,'query_settings':QUERY_SETTINGS}
  ad={'id':'20','campaign_id':'10','adset_id':'30','spend':None,'purchases':None,'attributed_revenue':None,'efficiency_integrity':{'status':'comparison_unavailable'}}
  detail={'ads':[ad],'adsets':[]};apply_period_delivery(data,[],detail,s,e,n,ACCOUNT)
  self.assertEqual(ad['spend'],30);self.assertIsNone(ad['purchases']);self.assertIsNone(ad['roas']);self.assertFalse(ad['delivery_evidence']['daily_missing_rows_filled'])
  ad['spend']=40;apply_period_delivery(data,[],detail,s,e,n,ACCOUNT)
  self.assertIsNone(ad['spend']);self.assertEqual(ad['efficiency_integrity']['reason'],'daily_period_delivery_conflict')
 def test_period_crosslevel_conflict_blocks_efficiency(self):
  v,s,e,n=self.fixture();r=v['windows']['7']['campaign'];r['rows'][0]['spend']='40';r['rows_sha256']=digest(r['rows'])
  ad={'id':'20','campaign_id':'10','adset_id':'30','spend':30,'purchases':2}
  apply_period_delivery({'period_delivery':v,'query_settings':QUERY_SETTINGS},[],{'ads':[ad],'adsets':[]},s,e,n,ACCOUNT)
  self.assertIsNone(ad['spend']);self.assertEqual(ad['efficiency_integrity']['status'],'blocked')
 def test_adset_conflict_missing_results_and_equal_spend_ratios(self):
  for prior,revenue,registrations in ((40,100,2),(None,None,None),(30,90,3)):
   v,s,e,n=self.fixture();ad={'id':'20','campaign_id':'10','adset_id':'30','spend':30}
   adset={'id':'30','campaign_id':'10','spend':prior,'attributed_revenue':revenue,'registrations':registrations,'result_type':'registration','roas':2.5,'cost_per_registration':999}
   apply_period_delivery({'period_delivery':v,'query_settings':QUERY_SETTINGS},[],{'ads':[ad],'adsets':[adset]},s,e,n,ACCOUNT)
   with self.subTest(prior=prior):
    if prior==40:
     self.assertIsNone(adset['spend']);self.assertEqual(adset['efficiency_integrity']['status'],'blocked');self.assertIsNone(adset['roas']);self.assertIsNone(adset['cost_per_registration'])
    elif prior is None:
     self.assertEqual(adset['spend'],30);self.assertIsNone(adset['roas']);self.assertIsNone(adset['cost_per_registration'])
    else:
     self.assertEqual(adset['spend'],30);self.assertEqual(adset['roas'],3);self.assertEqual(adset['cost_per_registration'],10)
 def test_global_total_uses_only_complete_matching_period_populations(self):
  for mode in ('matching','conflict','missing_parent','existing_conflict','daily_total_conflict'):
   v,s,e,n=self.fixture();campaign=v['windows']['7']['campaign']
   if mode=='conflict':campaign['rows'][0]['spend']='40'
   if mode=='missing_parent':campaign['rows']=[]
   campaign['rows_sha256']=digest(campaign['rows'])
   metrics={'spend':40 if mode=='daily_total_conflict' else None,'purchases':None,'roas':None}
   ad={'id':'20','campaign_id':'10','adset_id':'30','spend':None}
   if mode=='existing_conflict':ad['efficiency_integrity']={'status':'blocked','reason':'cross_report_conflict'}
   apply_period_delivery({'period_delivery':v,'query_settings':QUERY_SETTINGS},[],{'ads':[ad],'adsets':[]},s,e,n,ACCOUNT,metrics)
   with self.subTest(mode=mode):
    if mode=='matching':
     self.assertEqual(metrics['spend'],30);self.assertEqual(metrics['spend_evidence']['scope'],'whole_account_complete_provider_delivery_population');self.assertIsNone(metrics['purchases']);self.assertIsNone(metrics['roas'])
    else:self.assertIsNone(metrics['spend']);self.assertNotIn('spend_evidence',metrics)

 def test_global_rejects_omitted_daily_entity_and_partial_lower_bound(self):
  for mode in ('omitted','lower_bound'):
   v,s,e,n=self.fixture();metrics={'spend':None,'purchases':None}
   if mode=='omitted':
    ads=[{'id':'99','campaign_id':'11','adset_id':'31','spend':40}];rows=[]
   else:
    ads=[{'id':'20','campaign_id':'10','adset_id':'30','spend':None}]
    rows=[{'ad_id':'20','campaign_id':'10','date_start':s.isoformat(),'spend':'40'},{'ad_id':'20','campaign_id':'10','date_start':e.isoformat()}]
   apply_period_delivery({'period_delivery':v,'query_settings':QUERY_SETTINGS,'rows':rows},[],{'ads':ads,'adsets':[]},s,e,n,ACCOUNT,metrics)
   with self.subTest(mode=mode):
    self.assertIsNone(metrics['spend']);self.assertNotIn('spend_evidence',metrics)
    self.assertEqual(metrics['spend_integrity']['reason'],'period_population_or_daily_lower_bound_conflict')

 def test_hierarchy_mismatch_blocks_entity_and_global_claim(self):
  for location in ('daily','display'):
   for field in ('campaign_id','adset_id'):
    v,s,e,n=self.fixture();metrics={'spend':None}
    ad={'id':'20','campaign_id':'10','adset_id':'30','spend':None}
    raw={'ad_id':'20','campaign_id':'10','adset_id':'30','date_start':s.isoformat(),'spend':'10'}
    (raw if location=='daily' else ad)[field]='99'
    apply_period_delivery({'period_delivery':v,'query_settings':QUERY_SETTINGS,'rows':[raw]},[],{'ads':[ad],'adsets':[]},s,e,n,ACCOUNT,metrics)
    with self.subTest(location=location,field=field):
     self.assertIsNone(ad['spend']);self.assertEqual(ad['efficiency_integrity']['reason'],'daily_period_hierarchy_conflict')
     self.assertIsNone(metrics['spend']);self.assertNotIn('spend_evidence',metrics)

 def test_absent_entity_not_zero_and_scope_mismatch_not_joined(self):
  v,s,e,n=self.fixture();ads=[{'id':'99','spend':None},{'id':'20','campaign_id':'999','adset_id':'30','spend':None}]
  apply_period_delivery({'period_delivery':v,'query_settings':QUERY_SETTINGS},[],{'ads':ads,'adsets':[]},s,e,n,ACCOUNT)
  self.assertTrue(all(a['spend'] is None for a in ads))

if __name__=='__main__':unittest.main()
