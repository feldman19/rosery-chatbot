"""End to end: the real local server behind the real phone gateway, on loopback.

Only the Tailscale address check is replaced, and only here, so the gateway can bind 127.0.0.1 and pin the
"phone" to 127.0.0.2. Everything else is the code the owner runs: the sign-in code, the cookie, the route
list, and the local server's own session and origin checks behind it. Nothing here reaches Meta or Shopify.
"""
import http.client
import ipaddress
import json
import re
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import phone_gateway
from phone_gateway import COOKIE, PhoneAccess, make_gateway
from server import make_server

PC, PHONE, STRANGER = '127.0.0.1', '127.0.0.2', '127.0.0.3'


class PhoneGatewayEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaigner-test-', dir=Path(__file__).parent)
        self.backend = make_server(0, self.temp.name, workspace='manager')
        threading.Thread(target=self.backend.serve_forever, daemon=True).start()
        self.now = 1000.0
        self.access = PhoneAccess(Path(self.temp.name) / 'phone-gateway.json', clock=lambda: self.now)
        self.code = self.access.new_code()
        with mock.patch.object(phone_gateway, 'TAILNET', ipaddress.ip_network('127.0.0.0/8')):
            self.gateway = make_gateway(PC, PHONE, self.access, port=0, backend_port=self.backend.server_port)
        threading.Thread(target=self.gateway.serve_forever, daemon=True).start()

    def tearDown(self):
        for server in (self.gateway, self.backend):
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def call(self, method, path, body=None, headers=None, source=PHONE):
        conn = http.client.HTTPConnection(PC, self.gateway.server_port, timeout=30, source_address=(source, 0))
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.getheader('Set-Cookie'), response.read().decode('utf-8', 'replace')
        finally:
            conn.close()

    def sign_in(self, code):
        return self.call('POST', '/__login', f'code={code}', {'Content-Type': 'application/x-www-form-urlencoded'})

    def signed_in(self):
        status, cookie, _ = self.sign_in(self.code)
        self.assertEqual(status, 303)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        return {'Cookie': cookie.split(';', 1)[0]}

    def test_sign_in_then_simple_screen_and_relayed_post_keep_backend_checks(self):
        # Before sign-in: the code page, and nothing from the local server.
        status, _, page = self.call('GET', '/')
        self.assertEqual(status, 401)
        self.assertIn('/__login', page)
        self.assertEqual(self.call('GET', '/api/outcomes')[0], 401)
        # Another address on the same network is refused even with a valid cookie.
        phone = self.signed_in()
        self.assertEqual(self.call('GET', '/', headers=phone, source=STRANGER)[0], 403)

        # Signed in: the simple screen comes through from the local server, with its own session token.
        status, _, page = self.call('GET', '/', headers=phone)
        self.assertEqual(status, 200)
        token = re.search("const token='([^']+)'", page).group(1)
        self.assertIn('ביצעתי', page)
        session = {**phone, 'X-Local-Session': token}

        # GET: the local server still wants its session header.
        self.assertEqual(self.call('GET', '/api/outcomes', headers=phone)[0], 403)
        status, _, body = self.call('GET', '/api/outcomes', headers=session)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['provider_writes'], 0)

        # POST: relayed, and the local server's session check still applies.
        post = {'Content-Type': 'application/json'}
        status, _, body = self.call('POST', '/api/outcomes/mark', json.dumps({'proposal_id': 'pause:1'}), {**phone, **post})
        self.assertEqual(status, 403)
        self.assertIn('Local session', json.loads(body)['error'])
        # A forged token is refused too; the phone cookie is not a local session.
        forged = {**phone, **post, 'X-Local-Session': self.code}
        self.assertEqual(self.call('POST', '/api/outcomes/mark', json.dumps({'proposal_id': 'pause:1'}), forged)[0], 403)
        # With the session the request reaches the local server's own validation (exact fields, then state).
        self.assertEqual(self.call('POST', '/api/outcomes/mark', json.dumps({'proposal_id': 'pause:1', 'extra': 1}), {**session, **post})[0], 400)
        status, _, body = self.call('POST', '/api/outcomes/mark', json.dumps({'proposal_id': 'pause:1'}), {**session, **post})
        self.assertEqual(status, 409)
        self.assertIn('רענן', json.loads(body)['error'])

    def test_advanced_screen_and_unlisted_routes_are_refused(self):
        phone = self.signed_in()
        self.assertEqual(self.call('GET', '/advanced')[0], 401)
        for method, path in (('GET', '/advanced'), ('GET', '/advanced?x=1'), ('POST', '/api/execute'),
                             ('POST', '/api/connections/meta-executor/setup')):
            status, _, body = self.call(method, path, b'{}' if method == 'POST' else None, phone)
            self.assertEqual(status, 404, path)
            self.assertIn('לא זמין מהטלפון', json.loads(body)['error'])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg not installed')
    def test_uploaded_video_plays_on_the_phone_in_full(self):
        from test_launch_video_preview import make_media
        video, _ = make_media(self.temp.name)
        phone = self.signed_in()
        token = re.search("const token='([^']+)'", self.call('GET', '/', headers=phone)[2]).group(1)
        session = {**phone, 'X-Local-Session': token}
        status, _, body = self.call('POST', '/api/local-uploads', video, {**session, 'Content-Type': 'video/mp4',
                                    'X-File-Name': 'clip.mp4', 'X-Upload-Id': 'phone_preview_clip_upload'})
        self.assertEqual(status, 200, body)
        asset = json.loads(body)['asset']
        path = f"/api/creative-launch/video/{asset['id']}~{asset['sha256']}"
        conn = http.client.HTTPConnection(PC, self.gateway.server_port, timeout=30, source_address=(PHONE, 0))
        try:
            conn.request('GET', path, headers=session)
            response = conn.getresponse()
            self.assertEqual((response.status, response.getheader('Content-Type')), (200, 'video/mp4'))
            self.assertEqual(response.read(), video)
        finally:
            conn.close()
        self.assertEqual(self.call('GET', path, headers=phone)[0], 403)  # the local session is still required

    def test_five_wrong_codes_lock_sign_in(self):
        wrong = '000000' if self.code != '000000' else '111111'
        for _ in range(5):
            status, cookie, page = self.sign_in(wrong)
            self.assertEqual((status, cookie), (401, None))
            self.assertIn('קוד שגוי', page)
        status, cookie, _ = self.sign_in(self.code)  # the right code is refused while locked
        self.assertEqual((status, cookie), (401, None))
        self.assertEqual(self.call('GET', '/', headers={'Cookie': f'{COOKIE}=guess'})[0], 401)
        self.now += 901
        self.assertEqual(self.sign_in(self.code)[0], 303)


if __name__ == '__main__':
    unittest.main()
