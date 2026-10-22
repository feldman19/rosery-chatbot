from copy import deepcopy
from datetime import datetime, timezone, timedelta
import unittest
from pause_evaluation import evaluate_pause, ACCOUNT, MEANINGS

def fixture():
 now=datetime(2026,10,20,12,tzinfo=timezone.utc)
 row={'id':'synthetic_ad','name':'SYNTHETIC ONLY','spend':100,'purchases':1,
      'configured_active':True,'effective_status':'ACTIVE','result_type':'purchase'}
 report={'account_id':ACCOUNT,'freshness':{'state':'fresh','meta_last_success_at':now.isoformat()},
         'windows':{'7':{'since':'2026-10-13','until':'2026-10-19','ads':[row]}}}
 horizon={'start':now.isoformat(),'end':(now+timedelta(days=1)).isoformat()}
 scenario={'synthetic':True,'horizon':horizon}
 for name,bounds in [('avoidable_spend',(100,100)),('foregone_contribution',(30,50)),('change_cost',(5,10))]:
  scenario[name]={'lower':bounds[0],'upper':bounds[1],'meaning':MEANINGS[name],
     'scope':{'account_id':ACCOUNT,'entity_type':'ad','entity_id':row['id']},
     'horizon':horizon.copy(),'currency':'ILS','source_ref':'synthetic fixture only',
     'assumptions':['Invented bounds for arithmetic test; no business evidence']}
 return now,report,row,scenario

class PauseTests(unittest.TestCase):
 def setUp(self):self.now,self.report,self.row,self.scenario=fixture()
 def evaluate(self,economics=None):
  return evaluate_pause(self.report,self.row,economics or {'status':'synthetic','missing':[]},self.scenario,now=self.now)
 def test_past_cost_and_attributed_purchases_do_not_create_future_savings(self):
  first=self.evaluate();self.assertEqual(first['status'],'SCENARIO_PAUSE_DOMINATES')
  self.row.update(spend=100000,purchases=0)
  second=self.evaluate()
  self.assertEqual(first['benefit_pause_ils'],second['benefit_pause_ils'])
  self.assertEqual(first['status'],second['status'])
  self.assertEqual(first['benefit_pause_ils'],{'lower':'40','upper':'65'})
  self.assertFalse(first['live_recommendation_allowed'])
 def test_redistribution_can_remove_saving_and_reverse_result(self):
  self.scenario['avoidable_spend'].update(lower=0,upper=0)
  result=self.evaluate();self.assertEqual(result['status'],'SCENARIO_CONTINUE_DOMINATES')
  self.assertEqual(result['error_cost_bounds_ils']['false_pause_upper'],'60')
 def test_credible_range_crossing_zero_requires_abstention(self):
  self.scenario['foregone_contribution'].update(lower=30,upper=130)
  self.assertEqual(self.evaluate()['status'],'ABSTAIN')
 def test_attributed_sales_are_not_incremental_contribution(self):
  self.scenario['foregone_contribution']['meaning']='reported_meta_revenue'
  result=self.evaluate();self.assertEqual(result['status'],'ABSTAIN')
  self.assertIn('foregone_contribution:meaning_scope_or_provenance',result['missing'])
 def test_missing_economics_and_scope_block_even_positive_arithmetic(self):
  self.assertEqual(self.evaluate({'missing':['vat_basis']})['status'],'ABSTAIN')
  self.scenario['avoidable_spend']['scope']['entity_id']='other'
  self.assertEqual(self.evaluate()['status'],'ABSTAIN')
 def test_unknown_future_inputs_do_not_get_inferred_from_past(self):
  result=evaluate_pause(self.report,self.row,{'missing':[]},now=self.now)
  self.assertEqual(result['status'],'ABSTAIN');self.assertIsNone(result['benefit_pause_ils'])
  self.assertIn('avoidable_spend',result['missing'])
 def test_unchanged_fixed_cost_reserve_does_not_block_relative_comparison(self):
  result=self.evaluate({'missing':['owner_profit_and_fixed_cost_reserve']})
  self.assertEqual(result['status'],'SCENARIO_PAUSE_DOMINATES')

if __name__=='__main__':unittest.main()
