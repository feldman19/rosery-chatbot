"""Actual bundled-Pillow derivatives; source bytes remain authoritative."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from image_validation import _image_python, thumbnail_image, THUMB_MAX_BYTES
from preparation_task import PreparationTask


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'fixture.png'
        program = """from PIL import Image
import sys
image=Image.effect_noise((1600,1200),100).convert('RGB')
exif=Image.Exif();exif[274]=6
image.save(sys.argv[1],format='PNG',exif=exif)
"""
        subprocess.run([_image_python(), '-I', '-c', program, str(source)], check=True,
                       capture_output=True, timeout=15)
        self.original = source.read_bytes()
        self.assertGreater(len(self.original), THUMB_MAX_BYTES)
        self.sha = hashlib.sha256(self.original).hexdigest()
        self.identity = 'thumbnailimage1'
        cache = self.root / 'drive-assets'
        cache.mkdir()
        self.path = cache / (self.identity + '-' + self.sha + '.bin')
        self.path.write_bytes(self.original)
        request = {'id': 'r1', 'revision': 1, 'goal': 'source-test', 'drive_urls': []}
        materials = {'configured': True, 'binding_verified': True, 'assets': {
            'complete': True, 'assets': [{'id': self.identity, 'sha256': self.sha,
            'content_read': True, 'format_check': 'decoded', 'mime_type': 'image/png',
            'dimensions': {'width': 1600, 'height': 1200}}]}}
        self.service = PreparationTask(self.root,
            intake=SimpleNamespace(latest=lambda: request),
            workflow=SimpleNamespace(report=lambda _: {'request': {
                'brief': request['goal'], 'drive_urls': [], 'id': 'launch1', 'revision': 1}}),
            materials=SimpleNamespace(status=lambda _: materials),
            proposal=SimpleNamespace(report=lambda _: {'available': False}),
            loop=SimpleNamespace(report=lambda: {'job': None}))
        self.key = self.identity + '~' + self.sha

    def test_large_original_small_oriented_jpeg_and_source_unchanged(self):
        derivative, mime = self.service.thumbnail(self.key)
        self.assertEqual(mime, 'image/jpeg')
        self.assertTrue(derivative.startswith(b'\xff\xd8\xff'))
        self.assertLessEqual(len(derivative), THUMB_MAX_BYTES)
        target = self.root / 'thumbnail.jpg'
        target.write_bytes(derivative)
        result = subprocess.run([_image_python(), '-I', '-c',
            "from PIL import Image;import json,sys;im=Image.open(sys.argv[1]);im.load();print(json.dumps([im.width,im.height,im.mode]))",
            str(target)], capture_output=True, check=True, timeout=15)
        # EXIF orientation6 swaps1600x1200 before scaling:360x480.
        self.assertEqual(json.loads(result.stdout), [360, 480, 'RGB'])
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.sha)
        report = self.service.report()
        row = report['candidates']['items'][0]
        self.assertEqual(row['preview_url'], '/api/preparation-task/media/thumb/' + self.key)
        self.assertEqual(row['media_url'], '/api/preparation-task/media/' + self.key)

    def test_warm_cached_derivative_does_not_bypass_current_source_hash(self):
        self.service.thumbnail(self.key)
        self.path.write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            self.service.thumbnail(self.key)

    def test_child_checks_actual_sha_even_when_called_directly(self):
        self.path.write_bytes(b'tampered')
        thumbnail_image.cache_clear()
        with self.assertRaises(ValueError):
            thumbnail_image(str(self.path.resolve()), self.sha)


if __name__ == '__main__':
    unittest.main()
