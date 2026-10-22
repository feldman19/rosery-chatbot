import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decision_evidence import assess_contract, report_assessment, REQUIRED
from decision_uncertainty import poisson_rate_interval, one_event_sensitivity

class UncertaintyTests(unittest.TestCase):
 def test_exact_reference_intervals(self):
  zero=poisson_rate_interval(0,1,.05)
  self.assertAlmostEqual(zero['rate_upper'],3.688879454,places=7)
  self.assertEqual(zero['rate_lower'],0);self.assertIsNone(zero['cpa_upper'])
  one=poisson_rate_interval(1,1,.05)
  self.assertAlmostEqual(one['rate_lower'],.025317808,places=7)
  self.assertAlmostEqual(one['rate_upper'],5.571643391,places=7)
 def test_invalid_counts_and_exposure(self):
  for count in (None,-1,1.5,True,float('nan')):
   with self.assertRaises(ValueError):poisson_rate_interval(count,10,.05)
  for spend in (0,-1,float('inf'),True):
   with self.assertRaises(ValueError):poisson_rate_interval(1,spend,.05)
 def test_count_sensitivity_not_ci(self):
  x=one_event_sensitivity(60,1)
  self.assertEqual(x['cpa_if_one_more_reported'],30)
  self.assertIsNone(x['cpa_if_one_less_reported'])
  self.assertIsNone(one_event_sensitivity(10,None))
 def test_exact_fixed_model_coverage(self):
  from math import exp
  intervals=[poisson_rate_interval(k,1,.05) for k in range(80)]
  for mean in (.1,1,2,5,20):
   probability=exp(-mean);coverage=0
   for k,ci in enumerate(intervals):
    if ci['rate_lower']<=mean<=ci['rate_upper']:coverage+=probability
    probability*=mean/(k+1)
   self.assertGreaterEqual(coverage,.95-1e-9)

class ContractTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,10,20,12,tzinfo=timezone.utc)
  self.identity={'account_id':'synthetic','entity_type':'adset','entity_id':'fixture'}
  self.records={key:{'scope':self.identity.copy(),'source_ref':'synthetic-fixture-not-real-evidence',
   'source_digest':'fixture-digest','observed_at':self.now.isoformat(),'valid_until':(self.now+timedelta(minutes=1)).isoformat(),
   'assessment':'verified','review_kind':'deterministic_source_check','value':{'fixture_only':True}} for key in REQUIRED['scale']}
 def assess(self):return assess_contract('scale',self.identity,self.records,now=self.now)
 def test_complete_contract_cannot_publish(self):
  x=self.assess();self.assertTrue(x['contract_satisfied']);self.assertFalse(x['live_recommendation_allowed']);self.assertFalse(x['execution_allowed'])
 def test_missing_is_specific_and_can_be_cleared(self):
  saved=self.records.pop('economics');x=self.assess();self.assertEqual(set(x['missing']),{'economics'})
  self.records['economics']=saved;self.assertTrue(self.assess()['contract_satisfied'])
 def test_scope_expiry_model_claim_and_binding(self):
  original=deepcopy(self.records)
  for field,value in [('scope',{}),('valid_until',self.now.isoformat()),('review_kind','llm_agreement'),('source_digest','')]:
   self.records=deepcopy(original);self.records['economics'][field]=value
   self.assertIn('economics',self.assess()['rejected'])
  self.records=original;before=self.assess()['evidence_digest'];self.records['proposal']['value']={'changed':True}
  self.assertNotEqual(before,self.assess()['evidence_digest'])
 def test_new_report_does_not_refresh_old_source(self):
  r={'checked_at':self.now.isoformat(),'freshness':{'meta_last_success_at':(self.now-timedelta(days=2)).isoformat()}}
  x=report_assessment(r,{'id':'a'},'ad',{'missing':[]})
  self.assertIn('recent_meta_source_timestamp',x['missing_evidence'])
 def test_malformed_observations_are_not_accepted(self):
  r={'checked_at':self.now.isoformat(),'freshness':{'state':'fresh','meta_last_success_at':self.now.isoformat()}}
  for count in (-1,True,float('nan'),float('inf')):
   row={'id':'a','purchases':count,'configured_active':'yes','effective_status':'ACTIVE',
        'change_evidence':{'entity_id':'other','entity_type':'ad','relevant_change_status':'verified',
                           'relevant_change_time':self.now.isoformat(),'source_ref':'fixture'}}
   x=report_assessment(r,row,'ad',{'missing':[]},now=self.now)
   self.assertNotIn('reported_purchase_count',x['accepted_facts'])
   self.assertNotIn('observed_configuration',x['accepted_facts'])
   self.assertNotIn('relevant_change_time',x['accepted_facts'])
 def test_complete_envelope_is_not_verified_source_or_decision(self):
  x=self.assess()
  self.assertEqual(x['status'],'shadow_envelope_complete')
  self.assertFalse(x['source_content_verified'])
  self.assertFalse(x['decision_value_schema_validated'])
 def test_observed_facts_and_system_gap_are_separate(self):
  report={'account_id':'scope','checked_at':self.now.isoformat(),'freshness':{'state':'fresh','meta_last_success_at':self.now.isoformat()}}
  row={'id':'a','configured_active':True,'effective_status':'ACTIVE','purchases':0,
       'change_evidence':{'entity_id':'a','entity_type':'ad','relevant_change_status':'verified','relevant_change_time':self.now.isoformat(),'source_ref':'saved-change'}}
  result=report_assessment(report,row,'ad',{'missing':[],'status':'conditional','segments':[{}]},now=self.now)
  records={x['name']:x for x in result['evidence_records']}
  self.assertEqual(records['reported_purchases']['status'],'observed')
  self.assertEqual(records['reported_purchases']['value'],0)
  self.assertEqual(records['relevant_change']['status'],'observed')
  self.assertNotIn('relevant_change_history',result['missing_evidence'])
  self.assertIn('executable_financial_policy_not_implemented',result['system_gaps'])
  self.assertNotIn('economic_loss_policy',result['missing_evidence'])
  self.assertFalse(result['capabilities']['paused_content_preparation']['economics_required'])
  row['purchases']=None
  result=report_assessment(report,row,'ad',{'missing':[]},now=self.now)
  self.assertEqual(next(x for x in result['evidence_records'] if x['name']=='reported_purchases')['status'],'unknown')

if __name__=='__main__':unittest.main()
