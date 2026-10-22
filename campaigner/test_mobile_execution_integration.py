"""Controlled HTTP integration. All provider calls use FakeProvider; no Meta I/O.

The mobile origin and execution flag exist only inside a temporary directory.
Production .local configuration, credentials and campaigns are never opened.
"""
import http.client
import json
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import mobile_web
import server
from campaign_operations import ACCOUNT, PARENT
import test_campaign_operations as operation_fixtures


class MobileExecutionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = operation_fixtures.CampaignOperationsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.service = self.fixture.service
        self.provider = self.fixture.provider
        self.config = self.fixture.root / 'mobile-origin.json'
        self.set_enabled(True)
        # The real manager routes and real operation engine are used. Only the
        # engine's provider/clock/source fixture is synthetic.
        with patch('server.CampaignOperations', return_value=self.service):
            self.manager = server.make_server(0, self.fixture.root, workspace='manager')
        self.manager_thread = threading.Thread(target=self.manager.serve_forever, daemon=True)
        self.manager_thread.start()
        self.addCleanup(self.close_manager)
        self.access = mobile_web.MobileAccess('i' * 43, time.time() + 120)
        self.backend = self.make_backend()
        self.mobile = mobile_web.make_server(self.fixture.root, 0, self.access, self.backend)
        self.mobile_thread = threading.Thread(target=self.mobile.serve_forever, daemon=True)
        self.mobile_thread.start()
        self.addCleanup(self.close_mobile)
        code, value, headers = self.request('/auth', {'key': 'i' * 43}, cookie=False)
        self.assertEqual(code, 200)
        self.assertTrue(value['signed_in'])
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]

    def make_backend(self):
        # Optional loopback port injection keeps the exact production relay code
        # testable without binding a test to the production manager's port.
        return mobile_web.Backend(backend_port=self.manager.server_port)

    def set_enabled(self, enabled):
        self.config.write_text(json.dumps({'origin': 'https://integration.trycloudflare.com',
                                           'remote_execution_enabled': enabled}))

    def close_manager(self):
        self.service.stop()
        self.manager.shutdown()
        self.manager.server_close()
        self.manager_thread.join(timeout=3)

    def close_mobile(self):
        self.mobile.shutdown()
        self.mobile.server_close()
        self.mobile_thread.join(timeout=3)

    def restart_manager(self):
        port = self.manager.server_port
        self.close_manager()
        with patch('server.CampaignOperations', return_value=self.service):
            self.manager = server.make_server(port, self.fixture.root, workspace='manager')
        self.manager_thread = threading.Thread(target=self.manager.serve_forever, daemon=True)
        self.manager_thread.start()

    def request(self, path, body=None, cookie=True, origin='https://integration.trycloudflare.com'):
        connection = http.client.HTTPConnection('127.0.0.1', self.mobile.server_port, timeout=10)
        headers = {'Host': 'integration.trycloudflare.com', 'X-Forwarded-Proto': 'https',
                   'Origin': origin, 'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = self.cookie
        connection.request('POST' if body is not None else 'GET', path,
                           json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        code, value, returned_headers = response.status, json.loads(response.read()), dict(response.getheaders())
        connection.close()
        return code, value, returned_headers

    def post(self, suffix, body):
        if suffix=='preview' and body.get('action')=='prepare_launch':body={'expected_source':self.fixture.binding(),**body}
        code, value, _ = self.request('/api/campaign-operations/' + suffix, body)
        return code, value

    def preview(self):
        code, result = self.post('preview', {'action': 'prepare_launch'})
        self.assertEqual(code, 200)
        self.assertEqual(result['operation']['state'], 'prepared')
        self.assertEqual(self.provider.writes, [])
        return result['operation']

    def wait_terminal(self, identity):
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            code, state, _ = self.request('/api/campaign-operations')
            self.assertEqual(code, 200)
            job = next(x for x in state['jobs'] if x['id'] == identity)
            if job['state'] not in ('queued', 'running'):
                return job
            threading.Event().wait(0.05)
        self.fail('Real HTTP worker did not reach a terminal state')

    def test_phone_exact_approval_creates_verified_paused_ads_and_replay_is_idempotent(self):
        proposal = self.preview()
        identity = proposal['operation_id']
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 400)
        self.assertEqual(self.provider.writes, [])
        self.assertEqual(self.post('approve', {'operation_id': identity,
                                              'digest': proposal['digest']})[0], 200)
        self.assertEqual(self.provider.writes, [])
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 200)
        job = self.wait_terminal(identity)
        self.assertEqual(job['state'], 'verified')
        self.assertEqual([kind for kind, _ in self.provider.writes], ['adsets', 'image', 'adcreatives', 'ads'])
        self.assertTrue(all(x['state'] == 'verified' for x in job['steps']))
        self.assertEqual(self.provider.nodes[PARENT]['status'], 'PAUSED')
        for kind, params in self.provider.writes:
            if kind in ('adsets', 'ads'):
                self.assertEqual(params['status'], 'PAUSED')
        before = len(self.provider.writes)
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 200)
        self.assertEqual(len(self.provider.writes), before)
        self.assertEqual(self.post('approve', {'operation_id': identity, 'digest': proposal['digest']})[0], 400)

    def test_wrong_digest_and_expired_approval_do_not_write(self):
        proposal = self.preview()
        self.assertEqual(self.post('approve', {'operation_id': proposal['operation_id'],
                                              'digest': '0' * 64})[0], 400)
        self.fixture.now += timedelta(minutes=16)
        self.assertEqual(self.post('approve', {'operation_id': proposal['operation_id'],
                                              'digest': proposal['digest']})[0], 400)
        self.assertEqual(self.provider.writes, [])

    def test_cross_account_parent_and_unapproved_flag_are_rejected_over_http(self):
        self.provider.nodes['999999'] = {'id': '999999', 'account_id': 'different',
                                         'campaign_id': PARENT, 'status': 'PAUSED'}
        code, result = self.post('preview', {'action': 'activate', 'entity_type': 'ad', 'entity_id': '999999'})
        self.assertEqual(code, 200)
        self.assertEqual(result['operation']['state'], 'blocked')
        self.assertTrue(result['operation']['blocked_reasons'])
        self.assertEqual(self.post('approve', {'operation_id': result['operation']['operation_id'],
                                              'digest': result['operation']['digest']})[0], 400)
        self.set_enabled(False)
        self.assertFalse(self.request('/api/remote-capabilities')[1]['can_manage_campaigns'])
        self.assertEqual(self.post('preview', {'action': 'prepare_launch'})[0], 403)
        self.assertEqual(self.provider.writes, [])

    def test_invalid_origin_session_and_extra_authority_never_reach_provider(self):
        self.assertEqual(self.request('/api/campaign-operations/preview', {'action': 'prepare_launch'},
                                      origin='https://evil.invalid')[0], 403)
        self.assertEqual(self.request('/api/campaign-operations/preview', {'action': 'prepare_launch'},
                                      cookie=False)[0], 401)
        self.assertEqual(self.post('preview', {'action': 'prepare_launch', 'account_id': ACCOUNT})[0], 400)
        self.assertEqual(self.provider.writes, [])

    def test_approved_source_change_blocks_worker_before_any_upload(self):
        proposal = self.preview()
        identity = proposal['operation_id']
        self.assertEqual(self.post('approve', {'operation_id': identity, 'digest': proposal['digest']})[0], 200)
        self.fixture.file.write_bytes(b'changed after approval')
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 200)
        self.assertEqual(self.wait_terminal(identity)['state'], 'blocked')
        self.assertEqual(self.provider.writes, [])

    def test_unknown_provider_outcome_is_visible_and_never_retried(self):
        proposal = self.preview()
        identity = proposal['operation_id']
        self.provider.fail_at = 1
        self.assertEqual(self.post('approve', {'operation_id': identity, 'digest': proposal['digest']})[0], 200)
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 200)
        job = self.wait_terminal(identity)
        self.assertEqual(job['state'], 'unknown')
        self.assertEqual(len(self.provider.writes), 1)
        self.assertEqual(self.post('execute', {'operation_id': identity})[1]['job']['state'], 'unknown')
        self.assertEqual(len(self.provider.writes), 1)

    def test_schedule_requires_exact_approved_time_and_runs_once(self):
        at = (self.fixture.now + timedelta(hours=1)).isoformat()
        code, result = self.post('preview', {'action': 'prepare_launch', 'scheduled_at': at})
        self.assertEqual(code, 200)
        proposal = result['operation']
        identity = proposal['operation_id']
        self.assertEqual(self.post('approve', {'operation_id': identity, 'digest': proposal['digest']})[0], 200)
        wrong = (self.fixture.now + timedelta(hours=2)).isoformat()
        self.assertEqual(self.post('schedule', {'operation_id': identity, 'at': wrong})[0], 400)
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 400)
        self.assertEqual(self.post('schedule', {'operation_id': identity, 'at': at})[0], 200)
        self.assertEqual(self.provider.writes, [])
        self.fixture.now += timedelta(hours=1)
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            state = self.request('/api/campaign-operations')[1]
            job = next(x for x in state['jobs'] if x['id'] == identity)
            if job['state'] == 'verified':
                break
            threading.Event().wait(0.05)
        else:
            self.fail('Scheduled worker did not complete through the HTTP route')
        self.assertEqual(len(self.provider.writes), 4)
        self.assertEqual(self.post('schedule', {'operation_id': identity, 'at': at})[0], 400)
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 200)
        self.assertEqual(len(self.provider.writes), 4)

    def test_parent_activation_inherited_budget_drift_blocks_without_changes(self):
        self.provider.nodes['888888'] = {'id': '888888', 'account_id': ACCOUNT, 'campaign_id': PARENT,
                                         'name': 'Existing active set', 'status': 'ACTIVE',
                                         'effective_status': 'CAMPAIGN_PAUSED',
                                         'daily_budget': '12000', 'lifetime_budget': '0'}
        code, result = self.post('preview', {'action': 'activate', 'entity_type': 'campaign', 'entity_id': PARENT})
        self.assertEqual(code, 200)
        proposal = result['operation']
        self.assertEqual(proposal['before']['descendants']['adsets'][0]['daily_budget'], '12000')
        identity = proposal['operation_id']
        self.assertEqual(self.post('approve', {'operation_id': identity, 'digest': proposal['digest']})[0], 200)
        self.provider.nodes['888888']['daily_budget'] = '24000'
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 200)
        self.assertEqual(self.wait_terminal(identity)['state'], 'blocked')
        self.assertEqual(self.provider.nodes[PARENT]['status'], 'PAUSED')
        self.assertEqual(self.provider.writes, [])

    def test_remote_permission_revocation_blocks_already_approved_work(self):
        proposal = self.preview()
        identity = proposal['operation_id']
        self.assertEqual(self.post('approve', {'operation_id': identity, 'digest': proposal['digest']})[0], 200)
        self.set_enabled(False)
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 403)
        self.assertEqual(self.service._get(identity)['state'], 'prepared')
        self.assertEqual(self.provider.writes, [])

    def test_mobile_session_expiry_and_cross_campaign_entity_fail_closed(self):
        self.provider.nodes['999999'] = {'id': '999999', 'account_id': ACCOUNT,
                                         'campaign_id': '111111', 'status': 'PAUSED'}
        code, result = self.post('preview', {'action': 'pause', 'entity_type': 'ad', 'entity_id': '999999'})
        self.assertEqual(code, 200)
        self.assertEqual(result['operation']['state'], 'blocked')
        for identity in self.access.sessions:
            self.access.sessions[identity] = time.time() - 1
        self.assertEqual(self.post('preview', {'action': 'prepare_launch'})[0], 401)
        self.assertEqual(self.provider.writes, [])

    def test_backend_port_injection_is_loopback_only_and_rejects_non_integer_ports(self):
        for value in (True, False, 0, 65536, '8767', 'https://external.invalid'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mobile_web.Backend(backend_port=value)
        self.assertEqual(mobile_web.Backend().base, 'http://127.0.0.1:8767')

    def test_phone_cancel_scheduled_work_revokes_approval_and_worker_never_writes(self):
        at = (self.fixture.now + timedelta(hours=1)).isoformat()
        code, result = self.post('preview', {'action': 'prepare_launch', 'scheduled_at': at})
        self.assertEqual(code, 200)
        proposal = result['operation']
        identity = proposal['operation_id']
        self.assertTrue(proposal['can_cancel'])
        self.assertEqual(self.post('cancel', {'operation_id': identity, 'digest': '0' * 64})[0], 400)
        self.assertEqual(self.post('cancel', {'operation_id': identity, 'digest': proposal['digest'],
                                             'force': True})[0], 400)
        self.assertEqual(self.post('approve', {'operation_id': identity, 'digest': proposal['digest']})[0], 200)
        self.assertEqual(self.post('schedule', {'operation_id': identity, 'at': at})[0], 200)
        code, cancelled = self.post('cancel', {'operation_id': identity, 'digest': proposal['digest']})
        self.assertEqual(code, 200)
        self.assertEqual(cancelled['operation']['state'], 'cancelled')
        self.assertEqual(cancelled['operation']['approval_status'], 'revoked')
        self.assertFalse(cancelled['operation']['provider_write_attempted'])
        self.fixture.now += timedelta(hours=1)
        # Give the actual scheduled worker a full tick; cancellation is durable.
        threading.Event().wait(2.2)
        state = self.request('/api/campaign-operations')[1]
        self.assertEqual(next(x for x in state['jobs'] if x['id'] == identity)['state'], 'cancelled')
        self.assertEqual(self.post('execute', {'operation_id': identity})[1]['job']['state'], 'cancelled')
        self.assertEqual(self.provider.writes, [])

    def test_phone_cancel_requires_remote_authority_and_exact_digest(self):
        proposal = self.preview()
        self.set_enabled(False)
        self.assertEqual(self.post('cancel', {'operation_id': proposal['operation_id'],
                                             'digest': proposal['digest']})[0], 403)
        self.assertEqual(self.service._get(proposal['operation_id'])['state'], 'prepared')
        self.assertEqual(self.provider.writes, [])

    def test_readonly_phone_candidate_preview_uses_private_scoped_cookie(self):
        self.set_enabled(False)
        path='/api/preparation-task/media/'+'a'*12+'~'+'b'*64
        with patch('server.PreparationTask.media',return_value=(b'PNG-BYTES','image/png')) as render:
            self.assertEqual(self.request(path,cookie=False)[0],401)
            connection=http.client.HTTPConnection('127.0.0.1',self.mobile.server_port,timeout=10)
            connection.request('GET',path,headers={'Host':'integration.trycloudflare.com','X-Forwarded-Proto':'https','Cookie':self.cookie})
            response=connection.getresponse();self.assertEqual(response.status,200);self.assertEqual(response.read(),b'PNG-BYTES');self.assertEqual(response.getheader('Content-Type'),'image/png');connection.close()
            render.assert_called_once()
        self.assertIn('campaigner_preparation_media=',self.backend.media_cookie)
        self.assertIn('campaigner_media=',self.backend.media_cookie)
        self.assertEqual(self.request('/api/preparation-task',{})[0],403)
        self.assertEqual(self.provider.writes,[])
    def test_phone_selection_creates_two_ads_without_empty_second_adset(self):
        second, third = self.fixture.add_selection_sources()
        code, result = self.post('preview', {'action': 'prepare_launch',
                                            'selected_asset_ids': [second, self.fixture.asset]})
        self.assertEqual(code, 200)
        proposal = result['operation']
        identity = proposal['operation_id']
        self.assertEqual({x['id'] for x in proposal['files']}, {self.fixture.asset, second})
        self.assertEqual([x['key'] for x in proposal['manifest']['adsets']], ['static'])
        self.assertEqual(self.post('approve', {'operation_id': identity, 'digest': proposal['digest']})[0], 200)
        self.assertEqual(self.post('execute', {'operation_id': identity})[0], 200)
        self.assertEqual(self.wait_terminal(identity)['state'], 'verified')
        self.assertEqual([kind for kind, _ in self.provider.writes].count('adsets'), 1)
        self.assertEqual([kind for kind, _ in self.provider.writes].count('ads'), 2)
        self.assertTrue(all(third not in params.get('name', '') for _, params in self.provider.writes))

    def test_phone_selection_rejects_invalid_shapes_and_unknown_or_cross_group_sources(self):
        second, third = self.fixture.add_selection_sources()
        for selected in ([], None, [self.fixture.asset, self.fixture.asset], [{}], ['short']):
            with self.subTest(selection=selected):
                self.assertEqual(self.post('preview', {'action': 'prepare_launch',
                                                       'selected_asset_ids': selected})[0], 400)
        for selected in (['unknownsourceid'], [third]):
            code, result = self.post('preview', {'action': 'prepare_launch',
                                                'selected_asset_ids': selected,
                                                'selected_adset_keys': ['static']})
            self.assertEqual(code, 200)
            self.assertEqual(result['operation']['state'], 'blocked')
        self.assertEqual(self.post('preview', {'action': 'pause', 'entity_type': 'campaign',
                                             'entity_id': PARENT, 'selected_asset_ids': [second]})[0], 400)
        self.assertEqual(self.provider.writes, [])

    def test_first_phone_get_after_actual_manager_restart_recovers_once(self):
        self.assertEqual(self.request('/api/campaign-operations')[0], 200)
        previous_token = self.backend.token
        self.restart_manager()
        self.assertEqual(self.request('/api/campaign-operations')[0], 200)
        self.assertNotEqual(self.backend.token, previous_token)
        self.assertEqual(self.provider.writes, [])

    def test_phone_thumbnail_requires_session_and_proxies_only_fixed_media_path(self):
        self.set_enabled(False)
        path='/api/preparation-task/media/thumb/'+'a'*12+'~'+'b'*64
        with patch('server.PreparationTask.thumbnail',return_value=(b'JPEG-THUMB','image/jpeg')) as render:
            self.assertEqual(self.request(path,cookie=False)[0],401)
            connection=http.client.HTTPConnection('127.0.0.1',self.mobile.server_port,timeout=10)
            connection.request('GET',path,headers={'Host':'integration.trycloudflare.com','X-Forwarded-Proto':'https','Cookie':self.cookie})
            response=connection.getresponse();self.assertEqual(response.status,200);self.assertEqual(response.read(),b'JPEG-THUMB');self.assertEqual(response.getheader('Content-Type'),'image/jpeg');connection.close()
            render.assert_called_once()
        self.assertEqual(self.provider.writes,[])

    def test_phone_post_after_manager_restart_is_never_replayed(self):
        self.assertEqual(self.request('/api/campaign-operations')[0], 200)
        self.restart_manager()
        calls = []
        original = self.manager.RequestHandlerClass.do_POST
        def counted(handler):
            calls.append(handler.path)
            return original(handler)
        with patch.object(self.manager.RequestHandlerClass, 'do_POST', counted):
            code, result = self.post('preview', {'action': 'prepare_launch'})
            self.assertEqual(code, 503)
            self.assertEqual(calls, ['/api/campaign-operations/preview'])
            self.assertIsNone(self.backend.token)
            self.assertEqual(self.provider.writes, [])
            # A subsequent read may establish the new session. Only a separate
            # explicit user POST is allowed to submit the preparation again.
            self.assertEqual(self.request('/api/campaign-operations')[0], 200)
            self.assertEqual(self.post('preview', {'action': 'prepare_launch'})[0], 200)
            self.assertEqual(len(calls), 2)
        self.assertEqual(self.provider.writes, [])

    def test_phone_get_reauthentication_is_bounded_when_new_session_is_rejected(self):
        self.backend.token = 'stale-local-session'
        calls = []
        original = self.manager.RequestHandlerClass.do_GET
        def reject_api(handler):
            if handler.path == '/api/campaign-operations':
                calls.append(handler.path)
                return handler.reply(403, {'error': 'Controlled persistent rejection'})
            return original(handler)
        with patch.object(self.manager.RequestHandlerClass, 'do_GET', reject_api):
            self.assertEqual(self.request('/api/campaign-operations')[0], 503)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(self.backend.token)
        self.assertEqual(self.provider.writes, [])


if __name__ == '__main__':
    unittest.main()
