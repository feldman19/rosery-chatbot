import io
from pathlib import Path
import tempfile
import time
import unittest

from creative_claims import CreativeClaimReviewRequired
from creative_launch import CreativeLaunch
from local_uploads import LocalUploads
from rulebook import load_rulebook
import launch_strategy
from test_video_creative import FFMPEG, make_clip

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
       b'\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82')


def analysis(assets, owner_brief):
    rows = [{'asset_id': a['asset_id'], 'sha256': a['sha256'], 'observed_product': 'צמיד בגוון זהב', 'angle': 'מתנה',
             'audience_hypothesis': 'גברים', 'visual_description': 'צמיד על יד', 'visible_text': '', 'description': '',
             'format': 'feed', 'warnings': []} for a in assets]
    video = {a['asset_id']: {'audio_status': 'checked', 'transcript': 'צמידים חדשים'} for a in assets if a.get('media_kind') == 'video'}
    return {'assets': rows, **({'video_review': video} if video else {})}


def copy(row, rulebook, transcript=None, source_name=None):
    return {'primary_text_options': [f'טקסט {i}' for i in range(5)], 'headline_options': [f'כותרת {i}' for i in range(5)]}


def report():
    return {'windows': {'7': {'adsets': [], 'ads': [], 'campaigns': [
        {'id': '120246379886090730', 'name': 'מכירות', 'configured_active': True, 'objective': 'OUTCOME_SALES'},
        {'id': '999', 'name': 'הרשמות', 'configured_active': True, 'objective': 'OUTCOME_LEADS'}]}}}


class CreativeLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.uploads = LocalUploads(self.dir, b'k' * 32)

    def tearDown(self):
        self.temp.cleanup()

    def launch(self, **overrides):
        args = dict(uploads=self.uploads, analyze=analysis, copy=copy, plan=launch_strategy.plan, report=report, rulebook=load_rulebook)
        args.update(overrides)
        return CreativeLaunch(self.dir, **args)

    def image(self, n=1):
        return self.uploads.put(io.BytesIO(PNG), size=len(PNG), name=f'img{n}.png', mime='image/png', upload_id=f'upload-image-{n:06d}')

    def wait(self, launch):
        for _ in range(100):
            state = launch.status()
            if state['state'] not in ('analyzing', 'writing_copy', 'planning'):
                return state
            time.sleep(0.05)
        self.fail('launch did not finish')

    def test_image_flow_reaches_owner_with_v2_draft(self):
        asset = self.image()
        launch = self.launch()
        launch.start({'goal': 'צמידים חדשים לחגים', 'sources': [{'id': asset['id'], 'sha256': asset['sha256']}],
                      'consent': True, 'campaign_id': '120246379886090730'})
        state = self.wait(launch)
        self.assertEqual(state['state'], 'ready_for_owner', state.get('detail'))
        draft = state['draft']
        self.assertEqual((draft['schema_version'], draft['creation_status'], draft['required_format']), (2, 'PAUSED', 'single_ad_flexible'))
        self.assertEqual(len(draft['ads'][0]['primary_text_options']), 5)
        self.assertEqual(draft['adsets'][0]['target'], {'mode': 'new'})
        self.assertIsNone(draft['adsets'][0]['budget_minor'])
        self.assertEqual([c['id'] for c in state['campaigns']], ['120246379886090730'])
        self.assertEqual(state['provider_writes'], 0)

    def test_consent_and_exact_body_are_required(self):
        asset = self.image()
        launch = self.launch()
        source = [{'id': asset['id'], 'sha256': asset['sha256']}]
        with self.assertRaises(ValueError):
            launch.start({'goal': 'מטרה', 'sources': source, 'consent': False, 'campaign_id': None})
        with self.assertRaises(ValueError):
            launch.start({'goal': 'מטרה', 'sources': source, 'consent': True})
        with self.assertRaises(ValueError):
            launch.start({'goal': 'מטרה', 'sources': source, 'consent': True, 'campaign_id': 'abc'})

    def test_claim_problem_blocks_with_issues(self):
        asset = self.image()
        def bad_copy(*a, **k):
            raise CreativeClaimReviewRequired([{'asset_id': asset['id'], 'source_name': 'img1.png', 'field': 'primary_text',
                                                'category': 'delivery', 'claim': 'משלוח חינם'}])
        launch = self.launch(copy=bad_copy)
        launch.start({'goal': 'צמידים', 'sources': [{'id': asset['id'], 'sha256': asset['sha256']}], 'consent': True, 'campaign_id': None})
        state = self.wait(launch)
        self.assertEqual(state['state'], 'blocked')
        self.assertEqual(state['claim_issues'][0]['claim'], 'משלוח חינם')

    def test_restart_marks_running_job_interrupted(self):
        (self.dir / 'creative-launch.json').write_text('{"state": "analyzing", "id": "launch-x"}', encoding='utf-8')
        self.assertEqual(self.launch().status()['state'], 'interrupted')

    @unittest.skipUnless(FFMPEG, 'ffmpeg is not installed')
    def test_video_gets_thumbnail_and_audio_status(self):
        raw = make_clip(self.dir / 'v.mp4')
        video = self.uploads.put(io.BytesIO(raw), size=len(raw), name='v.mp4', mime='video/mp4', upload_id='upload-video-000001')
        launch = self.launch()
        launch.start({'goal': 'סרטון צמידים', 'sources': [{'id': video['id'], 'sha256': video['sha256']}], 'consent': True, 'campaign_id': None})
        state = self.wait(launch)
        self.assertEqual(state['state'], 'ready_for_owner', state.get('detail'))
        ad = state['draft']['ads'][0]
        self.assertEqual((ad['media_kind'], ad['review']['audio_status']), ('video', 'checked'))
        thumb = self.uploads.resolve([{'id': ad['video']['thumbnail_asset_id'], 'sha256': ad['video']['thumbnail_sha256']}])[0]
        self.assertEqual(thumb['mime_type'], 'image/jpeg')
        self.assertEqual(state['analysis'][0]['transcript'], 'צמידים חדשים')


if __name__ == '__main__':
    unittest.main()
