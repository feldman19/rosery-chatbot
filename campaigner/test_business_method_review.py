import unittest
from business_method_review import review_creative_method
from imported_business_method import load_imported_method

class MethodReviewTest(unittest.TestCase):
    def needs_import(self):
        if load_imported_method().get('status')!='available':
            self.skipTest('needs the private imported Claude-project export in outputs/ (not in git)')
    def test_real_rules_flag_copy_not_audience_or_finance(self):
        self.needs_import()
        result=review_creative_method([{'asset_id':'a','headline':'הנמכר ביותר 🔥'}])
        self.assertEqual(result['status'],'review_required')
        self.assertEqual({i['rule_id'] for i in result['issues']},{'headline_no_emoji','no_unverified_superlatives'})
        self.assertFalse(result['live_action_allowed'])
    def test_no_rewriting_or_gender_assumption(self):
        row={'asset_id':'a','headline':'מתנה בשבילו','primary_text':'בחרי את המתנה שלך'}
        self.assertEqual(review_creative_method([row])['issues'],[])
        self.assertEqual(row['primary_text'],'בחרי את המתנה שלך')
    def test_unavailable_never_claims_reviewed(self):
        r=review_creative_method([{'headline':'🔥'}],{'status':'unavailable'})
        self.assertEqual(r['status'],'unavailable');self.assertEqual(r['issues'],[])
    def test_source_bound_preferences_no_execution(self):
        self.needs_import()
        method=load_imported_method()
        self.assertEqual(method['status'],'available')
        self.assertFalse(method['financial_thresholds_executable'])
        self.assertFalse(method['approval_imported'])
