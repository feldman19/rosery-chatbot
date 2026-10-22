"""Synthetic, offline tests for actual creative URL attribution ambiguity."""
import copy
import unittest
from datetime import datetime,timedelta,timezone
from urllib.parse import parse_qs,urlsplit
from creative_audit import audit
from meta_direct import ACCOUNT,LAUNCH

NOW=datetime(2026,10,17,12,tzinfo=timezone.utc)
URL='https://roseryjewelry.com/pages/launch?variant=123&discount=KEEP&utm_source=meta&utm_medium=paid&utm_campaign=launch&utm_content=shared#signup'

def fixture():
 ads=[{'id':str(i),'name':'synthetic '+str(i),'adset_id':'set1','effective_status':'ACTIVE','creative':{'id':'creative'+str(i),'object_story_spec':{'link_data':{'link':URL}}}} for i in (1,2)]
 return {'sources':{'meta':{'last_success_at':NOW.isoformat(),'error':None,'data':{'verified':True,'complete':True,'account_id':ACCOUNT,'inventory':{'verified':True,'campaign':{'id':LAUNCH,'account_id':ACCOUNT,'effective_status':'ACTIVE'},'adsets':[{'id':'set1','effective_status':'ACTIVE'},{'id':'set2','effective_status':'ACTIVE'}],'ads':copy.deepcopy(ads)},'creative_inventory':{'verified':True,'account_id':ACCOUNT,'campaign_id':LAUNCH,'ads':ads}}}}}

class CreativeAuditTests(unittest.TestCase):
 def setUp(self):
  self.snapshot=fixture();self.source=self.snapshot['sources']['meta'];self.data=self.source['data'];self.ads=self.data['creative_inventory']['ads'];self.base=self.data['inventory']
 def result(self):return audit(self.snapshot,now=NOW)
 def test_two_active_same_utm_content_is_one_collision(self):
  r=self.result();self.assertTrue(r['verified']);self.assertEqual(2,r['active_ads_checked']);self.assertEqual(1,len(r['findings']));self.assertFalse(r['live_action_allowed'])
  self.assertEqual({'1','2'},{a['ad_id'] for a in r['findings'][0]['evidence']['ads']})
 def test_parent_campaign_paused_no_collision(self):
  self.base['campaign']['effective_status']='PAUSED';r=self.result();self.assertEqual([],r['findings']);self.assertEqual(0,r['active_ads_checked'])
 def test_parent_adset_paused_no_collision(self):
  self.base['adsets'][0]['effective_status']='PAUSED';self.assertEqual([],self.result()['findings'])
 def test_paused_child_no_collision(self):
  self.ads[1]['effective_status']='PAUSED';self.assertEqual([],self.result()['findings'])
 def test_base_ad_paused_no_collision(self):
  self.base['ads'][1]['effective_status']='PAUSED';self.assertEqual([],self.result()['findings'])
 def test_absent_creative_is_unknown_not_collision(self):
  self.ads[1].pop('creative');r=self.result();self.assertEqual([],r['findings']);self.assertIn('2',r['unknown_ads'])
 def test_unverified_creative_inventory_fails_closed(self):
  self.data['creative_inventory']['verified']=False;r=self.result();self.assertFalse(r['verified']);self.assertEqual([],r['findings'])
 def test_stale_or_source_error_fails_closed(self):
  self.source['last_success_at']=(NOW-timedelta(minutes=13)).isoformat();self.assertFalse(self.result()['verified'])
  self.source['last_success_at']=NOW.isoformat();self.source['error']='provider failure';self.assertFalse(self.result()['verified'])
 def test_unique_static_ad_id_prevents_false_collision(self):
  for ad in self.ads:ad['creative']['url_tags']='ad_id='+ad['id']
  self.assertEqual([],self.result()['findings'])
 def test_dynamic_ad_id_macro_prevents_false_collision(self):
  for ad in self.ads:ad['creative']['url_tags']='ad_id={{ad.id}}'
  self.assertEqual([],self.result()['findings'])
 def test_distinct_utm_content_prevents_false_collision(self):
  for ad in self.ads:ad['creative']['object_story_spec']['link_data']['link']=URL.replace('shared',ad['id'])
  self.assertEqual([],self.result()['findings'])
 def test_conflicting_url_and_tags_unknown(self):
  for ad in self.ads:ad['creative']['url_tags']='utm_content=different'
  r=self.result();self.assertEqual([],r['findings']);self.assertEqual(['1','2'],r['unknown_ads'])
 def test_same_ad_duplicate_cta_not_counted_as_two_ads(self):
  self.ads.pop();self.base['ads'].pop()
  self.ads[0]['creative']['object_story_spec']['link_data']['call_to_action']={'value':{'link':URL}}
  r=self.result();self.assertEqual(1,r['active_ads_checked']);self.assertEqual([],r['findings'])
 def test_proposed_urls_preserve_business_params_and_distinct_ids(self):
  self.ads[0]['creative']['url_tags']='custom_business=keep&utm_content=shared'
  self.ads[1]['creative']['url_tags']='custom_business=keep&utm_content=shared'
  r=self.result();self.assertEqual(1,len(r['findings']))
  for entry in r['findings'][0]['evidence']['ads']:
   p=urlsplit(entry['proposed_url']);q=parse_qs(p.query)
   self.assertEqual(['123'],q['variant']);self.assertEqual(['KEEP'],q['discount']);self.assertEqual([entry['ad_id']],q['utm_content']);self.assertEqual('signup',p.fragment)
   self.assertEqual(['keep'],parse_qs(entry['proposed_url_tags'])['custom_business']);self.assertNotIn('utm_content',parse_qs(entry['proposed_url_tags']))
 def test_inventory_missing_ad_cannot_claim_complete(self):
  self.base['ads'].pop();self.assertFalse(self.result()['verified'])
 def test_duplicate_ad_ids_fails_closed(self):
  self.ads.append(copy.deepcopy(self.ads[0]));self.assertFalse(self.result()['verified'])
 def test_creative_adset_drift_is_not_verified_collision(self):
  self.ads[1]['adset_id']='set2';r=self.result()
  self.assertTrue(not r['verified'] or ('2' in r['unknown_ads'] and not r['findings']))
 def test_creative_identity_drift_is_not_verified_collision(self):
  self.ads[1]['creative']['id']='changed-after-base-read';r=self.result()
  self.assertTrue(not r['verified'] or ('2' in r['unknown_ads'] and not r['findings']))
 def test_snapshot_not_mutated(self):
  before=copy.deepcopy(self.snapshot);self.result();self.assertEqual(before,self.snapshot)

if __name__=='__main__':unittest.main()
