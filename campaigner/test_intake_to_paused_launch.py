"""Real local preparation/storage bridge, deterministic analysis and fake Meta only."""
import hashlib,struct,zlib,tempfile,unittest,json
from unittest.mock import patch
from pathlib import Path
from work_intake import WorkIntake
from launch_workflow import LaunchWorkflow
from launch_materials import LaunchMaterials
from launch_proposal import LaunchProposal
from creative_preparation import CreativePreparation
from creative_selection import CreativeSelection
from operations_config import operation_proposal
from campaign_operations import CampaignOperations
from test_campaign_operations import BatchProvider


def image_bytes(width,height,color):
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
 pixels=(b'\x00'+bytes(color)*width)*height
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')


class IntakeToPausedLaunchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.key=b't'*32;(self.root/'drive-assets').mkdir()
  self.intake=WorkIntake(self.root);self.workflow=LaunchWorkflow(self.root)
  self.request=self.intake.create({'goal':'Prepare clothing sales creatives','drive_urls':['https://drive.google.com/drive/folders/syntheticfolder123']})
  self.workflow.create({'brief':self.request['goal'],'drive_urls':self.request['drive_urls']})
  self.assets=[];self.rows=[];self.originals={}
  for n in range(2):
   for fmt,other,size in [('feed','story',(108,135)),('story','feed',(90,160))]:
    identity=f'synthetic_{n}_{fmt}';raw=image_bytes(*size,(30+n*70,80,140));sha=hashlib.sha256(raw).hexdigest()
    (self.root/'drive-assets'/(identity+'-'+sha+'.bin')).write_bytes(raw);self.originals[identity]=raw
    self.assets.append({'id':identity,'sha256':sha,'name':identity+'.png','mime_type':'image/png','content_read':True,'format_check':'decoded','dimensions':{'width':size[0],'height':size[1]}})
    self.rows.append({'asset_id':identity,'sha256':sha,'format':fmt,'paired_asset_id':f'synthetic_{n}_{other}','concept_key':str(n),'angle':f'Angle {n}','primary_text':f'Original copy {n}','headline':f'Original headline {n}','description':'','visual_description':'Synthetic colored image','audience_hypothesis':'Test only','observed_product':'Clothes','visible_text':'','confidence':0.8,'warnings':[]})
  self.analysis_calls=[]
  def model_runner(payload):
   self.analysis_calls.append(json.loads(payload))
   return {'assets':self.rows,'proposed_adsets':[{'key':'clothes','label':'Clothing test','rationale':'One proposed product test','concept_keys':['0','1']}]}
  self.inventory=self.assets+[{**self.assets[0],'id':f'unselectedextra{i:03}'} for i in range(30)]
  CreativeSelection(self.root).save(self.request,[{k:a[k] for k in ('id','sha256')} for a in self.assets],self.inventory)
  materials=LaunchMaterials(self.root,self.key,reader=lambda d,u:{'verified':True,'complete':True},asset_reader=lambda d,m:{'complete':True,'assets':self.inventory})
  self.proposal=LaunchProposal(self.root,self.key)
  preparation=CreativePreparation(self.root,intake=self.intake,workflow=self.workflow,materials=materials,proposal=self.proposal)
  (self.root/'creative-image-sharing.json').write_text(json.dumps({'approved':True,'scope':'selected_drive_creatives'}))
  with patch('creative_vision.CreativeVision._run',side_effect=model_runner):preparation._run(self.request)
  self.assertEqual(preparation.status()['state'],'ready_for_owner',preparation.status())
  self.provider=BatchProvider()
  self.report=lambda:operation_proposal(self.proposal.report(self.intake.latest()))
  self.service=CampaignOperations(self.root,self.key,self.report,credential_loader=lambda:'synthetic-only',transport=self.provider)
 def preview(self):
  return self.service.preview({'action':'prepare_launch','expected_source':self.report()['source_binding'],'format_mode':'dedicated_feed_story_single_copy','creation_budget_minor':10000})['operation']
 def test_preparation_to_approved_paused_readback_preserves_originals(self):
  report=self.report();self.assertTrue(report['available']);self.assertEqual(len(self.analysis_calls),1)
  self.assertIn(self.request['goal'],json.dumps(self.analysis_calls[0]))
  self.assertEqual(len(self.inventory),34)
  self.assertEqual(sum(b['type']=='image' for b in self.analysis_calls[0]['message']['content']),4)
  self.assertNotIn('unselectedextra',json.dumps(self.analysis_calls[0]))
  proposal=report['proposal'];self.assertEqual(len(proposal['ads']),2)
  operation=self.preview();self.assertEqual(operation['blocked_reasons'],[])
  self.assertEqual(self.provider.writes,[])
  self.assertEqual(operation['manifest']['adsets'][0]['params']['daily_budget'],10000)
  self.assertNotIn('genders',operation['manifest']['adsets'][0]['params']['targeting'])
  for n,ad in enumerate(proposal['ads']):
   self.assertEqual(ad['primary_text'],f'Original copy {n}')
   self.assertEqual(ad['story']['asset_id'],f'synthetic_{n}_story')
  self.service.approve(operation['operation_id'],operation['digest'])
  self.assertEqual(self.provider.writes,[])
  result=self.service.execute(operation['operation_id'])
  self.assertEqual(result['job']['state'],'verified')
  self.assertEqual(sum(k=='adsets' for k,v in self.provider.writes),1)
  self.assertEqual(sum(k=='ads' for k,v in self.provider.writes),2)
  self.assertTrue(all(v['status']=='PAUSED' for k,v in self.provider.writes if k in ('ads','adsets')))
  self.assertEqual({v['sha'] for k,v in self.provider.writes if k=='image'},{hashlib.sha256(raw).hexdigest() for raw in self.originals.values()})
  creatives=[v for k,v in self.provider.writes if k=='adcreatives']
  self.assertEqual({v['asset_feed_spec']['bodies'][0]['text'] for v in creatives},{'Original copy 0','Original copy 1'})
  self.assertTrue(all(len(v['asset_feed_spec']['images'])==2 for v in creatives))
  for identity,raw in self.originals.items():
   self.assertEqual((self.root/'drive-assets'/(identity+'-'+hashlib.sha256(raw).hexdigest()+'.bin')).read_bytes(),raw)
 def test_new_request_invalidates_prepared_approval_before_any_write(self):
  operation=self.preview();self.assertEqual(operation['blocked_reasons'],[])
  self.service.approve(operation['operation_id'],operation['digest'])
  self.intake.create({'goal':'Different clothing launch','drive_urls':self.request['drive_urls']})
  self.assertFalse(self.report()['available'])
  with self.assertRaises(ValueError):self.service.execute(operation['operation_id'])
  self.assertEqual(self.provider.writes,[])
