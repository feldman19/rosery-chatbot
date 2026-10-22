import copy
import unittest
from datetime import datetime,timedelta,timezone
from work_status import work_status
NOW=datetime(2026,10,18,12,tzinfo=timezone.utc)
URL='https://drive.google.com/drive/folders/abcdefghijk'

def reports(count=2):
 request={'id':'launch-request','revision':1,'brief':'Launch the new collection','drive_urls':[URL],'saved_at':NOW.isoformat()}
 assets=[{'id':'asset-'+str(i),'sha256':'a'*64,'content_read':True,'creative_reviewed':True} for i in range(count)]
 launch={'request':request,'materials':{'metadata_verified':True,'assets_complete':True,'observed_at':NOW.isoformat(),'content_read_count':count,'assets':assets}}
 strategy={'request':None,'model_connected':False,'analysis':{'verified':True,'active_ads_count':7,'source_observed_at':NOW.isoformat()},'research_plan':{'state':'not_executed'}}
 metadatafiles=[{'id':a['id'],'mimeType':'image/jpeg'} for a in assets]+[{'id':'child-folder','mimeType':'application/vnd.google-apps.folder'}]
 material={'request_id':request['id'],'request_revision':1,'drive_urls':[URL],'verified':True,'binding_verified':True,'checked_at':NOW.isoformat(),'configured':True,'metadata':{'verified':True,'complete':True,'email':'inforosery@gmail.com','sources':[{'source_id':'root-folder','source_url':URL,'recursive':False,'files':metadatafiles}]}}
 return launch,strategy,material

class WorkStatusTests(unittest.TestCase):
 def call(self,launch,strategy,material=None):return work_status(launch,strategy,material,now=NOW)
 def test_no_goal_asks_only_business_intent(self):
  r=self.call({},{});self.assertEqual('needs_goal',r['state']);self.assertEqual('set_goal',r['owner_action']['id']);self.assertEqual([],r['completed']);self.assertFalse(r['background_execution'])
 def test_saved_goal_and_actual_content_counts_not_full_product_done(self):
  l,s,m=reports();r=self.call(l,s,m);self.assertEqual('Launch the new collection',r['goal']);self.assertEqual('launch',r['goal_binding']['source'])
  ids={x['id'] for x in r['completed']};self.assertTrue({'saved_goal','source_links','direct_content','active_ads'}<=ids)
  self.assertIn('creative_review',{x['id'] for x in r['blocked']});self.assertEqual('blocked',r['state']);self.assertFalse(r['ai_connected']);self.assertFalse(r['execution_approval'])
 def test_54_of_54_direct_files_never_claims_nested_scan(self):
  l,s,m=reports(54);r=self.call(l,s,m);direct=next(x for x in r['completed'] if x['id']=='direct_content');self.assertIn('54',direct['detail'])
  self.assertIn('nested_folders',{x['id'] for x in r['blocked']});self.assertFalse(any(x['id']=='nested_folders' for x in r['completed']))
 def test_partial_read_remains_partial_blocker(self):
  l,s,m=reports();l['materials']['assets_complete']=False;r=self.call(l,s,m);self.assertEqual('partial',next(x['state'] for x in r['blocked'] if x['id']=='direct_content'))
 def test_stale_content_or_binding_never_marked_done(self):
  for target in ('summary','signed_report','revision','urls','error'):
   l,s,m=reports()
   if target=='summary':l['materials']['observed_at']=(NOW-timedelta(minutes=13)).isoformat()
   elif target=='signed_report':m['checked_at']=(NOW-timedelta(minutes=13)).isoformat()
   elif target=='revision':m['request_revision']=99
   elif target=='urls':m['drive_urls']=[]
   else:m['error']='latest read failed'
   r=self.call(l,s,m);self.assertFalse(any(x['id']=='direct_content' for x in r['completed']))
 def test_manual_content_flags_without_hashes_not_completed(self):
  l,s,m=reports();l['materials']['assets'][0].pop('sha256');r=self.call(l,s,m);self.assertFalse(any(x['id']=='direct_content' for x in r['completed']))
 def test_new_strategy_goal_conflict_not_merged(self):
  l,s,m=reports();s['request']={'id':'strategy-request','revision':2,'goal':'Build brand awareness','category':'awareness','saved_at':(NOW+timedelta(seconds=1)).isoformat()}
  r=self.call(l,s,m);self.assertEqual('needs_consolidation',r['state']);self.assertEqual('Build brand awareness',r['goal']);self.assertEqual('consolidate_goals',r['owner_action']['id']);self.assertEqual(2,len(r['owner_action']['options']));self.assertFalse(r['live_action_allowed'])
 def test_same_normalized_goal_no_fake_conflict(self):
  l,s,m=reports();s['request']={'id':'strategy','revision':1,'goal':' launch  THE new collection ','category':'sales','saved_at':NOW.isoformat()}
  r=self.call(l,s,m);self.assertNotEqual('needs_consolidation',r['state']);self.assertIsNone(r['owner_action'])
 def test_generic_awareness_does_not_require_new_launch_assets(self):
  s={'request':{'id':'strategy','revision':1,'goal':'Build awareness','category':'awareness','saved_at':NOW.isoformat()},'analysis':{'verified':True,'active_ads_count':0,'source_observed_at':NOW.isoformat()}}
  r=self.call({},s);self.assertNotIn('direct_content',{x['id'] for x in r['blocked']});self.assertTrue(any(x['id']=='active_ads' for x in r['completed']))
 def test_stale_analysis_is_unknown_not_zero_ads(self):
  l,s,m=reports();s['analysis']['source_observed_at']=(NOW-timedelta(minutes=13)).isoformat();r=self.call(l,s,m)
  self.assertNotIn('active_ads',{x['id'] for x in r['completed']});self.assertIn('active_ads',{x['id'] for x in r['blocked']})
 def test_missing_drive_connection_can_require_owner_action(self):
  l,s,m=reports();l['materials']={};m={'configured':False};r=self.call(l,s,m);self.assertEqual('connect_drive',r['owner_action']['id'])
 def test_no_silent_model_or_research_completion_from_manual_flags(self):
  l,s,m=reports();s['model_connected']=True;s['research_plan']['state']='completed';r=self.call(l,s,m)
  self.assertFalse(r['ai_connected']);self.assertIn('research',{x['id'] for x in r['blocked']});self.assertNotEqual('completed',r['state'])
 def test_missing_scope_leaves_nested_coverage_unknown(self):
  l,s,m=reports();m.pop('metadata');r=self.call(l,s,m);self.assertIn('nested_folders',{x['id'] for x in r['blocked']})
 def test_never_mutates_reports(self):
  data=reports();saved=copy.deepcopy(data);self.call(*data);self.assertEqual(saved,data)
 def test_explicit_awareness_is_not_auto_reinterpreted_as_sales(self):
  s={'request':{'id':'strategy','revision':1,'goal':'Build awareness','category':'awareness','saved_at':NOW.isoformat()}}
  r=self.call({},s);self.assertEqual('awareness',r['category']);self.assertEqual('unsupported_goal',r['state']);self.assertIsNone(r['owner_action']);self.assertEqual('strategy',r['request']['id'])
 def test_saved_owner_sales_context_can_support_unclear_keyword_hint(self):
  l,s,m=reports();l['request']['goal']='sales';s['request']={'id':'strategy','revision':2,'goal':l['request']['brief'],'category':'unclear','clarifications':['Which goal?'],'saved_at':NOW.isoformat()}
  r=self.call(l,s,m);self.assertEqual('sales',r['category']);self.assertIsNone(r['owner_action']);self.assertFalse(r['execution_approval'])
if __name__=='__main__':unittest.main()
