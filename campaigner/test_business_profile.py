"""Synthetic local stores and injected providers only."""
from pathlib import Path
import tempfile
import unittest

from business_profile import BusinessProfile, creation_recipe, feature_keys
from creative_drift import CreativeDrift
from owner_actions import OwnerActions, ActionBlocked
import test_creative_drift as drift_fixture
import test_owner_actions as action_fixture
import test_copy_variants as copy_fixture
import test_server as http_fixture
import copy_variants
from rulebook import load_rulebook


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = BusinessProfile(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_and_per_business_isolation(self):
        self.assertTrue(all(self.profile.value(k) == ('allowed' if k == 'inline_comment' else 'off') for k in feature_keys()))
        features = creation_recipe(self.profile)['creative_fields']['degrees_of_freedom_spec']['creative_features_spec']
        self.assertEqual(set(features), set(feature_keys()))
        self.assertTrue(all(v == {'enroll_status': 'OPT_IN' if k == 'inline_comment' else 'OPT_OUT'} for k, v in features.items()))
        self.assertFalse(self.profile.path.exists())
        self.profile.update({'settings': {'inline_comment': 'off'}})
        self.assertEqual(BusinessProfile(Path(self.temp.name)/'other').value('inline_comment'), 'allowed')

    def test_history_binding_and_atomic_validation(self):
        before = self.profile.binding()
        result = self.profile.update({'settings': {'image_brightness_and_contrast': 'allowed'}, 'source': 'owner approved a system suggestion'})
        self.assertNotEqual(before, result['binding'])
        event = result['history'][0]
        self.assertEqual(event['old']['image_brightness_and_contrast'], 'off')
        self.assertEqual(event['new']['image_brightness_and_contrast'], 'allowed')
        self.assertEqual(event['source'], 'owner approved a system suggestion')
        self.assertIn('לפי ההגדרות שלך מ-', result['allowed_reasons'][0]['reason'])
        stored = self.profile.path.read_bytes()
        for body in ({'settings': {'image_brightness_and_contrast': True}}, {'settings': {'made_up': 'off'}}, {'settings': {'max_daily_budget_minor': True}}, {'settings': {'max_increase_percent': float('nan')}}, {'settings': {'inline_comment': 'allowed', 'banned_copy_words': [1]}}, {'settings': {}, 'extra': True}):
            with self.assertRaises(ValueError):
                self.profile.update(body)
            self.assertEqual(self.profile.path.read_bytes(), stored)

    def test_allow_brightness_removes_drift_and_opt_out_and_action_preserves_it(self):
        drift = CreativeDrift(Path(self.temp.name)/'drift.sqlite3')
        snapshot = drift_fixture.data(drift_fixture.ad('1', drift_fixture.spec(image_brightness_and_contrast='OPT_IN')))
        self.assertEqual(len(drift.check(snapshot, load_rulebook())['items']), 1)
        self.profile.update({'settings': {'image_brightness_and_contrast': 'allowed'}})
        self.assertEqual(drift.check(snapshot, load_rulebook())['items'], [])
        features = creation_recipe(self.profile)['creative_fields']['degrees_of_freedom_spec']['creative_features_spec']
        self.assertNotIn('image_brightness_and_contrast', features)
        meta = action_fixture.FakeMeta()
        meta.creatives[action_fixture.CREATIVE]['degrees_of_freedom_spec']['creative_features_spec']['image_brightness_and_contrast'] = {'enroll_status': 'OPT_IN'}
        actions = OwnerActions(self.temp.name, report=action_fixture.report, transport=meta, credentials=lambda: 'synthetic')
        view = actions.preview('drift:'+action_fixture.AD)
        self.assertNotIn('בהירות וניגודיות', [v['field'] for v in view['changes']])
        actions.confirm(view['confirm_token'])
        new = meta.creatives[meta.ads[action_fixture.AD]['creative']]
        self.assertEqual(new['degrees_of_freedom_spec']['creative_features_spec']['image_brightness_and_contrast'], {'enroll_status': 'OPT_IN'})

    def test_profile_change_invalidates_action_preview(self):
        meta = action_fixture.FakeMeta()
        actions = OwnerActions(self.temp.name, report=action_fixture.report, transport=meta, credentials=lambda: 'synthetic')
        view = actions.preview('drift:'+action_fixture.AD)
        self.profile.update({'settings': {'image_animation': 'allowed'}})
        with self.assertRaises(ActionBlocked):
            actions.confirm(view['confirm_token'])
        self.assertEqual(meta.writes, [])

    def test_learned_rules_scope_history_and_removal(self):
        result = self.profile.dont({'kind': 'disable_enhancements', 'scope': 'adset', 'entity_id': '12', 'reason': 'העדפה שלי'})
        report = {'creative_drift': {'items': [{'ad_id': '1', 'adset_id': '12', 'features': []}, {'ad_id': '2', 'adset_id': '13', 'features': []}]}}
        self.profile.annotate(report)
        self.assertTrue(report['creative_drift']['items'][0]['dismissed'])
        self.assertEqual(report['dismissed_items'][0]['hidden_reason'], 'מוסתר לפי ההגדרה שלך')
        self.assertNotIn('dismissed', report['creative_drift']['items'][1])
        self.profile.dont({'remove_id': result['donts'][0]['id']})
        self.assertEqual(self.profile.get()['donts'], [])
        self.assertEqual(len(self.profile.get()['history']), 2)

    def test_copy_gate_and_budget_limits(self):
        self.profile.update({'settings': {'banned_copy_words': ['אסור'], 'max_daily_budget_minor': 16000, 'max_increase_percent': 5}})
        with self.assertRaises(ValueError):
            copy_variants._check(copy_fixture.answer(texts=['אסור']+[str(i) for i in range(4)]), load_rulebook(), copy_fixture.ROW, None, self.profile)
        with self.assertRaises(ValueError):
            self.profile.check_budget(16000, 15000)
        with self.assertRaises(ValueError):
            self.profile.check_budget(17000)
        self.profile.check_budget(15500, 15000)
        with self.assertRaises(ValueError):
            self.profile.check_budget(None)
        report = action_fixture.report()
        self.profile.annotate(report)
        self.assertFalse(report['recommendations']['rule_engine']['proposals'][1]['ready_for_approval'])

    def test_guidance_unknown_and_honest_unwired_settings(self):
        from business_profile import guidance
        self.assertEqual(set(guidance()), set(feature_keys()))
        self.assertTrue(all(v['verdict'] in ('safe', 'harmful', 'depends', 'unknown') for v in guidance().values()))
        self.assertTrue(all(v['sources'] and all(u.startswith('https://') for u in v['sources']) for v in guidance().values() if v['verdict'] != 'unknown'))
        settings = {v['key']: v for section in self.profile.get()['sections'] for v in section['settings']}
        for key in ('excluded_products', 'tone_notes', 'no_change_dates'):
            self.assertIn('עוד לא בשימוש', settings[key]['what_it_changes'])


class ProfileHTTPTests(unittest.TestCase):
    setUp = http_fixture.ServerTests.setUp
    tearDown = http_fixture.ServerTests.tearDown
    request = http_fixture.ServerTests.request

    def test_authenticated_routes_and_invalid_posts(self):
        self.assertEqual(self.request('/api/business-profile', token=False)[0], 403)
        self.assertEqual(self.request('/api/business-profile')[0], 200)
        body = {'settings': {'image_brightness_and_contrast': 'allowed'}}
        self.assertEqual(self.request('/api/business-profile', body, token=False)[0], 403)
        self.assertEqual(self.request('/api/business-profile', body, origin=False)[0], 403)
        self.assertEqual(self.request('/api/business-profile', {'settings': {'unknown': 'allowed'}})[0], 400)
        code, result = self.request('/api/business-profile', body)
        self.assertEqual(code, 200)
        self.assertEqual(result['provider_writes'], 0)
        self.assertEqual(self.request('/api/business-profile')[1]['version'], 1)
        self.assertEqual(self.request('/api/business-profile/dont', {'kind': 'pause_ad', 'scope': 'everywhere', 'entity_id': '', 'reason': 'העדפה'})[0], 200)
        self.assertEqual(self.request('/api/business-profile/dont', {'kind': 'x', 'scope': 'bad', 'entity_id': '', 'reason': 'x'})[0], 400)
        from phone_gateway import GET_ROUTES, POST_ROUTES
        self.assertIn('/api/business-profile', GET_ROUTES)
        self.assertTrue({'/api/business-profile', '/api/business-profile/dont'} <= POST_ROUTES)


class ProfileLaunchBindingTests(unittest.TestCase):
    # Reuse only synthetic fixture methods, without inheriting its unrelated tests.
    import test_launch_proposal_schema as fixtures
    setUp = fixtures.V2ExecutionTests.setUp
    cache = fixtures.V2ExecutionTests.cache
    bind_book = fixtures.V2ExecutionTests.bind_book
    binding = fixtures.V2ExecutionTests.binding
    new_service = fixtures.V2ExecutionTests.new_service
    preview = fixtures.V2ExecutionTests.preview

    def test_allowed_feature_in_manifest_and_changed_profile_invalidates_approval(self):
        from campaign_operations import OperationBlocked
        profile = BusinessProfile(self.root)
        profile.update({'settings': {'image_brightness_and_contrast': 'allowed'}})
        self.proposal['business_profile'] = profile.binding()
        self.proposal['creation_recipe'] = creation_recipe(profile)
        operation = self.preview()
        self.assertEqual(operation['blocked_reasons'], [])
        spec = operation['manifest']['creation_recipe']['creative_fields']['degrees_of_freedom_spec']['creative_features_spec']
        self.assertNotIn('image_brightness_and_contrast', spec)
        self.assertEqual(spec['inline_comment'], {'enroll_status': 'OPT_IN'})
        profile.update({'settings': {'image_animation': 'allowed'}})
        with self.assertRaises(OperationBlocked):
            self.service.approve(operation['operation_id'], operation['digest'])
        self.assertEqual(self.provider.writes, [])

    def test_unbound_old_proposal_and_tampered_recipe_blocked(self):
        profile = BusinessProfile(self.root)
        profile.update({'settings': {'image_brightness_and_contrast': 'allowed'}})
        self.assertIn('Business profile changed', str(self.preview()['blocked_reasons']))
        self.proposal['business_profile'] = profile.binding()
        self.assertIn('Creation recipe differs', str(self.preview()['blocked_reasons']))

    def test_profile_budget_and_copy_limits_block_launch(self):
        profile = BusinessProfile(self.root)
        profile.update({'settings': {'max_daily_budget_minor': 1}})
        self.proposal['business_profile'] = profile.binding()
        self.assertTrue(self.preview()['blocked_reasons'])
        profile.update({'settings': {'max_daily_budget_minor': None, 'banned_copy_words': ['Text']}})
        self.proposal['business_profile'] = profile.binding()
        self.assertTrue(self.preview()['blocked_reasons'])
