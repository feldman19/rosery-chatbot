"""No model calls when creative brief exceeds existing sharing consent."""
import unittest
import json
import test_creative_vision as fixtures
from creative_vision import CreativeVision

class BriefPrivacyTests(unittest.TestCase):
    setUp=fixtures.VisionTests.setUp
    row=fixtures.VisionTests.row
    def test_hebrew_finance_contacts_never_reach_runner(self):
        cases=['שים 100 שקל','תקציב 100','תקציב יומי מאה','100 ש״ח','רווח 3000','הכנסות: 2000',
               'הטלפון הוא 054-123-4567','חייג +972 54 123 4567','שם הלקוחה: דנה','כתובת למשלוח: הרצל 5',
               'סיסמה: משהו','ת\u200bקציב 100','Budget USD 100','ROAS 3']
        called=[]
        for brief in cases:
            with self.subTest(brief=brief),self.assertRaises(ValueError)as error:
                CreativeVision(self.root,lambda p:called.append(p)).analyze([self.asset],owner_brief=brief)
            self.assertIn('לא נשלח',str(error.exception))
        self.assertEqual(called,[])
    def test_adcounts_and_catalog_goals_transmitted_unchanged(self):
        captured=[];vision=CreativeVision(self.root,lambda p:captured.append(json.loads(p))or{'assets':[self.row()]})
        briefs=['5 מודעות 2 אדסטים לחולצות','5מודעות 2אדסטים בגדים לקהל חדש','חולצות במידות 36 עד 44 עם 100% כותנה','צילום תקריב של תכשיט זהב 14 קראט']
        for brief in briefs:vision.analyze([self.asset],owner_brief=brief)
        self.assertEqual(len(captured),len(briefs))
        for payload,brief in zip(captured,briefs):self.assertIn(brief,payload['message']['content'][1]['text'])

if __name__=='__main__':unittest.main()
