import copy
from datetime import datetime, timezone
import unittest

from weekly_creative_context import weekly_creative_context, ACCOUNT


class WeeklyCreativeContextTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,21,10,tzinfo=timezone.utc)
        self.stamp=self.now.isoformat()
        self.row={'id':'111','name':'Clothing','campaign_id':'222','adset_id':'333','result_type':'purchase','purchases':4}
        self.report={'account_id':ACCOUNT,'freshness':{'state':'fresh','meta_last_success_at':self.stamp},'windows':{'7':{'since':'2026-10-14','until':'2026-10-20','partial':False,'ads':[self.row]}}}
        self.creative={'id':'111','campaign_id':'222','adset_id':'333','creative_id':'444','primary_text':'Observed copy','headline':'Observed headline','description':None,'image_url':'https://scontent.fbcdn.net/photo.jpg'}
        self.workspace={'account_id':ACCOUNT,'checked_at':self.stamp,'refresh_error':False,'ads':[self.creative]}
        self.raw={'id':'111','account_id':ACCOUNT,'campaign_id':'222','adset_id':'333','creative':{'id':'444'}}
        self.snapshot={'sources':{'meta':{'last_success_at':self.stamp,'data':{'account_id':ACCOUNT,'verified':True,'inventory':{'ads':[self.raw]}}}}}

    def get(self):return weekly_creative_context(self.report,self.workspace,snapshot=self.snapshot,now=self.now)

    def test_exact_join_preserves_content_without_historical_or_causal_claim(self):
        result=self.get();self.assertTrue(result['available'])
        item=result['items'][0]
        self.assertEqual(item['primary_text'],'Observed copy')
        self.assertEqual(item['performance']['reported_purchases'],4)
        self.assertFalse(item['historical_creative_match'])
        self.assertFalse(item['causal_explanation_available'])
        self.assertEqual(item['content_observed_at'],self.stamp)
        self.assertEqual(len(item['source_digest']),64)
        self.assertEqual(item['source_digest'],self.get()['items'][0]['source_digest'])
        self.creative['primary_text']='Changed copy'
        self.assertNotEqual(item['source_digest'],self.get()['items'][0]['source_digest'])

    def test_duplicate_or_mismatched_identity_is_not_joined(self):
        for collection in (self.report['windows']['7']['ads'],self.workspace['ads']):
            collection.append(copy.deepcopy(collection[0]))
            self.assertFalse(self.get()['available']);collection.pop()
        for key in ('campaign_id','adset_id','creative_id'):
            old=self.creative[key];self.creative[key]=''
            self.assertFalse(self.get()['available']);self.creative[key]=old

    def test_partial_registration_missing_and_zero_results_not_linked(self):
        for value in (None,0,True,float('nan')):
            self.row['purchases']=value;self.assertFalse(self.get()['available'])
        self.row['purchases']=4;self.row['result_type']='registration'
        self.assertFalse(self.get()['available'])

    def test_unknown_objective_still_links_reported_purchases_without_sales_claim(self):
        self.row['result_type']='unknown'
        self.report['windows']['7']['ads'].extend([None,'bad',{'id':[]}])
        self.workspace['ads'].extend([None,{'id':[]}])
        item=self.get()['items'][0]
        self.assertEqual(item['observed_result_type'],'unknown')
        self.assertEqual(item['performance']['reported_purchases'],4)
        self.assertFalse(item['causal_explanation_available'])
        self.row['result_type']='purchase';self.report['windows']['7']['partial']=True
        self.assertFalse(self.get()['available'])

    def test_stale_mismatched_or_failed_snapshot_has_no_content(self):
        self.workspace['checked_at']='2026-10-21T09:00:00+00:00'
        self.assertFalse(self.get()['available'])
        self.workspace['checked_at']=self.stamp;self.workspace['refresh_error']=True
        self.assertFalse(self.get()['available'])
        self.workspace['refresh_error']=False;self.workspace['account_id']='another'
        self.assertFalse(self.get()['available'])

    def test_untrusted_image_removed_and_meta_url_locally_bound(self):
        self.creative['image_url']='https://scontent.fbcdn.net.evil.test/photo.jpg'
        self.creative['meta_url']='https://evil.test'
        item=self.get()['items'][0]
        self.assertIsNone(item['image_url'])
        self.assertIn('selected_ad_ids=111',item['meta_url'])

    def test_conflicting_raw_duplicate_cannot_be_hidden_by_workspace_merge(self):
        data=self.snapshot['sources']['meta']['data']
        data['active_ads']={'ads':[{**self.raw,'creative':{'id':'555'}}]}
        self.assertFalse(self.get()['available'])
        data['active_ads']['ads']=[{**self.raw,'campaign_id':'999'}]
        self.assertFalse(self.get()['available'])
        data['active_ads']['ads']=[dict(self.raw)]
        self.creative['text_options']=['First','Second'];self.creative['has_placement_rules']=True
        item=self.get()['items'][0]
        self.assertEqual(item['text_options'],['First','Second'])
        self.assertTrue(item['has_placement_rules'])


if __name__=='__main__':unittest.main()
