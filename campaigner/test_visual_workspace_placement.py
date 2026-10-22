import unittest
from datetime import datetime,timezone
from visual_workspace import build_workspace,ACCOUNT
class ProjectionTests(unittest.TestCase):
 def fixture(self):
  ad={'id':'20','account_id':ACCOUNT,'creative':{'id':'30'},'placement_readback':{'status':'field_returned_unverified','checked_at':datetime.now(timezone.utc).isoformat(),'account_id':ACCOUNT,'ad_id':'20','creative_id':'30','placement_pair_verified':True,'rendered_preview_verified':True,'creative_asset_groups_spec':{'secret':'not public'}}}
  return ad,{'sources':{'meta':{'data':{'account_id':ACCOUNT,'verified':True,'active_ads':{'ads':[ad]}}}}}
 def test_public_summary_bounded_never_promotes_verification(self):
  ad,snapshot=self.fixture();result=build_workspace(snapshot)['ads'][0]['placement_readback']
  self.assertEqual(set(result),{'status','checked_at','account_id','ad_id','creative_id','placement_pair_verified','rendered_preview_verified'})
  self.assertFalse(result['placement_pair_verified']);self.assertFalse(result['rendered_preview_verified'])
 def test_invalid_status_binding_time_or_absence_not_projected(self):
  for field,value in (('status','verified'),('account_id','other'),('ad_id','21'),('creative_id','31'),('checked_at','2099-01-01T00:00:00+00:00'),('checked_at','invalid')):
   ad,snapshot=self.fixture();ad['placement_readback'][field]=value
   self.assertIsNone(build_workspace(snapshot)['ads'][0]['placement_readback'])
  ad,snapshot=self.fixture();ad.pop('placement_readback');self.assertIsNone(build_workspace(snapshot)['ads'][0]['placement_readback'])
if __name__=='__main__':unittest.main()
