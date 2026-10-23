import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import video_validation
from creative_vision import CreativeVision, FIELDS
from local_uploads import LocalUploads

try:
    FFMPEG = video_validation.tool('ffmpeg')
except ValueError:
    FFMPEG = None


def make_clip(path, size='720x1280', seconds=4, sound=True):
    args = [FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc=size={size}:rate=25:duration={seconds}']
    if sound:
        args += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}', '-shortest', '-c:a', 'aac']
    args += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)]
    subprocess.run(args, check=True)
    return path.read_bytes()


@unittest.skipUnless(FFMPEG, 'ffmpeg is not installed')
class VideoValidationTests(unittest.TestCase):
    def test_probe_frames_and_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / 'a.mp4'
            raw = make_clip(clip)
            self.assertEqual(video_validation.sniff(raw[:16]), 'video/mp4')
            facts = video_validation.probe(clip)
            self.assertEqual((facts['width'], facts['height'], facts['format'], facts['has_audio']), (720, 1280, 'story', True))
            shots = video_validation.frames(clip, facts['duration_ms'])
            self.assertEqual(len(shots), video_validation.FRAME_COUNT)
            self.assertEqual(shots[0][0], 500)
            self.assertTrue(all(raw.startswith(b'\xff\xd8') for _, raw in shots))
            self.assertGreater(len(video_validation.audio(clip)), 44)

    def test_square_without_sound_and_non_video_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / 'b.mp4'
            make_clip(clip, size='720x720', sound=False)
            facts = video_validation.probe(clip)
            self.assertEqual((facts['format'], facts['has_audio']), ('feed', False))
            self.assertIsNone(video_validation.audio(clip))
            junk = Path(temp) / 'c.mp4'
            junk.write_bytes(b'not a video at all')
            self.assertIsNone(video_validation.sniff(junk.read_bytes()[:16]))
            with self.assertRaises(ValueError):
                video_validation.probe(junk)


@unittest.skipUnless(FFMPEG, 'ffmpeg is not installed')
class VideoUploadAndReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.raw = make_clip(self.dir / 'source.mp4')
        self.uploads = LocalUploads(self.dir, b'k' * 32)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, mime='video/mp4', raw=None, upload_id='upload-video-0001'):
        raw = self.raw if raw is None else raw
        return self.uploads.put(io.BytesIO(raw), size=len(raw), name='clip.mp4', mime=mime, upload_id=upload_id)

    def test_upload_stores_video_facts_and_resolves(self):
        asset = self.put()
        self.assertEqual((asset['media_kind'], asset['has_audio'], asset['width'], asset['height']), ('video', True, 720, 1280))
        self.assertEqual(self.uploads.resolve([{'id': asset['id'], 'sha256': asset['sha256']}])[0]['duration_ms'], asset['duration_ms'])

    def test_upload_refuses_wrong_type(self):
        with self.assertRaises(ValueError):
            self.put(mime='video/mp4', raw=b'\x89PNG\r\n\x1a\n' + b'0' * 64)
        with self.assertRaises(ValueError):
            self.put(mime='video/quicktime')  # mp4 brand declared as MOV

    def test_review_sends_frames_and_transcript_never_the_file(self):
        asset = self.put()
        (self.dir / 'creative-image-sharing.json').write_text('{}', encoding='utf-8')
        sent = {}

        def runner(payload):
            sent['payload'] = json.loads(payload)
            row = {k: 'טקסט' for k in FIELDS}
            row.update(asset_id=asset['id'], sha256=asset['sha256'], format='story', confidence=0.8, warnings=[],
                       paired_asset_id=None, visible_text='', description='', concept_key='c1')
            return {'assets': [row], 'proposed_adsets': []}

        speech = {'status': 'transcribed', 'text': 'צמידים חדשים בגוון כסף', 'language': 'he', 'segments': []}
        with patch('transcription.transcribe', return_value=speech), \
                patch('creative_claims.validate_creative_claims'), patch('business_method_review.review_creative_method',
                                                                         return_value={'issues': []}):
            result = CreativeVision(self.dir, runner=runner).analyze(
                [dict(asset, asset_id=asset['id'])], local_image_review_consent=True)
        content = sent['payload']['message']['content']
        images = [c for c in content if c['type'] == 'image']
        self.assertEqual(len(images), video_validation.FRAME_COUNT)
        self.assertTrue(all(c['source']['media_type'] == 'image/jpeg' for c in images))
        whole = base64.b64encode(self.raw).decode('ascii')
        self.assertFalse(any(c['source']['data'] == whole for c in images))
        self.assertTrue(any('צמידים חדשים בגוון כסף' in c.get('text', '') for c in content))
        review = result['video_review'][asset['id']]
        self.assertEqual((review['audio_status'], review['media_kind']), ('checked', 'video'))
        self.assertEqual(result['review_provenance'][0]['sha256'], asset['sha256'])

    def test_failed_transcription_is_marked_not_checked(self):
        asset = self.put(upload_id='upload-video-0002')
        captured = {}

        def runner(payload):
            captured['text'] = payload
            row = {k: 'טקסט' for k in FIELDS}
            row.update(asset_id=asset['id'], sha256=asset['sha256'], format='story', confidence=0.5, warnings=[],
                       paired_asset_id=None, visible_text='', description='', concept_key='c1')
            return {'assets': [row], 'proposed_adsets': []}

        with patch('transcription.transcribe', return_value={'status': 'not_checked', 'text': '', 'language': None, 'segments': []}), \
                patch('creative_claims.validate_creative_claims'), patch('business_method_review.review_creative_method',
                                                                         return_value={'issues': []}):
            result = CreativeVision(self.dir, runner=runner).analyze([dict(asset, asset_id=asset['id'])],
                                                                      local_image_review_consent=True)
        self.assertEqual(result['video_review'][asset['id']]['audio_status'], 'not_checked')
        self.assertIn('Never invent speech', captured['text'])


if __name__ == '__main__':
    unittest.main()
