import json
import unittest
from copy import deepcopy
from pathlib import Path
from saved_metric_audit import compare_saved, number
from test_management_report import ManagementReportTests

class SavedEvidenceTests(unittest.TestCase):
    def test_actual_saved_contradiction_is_blocked_without_inventing_context(self):
        path=Path(__file__).resolve().parents[1]/'outputs/rosery-daily-review-2026-10-21.json'
        evidence=json.loads(path.read_text(encoding='utf-8'))
        result=compare_saved(evidence['metaPurchaseDaily'],evidence['metaSpendDaily'])
        self.assertFalse(result['efficiency_allowed'])
        self.assertIn('attribution_setting',result['missing_context'])
        self.assertTrue(any(c['key'][1]=='2026-10-20' and c['left_spend']=='0'
                            and c['right_spend']=='723.34' for c in result['candidate_conflicts']))
    def test_synthetic_matched_and_missing_values(self):
        context=dict(account_id='a',level='account',since='2026-10-20',until='2026-10-20',
                     timezone='Asia/Jerusalem',currency='ILS',action_report_time='impression',
                     attribution_setting='7d_click',breakdowns=[],observed_at='2026-10-21T06:00:00Z')
        row=dict(id='a',date_start='2026-10-20',date_stop='2026-10-20',amount_spent={'value':'0'})
        a={'ad_entities':[row]}
        self.assertTrue(compare_saved(a,a,context,context)['efficiency_allowed'])
        b=deepcopy(a);b['ad_entities'][0]['amount_spent']=None
        self.assertFalse(compare_saved(a,b,context,context)['efficiency_allowed'])
        self.assertEqual(number('0'),0);self.assertIsNone(number(None))
        for key in context:
            changed={**context,key:'different'}
            self.assertFalse(compare_saved(a,a,context,changed)['efficiency_allowed'],key)

class ReportPropagationTests(ManagementReportTests):
    def test_conflict_blocks_child_ratios(self):
        self.hierarchy()
        data=self.snapshot['sources']['meta']['data']
        data['rows']=[r for r in data['rows'] if r['ad_id']=='ad1']
        source=self.campaign_comparison();source['rows'][0]['spend']='723.34'
        result=self.report()['windows']['7']
        for kind in ('campaigns','adsets','ads'):
            for row in result[kind]:
                self.assertIsNone(row['spend']);self.assertIsNone(row['roas'])
                self.assertEqual(row['efficiency_integrity']['status'],'blocked')
    def test_incompatible_comparison_blocks_ratios(self):
        source=self.campaign_comparison();source['timezone']='America/New_York'
        result=self.report()['windows']['7']
        self.assertIsNone(result['metrics']['roas'])
        self.assertIsNone(result['campaigns'][0]['spend'])
    def test_compatible_comparison_preserves_ratios(self):
        self.campaign_comparison()
        result=self.report()['windows']['7']
        self.assertEqual(result['metrics']['roas'],5)
        self.assertEqual(result['campaigns'][0]['spend'],70)

if __name__=='__main__':unittest.main()
