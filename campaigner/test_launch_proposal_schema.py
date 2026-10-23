"""Shared pure contract tests plus synthetic v2 execution. No provider network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from launch_proposal_schema import validate_proposal_v2, make_valid_proposal_v2, digest
from campaign_operations import CampaignOperations, OperationBlocked, ACCOUNT, PARENT
from campaign_operations import digest as operation_digest
from campaign_operations_transport import CampaignTransport
from operations_config import operation_proposal
from meta_batch import validate_creation_v2, BatchRejected, plan_batches
from test_campaign_operations import FakeProvider
from test_launch_proposal import png


class SchemaTests(unittest.TestCase):
    def test_fixture_and_digest_contract(self):
        proposal = make_valid_proposal_v2()
        self.assertIs(validate_proposal_v2(proposal), proposal)
        self.assertEqual(digest(proposal), operation_digest(proposal))
        self.assertEqual(operation_proposal({'available': True, 'proposal': proposal})['proposal'], proposal)
        self.assertEqual([g['target']['mode'] for g in proposal['adsets']], ['new', 'existing'])
        self.assertEqual([a['media_kind'] for a in proposal['ads']], ['image', 'video'])
        self.assertNotIn('operation_config', proposal)

    def test_labelled_schema_refusals(self):
        cases = {
            'unknown_top': lambda p: p.update(extra=True),
            'unknown_nested': lambda p: p['ads'][0]['review'].update(extra=True),
            'unknown_budget_field': lambda p: p['adsets'][0].update(status='ACTIVE'),
            'missing_field': lambda p: p.pop('summary'),
            'unknown_version': lambda p: p.update(schema_version=3),
            'boolean_version': lambda p: p.update(schema_version=True),
            'revision_type': lambda p: p.update(request_revision=True),
            'naive_timestamp': lambda p: p.update(prepared_at='2026-10-23T10:00:00'),
            'overlong_validity': lambda p: p.update(expires_at='2026-10-23T11:00:00Z'),
            'format_narrowing': lambda p: p.update(required_format='feed_original_single_copy'),
            'active_status': lambda p: p.update(creation_status='ACTIVE'),
            'invalid_campaign_id': lambda p: p['strategy'].update(campaign_id='other'),
            'nonadvisory_strategy': lambda p: p['strategy'].update(source='authorization'),
            'invalid_rule_digest': lambda p: p['rules'].update(rulebook_sha256='bad'),
            'duplicate_citation': lambda p: p['rules']['citations'].append('full_copy_set'),
            'empty_adsets': lambda p: p.update(adsets=[]),
            'too_many_ads': lambda p: p.update(ads=p['ads']*8),
            'duplicate_group': lambda p: p['adsets'][1].update(key='group0'),
            'duplicate_existing_target': lambda p: p['adsets'][0].update(target=deepcopy(p['adsets'][1]['target']), budget_minor=None),
            'missing_reuse_rule': lambda p: p['adsets'][1]['target'].pop('rule_cited'),
            'wrong_reuse_rule': lambda p: p['adsets'][1]['target'].update(rule_cited='guessed'),
            'reuse_budget_write': lambda p: p['adsets'][1].update(budget_minor=10000),
            'boolean_budget': lambda p: p['adsets'][0].update(budget_minor=True),
            'targeting': lambda p: p['adsets'][0].update(targeting_mode='children'),
            'unknown_mode': lambda p: p['adsets'][0]['target'].update(mode='replace'),
            'duplicate_asset': lambda p: p['ads'][1].update(asset_id=p['ads'][0]['asset_id']),
            'invalid_sha': lambda p: p['ads'][0].update(sha256='bad'),
            'source_kind': lambda p: p['ads'][0].update(source_kind='device'),
            'unknown_assignment': lambda p: p['ads'][0].update(adset_key='missing'),
            'unreviewed': lambda p: p['ads'][0]['review'].update(visual_checked=False),
            'missing_video': lambda p: p['ads'][1].update(video=None),
            'invalid_media': lambda p: p['ads'][0].update(media_kind='carousel'),
            'technical_video': lambda p: p['ads'][1]['video'].update(technical_valid=False),
            'video_duration': lambda p: p['ads'][1]['video'].update(duration_ms=0),
            'audio_coverage': lambda p: p['ads'][1]['review'].update(audio_status='unknown'),
            'video_thumbnail': lambda p: p['ads'][1]['video'].update(thumbnail_asset_id='../x'),
            'five_texts': lambda p: p['ads'][0].update(primary_text_options=['one']),
            'five_headlines': lambda p: p['ads'][0].update(headline_options=['one']),
            'blank_copy': lambda p: p['ads'][0]['primary_text_options'].__setitem__(0, ''),
            'wrong_destination': lambda p: p['ads'][0].update(destination_url='https://example.com'),
            'destination_port': lambda p: p['ads'][0].update(destination_url='https://roseryjewelry.com:8443'),
            'cta': lambda p: p['ads'][0].update(cta='LEARN_MORE'),
            'tracking_type': lambda p: p['ads'][0].update(url_tags={}),
            'empty_group': lambda p: p['ads'][1].update(adset_key='group0'),
        }
        for label, mutate in cases.items():
            with self.subTest(refusal=label):
                proposal = make_valid_proposal_v2()
                mutate(proposal)
                with self.assertRaises(ValueError):
                    validate_proposal_v2(proposal)


class V2Provider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.campaign = '999999999999999'
        self.nodes[self.campaign] = dict(self.nodes[PARENT], id=self.campaign,daily_budget='0',lifetime_budget='0')
        self.video_status = 'ready'
        self.inventory_complete = True
        self.omit_campaign = False
        self.corrupt_copy = False

    def campaigns(self, token):
        return {'complete': True, 'data': [] if self.omit_campaign else [deepcopy(self.nodes[self.campaign])]}

    def inventory_v2(self, token, campaign):
        return dict(self.inventory(token, campaign), complete=self.inventory_complete)

    def create_v2(self, token, edge, params, campaign):
        validate_creation_v2(edge, params, campaign)
        result = self.create(token, edge, params)
        if edge == 'ads':
            self.nodes[result['id']]['campaign_id'] = campaign
        if edge == 'adcreatives' and self.corrupt_copy:
            self.nodes[result['id']]['asset_feed_spec']['bodies'][0]['text'] = 'Wrong copy'
        return result

    def upload_video(self, token, raw, name):
        self._write('video', {'name': name})
        self.sequence += 1
        return {'id': str(self.sequence)}

    def video_info(self, token, identity):
        return {'id': identity, 'account_id': ACCOUNT, 'status': {'video_status': self.video_status}}


class V2ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        (self.root/'drive-assets').mkdir()
        self.provider = V2Provider()
        self.now = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
        self.proposal = make_valid_proposal_v2()
        self.proposal['creation_recipe']={'adset_fields':{},'creative_fields':{'degrees_of_freedom_spec':{'creative_features_spec':{'standard_enhancements':{'enroll_status':'OPT_OUT'},'inline_comment':{'enroll_status':'OPT_IN'}}}},'optimization_type':'DEGREES_OF_FREEDOM'}
        self.proposal['strategy']['campaign_id'] = self.provider.campaign
        # A complete five-ad new group, mixed image/video, no forced story pair.
        self.proposal['adsets'] = self.proposal['adsets'][:1]
        self.proposal['ads'][1]['adset_key'] = 'group0'
        for index in range(2, 5):
            ad = deepcopy(self.proposal['ads'][0])
            ad['asset_id'] = 'fixture_asset_' + str(index)
            self.proposal['ads'].append(ad)
        self.book = {'status': 'available', 'rules': {key: {'params': {}} for key in
                     ('full_copy_set', 'ads_per_adset', 'new_creatives_new_dated_adset', 'cta_shop_now', 'no_carousel', 'offer_in_headline')}}
        self.book['rules']['ads_per_adset']['params'] = {'min': 5, 'max': 8}
        self.book['rules']['full_copy_set']['params'] = {'texts': 5, 'headlines': 5}
        self.book['rules']['cta_shop_now']['params'] = {'api_value': 'SHOP_NOW'}
        self.proposal['rules']['citations'] = list(self.book['rules'])
        self.bind_book()
        raw_image = png()
        for ad in self.proposal['ads']:
            raw = b'\x00\x00\x00\x18ftypmp42synthetic' if ad['media_kind'] == 'video' else raw_image
            ad['sha256'] = self.cache(ad['asset_id'], raw)
            if ad['video']:
                ad['video']['thumbnail_sha256'] = self.cache(ad['video']['thumbnail_asset_id'], raw_image)
        self.rule_patch = patch('rulebook.load_rulebook', side_effect=lambda: deepcopy(self.book))
        self.rule_patch.start()
        self.addCleanup(self.rule_patch.stop)
        self.addCleanup(self.temp.cleanup)
        self.service = self.new_service()

    def cache(self, asset, raw):
        sha = hashlib.sha256(raw).hexdigest()
        (self.root/'drive-assets'/(asset+'-'+sha+'.bin')).write_bytes(raw)
        return sha

    def bind_book(self):
        self.proposal['rules']['rulebook_sha256'] = digest(self.book)

    def binding(self):
        return {'request_id': self.proposal['request_id'], 'request_revision': self.proposal['request_revision'], 'proposal_hash': digest(self.proposal)}

    def new_service(self):
        return CampaignOperations(self.root, b'k'*32, lambda: {'available': True, 'proposal': deepcopy(self.proposal), 'source_binding': self.binding()},
                                  credential_loader=lambda: 'synthetic', transport=self.provider, clock=lambda: self.now)

    def preview(self, **options):
        return self.service.preview({'action': 'prepare_launch', 'expected_source': self.binding(), **options})['operation']

    def execute(self, operation):
        self.service.approve(operation['operation_id'], operation['digest'])
        return self.service.execute(operation['operation_id'])['operation']

    def test_copy_men_targeting_from_the_live_adset_and_paused_readback(self):
        men = {'genders': [1], 'age_min': 18, 'age_max': 65, 'geo_locations': {'countries': ['IL'], 'location_types': ['home']},
               'excluded_custom_audiences': [{'id': '120252833157650730', 'name': 'ROSERY | רוכשים 30 יום'}],
               'targeting_automation': {'advantage_audience': 1}}
        self.provider.nodes['555555555'] = {'id': '555555555', 'account_id': ACCOUNT, 'campaign_id': self.provider.campaign,
                                            'name': 'וידאו | גברים | 05/10', 'status': 'ACTIVE', 'effective_status': 'ACTIVE', 'targeting': men}
        self.proposal['adsets'][0]['targeting_mode'] = 'copy_men'
        operation = self.preview()
        self.assertEqual(operation['blocked_reasons'], [])
        group = operation['manifest']['adsets'][0]
        self.assertEqual(group['params']['targeting']['genders'], [1])
        self.assertEqual(group['params']['targeting']['excluded_custom_audiences'], [{'id': '120252833157650730'}])
        self.assertEqual(group['targeting_source']['excluded_audiences'][0]['name'], 'ROSERY | רוכשים 30 יום')
        self.assertEqual(self.execute(operation)['state'], 'verified')
        created = [n for n in self.provider.nodes.values() if n.get('name', '').startswith(self.proposal['adsets'][0]['label'])]
        self.assertEqual(created[0]['status'], 'PAUSED')

    def test_copy_women_without_a_live_women_adset_is_blocked(self):
        self.proposal['adsets'][0]['targeting_mode'] = 'copy_women'
        self.assertIn('No active ad set of that gender', ' '.join(self.preview()['blocked_reasons']))

    def test_mixed_media_exact_manifest_paused_and_readback(self):
        operation = self.preview()
        self.assertEqual(operation['blocked_reasons'], [])
        self.assertEqual(operation['digest'], digest(operation['manifest']))
        with self.assertRaises(OperationBlocked):
            self.service.approve(operation['operation_id'], 'a'*64)
        with self.assertRaises(OperationBlocked):
            self.service.execute(operation['operation_id'])
        result = self.execute(operation)
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(sum(edge == 'ads' for edge, p in self.provider.writes), 5)
        self.assertEqual(sum(edge == 'video' for edge, p in self.provider.writes), 1)

        for edge, params in self.provider.writes:
            if edge in ('adsets', 'ads'):
                self.assertEqual(params['status'], 'PAUSED')
            if edge == 'adsets':
                self.assertEqual(params['campaign_id'], self.provider.campaign)
            if edge == 'adcreatives':
                self.assertEqual(len(params['asset_feed_spec']['bodies']), 5)
                self.assertEqual(len(params['asset_feed_spec']['titles']), 5)
                self.assertNotIn('asset_customization_rules', params['asset_feed_spec'])
        again = self.preview()
        self.assertEqual(again['operation_id'], operation['operation_id'])
        self.service.execute(operation['operation_id'])
        self.assertEqual(sum(edge == 'video' for edge, p in self.provider.writes), 1)

    def test_status_exposes_verified_campaign_choices(self):
        rows=self.service.status()['entities']
        self.assertTrue(any(row['entity_id']==self.provider.campaign and row['observed_at'] for row in rows))

    def test_pending_video_blocks_reupload_across_proposal_revisions(self):
        self.provider.video_status='processing'
        ticks=iter(range(10000));self.service.monotonic=lambda:next(ticks)
        operation=self.preview()
        with patch('campaign_operations.time.sleep'):
            self.execute(operation)
        count=sum(edge=='video' for edge, p in self.provider.writes)
        self.proposal['request_revision']+=1
        next_operation=self.preview()
        self.assertNotEqual(next_operation['operation_id'],operation['operation_id'])
        self.execute(next_operation)
        self.assertEqual(sum(edge=='video' for edge, p in self.provider.writes),count)

    def test_host_envelope_fields_remain_closed(self):
        self.proposal.update(activation_available=False,state='draft_for_review',prepared_by='agent_review')
        for ad in self.proposal['ads']:
            ad['media_url']='/api/launch/proposal/media/'+ad['asset_id']
        self.assertEqual(validate_proposal_v2(self.proposal),self.proposal)
        self.proposal['activation_available']=True
        with self.assertRaises(ValueError):validate_proposal_v2(self.proposal)

    def test_labelled_executor_refusals(self):
        cases = {
            'stale_source_digest': lambda: self.book.update(changed=True),
            'expired_proposal': lambda: setattr(self, 'now', self.now + timedelta(minutes=16)),
            'future_proposal': lambda: setattr(self, 'now', self.now - timedelta(minutes=1)),
            'uncited_required_rule': lambda: self.proposal['rules']['citations'].remove('full_copy_set'),
            'campaign_outside_account': lambda: setattr(self.provider, 'omit_campaign', True),
            'incomplete_inventory': lambda: setattr(self.provider, 'inventory_complete', False),
            'blocking_question': lambda: self.proposal['owner_questions'].append('Choose campaign'),
            'blocker': lambda: self.proposal['blockers'].append('Unknown source'),
            'missing_source': lambda: self.proposal['ads'][0].update(sha256='e'*64),
            'count_rule': lambda: self.proposal['ads'].pop(),
            'missing_strategy': lambda: self.proposal.pop('strategy'),
            'invalid_strategy': lambda: self.proposal.update(strategy=None),
        }
        for label, mutate in cases.items():
            original = deepcopy(self.proposal)
            book = deepcopy(self.book)
            now = self.now
            with self.subTest(refusal=label):
                mutate()
                result = self.preview()
                self.assertTrue(result['blocked_reasons'])
                self.assertEqual(result['manifest']['steps'], [])
                self.assertEqual(self.provider.writes, [])
            self.proposal = original
            self.book = book
            self.now = now
            self.provider.omit_campaign = False
            self.provider.inventory_complete = True

    def test_existing_reuse_rule_and_live_predicate(self):
        group = self.proposal['adsets'][0]
        group['target'] = {'mode': 'existing', 'adset_id': '123456789', 'rule_cited': 'existing_adset_reuse'}
        group['budget_minor'] = None
        self.proposal['rules']['citations'].append('existing_adset_reuse')
        self.assertTrue(self.preview()['blocked_reasons'])
        self.rule_patch.stop()
        from rulebook import load_rulebook
        self.book=load_rulebook()
        self.assertEqual(self.book['rules']['existing_adset_reuse']['params']['max_active_ads'],5)
        self.bind_book()
        live = {'id': '123456789', 'account_id': ACCOUNT, 'campaign_id': self.provider.campaign, 'status': 'PAUSED', 'effective_status': 'PAUSED', 'name':'ring', 'product': 'ring', 'angle': 'style'}
        self.provider.nodes[live['id']] = live
        live['name'] = ''
        self.assertTrue(self.preview()['blocked_reasons'])
        live['name'] = 'ring'
        from account_history import CATEGORIES
        group['product']=next(label for label,words in CATEGORIES if 'ring' in words)
        for index in range(5):
            identity=str(800000+index)
            self.provider.nodes[identity]={'id':identity,'account_id':ACCOUNT,'campaign_id':self.provider.campaign,'adset_id':live['id'],'status':'ACTIVE','effective_status':'ACTIVE'}
        confirmation=[{'adset_id':live['id'],'same_angle':True}]
        self.assertIn('active count condition failed',self.preview(owner_confirmations=confirmation)['blocked_reasons'][0])
        for index in range(5):self.provider.nodes.pop(str(800000+index))
        operation = self.service.preview({'action':'prepare_launch','expected_source':self.binding(),'owner_confirmations':[{'adset_id':live['id'],'same_angle':True}]})['operation']
        self.assertEqual(operation['blocked_reasons'], [])
        self.assertEqual(operation['manifest']['owner_confirmations'],confirmation)
        result = self.execute(operation)
        self.assertEqual(result['state'], 'verified')
        self.assertFalse(any(edge in ('adsets', 'update') for edge, p in self.provider.writes))

    def test_preflight_drift_no_writes(self):
        operation = self.preview()
        self.service.approve(operation['operation_id'], operation['digest'])
        self.provider.nodes[self.provider.campaign]['status'] = 'ACTIVE'
        result = self.service.execute(operation['operation_id'])['operation']
        self.assertEqual(result['state'], 'blocked')
        self.assertEqual(self.provider.writes, [])

    def test_changed_copy_requires_new_approval(self):
        operation = self.preview()
        self.service.approve(operation['operation_id'], operation['digest'])
        self.proposal['ads'][0]['headline_options'][0] = 'Changed'
        with self.assertRaises(OperationBlocked):
            self.service.execute(operation['operation_id'])
        self.assertEqual(self.provider.writes, [])

    def test_repository_rulebook_supports_new_adset(self):
        # Source-bound rule loader only; no business profile, credentials or API.
        self.rule_patch.stop()
        from rulebook import load_rulebook
        self.book=load_rulebook()
        self.assertEqual(self.book['status'],'available')
        self.proposal['rules']['citations']=['full_copy_set','ads_per_adset','new_creatives_new_dated_adset','cta_shop_now','no_carousel','offer_in_headline']
        self.bind_book()
        self.assertEqual(self.execute(self.preview())['state'],'verified')

    def test_v2_stale_display_binding_refused_locally(self):
        displayed=self.binding()
        self.proposal['request_revision']+=1
        with patch.object(self.service,'credentials',side_effect=AssertionError('Must refuse before credentials')):
            result=self.service.preview({'action':'prepare_launch','expected_source':displayed})['operation']
        self.assertTrue(result['blocked_reasons'])
        self.assertEqual(self.provider.writes,[])

    def test_video_failure_timeout_no_retry_even_after_restart(self):
        self.provider.video_status = 'error'
        operation = self.preview()
        result = self.execute(operation)
        self.assertEqual(result['state'], 'unknown')
        count = sum(edge == 'video' for edge, p in self.provider.writes)
        self.service = self.new_service()
        self.service.execute(operation['operation_id'])
        self.assertEqual(sum(edge == 'video' for edge, p in self.provider.writes), count)
        self.assertEqual(self.preview()['operation_id'], operation['operation_id'])

    def test_video_timeout(self):
        self.provider.video_status = 'processing'
        ticks=iter(range(10000));self.service.monotonic=lambda:next(ticks)
        operation = self.preview()
        with patch('campaign_operations.time.sleep'):
            result = self.execute(operation)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(sum(edge == 'video' for edge, p in self.provider.writes), 1)
        self.assertFalse(any(edge == 'adcreatives' and p['asset_feed_spec']['ad_formats'] == ['SINGLE_VIDEO'] for edge, p in self.provider.writes))
        self.service=self.new_service()
        self.service.execute(operation['operation_id'])
        self.assertEqual(sum(edge == 'video' for edge, p in self.provider.writes),1)

    def test_readback_copy_mismatch_stops(self):
        self.provider.corrupt_copy = True
        result = self.execute(self.preview())
        self.assertEqual(result['state'], 'unknown')
        self.assertFalse(any(edge == 'ads' for edge, p in self.provider.writes))

    def test_ambiguous_upload_never_retried(self):
        self.provider.fail_at = 2
        operation = self.preview()
        result = self.execute(operation)
        self.assertEqual(result['state'], 'unknown')
        count = len(self.provider.writes)
        self.service = self.new_service()
        self.service.execute(operation['operation_id'])
        self.assertEqual(len(self.provider.writes), count)

    def test_v2_selectors_cannot_narrow_copy_or_budget(self):
        for options in ({'copy_variant_indices': {}}, {'creation_budget_minor': 100}, {'format_mode': 'feed_original'}, {'selected_asset_ids': ['missing']}, {'selected_adset_keys': ['missing']}):
            with self.subTest(options=options):
                self.assertTrue(self.preview(**options)['blocked_reasons'])
        self.assertEqual(self.provider.writes, [])

    def test_reconcile_saved_video_reads_only(self):
        self.provider.video_status = 'processing'
        ticks=iter(range(10000));self.service.monotonic=lambda:next(ticks)
        operation = self.preview()
        with patch('campaign_operations.time.sleep'):
            self.execute(operation)
        count = len(self.provider.writes)
        self.provider.video_status = 'ready'
        result = self.service.reconcile(operation['operation_id'], operation['digest'])
        self.assertEqual(len(self.provider.writes), count)
        self.assertEqual(result['operation']['state'], 'partial')
        self.assertTrue(any(s['kind'] == 'video' and s['state'] == 'verified' for s in result['job']['steps']))


class V2TransportTests(unittest.TestCase):
    def test_video_account_edge_readback(self):
        transport=CampaignTransport()
        with patch.object(transport,'_request',return_value={'id':'123456','status':{'video_status':'ready'}}) as request:
            self.assertEqual(transport.video_info('synthetic','123456')['account_id'],ACCOUNT)
            self.assertEqual(request.call_args.args[1],'123456')
        with patch.object(transport,'_request',return_value={}):
            self.assertTrue(transport.video_info('synthetic','123456')['pending'])

    def test_v2_does_not_use_legacy_batch(self):
        transport=CampaignTransport()
        params={'name':'Test','status':'PAUSED','adset_id':'123456','creative':{'creative_id':'234567'}}
        with patch.object(transport,'_request',return_value={'id':'345678'}) as request:
            transport.create_v2('synthetic','ads',params,'456789')
            self.assertEqual(request.call_args.args[1],'act_'+ACCOUNT+'/ads')
        for changed in (dict(params,status='ACTIVE'),dict(params,extra=True),dict(params,adset_id='not-resolved')):
            with self.assertRaises(BatchRejected):validate_creation_v2('ads',changed,'456789')
        with self.assertRaises(BatchRejected):
            plan_batches([{'id':'set','method':'POST','relative_url':'act_'+ACCOUNT+'/adsets','params':{'name':'Test','status':'PAUSED','campaign_id':'456789'},'depends_on':[]}])

    def test_video_dispatch_and_bounded_upload(self):
        transport = CampaignTransport()
        with patch.object(transport, '_request', return_value={'id': '123456'}) as request:
            transport.upload_video('synthetic', b'\x00\x00\x00\x18ftypmp42', 'test.mp4')
            self.assertEqual(request.call_args.args[1], 'act_'+ACCOUNT+'/advideos')
            self.assertIn(b'name="source"', request.call_args.kwargs['data'])
        for raw, name in ((b'bad', 'test.mp4'), (b'\x00\x00\x00\x18ftypmp42', '../bad')):
            with self.assertRaises(ValueError):
                transport.upload_video('synthetic', raw, name)


if __name__ == '__main__':
    unittest.main()
