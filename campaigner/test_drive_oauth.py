"""Synthetic OAuth tests: never contact Google or load host credentials."""
import base64
import hashlib
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock
from drive_oauth import DriveOAuth, CLIENT_ID, AUTH, TTL
from drive_direct import SCOPE


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.exchange = Mock(return_value={'refresh_token': 'synthetic-refresh-secret', 'scope': SCOPE})
        self.configure = Mock(return_value={'saved': True, 'verified': True})
        self.client = {'client_id': CLIENT_ID, 'client_secret': 'synthetic-client-secret'}
        self.flow = DriveOAuth('.', 8767, lambda: dict(self.client), clock=lambda: self.now,
            exchange=self.exchange, configure=self.configure)

    def begin(self):
        value = self.flow.begin()
        return value, parse_qs(urlsplit(value['authorization_url']).query)

    def test_exact_readonly_pkce_contract(self):
        value, params = self.begin()
        self.assertTrue(value['authorization_url'].startswith(AUTH + '?'))
        for key, expected in {'scope': SCOPE, 'client_id': CLIENT_ID, 'access_type': 'offline',
            'prompt': 'consent', 'include_granted_scopes': 'false', 'code_challenge_method': 'S256',
            'login_hint': 'inforosery@gmail.com', 'redirect_uri': 'http://127.0.0.1:8767/api/drive/oauth/callback'}.items():
            self.assertEqual(params[key], [expected])
        self.assertNotIn('secret', json.dumps(value))
        self.exchange.assert_not_called()
        result = self.flow.complete('synthetic-code', params['state'][0])
        form = self.exchange.call_args.args[0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(params['code_challenge'], [challenge])
        self.assertEqual(form['grant_type'], 'authorization_code')
        self.assertNotIn('refresh_token', result)
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(self.configure.call_args.args[1]['type'], 'authorized_user')

    def test_replay_rejected(self):
        _, p = self.begin()
        self.flow.complete('code', p['state'][0])
        with self.assertRaises(ValueError): self.flow.complete('code', p['state'][0])
        self.assertEqual(self.exchange.call_count, 1)

    def test_expiry_boundary(self):
        _, p = self.begin()
        self.now += TTL
        with self.assertRaises(ValueError): self.flow.complete('code', p['state'][0])
        self.exchange.assert_not_called()

    def test_wrong_state_does_not_destroy_valid_flow(self):
        _, p = self.begin()
        with self.assertRaises(ValueError): self.flow.complete('code', 'x' * 43)
        self.flow.complete('code', p['state'][0])

    def test_new_begin_revokes_previous(self):
        _, old = self.begin()
        _, new = self.begin()
        with self.assertRaises(ValueError): self.flow.complete('code', old['state'][0])
        self.flow.complete('code', new['state'][0])

    def test_exchange_failure_consumes_and_redacts(self):
        _, p = self.begin()
        self.exchange.side_effect = RuntimeError('synthetic-sensitive-body')
        with self.assertRaisesRegex(ValueError, '^Drive authorization could not be verified$'):
            self.flow.complete('code', p['state'][0])
        with self.assertRaises(ValueError): self.flow.complete('code', p['state'][0])
        self.assertEqual(self.exchange.call_count, 1)
        self.configure.assert_not_called()

    def test_extra_scope_rejected_before_store(self):
        _, p = self.begin()
        self.exchange.return_value['scope'] += ' other'
        with self.assertRaises(ValueError): self.flow.complete('code', p['state'][0])
        self.configure.assert_not_called()

    def test_missing_refresh_fails_closed(self):
        _, p = self.begin()
        self.exchange.return_value = {'access_token': 'synthetic-access'}
        with self.assertRaises(ValueError): self.flow.complete('code', p['state'][0])
        self.configure.assert_not_called()

    def test_identity_verification_failure_redacted(self):
        _, p = self.begin()
        self.configure.side_effect = ValueError('raw identity secret')
        with self.assertRaisesRegex(ValueError, '^Drive authorization could not be verified$'):
            self.flow.complete('code', p['state'][0])

    def test_client_binding_and_status(self):
        self.client['client_id'] = 'wrong'
        self.assertFalse(self.flow.status()['client_configured'])
        with self.assertRaisesRegex(ValueError, 'client unavailable'): self.begin()
        self.exchange.assert_not_called()

    def test_status_never_exports_credentials(self):
        self.begin()
        status = self.flow.status()
        self.assertTrue(status['authorization_pending'])
        self.assertNotIn('secret', json.dumps(status))

    def test_port_cannot_inject_redirect(self):
        for port in (True, 0, 65536, '8767/path'):
            with self.assertRaises(ValueError): DriveOAuth('.', port)

    def test_concurrent_callbacks_exchange_only_once(self):
        _, p = self.begin()
        def callback():
            try: return self.flow.complete('code', p['state'][0])['saved']
            except ValueError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: callback(), range(2))), [False, True])
        self.assertEqual(self.exchange.call_count, 1)

    def test_invalid_code_consumes_valid_state(self):
        _, p = self.begin()
        with self.assertRaises(ValueError): self.flow.complete('code\nsecret', p['state'][0])
        with self.assertRaises(ValueError): self.flow.complete('code', p['state'][0])
        self.exchange.assert_not_called()


if __name__ == '__main__': unittest.main()
