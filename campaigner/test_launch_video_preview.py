"""The owner can watch an uploaded video before approving: the local server returns the exact uploaded bytes.

Read-only and local: owner session required, only a video with an unchanged signed upload receipt, nothing
goes to Meta. Needs ffmpeg (as the owner's computer has) to make a real short video.
"""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from server import make_server


def make_media(folder):
    video, image = Path(folder) / 'clip.mp4', Path(folder) / 'still.png'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x400:rate=24:duration=2',
                    '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-y', str(video)], check=True, timeout=60)
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=1080x1350', '-frames:v', '1', '-y', str(image)],
                   check=True, timeout=60)
    return video.read_bytes(), image.read_bytes()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg not installed')
class LaunchVideoPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaigner-test-', dir=Path(__file__).parent)
        self.server = make_server(0, self.temp.name, workspace='manager')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.token = re.search("const token='([^']+)'", urllib.request.urlopen(self.base).read().decode()).group(1)
        self.video, self.image = make_media(self.temp.name)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def upload(self, raw, mime, name):
        req = urllib.request.Request(self.base + '/api/local-uploads', data=raw, headers={
            'Origin': self.base, 'X-Local-Session': self.token, 'Content-Type': mime,
            'X-Upload-Id': 'preview_' + name.replace('.', '_') + '_upload', 'X-File-Name': name})
        with urllib.request.urlopen(req) as response:
            asset = json.load(response)['asset']
        return asset['id'] + '~' + asset['sha256']

    def get(self, path, token=True, headers=None):
        req = urllib.request.Request(self.base + path, headers={'X-Local-Session': self.token if token else 'bad', **(headers or {})})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.headers.get('Content-Type'), response.read()
        except urllib.error.HTTPError as error:
            return error.code, None, error.read()

    def test_uploaded_video_comes_back_exactly_for_the_owner_only(self):
        key = self.upload(self.video, 'video/mp4', 'clip.mp4')
        status, kind, raw = self.get('/api/creative-launch/video/' + key)
        self.assertEqual((status, kind, raw), (200, 'video/mp4', self.video))
        self.assertEqual(self.get('/api/creative-launch/video/' + key, token=False)[0], 403)
        # The still-frame route still returns a JPEG frame, not the video.
        status, kind, _ = self.get('/api/creative-launch/media/' + key)
        self.assertEqual((status, kind), (200, 'image/jpeg'))

    def test_byte_ranges_for_the_iphone_player(self):
        key = self.upload(self.video, 'video/mp4', 'clip.mp4')
        path, size = '/api/creative-launch/video/' + key, len(self.video)
        req = urllib.request.Request(self.base + path, headers={'X-Local-Session': self.token, 'Range': 'bytes=0-1'})
        with urllib.request.urlopen(req) as response:  # Safari's first probe
            self.assertEqual((response.status, response.headers['Content-Range'], response.read()), (206, f'bytes 0-1/{size}', self.video[:2]))
            self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
        for wanted, part in ((f'bytes=100-', self.video[100:]), ('bytes=-50', self.video[-50:]), (f'bytes=10-{size + 999}', self.video[10:])):
            status, _, raw = self.get(path, headers={'Range': wanted})
            self.assertEqual((status, raw), (206, part), wanted)
        for wanted in (f'bytes={size}-', 'bytes=5-2', 'bytes=x', 'bytes=-'):
            self.assertEqual(self.get(path, headers={'Range': wanted})[0], 416, wanted)

    def test_video_cookie_from_the_page_works_without_the_header(self):
        key = self.upload(self.video, 'video/mp4', 'clip.mp4')
        page = urllib.request.urlopen(self.base)
        cookies = [c.split(';', 1)[0] for c in page.headers.get_all('Set-Cookie')]
        video_cookie = next(c for c in cookies if c.startswith('campaigner_video='))
        self.assertIn('Path=/api/creative-launch/video/', next(c for c in page.headers.get_all('Set-Cookie') if c.startswith('campaigner_video=')))
        path = '/api/creative-launch/video/' + key
        req = urllib.request.Request(self.base + path, headers={'Cookie': video_cookie, 'Range': 'bytes=0-9'})
        with urllib.request.urlopen(req) as response:
            self.assertEqual((response.status, response.read()), (206, self.video[:10]))
        for headers in ({'Cookie': 'campaigner_video=wrong'}, {'Cookie': video_cookie, 'Sec-Fetch-Site': 'cross-site'}, {}):
            req = urllib.request.Request(self.base + path, headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req)
            self.assertEqual(caught.exception.code, 403, headers)

    def test_a_file_changed_after_it_played_is_refused(self):
        key = self.upload(self.video, 'video/mp4', 'clip.mp4')
        path = '/api/creative-launch/video/' + key
        self.assertEqual(self.get(path)[0], 200)
        identity, sha = key.split('~')
        (Path(self.temp.name) / 'drive-assets' / (identity + '-' + sha + '.bin')).write_bytes(b'0' * len(self.video))
        self.assertEqual(self.get(path)[0], 404)

    def test_page_policy_lets_its_own_previews_show(self):
        # Previews are blob: URLs the page makes from files on this computer; other sources stay blocked.
        policy = urllib.request.urlopen(self.base).headers['Content-Security-Policy']
        self.assertIn("img-src 'self' blob:", policy)
        self.assertIn("media-src 'self' blob:", policy)
        self.assertIn("default-src 'self'", policy)
        self.assertNotIn('*', policy.replace('https://*.fbcdn.net', '').replace('https://*.cdninstagram.com', ''))

    def test_not_a_video_or_changed_or_malformed_key_is_not_found(self):
        image = self.upload(self.image, 'image/png', 'still.png')
        self.assertEqual(self.get('/api/creative-launch/video/' + image)[0], 404)
        key = self.upload(self.video, 'video/mp4', 'clip.mp4')
        identity, sha = key.split('~')
        self.assertEqual(self.get('/api/creative-launch/video/' + identity + '~' + 'a' * 64)[0], 404)
        self.assertEqual(self.get('/api/creative-launch/video/../secrets')[0], 404)
        # Bytes changed on disk after upload → refused, never served.
        stored = Path(self.temp.name) / 'drive-assets' / (identity + '-' + sha + '.bin')
        stored.write_bytes(self.video[:-10] + b'0' * 10)
        self.assertEqual(self.get('/api/creative-launch/video/' + key)[0], 404)


if __name__ == '__main__':
    unittest.main()
