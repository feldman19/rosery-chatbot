"""Isolated HTTP contract proof with fake Drive/model/Meta boundaries only."""
import hashlib,json,re,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import urllib.request,urllib.error
from server import make_server
from launch_materials import LaunchMaterials
from campaign_operations import CampaignOperations
from test_campaign_operations import BatchProvider
from test_intake_to_paused_launch import image_bytes


class HTTPCreativeLaunchFlowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);(self.root/'drive-assets').mkdir();self.provider=BatchProvider();self.rows=[];self.assets=[];self.model_calls=[]
  for n in range(2):
   for fmt,other,size in [('feed','story',(108,135)),('story','feed',(90,160))]:
    identity=f'httpasset_{n}_{fmt}';raw=image_bytes(*size,(50+n*50,120,160));sha=hashlib.sha256(raw).hexdigest()
    (self.root/'drive-assets'/(identity+'-'+sha+'.bin')).write_bytes(raw)
    self.assets.append({'id':identity,'sha256':sha,'name':identity+'.png','mime_type':'image/png','content_read':True,'format_check':'decoded','dimensions':dict(zip(('width','height'),size))})
    self.rows.append({'asset_id':identity,'sha256':sha,'format':fmt,'paired_asset_id':f'httpasset_{n}_{other}','concept_key':str(n),'angle':f'Angle {n}','primary_text':f'HTTP copy {n}','headline':f'HTTP headline {n}','description':'','visual_description':'Synthetic image','observed_product':'Clothes','audience_hypothesis':'Hypothesis','visible_text':'','confidence':.8,'warnings':[]})
  (self.root/'creative-image-sharing.json').write_text(json.dumps({'approved':True,'scope':'selected_drive_creatives'}))
  self.drive_calls=[]
  def material_factory(directory,key):return LaunchMaterials(directory,key,reader=lambda d,u:self.drive_calls.append(u) or {'verified':True,'complete':True},asset_reader=lambda d,m:{'complete':True,'assets':self.assets})
  def operations_factory(directory,key,proposal_loader):return CampaignOperations(directory,key,proposal_loader,credential_loader=lambda:'fake-http-token',transport=self.provider)
  def model_runner(payload):
   self.model_calls.append(json.loads(payload))
   return {'assets':self.rows,'proposed_adsets':self.groups if hasattr(self,'groups') else [] if len(self.rows)==1 else [{'key':'clothing','label':'Clothes','rationale':'Same product category test','concept_keys':['0','1']}]}
  for target,kwargs in [('server.LaunchMaterials',{'side_effect':material_factory}),('server.CampaignOperations',{'side_effect':operations_factory}),('creative_vision.CreativeVision._run',{'side_effect':model_runner}),('server.load_rosery_dossier',{'return_value':{}})]:
   mock=patch(target,**kwargs);mock.start();self.addCleanup(mock.stop)
  self.server=make_server(0,self.root,workspace='manager',start_live=False,start_execution_workers=False)
  self.addCleanup(self.server.server_close)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.server.shutdown)
  self.base=f'http://127.0.0.1:{self.server.server_port}'
  page=urllib.request.urlopen(self.base,timeout=5).read().decode();self.token=re.search("const token='([^']+)'",page).group(1)
 def request(self,path,body=None,*,authorized=True):
  headers={'X-Local-Session':self.token if authorized else 'invalid'}
  if body is not None:headers.update({'Content-Type':'application/json','Origin':self.base})
  request=urllib.request.Request(self.base+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
  try:
   with urllib.request.urlopen(request,timeout=5) as response:return response.status,json.load(response)
  except urllib.error.HTTPError as error:return error.code,json.load(error)
 def wait(self,path,predicate,timeout=12):
  deadline=time.monotonic()+timeout
  while time.monotonic()<deadline:
   code,value=self.request(path);self.assertEqual(code,200)
   if predicate(value):return value
   time.sleep(.05)
  self.fail('Local HTTP workflow did not finish: '+str(value))
 def test_actual_owner_routes_prepare_approve_and_read_back_paused_ads(self):
  body={'goal':'Prepare clothing sales through owner interface','drive_urls':['https://drive.google.com/drive/folders/httpfakefolder123']}
  self.assertEqual(self.request('/api/work/request',body,authorized=False)[0],403)
  code,saved=self.request('/api/work/request',body);self.assertEqual(code,200);self.assertTrue(saved['preparation_started_or_queued'])
  report=self.wait('/api/preparation-task',lambda v:v.get('proposal_ready') is True)
  self.assertEqual(len(self.model_calls),1);self.assertIn(body['goal'],json.dumps(self.model_calls[0]));self.assertEqual(self.provider.writes,[])
  request=report['request']
  wrong={'action':'select','idempotency_key':'stale-http-choice','request_id':request['id'],'request_revision':request['revision']+1,'selected_sources':[]}
  self.assertEqual(self.request('/api/preparation-task',wrong)[0],409)
  code,proposal=self.request('/api/launch/proposal');self.assertEqual(code,200);self.assertTrue(proposal['available'])
  preview={'action':'prepare_launch','expected_source':proposal['source_binding'],'format_mode':'dedicated_feed_story_single_copy','creation_budget_minor':10000}
  code,result=self.request('/api/campaign-operations/preview',preview);self.assertEqual(code,200);operation=result['operation'];self.assertEqual(operation['blocked_reasons'],[])
  self.assertEqual(self.provider.writes,[])
  approval={'operation_id':operation['operation_id'],'digest':operation['digest']}
  self.assertEqual(self.request('/api/campaign-operations/approve',approval,authorized=False)[0],403)
  self.assertEqual(self.request('/api/campaign-operations/approve',{**approval,'digest':'0'*64})[0],400)
  self.assertEqual(self.provider.writes,[])
  self.assertEqual(self.request('/api/campaign-operations/approve',approval)[0],200)
  self.assertEqual(self.request('/api/campaign-operations/execute',{'operation_id':operation['operation_id']})[0],200)
  status=self.wait('/api/campaign-operations',lambda v:any(j.get('id')==operation['operation_id'] and j.get('state')=='verified' for j in v.get('jobs',[])))
  self.assertEqual(sum(k=='ads' for k,v in self.provider.writes),2);self.assertEqual(sum(k=='adsets' for k,v in self.provider.writes),1)
  self.assertTrue(all(v['status']=='PAUSED' for k,v in self.provider.writes if k in ('ads','adsets')))
  self.assertEqual(next(v['daily_budget'] for k,v in self.provider.writes if k=='adsets'),'10000')
  self.assertEqual({v['asset_feed_spec']['bodies'][0]['text'] for k,v in self.provider.writes if k=='adcreatives'},{'HTTP copy 0','HTTP copy 1'})

 def test_unpaired_source_is_actionable_over_http_without_proposal(self):
  self.assets=self.assets[:1];self.rows=self.rows[:1];self.rows[0]['paired_asset_id']=None
  code,saved=self.request('/api/work/request',{'goal':'Prepare clothing sales','drive_urls':['https://drive.google.com/drive/folders/httpfakefolder123']})
  self.assertEqual(code,200)
  report=self.wait('/api/preparation-task',lambda v:v.get('creative_preparation',{}).get('state')=='blocked')
  status=report['creative_preparation'];issue=status['source_issue'];request=report['request']
  self.assertEqual(issue['request_id'],request['id']);self.assertEqual(issue['request_revision'],request['revision'])
  self.assertEqual(issue['asset_id'],self.assets[0]['id']);self.assertEqual(issue['sha256'],self.assets[0]['sha256']);self.assertEqual(issue['missing_format'],'story')
  self.assertIn(self.assets[0]['name'],status['detail']);self.assertIn('סטורי',status['detail'])
  self.assertTrue(status['review_receipt_id']);self.assertFalse(report['proposal_ready']);self.assertTrue(report['actions']['can_select'])
  self.assertEqual(len(self.model_calls),1);self.assertEqual(self.provider.writes,[])

 def test_device_sources_require_consent_then_prepare_over_owner_http(self):
  sources=[];mapping={}
  for i,asset in enumerate(self.assets):
   raw=(self.root/'drive-assets'/(asset['id']+'-'+asset['sha256']+'.bin')).read_bytes()
   req=urllib.request.Request(self.base+'/api/local-uploads',data=raw,headers={'Origin':self.base,'X-Local-Session':self.token,'Content-Type':'image/png','X-Upload-Id':'synthetic_upload_'+str(i).zfill(4),'X-File-Name':asset['name']})
   with urllib.request.urlopen(req,timeout=5) as response:uploaded=json.load(response)['asset']
   mapping[asset['id']]=uploaded['id'];sources.append({'id':uploaded['id'],'sha256':uploaded['sha256']})
  for row in self.rows:
   row['asset_id']=mapping[row['asset_id']];row['paired_asset_id']=mapping[row['paired_asset_id']]
  body={'action':'save','mode':'new','brief':'Prepare clothing sales','drive_urls':[],'local_sources':sources,'local_image_review_consent':False,'expected_request_id':None,'expected_request_revision':None,'idempotency_key':'device-no-consent'}
  code,result=self.request('/api/preparation-task',body);self.assertEqual(code,200,result)
  blocked=self.wait('/api/preparation-task',lambda v:v.get('creative_preparation',{}).get('state')=='blocked')
  self.assertEqual(self.model_calls,[]);self.assertFalse(blocked['proposal_ready']);self.assertEqual(self.provider.writes,[])
  request=blocked['request'];body.update(local_image_review_consent=True,expected_request_id=request['id'],expected_request_revision=request['revision'],idempotency_key='device-explicit-consent')
  code,result=self.request('/api/preparation-task',body);self.assertEqual(code,200,result)
  ready=self.wait('/api/preparation-task',lambda v:v.get('proposal_ready') is True)
  self.assertEqual(len(self.model_calls),1);self.assertEqual(ready['request']['local_sources'],sources);self.assertEqual(self.provider.writes,[])

  self.assertEqual(self.drive_calls,[])
  code,proposal=self.request('/api/launch/proposal');self.assertEqual(code,200)
  for ad in proposal['proposal']['ads']:
   self.assertEqual(ad['source_kind'],'device');self.assertEqual(ad['story']['source_kind'],'device')
  code,result=self.request('/api/campaign-operations/preview',{'action':'prepare_launch','expected_source':proposal['source_binding'],'format_mode':'dedicated_feed_story_single_copy','creation_budget_minor':10000})
  self.assertEqual(code,200,result);operation=result['operation'];self.assertEqual(operation['blocked_reasons'],[])
  self.assertTrue(all(f['source_kind']=='device' for f in operation['files']));self.assertEqual(len(operation['files']),4)
  self.assertEqual(self.provider.writes,[])
  approval={'operation_id':operation['operation_id'],'digest':operation['digest']}
  self.assertEqual(self.request('/api/campaign-operations/approve',approval)[0],200)
  self.assertEqual(self.request('/api/campaign-operations/execute',{'operation_id':operation['operation_id']})[0],200)
  status=self.wait('/api/campaign-operations',lambda v:any(j.get('id')==operation['operation_id'] and j.get('state') in ('verified','failed','blocked','unknown') for j in v.get('jobs',[])))
  job=next(j for j in status['jobs'] if j['id']==operation['operation_id']);self.assertEqual(job['state'],'verified',job)
  self.assertEqual(sum(k=='ads' for k,v in self.provider.writes),2);self.assertEqual(sum(k=='adsets' for k,v in self.provider.writes),1)
  self.assertTrue(all(v['status']=='PAUSED' for k,v in self.provider.writes if k in ('ads','adsets')))
  self.assertEqual({v['sha'] for k,v in self.provider.writes if k=='image'},{s['sha256'] for s in sources})
  creatives=[v['asset_feed_spec'] for k,v in self.provider.writes if k=='adcreatives']
  self.assertEqual({v['bodies'][0]['text'] for v in creatives},{'HTTP copy 0','HTTP copy 1'})
  self.assertTrue(all(len(v['images'])==2 for v in creatives));self.assertEqual(self.drive_calls,[])

 def test_fifteen_device_ads_three_groups_exact_scope_and_retry(self):
  started=time.monotonic();self.rows=[];sources=[];originals={}
  self.groups=[{'key':'group'+str(g),'label':'Group '+str(g),'rationale':'Synthetic grouping contract only','concept_keys':[str(n) for n in range(g*5,g*5+5)]} for g in range(3)]
  for n in range(15):
   pair={}
   for fmt,size in [('feed',(108,135)),('story',(90,160))]:
    raw=image_bytes(*size,(n*13+10,110,150));sha=hashlib.sha256(raw).hexdigest()
    req=urllib.request.Request(self.base+'/api/local-uploads',data=raw,headers={'Origin':self.base,'X-Local-Session':self.token,'Content-Type':'image/png','X-Upload-Id':'batch15_'+str(n).zfill(4)+'_'+fmt+'_upload','X-File-Name':str(n)+'_'+fmt+'.png'})
    with urllib.request.urlopen(req,timeout=5) as response:asset=json.load(response)['asset']
    sources.append({'id':asset['id'],'sha256':sha});pair[fmt]=asset;originals[(n,fmt)]=raw
   for fmt,other in [('feed','story'),('story','feed')]:
    asset=pair[fmt];self.rows.append({'asset_id':asset['id'],'sha256':asset['sha256'],'format':fmt,'paired_asset_id':pair[other]['id'],'concept_key':str(n),'angle':'Distinct angle '+str(n),'primary_text':'Batch copy '+str(n),'headline':'Batch headline '+str(n),'description':'','visual_description':'Synthetic image','observed_product':'Clothes','audience_hypothesis':'Hypothesis','visible_text':'','confidence':.8,'warnings':[]})
  code,value=self.request('/api/preparation-task',{'action':'save','mode':'new','brief':'Prepare clothing sales','drive_urls':[],'local_sources':sources,'local_image_review_consent':True,'expected_request_id':None,'expected_request_revision':None,'idempotency_key':'batch15-device-save'})
  self.assertEqual(code,200,value);self.wait('/api/preparation-task',lambda v:v.get('proposal_ready') is True,30)
  self.assertEqual(len(self.model_calls),1);self.assertEqual(self.drive_calls,[])
  code,p=self.request('/api/launch/proposal');self.assertEqual(code,200);self.assertEqual(len(p['proposal']['ads']),15);self.assertEqual(len(p['proposal']['adsets']),3)
  code,result=self.request('/api/campaign-operations/preview',{'action':'prepare_launch','expected_source':p['source_binding'],'format_mode':'dedicated_feed_story_single_copy','creation_budget_minor':10000})
  self.assertEqual(code,200,result);operation=result['operation'];self.assertEqual(operation['blocked_reasons'],[]);self.assertEqual(len(operation['files']),30);self.assertTrue(all(x['source_kind']=='device' for x in operation['files']));self.assertEqual(self.provider.writes,[])
  approval={'operation_id':operation['operation_id'],'digest':operation['digest']}
  self.assertEqual(self.request('/api/campaign-operations/approve',approval)[0],200)
  self.assertEqual(self.request('/api/campaign-operations/execute',{'operation_id':operation['operation_id']})[0],200)
  status=self.wait('/api/campaign-operations',lambda v:any(j['id']==operation['operation_id'] and j['state'] in ('verified','unknown','partial','blocked') for j in v['jobs']),30)
  job=next(j for j in status['jobs'] if j['id']==operation['operation_id']);self.assertEqual(job['state'],'verified',job.get('reason'))
  ads=[v for k,v in self.provider.writes if k=='ads'];sets=[v for k,v in self.provider.writes if k=='adsets'];self.assertEqual(len(ads),15);self.assertEqual(len(sets),3);self.assertTrue(all(v['status']=='PAUSED' for v in ads+sets));self.assertTrue(all(v['daily_budget']=='10000' for v in sets))
  self.assertEqual({v['sha'] for k,v in self.provider.writes if k=='image'},{s['sha256'] for s in sources})
  group_counts={}
  for ad in ads:
   creative=self.provider.nodes[ad['creative']['creative_id']];spec=creative['asset_feed_spec'];n=int(spec['bodies'][0]['text'].split()[-1]);adset=self.provider.nodes[ad['adset_id']]
   self.assertIn('Group '+str(n//5),adset['name']);group_counts[n//5]=group_counts.get(n//5,0)+1
   self.assertEqual(spec['titles'][0]['text'],'Batch headline '+str(n));self.assertEqual(len(spec['images']),2)
   bylabel={x['adlabels'][0]['name']:x['hash'] for x in spec['images']}
   self.assertEqual(bylabel['feed_original'],hashlib.md5(originals[(n,'feed')]).hexdigest());self.assertEqual(bylabel['story_original'],hashlib.md5(originals[(n,'story')]).hexdigest())
  self.assertEqual(group_counts,{0:5,1:5,2:5});before=len(self.provider.writes)
  self.assertEqual(self.request('/api/campaign-operations/execute',{'operation_id':operation['operation_id']})[0],200)
  time.sleep(.05);self.assertEqual(len(self.provider.writes),before);self.assertEqual(self.drive_calls,[])
  print('LOCAL FAKE-PROVIDER ONLY: 15 ads / 30 original images / 3 groups, paused readback and retry; elapsed %.3fs; not real Meta latency.'%(time.monotonic()-started))
