import gzip
import http.client
import json
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

from server import make_server


class ResponseCompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_server(0, self.temp.name, workspace='manager')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, _, page = self.get('/')
        self.token = re.search(rb"const token='([^']+)'", page).group(1).decode()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def get(self, path, encoding='identity'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Accept-Encoding': encoding, 'X-Local-Session':getattr(self, 'token', '')}
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_large_json_and_html_match_identity_after_decompression(self):
        report = {'summary': 'פרסום ברור ' * 1000}
        with patch('server.management_report', return_value=report), patch('server.build_owner_action_proposals', return_value=[]), patch('server.build_recommendations', return_value={}):
            for path in ('/api/management-report', '/'):
                code, identity_headers, identity = self.get(path)
                status, headers, compressed = self.get(path, 'br, gzip;q=0.8')
                self.assertEqual((code, status), (200, 200))
                self.assertEqual(gzip.decompress(compressed), identity)
                self.assertLess(len(compressed), len(identity))
                self.assertEqual(headers['Content-Encoding'], 'gzip')
                self.assertEqual(int(headers['Content-Length']), len(compressed))
                self.assertEqual(headers['Vary'], 'Accept-Encoding')
                for key in ('Cache-Control', 'X-Content-Type-Options', 'Referrer-Policy', 'Content-Security-Policy'):
                    self.assertEqual(headers[key], identity_headers[key])

    def test_zero_unknown_and_malformed_weights_use_identity(self):
        for encoding in ('gzip;q=0', 'br', 'gzip;q=bogus', 'gzip;q=1.5', '*;q=1, gzip;q=0', 'gzip;q=0, gzip;q=1'):
            _, headers, body = self.get('/', encoding)
            self.assertNotIn('Content-Encoding', headers)
            self.assertTrue(body.startswith(b'<!'))
            self.assertEqual(headers['Vary'], 'Accept-Encoding')

    def test_small_json_and_large_media_not_compressed(self):
        with patch('server.management_report', return_value={'ok':True}), patch('server.build_owner_action_proposals', return_value=[]), patch('server.build_recommendations', return_value={}), patch('server.load_rosery_dossier', return_value={}):
            _, headers, body = self.get('/api/management-report', 'gzip')
            self.assertNotIn('Content-Encoding', headers)
            self.assertTrue(json.loads(body)['ok'])
        media = b'\x89PNG\r\n\x1a\n' + b'x' * 2048
        handler = self.server.RequestHandlerClass
        with patch.object(handler, 'do_GET', lambda request: request.reply(200, media, 'image/png')):
            _, headers, body = self.get('/synthetic-media', 'gzip')
        self.assertEqual(body, media)
        self.assertNotIn('Content-Encoding', headers)
        self.assertNotIn('Vary', headers)
        self.assertEqual(int(headers['Content-Length']), len(media))


if __name__ == '__main__':
    unittest.main()
