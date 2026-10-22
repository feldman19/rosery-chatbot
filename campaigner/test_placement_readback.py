import unittest,time
from placement_readback import enrich_placement_readback
class PlacementTests(unittest.TestCase):
 def run_case(self,response):
  ads=[{'id':'20','account_id':'10','creative':{'id':'30'}}]
  def graph(*args):
   if isinstance(response,Exception):raise response
   return response
  enrich_placement_readback(graph,'not_real','10',ads,time.monotonic()+5)
  return ads[0]['placement_readback']
 def test_absence_never_means_missing_story(self):
  x=self.run_case({'data':[{'id':'20','account_id':'10','creative':{'id':'30'}}]})
  self.assertEqual(x['status'],'unknown_field_not_returned');self.assertFalse(x['placement_pair_verified']);self.assertEqual(x['creative_id'],'30')
 def test_field_is_unverified_not_placement_proof(self):
  x=self.run_case({'data':[{'id':'20','account_id':'10','creative':{'id':'30'},'creative_asset_groups_spec':{'groups':[]}}]})
  self.assertEqual(x['status'],'field_returned_unverified');self.assertFalse(x['placement_pair_verified'])
 def test_failure_wrong_scope_changedcreative_or_partial_preserves_unknown(self):
  for response in (ValueError('unsupported'),{'data':[]},{'data':[{'id':'20','account_id':'99','creative':{'id':'30'}}]},{'data':[{'id':'20','account_id':'10','creative':{'id':'31'}}]},{'data':[],'paging':{'next':'x'}}):
   self.assertEqual(self.run_case(response)['status'],'unavailable')
if __name__=='__main__':unittest.main()
