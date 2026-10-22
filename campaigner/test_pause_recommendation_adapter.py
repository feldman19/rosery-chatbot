"""Synthetic envelopes prove integration, not profitability or forecast quality."""
import unittest
import hashlib
import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from test_pause_evaluation import fixture
from action_recommendations import build_recommendations

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.now,self.report,self.row,self.scenario=fixture()
        self.report['checked_at']=self.now.isoformat();self.report['currency']='ILS'
        self.report['windows']['7']['partial']=False
        self.scenario['avoidable_spend']['net_of_budget_redistribution']=True
        self.scenario['foregone_contribution']['includes_substitution']=True
        self.scenario['change_cost']['excludes_foregone_contribution_costs']=True
    def envelope(self):
        return {'scope':self.scenario['avoidable_spend']['scope'].copy(),'source_ref':'synthetic only',
            'source_digest':hashlib.sha256(json.dumps(self.scenario,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
            'observed_at':self.now.isoformat(),'valid_until':(self.now+timedelta(hours=1)).isoformat(),
            'review_kind':'documented_human_review','value':deepcopy(self.scenario)}
    def evaluate(self,record=None,missing=None):
        dossier={'business_id':'rosery','owner_evidence':{'pause_scenarios':[record or self.envelope()]}}
        economics={'status':'synthetic','missing':missing or [],'segments':[]}
        with patch('action_recommendations.order_economics',return_value=economics):
            result=build_recommendations(self.report,dossier,now=self.now)
        item=result['recommendations'][0]
        self.assertEqual(item['action'],'INSUFFICIENT_EVIDENCE');self.assertFalse(item['live_change'])
        self.assertFalse(item['pause_evaluation']['execution_allowed'])
        return item['pause_evaluation']
    def test_positive_negative_overlap_are_only_conditional(self):
        result=self.evaluate();self.assertEqual(result['status'],'SCENARIO_PAUSE_DOMINATES');self.assertEqual(result['evidence_status'],'conditional')
        self.scenario['avoidable_spend'].update(lower=0,upper=0)
        self.assertEqual(self.evaluate()['status'],'SCENARIO_CONTINUE_DOMINATES')
        self.scenario['avoidable_spend'].update(lower=100,upper=100)
        self.scenario['foregone_contribution'].update(lower=30,upper=130)
        result=self.evaluate();self.assertEqual(result['status'],'ABSTAIN');self.assertEqual(result['evidence_status'],'conditional')
    def test_invalid_provenance_currency_horizon_scope_and_doublecount_block(self):
        for field,value in [('currency','USD'),('scope',{}),('horizon',{}),('meaning','reported_meta_revenue')]:
            saved=deepcopy(self.scenario);self.scenario['foregone_contribution'][field]=value
            self.assertEqual(self.evaluate()['evidence_status'],'blocked');self.scenario=saved
        self.scenario['change_cost']['excludes_foregone_contribution_costs']=False
        self.assertEqual(self.evaluate()['evidence_status'],'blocked')
        record=self.envelope();record['source_digest']='wrong'
        self.assertEqual(self.evaluate(record)['evidence_status'],'blocked')
        record=self.envelope();record['scope']['entity_id']='different'
        self.assertEqual(self.evaluate(record)['evidence_status'],'blocked')
    def test_actual_missing_economics_block(self):
        self.assertEqual(self.evaluate(missing=['reconciled_cogs_coverage'])['evidence_status'],'blocked')
    def test_readiness_separates_missing_evidence_from_unimplemented_policy(self):
        ready=self.evaluate()['decision_readiness']
        self.assertEqual(ready['state'],'scenario_ready_not_decision')
        self.assertEqual(ready['evidence_gaps'],[])
        self.assertIn('prospective_pause_effect_validation_not_implemented',ready['system_gaps'])
        self.assertFalse(ready['approval_ready'])
        blocked=self.evaluate(missing=['reconciled_cogs_coverage'])['decision_readiness']
        self.assertEqual(blocked['state'],'blocked_by_evidence')
        self.assertIn('economics:reconciled_cogs_coverage',blocked['evidence_gaps'])
        self.assertFalse(blocked['requires_owner_threshold'])
    def test_no_scenario_high_meta_roas_does_not_invent_incrementality(self):
        self.row.update(roas=1000,attributed_revenue=100000,purchases=999)
        result=build_recommendations(self.report,now=self.now)
        pause=result['recommendations'][0]['pause_evaluation']
        self.assertEqual(pause['policy_status'],'implemented_scenario_only')
        self.assertEqual(pause['evidence_status'],'blocked');self.assertIsNone(pause['benefit_pause_ils'])
        self.assertIn('scoped_future_evidence_not_recorded',pause['missing'])
        self.assertEqual(result['policy_status']['scale'],'scale_forecast_not_implemented')

if __name__=='__main__':unittest.main()
