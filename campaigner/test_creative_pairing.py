"""Exact source pairing diagnostics; synthetic inputs, no providers."""
import unittest
from creative_preparation import build_creative_draft,CreativePairingError
from creative_grouping import validate_proposed_adsets
from test_batch_capacity import BatchCapacityTests
class PairingTests(unittest.TestCase):
 def test_each_missing_format_names_exact_source(self):
  for index,missing in [(0,'story'),(1,'feed')]:
   assets,analysis=BatchCapacityTests().materials(1)
   assets=[assets[index]];analysis['assets']=[analysis['assets'][index]];analysis['assets'][0]['paired_asset_id']=None;analysis['proposed_adsets']=[]
   self.assertEqual(validate_proposed_adsets([],analysis['assets']),[])
   with self.assertRaises(CreativePairingError) as caught:build_creative_draft({'id':'request','revision':3},assets,analysis)
   issue=caught.exception.source_issue
   self.assertEqual(issue['asset_id'],assets[0]['id']);self.assertEqual(issue['sha256'],assets[0]['sha256']);self.assertEqual(issue['missing_format'],missing);self.assertEqual(issue['request_revision'],3)
   self.assertIn(assets[0]['name'],str(caught.exception))
 def test_mismatched_hash_never_produces_source_issue(self):
  assets,analysis=BatchCapacityTests().materials(1);assets=assets[:1];analysis['assets']=analysis['assets'][:1];analysis['assets'][0]['sha256']='b'*64
  with self.assertRaises(ValueError) as caught:build_creative_draft({'id':'request','revision':1},assets,analysis)
  self.assertNotIsInstance(caught.exception,CreativePairingError)
