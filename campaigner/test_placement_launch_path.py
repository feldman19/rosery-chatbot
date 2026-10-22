"""Synthetic only: no credentials, network, or live service restart."""
import hashlib,json,unittest
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlencode
from test_campaign_operations import CampaignOperationsTests,BatchProvider
from campaign_operations_transport import CampaignTransport,ACCOUNT
from canary_plan import build_candidate,placement_readback_matches

class PlacementProvider(BatchProvider):
    corrupt=False
    def read(self,*args):
        row=super().read(*args)
        if 'asset_feed_spec' in row:
            spec=row['asset_feed_spec']
            for image in spec['images']:image['adlabels'][0]['id']='123456'
            for rule in spec['asset_customization_rules']:
                rule['image_label']['id']='123456';rule.pop('is_default',None)
                rule['customization_spec'].update(age_min=13,age_max=65)
            if self.corrupt:spec['images'][1]['hash']=spec['images'][0]['hash']
        return row

class PlacementLaunchTests(unittest.TestCase):
    def setUp(self):
        self.f=CampaignOperationsTests(methodName='runTest');self.f.setUp();f=self.f
        f.provider=PlacementProvider();f.proposal['required_format']='dedicated_feed_story_single_copy'
        ad=f.proposal['ads'][0];raw=f.file.read_bytes()+b'story';sha=hashlib.sha256(raw).hexdigest()
        ad['story']={'asset_id':'storysource123','sha256':sha,'source_name':'story.png','review':{'visual_checked':True}}
        (f.root/'drive-assets'/('storysource123-'+sha+'.bin')).write_bytes(raw)
        ad['url_tags']='utm_content=c11&ad_id={{ad.id}}';f.service=f.new_service()
    def tearDown(self):self.f.tearDown()
    def preview(self):return self.f.preview(format_mode='dedicated_feed_story_single_copy')
    def test_owner_budget_uses_real_adapter_without_old_schedule_or_automatic_activation(self):
        from operations_config import operation_proposal
        from campaign_operations import CampaignOperations
        f=self.f;f.proposal['creation_budget_entry_required']=True
        f.proposal['adsets']=[{'key':'style','label':'Synthetic style'}]
        f.proposal['ads'][0]['adset_key']='style'
        service=CampaignOperations(f.root,f.key,lambda:operation_proposal({'available':True,'proposal':f.proposal,'source_binding':f.binding()}),credential_loader=lambda:'synthetic',transport=f.provider,clock=lambda:f.now)
        body={'action':'prepare_launch','expected_source':f.binding(),'format_mode':'dedicated_feed_story_single_copy','creation_budget_minor':10000}
        p=service.preview(body)['operation'];self.assertEqual(p['blocked_reasons'],[])
        self.assertFalse(f.provider.writes)
        group=p['manifest']['adsets'][0]['params']
        self.assertEqual(group['status'],'PAUSED');self.assertEqual(group['daily_budget'],10000)
        self.assertNotIn('start_time',group);self.assertIn('story',group['targeting']['facebook_positions'])
        service.approve(p['operation_id'],p['digest']);result=service.execute(p['operation_id'])
        self.assertEqual(result['job']['state'],'verified')
        self.assertTrue(all(v['status']=='PAUSED' for k,v in f.provider.writes if k in ('ads','adsets')))
        self.assertEqual(result['operation']['manifest']['creation_budget_minor'],10000)
    def test_status_distinguishes_supported_creation_from_unproven_usability(self):
        status=self.f.service.status()
        self.assertTrue(status['capabilities']['paired_images_single_copy'])
        self.assertTrue(status['placement_customization'])
        self.assertFalse(status['capabilities']['full_launch'])
        self.assertFalse(status['full_launch_blueprint']['execution_available'])
        self.assertEqual(status['launch_workflow']['validation']['provider_rendered_qa'],'unknown')
        self.assertFalse(status['launch_workflow']['validation']['full_launch_ready'])
        self.f.proposal['required_format']='dedicated_feed_story_five_copy'
        blocked=self.f.service.status()
        self.assertFalse(blocked['placement_customization'])
        self.assertIsNone(blocked['launch_workflow']['request_format'])
        self.assertTrue(blocked['launch_workflow']['blocker'])
        self.assertEqual(self.f.provider.writes,[])
    def test_batch_two_sources_reuse_receipts_no_full_claim(self):
        f=self.f;p=self.preview();self.assertEqual(p['blocked_reasons'],[])
        r=f.execute(p);self.assertEqual(r['job']['state'],'verified')
        self.assertEqual([len(x) for x in f.provider.batches],[1,1,1])
        self.assertEqual(sum(k=='image' for k,v in f.provider.writes),2)
        self.assertFalse(r['operation']['full_launch_ready'])
        self.assertEqual(r['operation']['completion_scope'],'dedicated_feed_story_single_copy_api_readback')
        self.assertFalse(r['operation']['manifest']['ui_edit_save_reopen_verified'])
        f.proposal['ads'][0]['primary_text']+=' revised';r=f.execute(self.preview())
        self.assertEqual(r['job']['state'],'verified')
        self.assertTrue(all(s['result']['reused'] for s in r['job']['steps'] if s['kind']=='image'))
        self.assertEqual(sum(k=='image' for k,v in f.provider.writes),2)
    def test_story_substituted_with_feed_fails_readback(self):
        self.f.provider.corrupt=True;r=self.f.execute(self.preview())
        self.assertNotEqual(r['job']['state'],'verified')
        self.assertFalse(any(k=='ads' for k,v in self.f.provider.writes))
    def test_actual_full_requirement_cannot_be_narrowed_by_selector(self):
        self.f.proposal['required_format']='dedicated_feed_story_five_copy'
        p=self.preview();self.assertEqual(p['state'],'blocked');self.assertEqual(self.f.provider.writes,[])
        with self.assertRaises(ValueError):self.f.approve(p)
    def test_missing_explicit_selector_and_unreviewed_story_block(self):
        self.assertEqual(self.f.preview()['state'],'blocked')
        self.f.proposal['ads'][0]['story']['review']['visual_checked']=False
        self.assertEqual(self.preview()['state'],'blocked')
    def test_transport_accepts_only_exact_resolved_recipe(self):
        c=build_candidate(self.f.proposal['ads'][0],'a'*32,'b'*32)
        def wire(v):return [{'method':'POST','relative_url':'act_'+ACCOUNT+'/adcreatives','body':urlencode({k:json.dumps(x) if isinstance(x,(dict,list)) else x for k,x in v.items()})}]
        with patch('campaign_operations_transport.urllib.request.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value=b'[{"code":200,"body":"{\\"id\\":\\"700001\\"}"}]'
            CampaignTransport().batch('synthetic',wire(c));self.assertEqual(build.return_value.open.call_count,1)
            bad=deepcopy(c);bad['asset_feed_spec']['bodies'].append({'text':'unapproved second'})
            with self.assertRaises(ValueError):CampaignTransport().batch('synthetic',wire(bad))
            self.assertEqual(build.return_value.open.call_count,1)
    def test_copy_url_and_rule_drift_rejected(self):
        expected=build_candidate(self.f.proposal['ads'][0],'a'*32,'b'*32)
        for field in ('bodies','link_urls','asset_customization_rules'):
            actual=deepcopy(expected);actual['asset_feed_spec'][field]=[]
            self.assertFalse(placement_readback_matches(actual,expected))

if __name__=='__main__':unittest.main()
