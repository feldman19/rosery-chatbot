"""Synthetic provider only. Never loads credentials or calls Meta."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib,json,sqlite3,tempfile,unittest
from copy import deepcopy
from unittest.mock import patch
from campaign_operations import CampaignOperations,ACCOUNT,PARENT,PAGE,INSTAGRAM,PIXEL,digest
from campaign_operations_transport import ProviderRejected,CampaignTransport
from test_launch_proposal import png

class FakeProvider:
    def __init__(self):
        self.nodes={PARENT:{'id':PARENT,'account_id':ACCOUNT,'name':'Existing sales','status':'PAUSED','effective_status':'PAUSED','objective':'OUTCOME_SALES','daily_budget':'0','lifetime_budget':'0'}};self.writes=[];self.images={};self.sequence=700000;self.fail_at=None;self.reject=False;self.readback_fail=False
    def read(self,token,node,fields):
        if node=='act_'+ACCOUNT:return {'account_id':ACCOUNT,'currency':'ILS'}
        if self.readback_fail and node!=PARENT:raise TimeoutError('private provider body')
        return deepcopy(self.nodes[node])
    def _write(self,kind,params):
        self.writes.append((kind,deepcopy(params)))
        if len(self.writes)==self.fail_at:
            if self.reject:raise ProviderRejected('explicit rejection')
            raise TimeoutError('secret body')
    def create(self,token,edge,params):
        self._write(edge,params);self.sequence+=1;identity=str(self.sequence);raw={'id':identity,'account_id':ACCOUNT,**deepcopy(params)}
        if edge=='ads':raw['creative']={'id':params['creative']['creative_id']};raw['campaign_id']=PARENT
        self.nodes[identity]=raw;return {'id':identity}
    def update(self,token,node,params):self._write('update',params);self.nodes[node].update(deepcopy(params));return {'success':True}
    def upload(self,token,raw,mime,name):
        self._write('image',{'name':name,'sha':hashlib.sha256(raw).hexdigest()});image_hash=hashlib.md5(raw).hexdigest();self.images[image_hash]={'hash':image_hash,'name':name,'account_id':ACCOUNT};return {'images':{name:self.images[image_hash]}}
    def image_info(self,token,image_hash):return {'data':[deepcopy(self.images[image_hash])]}
    def inventory(self,token,parent):
        sets=[deepcopy(x) for x in self.nodes.values() if x.get('campaign_id')==parent and 'creative' not in x and 'adset_id' not in x]
        ads=[deepcopy(x) for x in self.nodes.values() if x.get('campaign_id')==parent and 'adset_id' in x]
        return {'complete':True,'adsets':sets,'ads':ads}

class BatchProvider(FakeProvider):
    def __init__(self):
        super().__init__();self.batches=[];self.ambiguous=False
    def batch(self,token,descriptors):
        from urllib.parse import parse_qs
        self.batches.append(deepcopy(descriptors));out=[]
        for descriptor in descriptors:
            params={}
            for key,values in parse_qs(descriptor['body'],keep_blank_values=True).items():
                value=values[0]
                try:value=json.loads(value) if value.startswith(('{','[')) or value in ('true','false') else value
                except ValueError:pass
                params[key]=value
            result=self.create(token,descriptor['relative_url'].split('/')[-1],params)
            out.append({'code':200,'body':json.dumps(result)})
        return [None]*len(out) if self.ambiguous else out

class CampaignOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name);self.now=datetime(2026,10,18,10,tzinfo=timezone.utc);self.provider=FakeProvider();self.key=b'k'*32
        raw=png();sha=hashlib.sha256(raw).hexdigest();self.asset='abcdefghijkl';(self.root/'drive-assets').mkdir();self.file=self.root/'drive-assets'/(self.asset+'-'+sha+'.bin');self.file.write_bytes(raw)
        self.proposal={'id':'proposal1','request_id':'request1','request_revision':1,'ads':[{'asset_id':self.asset,'source_name':'Actual source','sha256':sha,'review':{'visual_checked':True},'destination_url':'https://roseryjewelry.com/collections/all?utm_content=one','headline':'Actual headline','primary_text':'Actual copy','description':'Actual description','cta':'SHOP_NOW','story':{'asset_id':'story-unused'},'primary_text_options':['not assigned']}],
          'operation_config':{'account_id':ACCOUNT,'campaign_id':PARENT,'source_ref':'verified-host-config','page_id':PAGE,'instagram_user_id':INSTAGRAM,'pixel_id':PIXEL,'limitations':['Feed only; extra options not assigned'],'adsets':[{'key':'static','name':'New static test','targeting':{'geo_locations':{'countries':['IL']},'age_min':18,'genders':[1],'publisher_platforms':['facebook','instagram'],'facebook_positions':['feed'],'instagram_positions':['stream']},'optimization_goal':'OFFSITE_CONVERSIONS','billing_event':'IMPRESSIONS','promoted_object':{'pixel_id':PIXEL,'custom_event_type':'PURCHASE'},'start_time':(self.now+timedelta(days=1)).isoformat(),'end_time':None,'budget':{'type':'daily','minor':12000,'approved':True},'asset_ids':[self.asset]}]}}
        self.service=self.new_service()
    def tearDown(self):self.temp.cleanup()
    def binding(self):return {'request_id':self.proposal['request_id'],'request_revision':self.proposal['request_revision'],'proposal_hash':digest(self.proposal)}
    def new_service(self):return CampaignOperations(self.root,self.key,lambda:{'available':True,'proposal':deepcopy(self.proposal),'source_binding':self.binding()},credential_loader=lambda:'synthetic-token',transport=self.provider,clock=lambda:self.now)
    def preview(self,**body):return self.service.preview({'action':'prepare_launch','expected_source':self.binding(),**body})['operation']
    def approve(self,p):return self.service.approve(p['operation_id'],p['digest'])['operation']
    def execute(self,p):self.approve(p);return self.service.execute(p['operation_id'])
    def test_device_source_kind_survives_frozen_preview(self):
        row=self.proposal['ads'][0];identity='local_'+'a'*32
        newpath=self.root/'drive-assets'/(identity+'-'+row['sha256']+'.bin');newpath.write_bytes(self.file.read_bytes())
        row.update(asset_id=identity,source_kind='device');self.proposal['operation_config']['adsets'][0]['asset_ids']=[identity]
        operation=self.preview();self.assertEqual(operation['files'][0]['source_kind'],'device');self.assertEqual(operation['files'][0]['id'],identity)
        row['source_kind']='drive'
        blocked=self.preview();self.assertTrue(blocked['blocked_reasons']);self.assertFalse(blocked['execution_ready'])
        self.assertIn('Source type',str(blocked['blocked_reasons']))
        self.assertEqual(self.provider.writes,[])
    def test_full_format_cannot_be_approved_as_feed_only(self):
        self.proposal['required_format']='dedicated_feed_story_five_copy'
        self.proposal['ads'][0]['story']={'asset_id':'story-source','sha256':'b'*64,'source_name':'R03_STORY.jpg'}
        self.proposal['ads'][0]['primary_text_options']=['Actual copy','two','three','four','five']
        for selector in ({},{'format_mode':'feed_original'}):
            p=self.preview(**selector)
            self.assertEqual(p['state'],'blocked');self.assertFalse(p['execution_ready'])
            required=p['manifest']['format_requirements']
            self.assertEqual(required['source_count'],2)
            self.assertEqual(len(required['concepts'][0]['primary_texts']),5)
            self.assertEqual(p['manifest']['required_format'],'dedicated_feed_story_five_copy')
            with self.assertRaises(ValueError):self.approve(p)
            self.assertEqual(p['manifest']['steps'],[])
        self.assertEqual(self.provider.writes,[])

    def test_full_requirement_change_blocks_prior_partial_approval(self):
        p=self.preview();self.approve(p)
        self.proposal['required_format']='dedicated_feed_story_five_copy'
        with self.assertRaises(ValueError):self.service.execute(p['operation_id'])
        self.assertEqual(self.provider.writes,[])

    def test_partial_recipe_receipt_cannot_claim_full_launch_ready(self):
        p=self.preview();result=self.execute(p)
        self.assertEqual(result['operation']['state'],'verified')
        self.assertFalse(result['operation']['full_launch_ready'])
        self.assertEqual(result['operation']['completion_scope'],'feed_only_single_copy_api_readback')

    def test_preparation_under_active_parent_never_changes_it(self):
        self.provider.nodes[PARENT]['status']='ACTIVE'
        self.provider.nodes[PARENT]['effective_status']='ACTIVE'
        p=self.preview();self.assertEqual(p['blocked_reasons'],[])
        result=self.execute(p);self.assertEqual(result['operation']['state'],'verified')
        self.assertEqual(self.provider.nodes[PARENT]['status'],'ACTIVE')
        self.assertTrue(all(v.get('status')=='PAUSED' for k,v in self.provider.writes if k in ('adsets','ads')))

    def test_two_groups_share_three_creation_waves_not_six_posts(self):
        self.provider=BatchProvider();self.service=self.new_service()
        second=deepcopy(self.proposal['operation_config']['adsets'][0]);second['key']='reviews';second['name']='Reviewed second group'
        self.proposal['operation_config']['adsets'].append(second)
        p=self.preview();result=self.execute(p)
        self.assertEqual(result['operation']['state'],'verified')
        self.assertEqual([len(w) for w in self.provider.batches],[2,2,2])
        self.assertEqual(len([x for x in self.provider.writes if x[0]=='image']),1)  # same bytes reused across groups

    def test_batch_path_is_connected_and_read_back(self):
        self.provider=BatchProvider();self.service=self.new_service()
        p=self.preview();result=self.execute(p)
        self.assertEqual(result['operation']['state'],'verified')
        self.assertEqual(len(self.provider.batches),3)
        self.assertTrue(all(s['state']=='verified' for s in self.service._get(p['operation_id'])['steps']))

    def test_ambiguous_batch_never_retries(self):
        self.provider=BatchProvider();self.provider.ambiguous=True;self.service=self.new_service()
        p=self.preview();result=self.execute(p)
        self.assertEqual(result['operation']['state'],'unknown')
        self.assertEqual(len(self.provider.batches),1)
        self.service.execute(p['operation_id'])
        self.assertEqual(len(self.provider.batches),1)

    def test_stale_display_cannot_silently_choose_new_copy(self):
        displayed=self.binding()
        self.proposal['ads'][0]['primary_text_options']=['Changed copy at selected index']
        p=self.preview(expected_source=displayed,copy_variant_indices={self.asset:1})
        self.assertTrue(p['blocked_reasons']);self.assertIn('Displayed proposal changed',p['blocked_reasons'][0]);self.assertEqual(p['manifest']['steps'],[]);self.assertEqual(self.provider.writes,[])
    def test_missing_or_malformed_display_binding_is_rejected(self):
        from campaign_operations import OperationBlocked
        for binding in (None,{},dict(self.binding(),request_revision=True),dict(self.binding(),proposal_hash='not-a-hash')):
            with self.subTest(binding=binding),self.assertRaises(OperationBlocked):self.service.preview({'action':'prepare_launch','expected_source':binding})
        self.assertEqual(self.provider.writes,[])
    def test_reviewed_copy_variant_is_frozen_and_executed_exactly(self):
        self.proposal['ads'][0]['primary_text_options']=['Actual copy','Alternative reviewed copy']
        p=self.preview(copy_variant_indices={self.asset:1})
        self.assertEqual(p['blocked_reasons'],[])
        self.assertEqual(p['manifest']['copy_choices'][0]['primary_text'],'Alternative reviewed copy')
        result=self.execute(p);self.assertEqual(result['operation']['state'],'verified')
        creative=next(value for kind,value in self.provider.writes if kind=='adcreatives')
        self.assertEqual(creative['object_story_spec']['link_data']['message'],'Alternative reviewed copy')
        self.assertNotIn('asset_feed_spec',creative)
    def test_reviewed_copy_invalid_or_unselected_options_never_write(self):
        for value in [{self.asset:True},{self.asset:5},{'not-a-selected-asset':1},{self.asset:4},['bad']]:
            with self.subTest(value=value):
                p=self.preview(copy_variant_indices=value);self.assertTrue(p['blocked_reasons']);self.assertEqual(self.provider.writes,[])
    def test_copy_variants_have_distinct_intent_but_explicit_original_is_idempotent(self):
        original=self.preview();same=self.preview(copy_variant_indices={self.asset:0});self.assertEqual(original['operation_id'],same['operation_id'])
        alternative=self.preview(copy_variant_indices={self.asset:1});self.assertNotEqual(original['operation_id'],alternative['operation_id'])
        self.assertNotEqual(original['digest'],alternative['digest'])
    def test_copy_option_change_invalidates_approved_source(self):
        p=self.preview(copy_variant_indices={self.asset:1});self.approve(p)
        self.proposal['ads'][0]['primary_text_options']=['Different reviewed text']
        with self.assertRaises(ValueError):self.service.enqueue(p['operation_id'])
        self.assertEqual(self.provider.writes,[])
    def test_preparation_creates_only_paused_standard_objects_and_verifies(self):
        p=self.preview();self.assertEqual(p['blocked_reasons'],[]);self.assertFalse(p['execution_ready']);self.assertEqual(len(p['files']),1)
        result=self.execute(p);self.assertEqual(result['operation']['state'],'verified');self.assertEqual([x[0] for x in self.provider.writes],['adsets','image','adcreatives','ads'])
        for kind,params in self.provider.writes:
            if kind in ('adsets','ads'):self.assertEqual(params['status'],'PAUSED')
            if kind=='adcreatives':self.assertNotIn('asset_feed_spec',params);self.assertIn('object_story_spec',params)
        self.assertFalse(p['manifest']['story_images_assigned']);self.assertFalse(p['manifest']['additional_text_options_assigned']);self.assertIn('Feed only; extra options not assigned',p['manifest']['limitations'])
        self.assertTrue(all(x['state']=='verified' for x in result['job']['steps']))
    def test_duplicate_preview_and_execute_are_idempotent(self):
        p=self.preview();self.assertEqual(p['operation_id'],self.preview()['operation_id']);self.execute(p);count=len(self.provider.writes);self.service.execute(p['operation_id']);self.service.enqueue(p['operation_id']);self.assertEqual(len(self.provider.writes),count)
    def test_stale_other_instance_approval_cannot_restore_completed_job(self):
        p=self.preview();other=self.new_service();original=self.service._put
        def interleave(job):
            other.approve(p['operation_id'],p['digest']);other.execute(p['operation_id']);original(job)
        with patch.object(self.service,'_put',side_effect=interleave):
            with self.assertRaises(ValueError):self.approve(p)
        self.assertEqual(self.service._get(p['operation_id'])['state'],'verified')
        self.service.execute(p['operation_id'])
        self.assertEqual(len(self.provider.writes),4)
    def test_stale_other_instance_enqueue_cannot_restore_completed_job(self):
        p=self.preview();self.approve(p);other=self.new_service();original=self.service._put
        def interleave(job):other.execute(p['operation_id']);original(job)
        with patch.object(self.service,'_put',side_effect=interleave):
            with self.assertRaises(ValueError):self.service.enqueue(p['operation_id'])
        self.assertEqual(self.service._get(p['operation_id'])['state'],'verified')
        self.service.execute(p['operation_id'])
        self.assertEqual(len(self.provider.writes),4)
    def test_stale_other_instance_schedule_cannot_restore_completed_job(self):
        at=(self.now+timedelta(hours=1)).isoformat();p=self.preview(scheduled_at=at);self.approve(p)
        other=self.new_service();original=self.service._put
        def interleave(job):
            other.schedule(p['operation_id'],at);self.now+=timedelta(hours=1);other.execute(p['operation_id']);original(job)
        with patch.object(self.service,'_put',side_effect=interleave):
            with self.assertRaises(ValueError):self.service.schedule(p['operation_id'],at)
        self.assertEqual(self.service._get(p['operation_id'])['state'],'verified')
        self.service.execute(p['operation_id'])
        self.assertEqual(len(self.provider.writes),4)
    def test_legacy_signed_record_without_revision_upgrades_on_transition(self):
        p=self.preview();job=self.service._get(p['operation_id']);job.pop('revision')
        raw=json.dumps(job,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        with self.service.db() as db:
            db.execute('UPDATE operations SET payload=?,signature=? WHERE id=?',(raw,self.service._sign(p['operation_id'],raw),p['operation_id']))
        self.approve(p)
        self.assertEqual(self.service._get(p['operation_id'])['revision'],1)
        self.assertEqual(self.service.execute(p['operation_id'])['operation']['state'],'verified')
    def test_requires_exact_digest(self):
        p=self.preview()
        with self.assertRaises(ValueError):self.service.approve(p['operation_id'],'0'*64)
        with self.assertRaises(ValueError):self.service.execute(p['operation_id'])
        self.assertEqual(self.provider.writes,[])
    def test_changed_proposal_revision_invalidates_approval(self):
        p=self.preview();self.approve(p);self.proposal['request_revision']=2
        with self.assertRaises(ValueError):self.service.execute(p['operation_id'])
        self.assertEqual(self.provider.writes,[])
    def test_source_revision_drift_during_provider_preflight_blocks_first_write(self):
        p=self.preview();self.approve(p);original=self.provider.read
        def drift(token,node,fields):
            result=original(token,node,fields)
            if node=='act_'+ACCOUNT:self.proposal['request_revision']=2
            return result
        self.provider.read=drift
        self.assertEqual(self.service.execute(p['operation_id'])['operation']['state'],'blocked')
        self.assertEqual(self.provider.writes,[])
    def test_approval_expiry_during_provider_preflight_blocks_first_write(self):
        p=self.preview();self.approve(p);original=self.provider.read
        def delay(token,node,fields):
            result=original(token,node,fields)
            if node=='act_'+ACCOUNT:self.now+=timedelta(minutes=16)
            return result
        self.provider.read=delay
        self.assertEqual(self.service.execute(p['operation_id'])['operation']['state'],'blocked')
        self.assertEqual(self.provider.writes,[])
    def test_source_tamper_blocks_before_writes(self):
        p=self.preview();self.approve(p);self.file.write_bytes(b'changed');result=self.service.execute(p['operation_id']);self.assertEqual(result['operation']['state'],'blocked');self.assertEqual(self.provider.writes,[])
    def test_unapproved_budget_and_missing_identity_fail_closed(self):
        self.proposal['operation_config']['adsets'][0]['budget']['approved']=False;p=self.preview();self.assertTrue(p['blocked_reasons'])
        with self.assertRaises(ValueError):self.approve(p)
        self.proposal['operation_config']['page_id']='wrong';self.assertTrue(self.preview()['blocked_reasons']);self.assertEqual(self.provider.writes,[])
    def test_past_start_and_unsupported_story_mode_block(self):
        self.proposal['operation_config']['adsets'][0]['start_time']=self.now.isoformat();self.assertTrue(self.preview()['blocked_reasons']);self.assertTrue(self.preview(format_mode='story')['blocked_reasons'])
    def test_timeout_unknown_never_retries(self):
        p=self.preview();self.provider.fail_at=1;result=self.execute(p);self.assertEqual(result['operation']['state'],'unknown');self.assertIsNone(result['operation']['provider_write_performed']);self.assertTrue(result['operation']['provider_write_attempted']);self.service.execute(p['operation_id']);self.service.enqueue(p['operation_id']);self.assertEqual(len(self.provider.writes),1)
    def test_partial_explicit_rejection_cannot_requeue(self):
        p=self.preview();self.provider.fail_at=2;self.provider.reject=True;result=self.execute(p);self.assertEqual(result['operation']['state'],'partial');count=len(self.provider.writes)
        with self.assertRaises(ValueError):self.service.enqueue(p['operation_id'])
        self.service.execute(p['operation_id']);self.assertEqual(len(self.provider.writes),count)
    def test_created_id_retained_if_readback_fails(self):
        p=self.preview();self.provider.readback_fail=True;result=self.execute(p);self.assertEqual(result['operation']['state'],'unknown');self.assertIn('provider_id',result['job']['steps'][0])
    def test_database_tamper_rejected(self):
        p=self.preview()
        with self.service.db() as db:db.execute("UPDATE operations SET payload='{}'")
        with self.assertRaises(ValueError):self.service.approve(p['operation_id'],p['digest'])
    def test_restart_inflight_becomes_unknown(self):
        p=self.preview();job=self.service._get(p['operation_id']);job['state']='running';job['steps'][0]['state']='inflight';self.service._put(job);restarted=self.new_service();self.assertEqual(restarted.status()['jobs'][0]['state'],'unknown');self.assertEqual(self.provider.writes,[])
    def test_schedule_bound_to_digest_durable_and_one_shot(self):
        at=(self.now+timedelta(hours=1)).isoformat();p=self.preview(scheduled_at=at);self.approve(p);self.assertTrue(self.service.status()['proposals'][0]['schedule_ready'])
        with self.assertRaises(ValueError):self.service.schedule(p['operation_id'],(self.now+timedelta(hours=2)).isoformat())
        self.service.schedule(p['operation_id'],at);self.service=self.new_service();self.assertEqual(self.service.tick(),[]);self.now+=timedelta(hours=1);self.assertEqual(self.service.tick()[0]['operation']['state'],'verified');count=len(self.provider.writes);self.service.tick();self.assertEqual(len(self.provider.writes),count)
    def test_schedule_prepare_at_adset_start_is_rejected(self):
        at=self.proposal['operation_config']['adsets'][0]['start_time']
        with self.assertRaises(ValueError):self.preview(scheduled_at=at)
    def test_enqueue_does_not_make_remote_writes(self):
        p=self.preview();self.approve(p);result=self.service.enqueue(p['operation_id']);self.assertEqual(result['operation']['state'],'queued');self.assertEqual(self.provider.writes,[]);self.service.tick();self.assertEqual(len(self.provider.writes),4)
    def test_cancel_unattempted_prepared_queued_and_scheduled_jobs(self):
        for state in ('prepared','queued','scheduled'):
            with self.subTest(state=state):
                body={'scheduled_at':(self.now+timedelta(hours=1)).isoformat()} if state=='scheduled' else {}
                p=self.preview(**body)
                if state!='prepared':self.approve(p)
                if state=='queued':self.service.enqueue(p['operation_id'])
                if state=='scheduled':self.service.schedule(p['operation_id'],body['scheduled_at'])
                self.assertTrue(self.service._public(self.service._get(p['operation_id']))['can_cancel'])
                result=self.service.cancel(p['operation_id'],p['digest'])
                self.assertEqual(result['operation']['state'],'cancelled')
                self.assertEqual(result['operation']['approval_status'],'revoked')
                self.assertFalse(result['operation']['can_cancel'])
                self.assertFalse(result['operation']['provider_write_attempted'])
                self.assertEqual(self.service.cancel(p['operation_id'],p['digest'])['operation']['state'],'cancelled')
                self.service.tick();self.service.execute(p['operation_id']);self.service.enqueue(p['operation_id'])
                self.assertEqual(self.provider.writes,[])
    def test_cancel_digest_required_but_expired_obsolete_pending_can_be_revoked(self):
        p=self.preview()
        with self.assertRaises(ValueError):self.service.cancel(p['operation_id'],'0'*64)
        self.now+=timedelta(minutes=16);self.proposal['request_revision']=2
        self.assertEqual(self.service.cancel(p['operation_id'],p['digest'])['operation']['state'],'cancelled')
        self.assertEqual(self.provider.writes,[])
    def test_cancelled_proposal_can_be_reprepared_with_new_exact_approval(self):
        p=self.preview();self.service.cancel(p['operation_id'],p['digest']);fresh=self.preview()
        self.assertNotEqual(p['operation_id'],fresh['operation_id'])
        self.assertEqual(fresh['state'],'prepared');self.assertEqual(fresh['approval_status'],'pending')
        self.assertEqual(self.provider.writes,[])
    def test_cancel_rejects_attempted_terminal_jobs(self):
        for state in ('unknown','partial','verified','not_applied','blocked','stale'):
            with self.subTest(state=state):
                p=self.preview()
                job=self.service._get(p['operation_id']);job['state']=state;self.service._put(job)
                with self.assertRaises(ValueError):self.service.cancel(p['operation_id'],p['digest'])
                # Use a different signed proposal revision for the next fixture.
                self.proposal['request_revision']+=1
        p=self.preview();job=self.service._get(p['operation_id']);job['steps'][0]['state']='inflight';self.service._put(job)
        with self.assertRaises(ValueError):self.service.cancel(p['operation_id'],p['digest'])
        self.assertEqual(self.provider.writes,[])
    def test_cancel_wins_worker_claim_and_stale_approval_cannot_restore_job(self):
        p=self.preview();self.approve(p);other=self.new_service();original=self.service._valid
        def interleave(job):original(job);other.cancel(p['operation_id'],p['digest'])
        with patch.object(self.service,'_valid',side_effect=interleave):
            self.assertEqual(self.service.execute(p['operation_id'])['operation']['state'],'cancelled')
        self.assertEqual(self.provider.writes,[])
        fresh=self.preview();original_put=self.service._put
        def cancel_before_approval(job):other.cancel(fresh['operation_id'],fresh['digest']);original_put(job)
        with patch.object(self.service,'_put',side_effect=cancel_before_approval):
            with self.assertRaises(ValueError):self.approve(fresh)
        self.assertEqual(self.service._get(fresh['operation_id'])['state'],'cancelled')
        self.assertEqual(self.provider.writes,[])
    def test_cancel_after_worker_claim_stops_before_first_provider_write(self):
        p=self.preview();self.approve(p);other=self.new_service();original=self.provider.read;seen=[]
        def cancel_after_claim(token,node,fields):
            if node=='act_'+ACCOUNT:
                self.assertTrue(other.cancel(p['operation_id'],p['digest'])['stop_requested'])
                seen.append(True)
            return original(token,node,fields)
        self.provider.read=cancel_after_claim
        self.assertEqual(self.service.execute(p['operation_id'])['operation']['state'],'blocked')
        self.assertEqual(seen,[True]);self.assertEqual(self.provider.writes,[])
    def add_selection_sources(self):
        second='mnopqrstuvwx';third='zyxwvutsrqpo';base=self.proposal['ads'][0]
        for identity in (second,third):
            ad=deepcopy(base);ad['asset_id']=identity;ad['source_name']='Source '+identity
            self.proposal['ads'].append(ad)
            (self.root/'drive-assets'/(identity+'-'+ad['sha256']+'.bin')).write_bytes(self.file.read_bytes())
        first_group=self.proposal['operation_config']['adsets'][0]
        first_group['asset_ids']=[self.asset,second]
        other=deepcopy(first_group);other.update(key='reviews',name='Other reviewed group',asset_ids=[third])
        self.proposal['operation_config']['adsets'].append(other)
        return second,third
    def test_selected_sources_create_only_requested_ads_and_no_empty_group(self):
        second,third=self.add_selection_sources()
        p=self.preview(selected_asset_ids=[second,self.asset])
        self.assertEqual(p['blocked_reasons'],[])
        self.assertEqual({x['id'] for x in p['files']},{self.asset,second})
        self.assertEqual([x['key'] for x in p['manifest']['adsets']],['static'])
        self.assertEqual(p['manifest']['adsets'][0]['params']['daily_budget'],12000)
        result=self.execute(p)
        self.assertEqual(result['operation']['state'],'verified')
        self.assertEqual([kind for kind,_ in self.provider.writes].count('adsets'),1)
        self.assertEqual([kind for kind,_ in self.provider.writes].count('ads'),2)
        self.assertEqual(len(self.provider.writes),6)  # two selected sources share the same verified bytes
        self.assertTrue(all(third not in params.get('name','') for _,params in self.provider.writes))
    def test_selection_order_is_idempotent_and_full_proposal_stays_bound(self):
        second,third=self.add_selection_sources()
        p=self.preview(selected_asset_ids=[third,self.asset],selected_adset_keys=['reviews','static'])
        same=self.preview(selected_asset_ids=[self.asset,third],selected_adset_keys=['static','reviews'])
        self.assertEqual(p['operation_id'],same['operation_id'])
        self.approve(p);self.proposal['ads'][1]['headline']='Unselected source changed'
        with self.assertRaises(ValueError):self.service.execute(p['operation_id'])
        self.assertEqual(self.provider.writes,[])
    def test_invalid_unknown_or_other_group_source_selection_is_blocked(self):
        second,third=self.add_selection_sources()
        for selected in ([],None,[self.asset,self.asset],['unknownsourceid'],[third],[self.asset]*11,[{}]):
            with self.subTest(selection=selected):
                p=self.preview(selected_asset_ids=selected,selected_adset_keys=['static'])
                self.assertTrue(p['blocked_reasons'])
        self.assertEqual(self.provider.writes,[])
        with self.assertRaises(ValueError):
            self.service.preview({'action':'pause','entity_type':'campaign','entity_id':PARENT,'selected_asset_ids':[self.asset]})
    def test_selected_source_requires_review_but_unselected_group_is_not_created(self):
        second,third=self.add_selection_sources();self.proposal['ads'][1]['review']['visual_checked']=False
        p=self.preview(selected_asset_ids=[second])
        self.assertTrue(p['blocked_reasons']);self.assertEqual(self.provider.writes,[])
        p=self.preview(selected_asset_ids=[third])
        self.assertEqual(p['blocked_reasons'],[])
        self.assertEqual([x['key'] for x in p['manifest']['adsets']],['reviews'])
    def test_long_source_id_preserves_binding_and_uses_bounded_upload_name(self):
        identity='L'*150;ad=self.proposal['ads'][0];raw,info=self.service._source(ad);ad['asset_id']=identity
        self.proposal['operation_config']['adsets'][0]['asset_ids']=[identity]
        # Isolate the provider-filename contract from Windows MAX_PATH in the
        # existing cache layout. Real file integrity is covered separately;
        # this source fixture still enforces exact ID and byte hash binding.
        def long_source(row):
            self.assertEqual(row['asset_id'],identity);self.assertEqual(row['sha256'],hashlib.sha256(raw).hexdigest())
            return raw,{**deepcopy(info),'id':identity}
        self.service._source=long_source
        p=self.preview();self.assertEqual(p['blocked_reasons'],[])
        self.assertEqual(p['files'][0]['id'],identity);self.assertEqual(p['files'][0]['sha256'],hashlib.sha256(raw).hexdigest())
        image=next(step for step in p['manifest']['steps'] if step['kind']=='image')
        name=image['params']['name'];self.assertEqual(image['params']['asset_id'],identity)
        self.assertLessEqual(len(name),120);self.assertRegex(name,r'^[A-Za-z0-9_.-]+$')
        self.assertIn(hashlib.sha256(identity.encode()).hexdigest(),name)
        # Exercise the real transport's filename validation/multipart builder
        # while replacing only its final request with an offline sentinel.
        transport=CampaignTransport()
        with patch.object(transport,'_request',return_value={'offline':True}) as wire:
            self.assertEqual(transport.upload('synthetic',raw,'image/png',name),{'offline':True})
            self.assertIn(raw,wire.call_args.kwargs['data'])
        self.assertEqual(self.execute(p)['operation']['state'],'verified')
        self.assertEqual(len(self.provider.writes),4)
    def test_default_and_explicit_all_feed_selectors_have_same_effective_intent(self):
        p=self.preview();saved=self.service._get(p['operation_id'])
        same=self.preview(format_mode='feed_original',selected_asset_ids=[self.asset],selected_adset_keys=['static'])
        self.assertEqual(same['operation_id'],p['operation_id'])
        self.assertEqual(self.service._get(p['operation_id']),saved)
        self.assertEqual(self.provider.writes,[])
    def test_attempted_same_content_cannot_duplicate_by_schedule_or_parent_name(self):
        p=self.preview();self.execute(p);saved=self.service._get(p['operation_id'])
        self.now+=timedelta(hours=1);self.provider.nodes[PARENT]['name']='Unrelated renamed campaign'
        same=self.preview(format_mode='feed_original',selected_asset_ids=[self.asset],
                          scheduled_at=(self.now+timedelta(hours=1)).isoformat())
        self.assertEqual(same['operation_id'],p['operation_id'])
        self.assertEqual(self.service._get(p['operation_id']),saved)
        self.assertEqual(len(self.provider.writes),4)
    def test_different_unapproved_schedules_need_one_authority_cancel_before_reapproval(self):
        first_at=(self.now+timedelta(hours=1)).isoformat();second_at=(self.now+timedelta(hours=2)).isoformat()
        # Force the unapproved exact-match record to sort before the approved
        # record; correctness cannot depend on random operation ID row order.
        with patch('campaign_operations.secrets.token_hex',side_effect=['f'*32,'0'*32]):
            first=self.preview(scheduled_at=first_at);second=self.preview(scheduled_at=second_at)
        self.assertNotEqual(first['operation_id'],second['operation_id'])
        self.approve(first)
        with self.assertRaises(ValueError):self.approve(second)
        self.assertEqual(self.service._get(second['operation_id'])['approval_status'],'pending')
        # A new preview never silently changes the already approved date.
        self.assertEqual(self.preview(scheduled_at=second_at)['operation_id'],first['operation_id'])
        self.service.cancel(first['operation_id'],first['digest']);self.approve(second)
        self.service.schedule(second['operation_id'],second_at);self.now+=timedelta(hours=2)
        self.service.tick();self.assertEqual(len(self.provider.writes),4)
        self.assertEqual(self.service._get(first['operation_id'])['state'],'cancelled')
    def test_attempted_job_has_priority_over_exact_pending_alternative(self):
        first_at=(self.now+timedelta(hours=1)).isoformat();second_at=(self.now+timedelta(hours=2)).isoformat()
        with patch('campaign_operations.secrets.token_hex',side_effect=['f'*32,'0'*32]):
            first=self.preview(scheduled_at=first_at);second=self.preview(scheduled_at=second_at)
        self.approve(first);self.now+=timedelta(hours=1)
        self.assertEqual(self.service.execute(first['operation_id'])['operation']['state'],'verified')
        prior=self.preview(scheduled_at=second_at)
        self.assertEqual(prior['operation_id'],first['operation_id'])
        self.assertEqual(prior['state'],'verified')
        self.assertEqual(self.service._get(second['operation_id'])['approval_status'],'pending')
        self.assertEqual(len(self.provider.writes),4)
    def test_legacy_duplicate_approvals_cannot_both_become_writable(self):
        first=self.preview(scheduled_at=(self.now+timedelta(hours=1)).isoformat())
        second=self.preview(scheduled_at=(self.now+timedelta(hours=2)).isoformat())
        self.approve(first)
        # Simulate a pre-upgrade signed duplicate approval without editing the
        # original historical record or bypassing provider scope.
        legacy=self.service._get(second['operation_id']);legacy['approval_status']='approved'
        with self.service.db() as db:self.service._save(db,legacy)
        self.now+=timedelta(hours=2)
        with self.assertRaises(ValueError):self.service.execute(second['operation_id'])
        self.assertEqual(self.provider.writes,[])
        self.service.cancel(first['operation_id'],first['digest'])
        self.assertEqual(self.service.execute(second['operation_id'])['operation']['state'],'verified')
        self.assertEqual(len(self.provider.writes),4)
    def test_parent_drift_before_write_blocks(self):
        p=self.preview();self.approve(p);self.provider.nodes[PARENT]['status']='ACTIVE';result=self.service.execute(p['operation_id']);self.assertEqual(result['operation']['state'],'blocked');self.assertEqual(self.provider.writes,[])
    def test_pause_and_budget_readback_and_child_activation(self):
        p=self.service.preview({'action':'set_budget','entity_type':'campaign','entity_id':PARENT,'budget_minor':12000})['operation'];self.assertEqual(self.execute(p)['operation']['state'],'verified')
        p=self.service.preview({'action':'pause','entity_type':'campaign','entity_id':PARENT})['operation'];self.assertEqual(self.execute(p)['operation']['state'],'verified')
        child='888888';self.provider.nodes[child]={'id':child,'account_id':ACCOUNT,'campaign_id':PARENT,'status':'PAUSED','name':'Known child'};p=self.service.preview({'action':'activate','entity_type':'ad','entity_id':child})['operation'];self.assertEqual(self.execute(p)['operation']['state'],'verified');self.assertEqual(self.provider.nodes[child]['status'],'ACTIVE')
    def test_campaign_activation_exact_descendants_and_drift(self):
        self.provider.nodes['888888']={'id':'888888','account_id':ACCOUNT,'campaign_id':PARENT,'name':'Active old set','status':'ACTIVE','effective_status':'CAMPAIGN_PAUSED','daily_budget':'12000','lifetime_budget':'0'}
        p=self.service.preview({'action':'activate','entity_type':'campaign','entity_id':PARENT})['operation'];self.assertEqual(p['blocked_reasons'],[]);self.assertEqual(p['before']['descendants']['adsets'][0]['daily_budget'],'12000');self.approve(p);self.provider.nodes['888888']['daily_budget']='24000';result=self.service.execute(p['operation_id']);self.assertEqual(result['operation']['state'],'blocked');self.assertEqual(self.provider.writes,[])
        p=self.service.preview({'action':'activate','entity_type':'campaign','entity_id':PARENT})['operation'];result=self.execute(p);self.assertEqual(result['operation']['state'],'verified');self.assertNotIn('remains PAUSED',result['operation']['reason']);self.assertIn('Requested mutation fields read back',result['operation']['reason']);self.assertEqual(self.provider.nodes['888888']['daily_budget'],'24000');self.assertEqual(len(self.provider.writes),1)
    def test_management_can_choose_observed_child_without_new_write(self):
        self.provider.nodes['888888']={'id':'888888','account_id':ACCOUNT,'campaign_id':PARENT,'name':'Existing set','status':'ACTIVE','effective_status':'CAMPAIGN_PAUSED','daily_budget':'12000','lifetime_budget':'0'}
        self.service.preview({'action':'activate','entity_type':'campaign','entity_id':PARENT})
        child=next(x for x in self.service.status()['entities'] if x['entity_id']=='888888')
        self.assertEqual(child['entity_type'],'adset')
        self.assertEqual(child['budget_minor'],'12000')
        self.assertTrue(child['requires_fresh_preview'])
        self.assertEqual(self.provider.writes,[])
    def test_creation_intent_cannot_duplicate_after_expiry(self):
        p=self.preview();self.execute(p);self.now+=timedelta(hours=1);same=self.preview();self.assertEqual(same['operation_id'],p['operation_id']);self.assertEqual(len(self.provider.writes),4)
    def test_wrong_scope_entity_and_unknown_config_not_approved(self):
        self.provider.nodes['999999']={'id':'999999','account_id':ACCOUNT,'campaign_id':'123456','status':'PAUSED'};p=self.service.preview({'action':'pause','entity_type':'ad','entity_id':'999999'})['operation'];self.assertTrue(p['blocked_reasons'])
    def test_iso_readback_normalization(self):
        self.assertTrue(self.service._match({'start_time':'2026-10-19T10:00:00+0000'},{'start_time':'2026-10-19T10:00:00+00:00'}))
    def test_external_credential_errors_are_redacted(self):
        self.service.credentials=lambda:(_ for _ in ()).throw(ValueError('raw-private-token'));p=self.preview();self.assertNotIn('raw-private-token',json.dumps(p));self.assertTrue(p['blocked_reasons'])
    def test_transport_rejects_arbitrary_nodes_edges_and_bad_multipart(self):
        transport=CampaignTransport()
        for node in ('https://evil.invalid','12345/ads','act_12345'):
            with self.assertRaises(ValueError):transport.read('fake',node,'id')
        with self.assertRaises(ValueError):transport.create('fake','campaigns',{})
        with self.assertRaises(ValueError):transport.upload('fake',b'bytes','text/html','name.png')
    def test_missing_descendant_budget_never_becomes_zero(self):
        self.provider.nodes['888888']={'id':'888888','account_id':ACCOUNT,'campaign_id':PARENT,'name':'Unknown budget','status':'ACTIVE','effective_status':'CAMPAIGN_PAUSED','daily_budget':'12000'}
        p=self.service.preview({'action':'activate','entity_type':'campaign','entity_id':PARENT})['operation'];self.assertTrue(p['blocked_reasons']);self.assertEqual(self.provider.writes,[])
    def test_transport_multipart_uses_fixed_graph_and_exact_bytes(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"images":{}}'
        opener=unittest.mock.Mock();opener.open.return_value=Response()
        with patch('campaign_operations_transport.urllib.request.build_opener',return_value=opener):CampaignTransport().upload('synthetic-token',b'approved-exact-bytes','image/png','approved.png')
        request=opener.open.call_args.args[0];self.assertEqual(request.full_url,'https://graph.facebook.com/v26.0/act_'+ACCOUNT+'/adimages');self.assertIn(b'approved-exact-bytes',request.data);self.assertIn(b'name="filename"; filename="approved.png"',request.data);self.assertEqual(opener.open.call_args.kwargs['timeout'],25)

if __name__=='__main__':unittest.main()
