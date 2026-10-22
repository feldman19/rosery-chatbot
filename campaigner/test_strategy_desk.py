import copy
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from strategy_desk import StrategyDesk
from meta_direct import ACCOUNT
NOW=datetime(2026,10,17,12,tzinfo=timezone.utc)

def snapshot(objective='OUTCOME_SALES'):
 ads=[{'id':'100','name':'Synthetic creative','account_id':ACCOUNT,'status':'ACTIVE','effective_status':'ACTIVE','campaign':{'id':'200','name':'Synthetic campaign','objective':objective,'effective_status':'ACTIVE'},'adset':{'id':'300','optimization_goal':'OFFSITE_CONVERSIONS','effective_status':'ACTIVE'},'creative':{'id':'400','body':'A synthetic ad opening'}}]
 rows=[{'ad_id':'100','date_start':(NOW.date()-timedelta(days=d)).isoformat(),'date_stop':(NOW.date()-timedelta(days=d)).isoformat(),'spend':'10.50','impressions':'100','actions':[{'action_type':'landing_page_view','value':'4'},{'action_type':'omni_purchase','value':'2'},{'action_type':'purchase','value':'2'}]} for d in range(1,8)]
 return {'sources':{'meta':{'last_success_at':NOW.isoformat(),'error':None,'data':{'verified':True,'account_id':ACCOUNT,'complete':True,'currency':'ILS','timezone':'Asia/Jerusalem','since':'2026-10-10','until':'2026-10-17','rows':rows,'active_ads':{'verified':True,'complete':True,'account_id':ACCOUNT,'ads':ads,'checked_at':NOW.isoformat()}}}}}

class StrategyDeskTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.directory=Path(self.temp.name);self.desk=StrategyDesk(self.directory,clock=lambda:NOW)
 def tearDown(self):self.temp.cleanup()
 def test_free_goal_preserved_not_forced_sales_or_ai(self):
  r=self.desk.create({'goal':'Explore a different style for this brand'})
  self.assertEqual('unclear',r['request']['category']);self.assertTrue(r['request']['clarifications']);self.assertFalse(r['request']['execution_approval'])
  report=self.desk.report(snapshot());self.assertFalse(report['model_connected']);self.assertEqual('not_executed',report['research_plan']['state']);self.assertEqual([],report['research_plan']['sources'])
 def test_clear_goals_lightweight_hints(self):
  for goal,expected in [('More awareness for our new collection','awareness'),('Find new audiences and new customers','new_audience'),('Increase sales for these products','sales'),('More registration signups','registration')]:
   self.assertEqual(expected,self.desk.create({'goal':goal})['request']['category'])
 def test_conflicting_primary_goals_require_clarification(self):
  request=self.desk.create({'goal':'Increase awareness and sales'})['request'];self.assertEqual('unclear',request['category']);self.assertTrue(request['clarifications'])
 def test_persistent_versions_and_duplicate_save(self):
  a=self.desk.create({'goal':'Increase awareness'});b=self.desk.create({'goal':'Increase sales'});self.assertEqual(a['request']['id'],b['request']['id']);self.assertEqual(2,b['request']['revision'])
  self.assertTrue(self.desk.create({'goal':'Increase sales'})['unchanged']);reloaded=StrategyDesk(self.directory,clock=lambda:NOW);self.assertEqual(b['request'],reloaded.latest())
 def test_user_flags_cannot_verify_or_authorize(self):
  with self.assertRaises(ValueError):self.desk.create({'goal':'Increase sales','verified':True})
  with self.assertRaises(ValueError):self.desk.create({'goal':'client_secret=do-not-save-this'})
 def test_research_contract_separates_primary_sources_causality_contradictions(self):
  r=self.desk.report(snapshot());ids={x['id'] for x in r['research_plan']['requirements']};self.assertTrue({'official','independent','contradictions','separation','approval'}<=ids)
  self.assertTrue(r['business_dossier']['missing']);self.assertTrue(all('source_ref' in k for k in r['business_dossier']['known']))
 def test_active_ad_analysis_full_day_1_3_7_metrics(self):
  r=self.desk.analyze(snapshot());self.assertTrue(r['verified']);self.assertEqual(1,r['active_ads_count']);self.assertEqual(ACCOUNT,r['account_id']);self.assertEqual('ILS',r['currency'])
  windows=r['ads'][0]['windows'];self.assertEqual([1,3,7],[w['days'] for w in windows]);self.assertEqual([1050,3150,7350],[w['spend_minor'] for w in windows]);self.assertEqual([2,6,14],[w['purchases_attributed'] for w in windows])
  self.assertEqual('sales',r['ads'][0]['goal']);self.assertFalse(r['live_action_allowed']);self.assertEqual([],r['numerical_recommendations'])
 def test_partial_today_excluded(self):
  s=snapshot();s['sources']['meta']['data']['rows'].append({'ad_id':'100','date_start':'2026-10-17','date_stop':'2026-10-17','spend':'9999','impressions':'9999','actions':[]})
  self.assertEqual(7350,self.desk.analyze(s)['ads'][0]['windows'][2]['spend_minor'])
 def test_missing_actions_are_unknown_not_zero(self):
  s=snapshot()
  for row in s['sources']['meta']['data']['rows']:row.pop('actions')
  w=self.desk.analyze(s)['ads'][0]['windows'][0];self.assertIsNone(w['purchases_attributed']);self.assertIsNone(w['landing_views']);self.assertEqual(1050,w['spend_minor'])
 def test_no_rows_not_zero(self):
  s=snapshot();s['sources']['meta']['data']['rows']=[];w=self.desk.analyze(s)['ads'][0]['windows'][0];self.assertIsNone(w['spend_minor']);self.assertIsNone(w['impressions'])
 def test_awareness_does_not_require_purchases(self):
  s=snapshot('OUTCOME_AWARENESS')
  for row in s['sources']['meta']['data']['rows']:row['actions']=[]
  ad=self.desk.analyze(s)['ads'][0];self.assertEqual('awareness',ad['goal']);self.assertFalse(any('objective result event' in q for q in ad['unknowns']));self.assertTrue(any('Reach, frequency' in q for q in ad['unknowns']))
 def test_lead_objective_is_not_assumed_registration(self):
  ad=self.desk.analyze(snapshot('OUTCOME_LEADS'))['ads'][0];self.assertEqual('leads',ad['goal']);self.assertIsNone(ad['windows'][0]['leads_attributed']);self.assertIsNone(ad['windows'][0]['registrations_attributed'])
 def test_stale_wrong_incomplete_inventory_fail_closed(self):
  for kind in ('stale','error','partial','wrong'):
   s=snapshot();src=s['sources']['meta'];inv=src['data']['active_ads']
   if kind=='stale':inv['checked_at']=(NOW-timedelta(minutes=13)).isoformat()
   elif kind=='error':src['error']='failed'
   elif kind=='partial':inv['complete']=False
   else:inv['account_id']='other'
   r=self.desk.analyze(s);self.assertFalse(r['verified']);self.assertIsNone(r['active_ads_count']);self.assertEqual([],r['ads'])
 def test_paused_parent_does_not_enter_active_analysis(self):
  s=snapshot();s['sources']['meta']['data']['active_ads']['ads'][0]['campaign']['effective_status']='PAUSED';r=self.desk.analyze(s);self.assertFalse(r['verified']);self.assertEqual([],r['ads'])
 def test_unknown_timezone_keeps_inventory_but_not_full_day_metrics(self):
  s=snapshot();s['sources']['meta']['data']['timezone']='invalid/zone';r=self.desk.analyze(s);self.assertTrue(r['verified']);self.assertIsNone(r['ads'][0]['windows'][0]['spend_minor'])
 def test_copy_observation_not_causal_claim_and_test_not_approved(self):
  ad=self.desk.analyze(snapshot())['ads'][0];self.assertFalse(ad['observed'][0]['is_performance_explanation']);self.assertFalse(ad['hypotheses'][0]['causal_evidence']);self.assertIsNone(ad['controlled_tests'][0]['budget_proposed']);self.assertFalse(ad['controlled_tests'][0]['execution_available'])
 def test_duplicate_daily_grain_does_not_double_count(self):
  s=snapshot();rows=s['sources']['meta']['data']['rows'];rows.append(copy.deepcopy(rows[0]));self.assertIsNone(self.desk.analyze(s)['ads'][0]['windows'][0]['spend_minor'])
 def test_currency_missing_never_assumes_ils(self):
  s=snapshot();s['sources']['meta']['data'].pop('currency');r=self.desk.analyze(s);self.assertIsNone(r['currency']);self.assertIsNone(r['ads'][0]['windows'][0]['spend_minor'])
 def test_negative_nonfinite_or_fractional_minor_not_truncated(self):
  for value in ('NaN','-1','1.005'):
   s=snapshot();s['sources']['meta']['data']['rows'][0]['spend']=value;self.assertIsNone(self.desk.analyze(s)['ads'][0]['windows'][0]['spend_minor'])
 def test_snapshot_not_mutated(self):
  s=snapshot();before=copy.deepcopy(s);self.desk.analyze(s);self.assertEqual(before,s)
 def test_actual_verified_registration_event_refines_leads_objective(self):
  s=snapshot('OUTCOME_LEADS');data=s['sources']['meta']['data'];data['active_ads']['ads'][0]['adset']['promoted_object']={'custom_event_type':'COMPLETE_REGISTRATION'}
  for row in data['rows']:row['actions']=[{'action_type':'omni_complete_registration','value':'3'}]
  ad=self.desk.analyze(s)['ads'][0];self.assertEqual('registration',ad['goal']);self.assertEqual(3,ad['windows'][0]['registrations_attributed']);self.assertFalse(any('objective result event' in q for q in ad['unknowns']))
 def test_registration_event_not_applied_to_incompatible_objective(self):
  s=snapshot('OUTCOME_AWARENESS');s['sources']['meta']['data']['active_ads']['ads'][0]['adset']['promoted_object']={'custom_event_type':'COMPLETE_REGISTRATION'}
  self.assertEqual('awareness',self.desk.analyze(s)['ads'][0]['goal'])
 def test_multi_day_row_including_partial_not_treated_full_day(self):
  s=snapshot();s['sources']['meta']['data']['rows'][0]['date_stop']='2026-10-17';w=self.desk.analyze(s)['ads'][0]['windows'][0];self.assertIsNone(w['spend_minor']);self.assertIsNone(w['purchases_attributed'])
 def test_missing_date_stop_not_assumed_daily(self):
  s=snapshot();s['sources']['meta']['data']['rows'][0].pop('date_stop');self.assertIsNone(self.desk.analyze(s)['ads'][0]['windows'][0]['spend_minor'])
if __name__=='__main__':unittest.main()
