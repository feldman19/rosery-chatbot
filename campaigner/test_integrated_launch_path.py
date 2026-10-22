"""Isolated 2-set/7-ad synthetic integration. Never contacts Meta."""
import hashlib,json,unittest
from copy import deepcopy
from campaign_operations import CampaignOperations
from campaign_operations_transport import CampaignTransport
from test_campaign_operations import CampaignOperationsTests,BatchProvider
from test_operation_timings import ControlledClock,TimedProvider

class IntegratedProvider(TimedProvider):
    def __init__(self,clock,outcome=None):
        super().__init__(clock);self.outcome=outcome;self.request_counts={k:0 for k in ('read','upload','image_info','batch')};self.batch_sizes=[]
    def read(self,*args):self.request_counts['read']+=1;return super().read(*args)
    def upload(self,*args):self.request_counts['upload']+=1;return super().upload(*args)
    def image_info(self,*args):self.request_counts['image_info']+=1;return super().image_info(*args)
    def batch(self,token,descriptors):
        self.request_counts['batch']+=1;self.batch_sizes.append(len(descriptors))
        wave=self.calls+1
        if (self.outcome=='partial' and wave==2) or (self.outcome=='unknown' and wave==3):
            self.calls+=1;self.timer.advance(5)
            if self.outcome=='unknown':raise TimeoutError('synthetic timeout; no private body retained')
            accepted=BatchProvider.batch(self,token,descriptors[:3])
            return accepted+[{'code':400,'body':'{"error":{"code":100}}'} for _ in descriptors[3:]]
        return super().batch(token,descriptors)

class IntegratedLaunchPathTests(unittest.TestCase):
    def setUp(self):
        self.fixture=CampaignOperationsTests(methodName='runTest');self.fixture.setUp()
        f=self.fixture;template=deepcopy(f.proposal['ads'][0]);base=f.file.read_bytes();ads=[]
        for index in range(7):
            identity='feedAsset%04d'%index;raw=base+bytes([index]);sha=hashlib.sha256(raw).hexdigest()
            (f.root/'drive-assets'/(identity+'-'+sha+'.bin')).write_bytes(raw)
            ad=deepcopy(template);ad.update(asset_id=identity,source_name='SYNTHETIC_%d_FEED.png'%index,sha256=sha,
                adset_key='style' if index<4 else 'reviews',primary_text='Synthetic reviewed copy %d'%index)
            ad['primary_text_options']=[ad['primary_text'],'second','third','fourth','fifth']
            ad['story']={'asset_id':'storyAsset%04d'%index,'sha256':hashlib.sha256(b'synthetic story'+bytes([index])).hexdigest(),'source_name':'SYNTHETIC_%d_STORY.png'%index}
            ads.append(ad)
        f.proposal['ads']=ads;group=deepcopy(f.proposal['operation_config']['adsets'][0])
        f.proposal['operation_config']['adsets']=[]
        for key in ('style','reviews'):
            value=deepcopy(group);value.update(key=key,name='Synthetic '+key,asset_ids=[a['asset_id'] for a in ads if a['adset_key']==key]);f.proposal['operation_config']['adsets'].append(value)
        self.timer=ControlledClock();self.provider=IntegratedProvider(self.timer);self.credentials_calls=0
        self.service=self.make_service()
    def tearDown(self):self.fixture.tearDown()
    def credentials(self):self.credentials_calls+=1;self.timer.advance(1);return 'synthetic-token'
    def make_service(self,transport=None):
        f=self.fixture
        return CampaignOperations(f.root,f.key,lambda:{'available':True,'proposal':deepcopy(f.proposal),'source_binding':f.binding()},
            credential_loader=self.credentials,transport=transport or self.provider,clock=lambda:f.now,monotonic=self.timer)
    def run_partial_scope(self):
        # Explicit synthetic sub-scope; never modifies the real host proposal.
        f=self.fixture;f.proposal['required_format']='feed_original_single_copy'
        p=self.service.preview({'action':'prepare_launch','expected_source':f.binding()})['operation']
        self.assertEqual(p['blocked_reasons'],[]);self.service.approve(p['operation_id'],p['digest'])
        self.provider.request_counts={k:0 for k in self.provider.request_counts};self.provider.batch_sizes=[];self.timer.seconds=0
        return self.service.execute(p['operation_id'])
    def test_full_2sets_7ads_blocks_before_credentials_and_preserves_14_sources(self):
        f=self.fixture;f.proposal['required_format']='dedicated_feed_story_five_copy'
        p=self.service.preview({'action':'prepare_launch','expected_source':f.binding()})['operation']
        self.assertEqual(p['state'],'blocked');self.assertEqual(self.credentials_calls,0)
        req=p['manifest']['format_requirements'];self.assertEqual((req['adset_count'],req['ad_count'],req['source_count']),(2,7,14))
        self.assertTrue(all(len(c['primary_texts'])==5 and len(c['media'])==2 for c in req['concepts']))
        self.assertFalse(p['full_launch_ready']);self.assertFalse(p['execution_ready'])
        with self.assertRaises(ValueError):self.service.approve(p['operation_id'],p['digest'])
        self.assertEqual(sum(self.provider.request_counts.values()),0);self.assertEqual(self.provider.writes,[])
    def test_partial_scope_integrates_batch_reuse_and_timing_without_full_claim(self):
        cold=self.run_partial_scope();self.assertEqual(cold['job']['state'],'verified')
        cold_counts=dict(self.provider.request_counts);self.assertEqual(self.provider.batch_sizes,[2,7,7]);self.assertEqual(cold_counts['upload'],7)
        self.assertEqual(cold['job']['timings']['total_seconds'],87)
        for ad in self.fixture.proposal['ads']:
            ad['primary_text']+=' revision2';ad['primary_text_options'][0]=ad['primary_text']
        warm=self.run_partial_scope();self.assertEqual(warm['job']['state'],'verified')
        self.assertEqual(self.provider.batch_sizes,[2,7,7]);self.assertEqual(self.provider.request_counts['upload'],0)
        self.assertEqual(self.provider.request_counts['image_info'],7);self.assertEqual(warm['job']['timings']['total_seconds'],66)
        self.assertFalse(warm['operation']['full_launch_ready'])
        self.assertEqual(warm['operation']['completion_scope'],'feed_only_single_copy_api_readback')
        self.assertTrue(all(s['result']['reused'] for s in warm['job']['steps'] if s['kind']=='image'))
        self.assertEqual(len({s['result']['id'] for s in warm['job']['steps'] if s['kind'] in ('adsets','ads')}),9)
    def test_partial_and_unknown_restart_do_not_duplicate(self):
        for outcome in ('partial','unknown'):
            with self.subTest(outcome=outcome):
                # Distinct, explicitly reviewed synthetic revisions avoid reusing an earlier semantic job.
                for ad in self.fixture.proposal['ads']:ad['primary_text']+=' '+outcome
                self.provider.outcome=outcome;self.provider.calls=0
                result=self.run_partial_scope();self.assertEqual(result['job']['state'],outcome)
                attempted=dict(self.provider.request_counts);known=[s.get('provider_id') for s in result['job']['steps'] if s.get('provider_id')]
                self.assertTrue(known);self.assertEqual(result['job']['timings']['outcome'],outcome)
                restarted=self.make_service();again=restarted.execute(result['job']['id'])
                self.assertEqual(self.provider.request_counts,attempted);self.assertEqual(again['job']['timings'],result['job']['timings'])
                self.assertEqual([s.get('provider_id') for s in again['job']['steps'] if s.get('provider_id')],known)
    def test_injected_receipts_cannot_be_reused_as_meta_upload_provenance(self):
        result=self.run_partial_scope();step=next(s for s in result['job']['steps'] if s['kind']=='image')
        self.assertEqual(step['result']['upload_provenance']['transport_kind'],'injected_not_meta')
        # Exact real transport type, but only the local receipt lookup is called.
        # No credentials or provider method is invoked in this regression test.
        real=self.make_service(CampaignTransport());counts=dict(self.provider.request_counts)
        receipt=real._uploaded_image_receipt(step['params']['sha256'],step['result']['upload_provenance']['byte_count'],('not-current','not-current'))
        self.assertIsNone(receipt);self.assertEqual(self.provider.request_counts,counts)

    def placement_fixture(self):
        """Extend existing seven-concept fixture; all fourteen decoded files differ."""
        f=self.fixture;base=f.file.read_bytes();self.expected_pairs={}
        for index,ad in enumerate(f.proposal['ads']):
            story=ad['story'];raw=base+b'dedicated-story'+bytes([index]);sha=hashlib.sha256(raw).hexdigest()
            story.update(sha256=sha,review={'visual_checked':True})
            (f.root/'drive-assets'/(story['asset_id']+'-'+sha+'.bin')).write_bytes(raw)
            ad['url_tags']='utm_content=concept%d&ad_id={{ad.id}}'%index
            feed=(f.root/'drive-assets'/(ad['asset_id']+'-'+ad['sha256']+'.bin')).read_bytes()
            self.expected_pairs[ad['primary_text']]=(hashlib.md5(feed).hexdigest(),hashlib.md5(raw).hexdigest())
        self.assertEqual(len({x for pair in self.expected_pairs.values() for x in pair}),14)
        f.proposal['required_format']='dedicated_feed_story_single_copy'

    def run_placement_scope(self):
        f=self.fixture
        p=self.service.preview({'action':'prepare_launch','expected_source':f.binding(),
            'format_mode':'dedicated_feed_story_single_copy'})['operation']
        self.assertEqual(p['blocked_reasons'],[]);self.assertEqual(len(p['files']),14)
        self.service.approve(p['operation_id'],p['digest'])
        self.provider.request_counts={k:0 for k in self.provider.request_counts};self.provider.batch_sizes=[]
        return self.service.execute(p['operation_id'])

    def test_placement_2sets_7ads_exact_pairs_and_14_source_warm_reuse(self):
        self.placement_fixture();cold=self.run_placement_scope()
        self.assertEqual(cold['job']['state'],'verified');cold_counts=dict(self.provider.request_counts)
        self.assertEqual(self.provider.batch_sizes,[2,7,7]);self.assertEqual(cold_counts,{'read':18,'upload':14,'image_info':14,'batch':3})
        steps=cold['job']['steps'];images={s['key']:s['result']['hash'] for s in steps if s['kind']=='image'}
        for step in (s for s in steps if s['kind']=='adcreatives'):
            stored=self.provider.nodes[step['result']['id']]['asset_feed_spec']
            pair=tuple(x['hash'] for x in stored['images']);self.assertEqual(pair,self.expected_pairs[stored['bodies'][0]['text']])
            self.assertEqual(pair,tuple(images[x['hash']['ref']] for x in step['params']['asset_feed_spec']['images']))
            self.assertEqual(stored['asset_customization_rules'][0]['image_label']['name'],'story_original')
        created=[s['result']['id'] for s in steps if s['kind'] in ('adsets','adcreatives','ads')]
        self.assertEqual(len(set(created)),16)
        self.assertEqual([s['result']['id'] for s in self.service._get(cold['job']['id'])['steps'] if s['kind'] in ('adsets','adcreatives','ads')],created)
        for ad in self.fixture.proposal['ads']:ad['headline']+=' revised'
        warm=self.run_placement_scope();self.assertEqual(warm['job']['state'],'verified')
        self.assertEqual(self.provider.request_counts,{'read':18,'upload':0,'image_info':14,'batch':3})
        self.assertTrue(all(s['result']['reused'] for s in warm['job']['steps'] if s['kind']=='image'))
        self.assertFalse(warm['operation']['full_launch_ready']);self.assertFalse(warm['operation']['manifest']['additional_text_options_assigned'])
        # Restoring the true full requirement cannot be bypassed by the PAC selector.
        self.fixture.proposal['required_format']='dedicated_feed_story_five_copy';before=dict(self.provider.request_counts)
        blocked=self.service.preview({'action':'prepare_launch','expected_source':self.fixture.binding(),
            'format_mode':'dedicated_feed_story_single_copy'})['operation']
        self.assertEqual(blocked['state'],'blocked');self.assertEqual(self.provider.request_counts,before)
        self.assertEqual(self.fixture.proposal['required_format'],'dedicated_feed_story_five_copy')

    def test_placement_cross_concept_story_swap_blocks_success_and_persists_ids(self):
        self.placement_fixture();read=self.provider.read;seen=[]
        def swapped(*args):
            row=read(*args)
            if 'asset_feed_spec' in row:
                seen.append(row['asset_feed_spec']['images'][1]['hash'])
                if len(seen)==2:row['asset_feed_spec']['images'][1]['hash']=seen[0]
            return row
        self.provider.read=swapped
        result=self.run_placement_scope();self.assertNotEqual(result['job']['state'],'verified')
        self.assertEqual(self.provider.batch_sizes,[2,7]);self.assertFalse(any(k=='ads' for k,v in self.provider.writes))
        self.assertEqual(self.provider.request_counts,{'read':11,'upload':14,'image_info':14,'batch':2})
        saved=self.service._get(result['job']['id']);creatives=[s for s in saved['steps'] if s['kind']=='adcreatives']
        self.assertTrue(all(s.get('provider_id') for s in creatives));self.assertEqual(sum(s['state']=='verified' for s in creatives),6)
        counts=dict(self.provider.request_counts);self.service.execute(result['job']['id'])
        self.assertEqual(self.provider.request_counts,counts)

    def test_paired_creation_response_lost_after_provider_commit_never_retries(self):
        self.placement_fixture()
        original=self.provider.batch
        def committed_but_lost(token,descriptors):
            result=original(token,descriptors)
            if any(x['relative_url'].endswith('/ads') for x in descriptors):
                raise TimeoutError('Synthetic post-commit response loss')
            return result
        self.provider.batch=committed_but_lost
        result=self.run_placement_scope()
        self.assertEqual(result['job']['state'],'unknown')
        created=sorted(self.provider.nodes)
        writes=len(self.provider.writes)
        self.assertEqual(sum(k=='ads' for k,v in self.provider.writes),7)
        self.assertTrue(all(s['state']=='unknown' for s in result['job']['steps'] if s['kind']=='ads'))
        operation=result['job']['id']
        self.service.enqueue(operation);self.service.execute(operation)
        restarted=self.make_service();again=restarted.execute(operation)
        self.assertEqual(again['job']['state'],'unknown')
        self.assertEqual(len(self.provider.writes),writes)
        self.assertEqual(sorted(self.provider.nodes),created)
        self.assertFalse(again['operation']['execution_ready'])
        self.assertIsNone(again['operation']['provider_write_performed'])

if __name__=='__main__':unittest.main()
