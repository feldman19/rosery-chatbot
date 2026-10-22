"""Offline operator-facing service tests; settings fixes do not need cohort LTV."""
import copy
import unittest
from datetime import timedelta
from operator_service import report
from test_creative_audit import fixture,NOW
from meta_direct import LAUNCH

class OperatorServiceTests(unittest.TestCase):
 def setUp(self):self.snapshot=fixture()
 def call(self):return report(self.snapshot,now=NOW)
 def test_missing_cohort_does_not_block_tracking_case(self):
  r=self.call();cases=[x for x in r['cases'] if x['type']=='tracking_fix'];self.assertEqual(1,len(cases));self.assertEqual(cases[0]['id'],r['next'])
  self.assertEqual('prepared_for_review',cases[0]['state']);self.assertTrue(cases[0]['requires_approval'])
 def test_no_authority_or_provider_writes(self):
  r=self.call();self.assertFalse(r['live_action_allowed']);self.assertEqual(0,r['provider_writes'])
  self.assertTrue(all(not c['execution_available'] for c in r['cases']))
 def test_prepared_payload_has_real_ids_hashes_exact_before_after(self):
  c=next(x for x in self.call()['cases'] if x['type']=='tracking_fix')
  self.assertEqual(LAUNCH,c['evidence']['campaign_id']);self.assertEqual({'1','2'},{a['ad_id'] for a in c['evidence']['ads']})
  self.assertTrue(c['evidence_refs'][0].startswith('sha256:'));self.assertNotEqual(c['before'],c['after'])
  self.assertEqual(2,len(c['before']['urls']));self.assertEqual(2,len(c['after']['urls']))
 def test_missing_and_stale_never_claim_completed(self):
  for snapshot in ({'sources':{}},copy.deepcopy(self.snapshot)):
   if snapshot.get('sources',{}).get('meta'):snapshot['sources']['meta']['last_success_at']=(NOW-timedelta(minutes=13)).isoformat()
   r=report(snapshot,now=NOW);self.assertNotIn('הבדיקה הושלמה',r['headline']);self.assertFalse(r['tracking']['verified'])
 def test_paused_campaign_does_not_prepare_tracking_fix(self):
  self.snapshot['sources']['meta']['data']['inventory']['campaign']['effective_status']='PAUSED'
  r=self.call();self.assertFalse(any(x['type']=='tracking_fix' for x in r['cases']));self.assertEqual(0,r['tracking']['active_ads_checked'])
 def test_headline_case_count_matches_actual_cases(self):
  r=self.call();self.assertTrue(str(len(r['cases'])) in r['headline'] or (len(r['cases'])==1 and 'אחד' in r['headline']));self.assertIsNotNone(r['next'])
 def test_unknown_destinations_do_not_claim_completed_audit(self):
  data=self.snapshot['sources']['meta']['data']
  for ad in data['creative_inventory']['ads']:ad['creative'].pop('object_story_spec')
  r=self.call();self.assertEqual(['1','2'],r['tracking']['unknown_ads']);self.assertNotIn('הבדיקה הושלמה',r['headline'])
 def test_does_not_mutate_source(self):
  saved=copy.deepcopy(self.snapshot);self.call();self.assertEqual(saved,self.snapshot)
if __name__=='__main__':unittest.main()
