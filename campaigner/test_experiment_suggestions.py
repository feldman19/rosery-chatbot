import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
from experiment_suggestions import propose_experiments, _descriptive_candidates, assess_experiment_evidence
from owner_action_results import execute_local

class Tests(unittest.TestCase):
 def setUp(self):
  rows=[{'id':k,'name':k,'adset_id':'s','campaign_id':'c','configured_active':True,'result_type':'purchase','spend':v,'purchases':2} for k,v in [('a',50),('b',100)]]
  self.report={'account_id':'393059510164294','freshness':{'state':'fresh'},'windows':{k:{'partial':False,'ads':deepcopy(rows)} for k in ('3','7')}}
 def test_preparation_only_and_persistence(self):
  cards=_descriptive_candidates(self.report);self.assertEqual(len(cards),1)
  c=cards[0]
  with TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
   body={'id':c['id'],'source_binding':c['source_binding']}
   result=execute_local(Path(d),self.report,cards,body)
   self.assertEqual(result,execute_local(Path(d),self.report,cards,body))
   self.assertEqual(result['provider_writes'],0);self.assertFalse(result['brief']['launch_authorized']);self.assertIsNone(result['brief']['budget'])
   with self.assertRaises(ValueError):execute_local(Path(d),self.report,cards,{**body,'source_binding':{}})
 def test_rankings_never_publish_recommendations(self):
  for purchases in (1,2,50,500):
   for window in self.report['windows'].values():
    for ad in window['ads']:ad['purchases']=purchases
   self.assertEqual(propose_experiments(self.report),[])
   self.assertFalse(assess_experiment_evidence(self.report)['recommendation_allowed'])
 def test_withdrawn_card_cannot_be_prepared(self):
  old=_descriptive_candidates(self.report)[0]
  with TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
   with self.assertRaises(ValueError):execute_local(Path(d),self.report,propose_experiments(self.report),{'id':old['id'],'source_binding':old['source_binding']})
 def test_stale_partial_missing_or_paused(self):
  for change in ('stale','partial','missing','paused'):
   r=deepcopy(self.report)
   if change=='stale':r['freshness']['state']='stale'
   if change=='partial':r['windows']['3']['partial']=True
   if change=='missing':r['windows']['3']['ads'][0]['purchases']=None
   if change=='paused':r['windows']['3']['ads'][0]['configured_active']=False
   self.assertEqual(propose_experiments(r),[])
 def test_reversed_recent_performance(self):
  self.report['windows']['3']['ads'][0]['spend']=200
  self.assertEqual(propose_experiments(self.report),[])
 def test_different_groups(self):
  self.report['windows']['7']['ads'][0]['adset_id']='other'
  self.assertEqual(propose_experiments(self.report),[])

if __name__=='__main__':unittest.main()

