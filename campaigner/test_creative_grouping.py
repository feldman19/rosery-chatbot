"""Synthetic model contract tests, no model calls or advertising writes."""
from copy import deepcopy
import unittest
import test_batch_capacity as fixtures
from creative_preparation import build_creative_draft
from creative_grouping import validate_proposed_adsets

class ProposedGroupingTests(unittest.TestCase):
 def setUp(self):
  self.assets,self.analysis=fixtures.BatchCapacityTests().materials(5)
  for n,row in enumerate(self.analysis['assets']):row['angle']='distinct angle '+str(n//2)
  self.groups=[{'key':'clothes','label':'בגדים','rationale':'בדיקת מוצרים מאותה קטגוריה','concept_keys':[str(n) for n in range(5)]}]
 def test_explicit_group_overrides_literal_angle_without_changing_ads(self):
  self.analysis['proposed_adsets']=self.groups
  draft=build_creative_draft({'id':'fake','revision':1},self.assets,self.analysis)
  self.assertEqual(len(draft['adsets']),1);self.assertEqual(len(draft['ads']),5)
  self.assertEqual({a['adset_key'] for a in draft['ads']},{draft['adsets'][0]['key']})
  self.assertTrue(draft['adsets'][0]['key'].startswith('creative_'));self.assertEqual(draft['adsets'][0]['advisory_key'],'clothes')
  self.assertEqual(len({a['classification']['angle'] for a in draft['ads']}),5)
  self.assertIsNone(draft['adsets'][0]['audience_hypothesis']);self.assertFalse(draft['activation_available'])
 def test_missing_duplicate_unknown_and_extra_authority_rejected(self):
  for mutate in (lambda g:g[0]['concept_keys'].pop(),lambda g:g[0]['concept_keys'].append('0'),lambda g:g[0]['concept_keys'].append('unknown'),lambda g:g[0].update(targeting='women')):
   groups=deepcopy(self.groups);mutate(groups)
   with self.assertRaises(ValueError):validate_proposed_adsets(groups,self.analysis['assets'])
 def test_old_analysis_preserves_old_grouping_not_silent_merge(self):
  with self.assertRaises(ValueError):build_creative_draft({'id':'fake','revision':1},self.assets,self.analysis)
 def test_empty_or_four_groups_rejected(self):
  for groups in ([],self.groups*4):
   with self.assertRaises(ValueError):validate_proposed_adsets(groups,self.analysis['assets'])
 def test_provenance_retained_and_cannot_change_source(self):
  self.analysis['proposed_adsets']=self.groups
  self.analysis['review_provenance']=[{'asset_id':a['id'],'sha256':a['sha256'],'review_sha256':'b'*64,'review_derivative':True,'source_bytes':10000,'review_bytes':1000,'width':1080,'height':1350} for a in self.assets]
  draft=build_creative_draft({'id':'fake','revision':1},self.assets,self.analysis)
  self.assertEqual(draft['review_provenance'],self.analysis['review_provenance'])
  self.analysis['review_provenance'][0]['sha256']='c'*64
  with self.assertRaises(ValueError):build_creative_draft({'id':'fake','revision':1},self.assets,self.analysis)
