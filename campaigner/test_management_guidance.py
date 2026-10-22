import unittest
from management_guidance import build_guidance


class GuidanceTests(unittest.TestCase):
    def report(self):
        return {'freshness':{'state':'fresh'},'windows':{'7':{'metrics':{'purchases':0,'registrations':0},'notes':[]}},'action_summary':{}}

    def test_zero_is_not_missing(self):
        result=build_guidance(self.report())
        self.assertNotIn('measurement',[x['id'] for x in result['cards']])
        self.assertIsNone(result['business_result']['profit'])
        self.assertFalse(result['live_action_performed'])

    def test_missing_measurement_prioritized_without_budget_advice(self):
        report=self.report();report['windows']['7']['metrics']['purchases']=None
        result=build_guidance(report)
        self.assertEqual(result['next_action']['target'],'measurement')
        self.assertIsNone(result['business_result']['target_cpa'])

    def test_stale_data_before_performance_conclusion(self):
        report=self.report();report['freshness']['state']='stale'
        self.assertEqual(build_guidance(report)['next_action']['target'],'refresh')

    def test_unknown_execution_not_success_and_no_retry(self):
        result=build_guidance(self.report(),{'jobs':[{'id':'a','state':'unknown','action':'activate','steps':[]}]})
        self.assertEqual(result['next_action']['target'],'track')
        self.assertFalse(result['journal'][0]['technical_verified'])

    def test_verified_is_not_business_success(self):
        result=build_guidance(self.report(),{'jobs':[{'id':'a','state':'verified','action':'pause','steps':[{'state':'verified'}]}]})
        self.assertTrue(result['journal'][0]['technical_verified'])
        self.assertEqual(result['journal'][0]['verified_steps'],1)
        self.assertIn('טרם נמדד',result['journal'][0]['business_result'])

    def test_preview_and_unknown_state_never_reported_as_execution(self):
        result=build_guidance(self.report(),{'jobs':[{'state':'prepared'},{'state':'new-state'}]})
        self.assertTrue(all(not x['technical_verified'] for x in result['journal']))
        self.assertIn('דורש בדיקה',result['journal'][0]['state_label'])

    def test_pending_and_evidence_remain_visible(self):
        report=self.report();report['action_summary']['pending_approval_count']=2
        self.assertIn('approval',[x['id'] for x in build_guidance(report)['cards']])


if __name__=='__main__':unittest.main()
