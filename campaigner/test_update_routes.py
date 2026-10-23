"""The screen's update routes and the runner's busy check, on the real local server."""
import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import run_owner
from server import make_server


class UpdateRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaigner-test-', dir=Path(__file__).parent)
        self.server = make_server(0, self.temp.name, workspace='manager')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.token = re.search("const token='([^']+)'", urllib.request.urlopen(self.base).read().decode()).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def call(self, path, body=None, headers=None):
        extra = {'X-Local-Session': self.token, **({'Content-Type': 'application/json', 'Origin': self.base} if body is not None else {}),
                 **(headers or {})}
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=extra)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_status_without_the_runner_says_so_and_needs_the_session(self):
        code, value = self.call('/api/update-status')
        self.assertEqual((code, value['state'], value['busy'], value['provider_writes']), (200, 'no_runner', [], 0))
        self.assertRegex(value['version'], 'המסך עודכן')
        self.assertEqual(self.call('/api/update-status', headers={'X-Local-Session': 'bad'})[0], 403)

    def test_status_shows_what_the_runner_wrote(self):
        (Path(self.temp.name) / 'update-status.json').write_text(json.dumps({'state': 'waiting', 'message': 'מחכה'}), encoding='utf-8')
        self.assertEqual(self.call('/api/update-status')[1]['message'], 'מחכה')

    def test_check_now_leaves_a_request_for_the_runner(self):
        self.assertEqual(self.call('/api/update/request', {})[0], 200)
        self.assertTrue((Path(self.temp.name) / 'update-request.json').exists())
        self.assertEqual(self.call('/api/update/request', {'x': 1})[0], 400)
        self.assertEqual(self.call('/api/update/request', {}, headers={'X-Local-Session': 'bad'})[0], 403)

    def test_busy_check_is_for_the_runner_only(self):
        secret = 's' * 40
        self.assertEqual(self.call('/api/supervisor/busy', headers={'X-Supervisor-Token': secret})[0], 403)  # no runner secret set
        with mock.patch.dict('os.environ', {'ROSERY_SUPERVISOR_TOKEN': secret}):
            self.assertEqual(self.call('/api/supervisor/busy', headers={'X-Supervisor-Token': 'wrong'})[0], 403)
            code, value = self.call('/api/supervisor/busy', headers={'X-Supervisor-Token': secret})
            self.assertEqual((code, value['busy']), (200, []))


class SmokeCheckTests(unittest.TestCase):
    def test_the_real_smoke_check_passes_on_this_code(self):
        with tempfile.TemporaryDirectory() as folder:
            Runner = run_owner.Runner({'data_dir': folder, 'port': 1, 'branch': 'x', 'every_minutes': 5})
            Runner._smoke()


if __name__ == '__main__':
    unittest.main()
