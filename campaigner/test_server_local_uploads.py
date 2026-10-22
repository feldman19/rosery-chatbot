"""Local upload HTTP contract: authentication, bounded input, real file validation."""
import http.client
import json
import re
import tempfile
import threading
import unittest
import uuid
from urllib.parse import quote
from unittest.mock import patch

from test_intake_to_paused_launch import image_bytes
from server import make_server


class LocalUploadHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_server(0, self.temp.name, workspace='manager')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        connection.request('GET', '/')
        page = connection.getresponse().read().decode()
        connection.close()
        self.token = re.search(r"const token='([^']+)'", page).group(1)
        self.png = image_bytes(20, 20, (0, 0, 255))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def upload(self, content=None, **overrides):
        content = self.png if content is None else content
        headers = {'Origin': self.origin, 'X-Local-Session':self.token,
                   'Content-Type':'image/png', 'Content-Length':str(len(content)),
                   'X-File-Name':quote('חולצה.png'), 'X-Upload-Id':str(uuid.uuid4())}
        headers.update(overrides)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('POST', '/api/local-uploads', body=content, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_auth_origin_size_and_mime_rejected_before_storage(self):
        with patch('server.LocalUploads.put') as store:
            for headers, expected in [({'X-Local-Session':'wrong'},403), ({'Origin':'https://example.com'},403),
                                      ({'Content-Length':str(20*1024*1024+1)},413),
                                      ({'Content-Type':'text/plain'},415),
                                      ({'Transfer-Encoding':'chunked'},400), ({'Content-Length':'0'},400)]:
                self.assertEqual(self.upload(**headers)[0], expected)
            store.assert_not_called()

    def test_real_upload_retry_returns_same_asset_and_no_analysis(self):
        upload_id = str(uuid.uuid4())
        with patch('server.CreativePreparation.start') as analyze:
            code, first = self.upload(**{'X-Upload-Id':upload_id})
            code2, second = self.upload(**{'X-Upload-Id':upload_id})
            self.assertEqual((code, code2), (200, 200))
            self.assertEqual(first['asset'], second['asset'])
            self.assertEqual(first['provider_writes'], 0)
            self.assertEqual(first['asset']['source_kind'], 'device')
            self.assertNotIn('path', first['asset'])
            analyze.assert_not_called()

    def test_traversal_invalid_image_and_mime_mismatch_rejected(self):
        for name in ('../outside.png', '..\\outside.png', 'C:\\outside.png'):
            self.assertEqual(self.upload(**{'X-File-Name':quote(name)})[0], 400)
        self.assertEqual(self.upload(b'not an image')[0], 400)
        self.assertEqual(self.upload(**{'Content-Type':'image/jpeg'})[0], 400)

    def test_retry_changed_bytes_rejected(self):
        upload_id = str(uuid.uuid4())
        self.assertEqual(self.upload(**{'X-Upload-Id':upload_id})[0], 200)
        other = image_bytes(20, 20, (255, 0, 0))
        self.assertEqual(self.upload(other, **{'X-Upload-Id':upload_id})[0], 400)


if __name__ == '__main__':
    unittest.main()
