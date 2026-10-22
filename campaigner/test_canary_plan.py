import copy
import unittest
from canary_plan import build_candidate

class CanaryPlanTests(unittest.TestCase):
    def ad(self):return {'primary_text':'Reviewed copy','primary_text_options':['Other'],
        'headline':'Headline','landing_url':'https://roseryjewelry.com/', 'url_tags':'utm_content=c11'}
    def test_exact_narrowed_candidate_without_guessed_enhancements(self):
        ad=self.ad();before=copy.deepcopy(ad);p=build_candidate(ad,'a'*32,'b'*32)
        self.assertNotIn('degrees_of_freedom_spec',p)
        self.assertEqual(p['asset_feed_spec']['bodies'],[{'text':'Reviewed copy'}])
        self.assertEqual(p['url_tags'],'utm_content=c11');self.assertEqual(ad,before)
        self.assertEqual([x['hash'] for x in p['asset_feed_spec']['images']],['a'*32,'b'*32])
    def test_missing_tracking_or_external_destination_blocks(self):
        for key,value in [('url_tags',None),('landing_url','https://other.example/')]:
            ad=self.ad();ad[key]=value
            with self.assertRaises(ValueError):build_candidate(ad,'a'*32,'b'*32)
    def test_hashes_must_be_exact(self):
        with self.assertRaises(ValueError):build_candidate(self.ad(),'guessed','b'*32)

if __name__=='__main__':unittest.main()
