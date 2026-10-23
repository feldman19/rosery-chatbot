"""Regression coverage for the creative-launch review; synthetic providers only."""
from datetime import timedelta
from unittest.mock import patch
import unittest
import test_launch_proposal_schema as fixtures
import test_http_creative_launch_flow as http_fixtures
from campaign_operations import OperationBlocked
from campaign_operations_transport import CampaignTransport, ProviderRejected


class ReviewTests(fixtures.V2ExecutionTests):
    def test_explicit_recipe_required(self):
        self.proposal.pop('creation_recipe')
        self.assertIn('Explicit creation_recipe',self.preview()['blocked_reasons'][0])

    def test_recipe_is_manifest_bound_and_sent(self):
        self.proposal['creation_recipe']={'adset_fields':{'is_dynamic_creative':True,'destination_type':'WEBSITE','bid_strategy':'LOWEST_COST_WITHOUT_CAP'},'creative_fields':{'degrees_of_freedom_spec':{'creative_features_spec':{'standard_enhancements':{'enroll_status':'OPT_OUT'},'inline_comment':{'enroll_status':'OPT_IN'}}}},'optimization_type':'DEGREES_OF_FREEDOM'}
        operation=self.preview()
        self.assertFalse(operation['blocked_reasons'])
        result=self.execute(operation)
        self.assertEqual(result['state'],'verified')
        self.assertTrue(next(p for edge,p in self.provider.writes if edge=='adsets')['is_dynamic_creative'])

    def test_readback_nested_list_extra_fields_only(self):
        self.assertTrue(self.service._match({'videos':[{'video_id':'123456','thumbnail_url':'extra','link_urls':[]}]},{'videos':[{'video_id':'123456'}]},2))
        self.assertFalse(self.service._match([{'text':'wrong'}],[{'text':'sent'}]))
        self.assertFalse(self.service._match([1,2],[1]))

    def test_approval_time_and_execution_crossing_expiry(self):
        operation=self.preview()
        self.now+=timedelta(minutes=13)
        self.service.approve(operation['operation_id'],operation['digest'])
        self.now+=timedelta(minutes=3)
        upload=self.provider.upload_video
        def slow(*args):
            self.now+=timedelta(minutes=30)
            return upload(*args)
        self.provider.upload_video=slow
        result=self.service.execute(operation['operation_id'])['operation']
        self.assertEqual(result['state'],'verified')

    def test_cbo_preview_blocks_without_writes(self):
        self.provider.nodes[self.provider.campaign]['daily_budget']='10000'
        self.assertIn('Campaign budget',self.preview()['blocked_reasons'][0])
        self.assertEqual(self.provider.writes,[])

    def test_unrelated_inventory_and_effective_status_do_not_block(self):
        operation=self.preview()
        self.service.approve(operation['operation_id'],operation['digest'])
        self.provider.nodes[self.provider.campaign]['effective_status']='IN_PROCESS'
        self.provider.nodes['7654321']={'id':'7654321','account_id':fixtures.ACCOUNT,'campaign_id':self.provider.campaign,'status':'PAUSED','effective_status':'PAUSED'}
        self.assertEqual(self.service.execute(operation['operation_id'])['operation']['state'],'verified')

    def test_video_pending_then_ready(self):
        original=self.provider.video_info
        calls=[]
        def read(*args):
            calls.append(1)
            return {'pending':True} if len(calls)==1 else original(*args)
        self.provider.video_info=read
        with patch('campaign_operations.time.sleep'):
            self.assertEqual(self.execute(self.preview())['state'],'verified')
        self.assertEqual(sum(edge=='video' for edge,p in self.provider.writes),1)

    def test_definite_upload_post_rejection_allows_new_upload(self):
        upload=self.provider.upload_video
        with patch.object(self.provider,'upload_video',side_effect=ProviderRejected()):
            self.execute(self.preview())
        self.proposal['request_revision']+=1
        self.assertEqual(self.execute(self.preview())['state'],'verified')
        self.assertEqual(sum(edge=='video' for edge,p in self.provider.writes),1)

    def poll_rejection(self,code):
        import io,json
        from urllib.error import HTTPError
        transport=CampaignTransport()
        error=HTTPError('https://graph.facebook.com/',400,'rate limited',{},io.BytesIO(json.dumps({'error':{'code':code}}).encode()))
        with patch.object(self.provider,'video_info',side_effect=transport.video_info), patch('campaign_operations_transport.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=error
            self.assertEqual(self.execute(self.preview())['state'],'unknown')
        self.proposal['request_revision']+=1
        self.execute(self.preview())
        self.assertEqual(sum(edge=='video' for edge,p in self.provider.writes),1)

    def test_rate_limit_4(self):self.poll_rejection(4)
    def test_rate_limit_17(self):self.poll_rejection(17)
    def test_rate_limit_80004(self):self.poll_rejection(80004)

    def test_processing_error_requires_explicit_abandon(self):
        self.provider.video_status='error'
        operation=self.preview()
        self.assertEqual(self.execute(operation)['state'],'unknown')
        self.proposal['request_revision']+=1
        blocked=self.preview()
        self.execute(blocked)
        self.assertEqual(sum(edge=='video' for edge,p in self.provider.writes),1)
        blocked_job=self.service._get(blocked['operation_id'])
        blocked_step=next(s for s in blocked_job['steps'] if s['kind']=='video')
        self.service.abandon_unknown_video(blocked_job['operation_id'],blocked_job['digest'],blocked_step['key'],True)
        job=self.service._get(operation['operation_id'])
        step=next(s for s in job['steps'] if s['kind']=='video')
        self.service.reconcile(job['operation_id'],job['digest'])
        self.service.abandon_unknown_video(job['operation_id'],job['digest'],step['key'],True)
        self.proposal['request_revision']+=1
        self.provider.video_status='ready'
        self.assertEqual(self.execute(self.preview())['state'],'verified')
        self.assertEqual(sum(edge=='video' for edge,p in self.provider.writes),2)

    def test_saved_video_abandon_requires_reconciled_failure(self):
        for status in ('ready','processing','unavailable','error','not_found'):
            with self.subTest(status=status):
                operation=self.preview()
                with patch.object(self.provider,'video_info',side_effect=ProviderRejected()):
                    self.assertEqual(self.execute(operation)['state'],'unknown')
                job=self.service._get(operation['operation_id'])
                step=next(s for s in job['steps'] if s['kind']=='video')
                with self.assertRaises(OperationBlocked):
                    self.service.abandon_unknown_video(job['operation_id'],job['digest'],step['key'],True)
                read={'id':step['provider_id'],'account_id':fixtures.ACCOUNT,'status':{'video_status':status}}
                if status=='not_found':read={'pending':True,'not_found':True}
                with patch.object(self.provider,'video_info',return_value=read) as poll:
                    if status=='unavailable':poll.side_effect=ProviderRejected()
                    self.service.reconcile(job['operation_id'],job['digest'])
                if status in ('error','not_found'):
                    self.service.abandon_unknown_video(job['operation_id'],job['digest'],step['key'],True)
                    self.assertEqual(next(s for s in self.service._get(job['operation_id'])['steps'] if s['key']==step['key'])['state'],'abandoned')
                else:
                    with self.assertRaises(OperationBlocked):
                        self.service.abandon_unknown_video(job['operation_id'],job['digest'],step['key'],True)
                with self.service.db() as db:db.execute('DELETE FROM operations')
                self.proposal['request_revision']+=1

    def test_created_objects_readback_rejection_stays_reconcilable(self):
        original=self.service._verify_created
        for kind in ('adsets','adcreatives','ads'):
            with self.subTest(kind=kind):
                operation=self.preview()
                def verify(token,step,job,params,identity):
                    if step['kind']==kind:raise ProviderRejected()
                    return original(token,step,job,params,identity)
                with patch.object(self.service,'_verify_created',side_effect=verify):
                    self.assertEqual(self.execute(operation)['state'],'unknown')
                job=self.service._get(operation['operation_id'])
                step=next(s for s in job['steps'] if s['kind']==kind and s.get('provider_id'))
                self.assertEqual(step['state'],'unknown')
                writes=list(self.provider.writes)
                recovered=self.service.reconcile(job['operation_id'],job['digest'])
                self.assertEqual(next(s for s in recovered['job']['steps'] if s['key']==step['key'])['state'],'verified')
                self.assertEqual(self.provider.writes,writes)
                with self.service.db() as db:db.execute('DELETE FROM operations')
                self.proposal['request_revision']+=1

    def test_degrees_spec_and_explicit_default_opt_out_required(self):
        for fields in ({},{'degrees_of_freedom_spec':{'creative_features_spec':{}}}):
            self.proposal['creation_recipe']['creative_fields']=fields
            self.assertTrue(self.preview()['blocked_reasons'])

    def test_v1_list_mismatch_remains_exact(self):
        actual={'videos':[{'video_id':'123456','extra':True}]}
        expected={'videos':[{'video_id':'123456'}]}
        self.assertFalse(self.service._match(actual,expected))
        self.assertTrue(self.service._match(actual,expected,2))

    def test_recipe_values_fail_closed(self):
        from copy import deepcopy
        original=deepcopy(self.proposal['creation_recipe'])
        cases=[('adset_fields',{'bid_strategy':'OTHER'}),('adset_fields',{'destination_type':'APP'}),('optimization_type','OTHER'),('creative_fields',{'degrees_of_freedom_spec':{'creative_features_spec':{'standard_enhancements':{'enroll_status':'OPT_IN'}}}}),('creative_fields',{'degrees_of_freedom_spec':{'extra':True}})]
        for field,value in cases:
            with self.subTest(field=field,value=value):
                self.proposal['creation_recipe']=deepcopy(original)
                self.proposal['creation_recipe'][field]=value
                self.assertTrue(self.preview()['blocked_reasons'])
                self.assertEqual(self.provider.writes,[])
        self.proposal['creation_recipe']=original
        self.proposal['creation_recipe']['creative_fields']={'degrees_of_freedom_spec':{'creative_features_spec':{'inline_comment':{'enroll_status':'OPT_IN'},'standard_enhancements':{'enroll_status':'OPT_OUT'}}}}
        operation=self.preview(owner_confirmations=[])
        self.assertFalse(operation['blocked_reasons'])
        self.assertEqual(operation['manifest']['creation_recipe'],self.proposal['creation_recipe'])
        self.assertEqual(operation['manifest']['owner_confirmations'],[])

    def test_unknown_video_requires_exact_owner_waiver(self):
        def fail(*args):raise TimeoutError()
        upload=self.provider.upload_video
        self.provider.upload_video=fail
        operation=self.preview();self.execute(operation)
        job=self.service._get(operation['operation_id'])
        step=next(s for s in job['steps'] if s['kind']=='video')
        with self.assertRaises(OperationBlocked):self.service.abandon_unknown_video(job['operation_id'],job['digest'],step['key'])
        self.service.abandon_unknown_video(job['operation_id'],job['digest'],step['key'],True)
        self.proposal['request_revision']+=1
        self.provider.upload_video=upload
        self.assertEqual(self.execute(self.preview())['state'],'verified')

    def test_status_cache_and_sanitized_exception(self):
        with patch.object(self.provider,'campaigns',wraps=self.provider.campaigns) as read:
            self.service.status();self.service.status()
            self.assertEqual(read.call_count,1)
        self.service._campaign_cache=None
        with patch.object(self.provider,'campaigns',side_effect=RuntimeError('PRIVATE')):
            self.assertNotIn('PRIVATE',str(self.service.status()))

    def test_mov_mime(self):
        ad=next(a for a in self.proposal['ads'] if a['media_kind']=='video')
        ad['sha256']=self.cache(ad['asset_id'],b'\x00\x00\x00\x18ftypqt  synthetic')
        self.assertEqual(self.service._video_source(ad)[1]['mime'],'video/quicktime')

    def test_video_upload_timeout_scales(self):
        transport=CampaignTransport()
        with patch.object(transport,'_request',return_value={'id':'123456'}) as request:
            transport.upload_video('synthetic',b'\x00\x00\x00\x18ftypmp42'+b'x'*1024,'test.mp4')
            self.assertGreater(request.call_args.kwargs['timeout'],60)

    def test_jpeg_upload_names(self):
        original=self.service._source
        def jpeg(row):
            raw,info=original(row)
            return raw,dict(info,mime='image/jpeg')
        with patch.object(self.service,'_source',side_effect=jpeg):
            operation=self.preview()
        images=[s for s in operation['manifest']['steps'] if s['kind']=='image']
        self.assertTrue(images)
        self.assertTrue(all(s['params']['name'].endswith('.jpg') for s in images))

    def test_history_reuse_uses_selected_campaign(self):
        with patch('creative_reuse.historical_source_reuse',return_value={}) as history:
            self.preview()
        self.assertEqual(history.call_args.args[3],self.provider.campaign)

    def test_saved_video_requires_explicit_waiver(self):
        self.provider.video_status='processing'
        ticks=iter(range(10000));self.service.monotonic=lambda:next(ticks)
        operation=self.preview()
        with patch('campaign_operations.time.sleep'):self.execute(operation)
        job=self.service._get(operation['operation_id'])
        step=next(s for s in job['steps'] if s['kind']=='video')
        with self.assertRaises(OperationBlocked):self.service.abandon_unknown_video(job['operation_id'],job['digest'],step['key'],False)

    def test_recipe_degrees_fields_readback_requested(self):
        self.proposal['creation_recipe']['creative_fields']={'degrees_of_freedom_spec':{'creative_features_spec':{'standard_enhancements':{'enroll_status':'OPT_OUT'},'inline_comment':{'enroll_status':'OPT_IN'}}}}
        with patch.object(self.provider,'read',wraps=self.provider.read) as read:
            self.assertEqual(self.execute(self.preview())['state'],'verified')
        self.assertTrue(any('degrees_of_freedom_spec' in call.args[2] for call in read.call_args_list))

    def test_missing_video_node_processing_and_other_errors_propagate(self):
        from campaign_operations_transport import ProviderRejected
        transport=CampaignTransport()
        with patch.object(transport,'read',side_effect=ProviderRejected(details={'code':100,'error_subcode':33})):
            self.assertTrue(transport.video_info('synthetic','123456')['pending'])
        with patch.object(transport,'read',side_effect=ProviderRejected(details={'code':190})):
            with self.assertRaises(ProviderRejected):transport.video_info('synthetic','123456')


class HTTPWaiverTests(unittest.TestCase):
    setUp=http_fixtures.HTTPCreativeLaunchFlowTests.setUp
    request=http_fixtures.HTTPCreativeLaunchFlowTests.request

    def test_authenticated_exact_owner_waiver_route(self):
        body={'operation_id':'a'*32,'digest':'b'*64,'step_key':'set0ad0media','owner_confirmation':True}
        with patch('campaign_operations.CampaignOperations.abandon_unknown_video',return_value={'waived':True}) as waive:
            self.assertEqual(self.request('/api/campaign-operations/abandon-video',body,authorized=False)[0],403)
            self.assertEqual(self.request('/api/campaign-operations/abandon-video',dict(body,owner_confirmation=False))[0],400)
            self.assertEqual(self.request('/api/campaign-operations/abandon-video',dict(body,extra=True))[0],400)
            waive.assert_not_called()
            self.assertEqual(self.request('/api/campaign-operations/abandon-video',body)[0],200)
            waive.assert_called_once_with(body['operation_id'],body['digest'],body['step_key'],True)
