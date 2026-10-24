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


def copy(row, rulebook, transcript=None, source_name=None, goal=None):
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

    def test_claim_problem_gets_one_fresh_attempt(self):
        asset = self.image()
        calls = []
        def flaky_copy(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise CreativeClaimReviewRequired([{'asset_id': 'a', 'source_name': 'img1.png', 'field': 'headline', 'category': 'delivery', 'claim': 'משלוח חינם'}])
            return copy(*a, **k)
        launch = self.launch(copy=flaky_copy)
        launch.start({'goal': 'צמידים', 'sources': [{'id': asset['id'], 'sha256': asset['sha256']}], 'consent': True, 'campaign_id': None})
        state = self.wait(launch)
        self.assertEqual(state['state'], 'ready_for_owner', state.get('detail'))
        self.assertEqual(len(calls), 2)

    def test_copy_gets_the_goal_and_its_brief_is_kept_beside_the_draft(self):
        asset = self.image()
        seen = {}
        def briefed(row, rulebook, **kw):
            seen.update(kw)
            return {**copy(row, rulebook), 'brief': {'avatar': 'גבר שקונה לעצמו'}, 'customer_read': {'done': True, 'rewrites': 1, 'still_flagged': []}}
        launch = self.launch(copy=briefed)
        launch.start({'goal': 'צמידים לגברים', 'sources': [{'id': asset['id'], 'sha256': asset['sha256']}], 'consent': True, 'campaign_id': None})
        state = self.wait(launch)
        self.assertEqual(seen['goal'], 'צמידים לגברים')
        self.assertEqual(state['copy_briefs'][asset['id']]['brief']['avatar'], 'גבר שקונה לעצמו')
        self.assertNotIn('brief', state['draft']['ads'][0])  # the proposal for Meta stays exactly as before

    def test_finalize_makes_a_valid_v2_proposal_for_the_write_path(self):
        from launch_proposal_schema import digest, validate_proposal_v2
        from operations_config import operation_proposal
        asset = self.image()
        launch = self.launch()
        launch.start({'goal': 'צמידים', 'sources': [{'id': asset['id'], 'sha256': asset['sha256']}], 'consent': True, 'campaign_id': None})
        key = self.wait(launch)['draft']['adsets'][0]['key']
        with self.assertRaises(ValueError):
            launch.finalize({'campaign_id': '999', 'budgets': {key: 10000}, 'genders': {key: 'men'}, 'confirm_same_angle': []})  # leads campaign
        with self.assertRaises(ValueError):
            launch.finalize({'campaign_id': '120246379886090730', 'budgets': {key: 10000}, 'genders': {}, 'confirm_same_angle': []})
        result = launch.finalize({'campaign_id': '120246379886090730', 'budgets': {key: 10000}, 'genders': {key: 'men'}, 'confirm_same_angle': []})
        self.assertEqual(launch.operation_report()['proposal']['adsets'][0]['targeting_mode'], 'copy_men')
        report = launch.operation_report()
        proposal = validate_proposal_v2(report['proposal'])
        self.assertEqual(result['expected_source']['proposal_hash'], digest(proposal))
        self.assertEqual((proposal['adsets'][0]['budget_minor'], proposal['strategy']['campaign_id']), (10000, '120246379886090730'))
        features = proposal['creation_recipe']['creative_fields']['degrees_of_freedom_spec']['creative_features_spec']
        self.assertEqual(features['inline_comment'], {'enroll_status': 'OPT_OUT'})
        self.assertTrue(all(v == {'enroll_status': 'OPT_OUT'} for k, v in features.items() if k != 'inline_comment'))
        self.assertEqual(operation_proposal(report)['source_binding'], result['expected_source'])
        # The write path's own checks: same rulebook digest and every rule it requires is cited.
        self.assertEqual(proposal['rules']['rulebook_sha256'], digest(load_rulebook()))
        self.assertTrue({'full_copy_set', 'ads_per_adset', 'new_creatives_new_dated_adset', 'cta_shop_now', 'no_carousel',
                         'offer_in_headline'} <= set(proposal['rules']['citations']) <= set(load_rulebook()['rules']))

    def test_restart_marks_running_job_interrupted(self):
        (self.dir / 'creative-launch.json').write_text('{"state": "analyzing", "id": "launch-x"}', encoding='utf-8')
        self.assertEqual(self.launch().status()['state'], 'interrupted')

    def test_finalize_merges_same_gender_and_keeps_total_budget(self):
        import copy as copying
        asset = self.image()
        launch = self.launch()
        launch.start({'goal': 'צמידים', 'sources': [{'id': asset['id'], 'sha256': asset['sha256']}],
                      'consent': True, 'campaign_id': None})
        state = self.wait(launch)
        draft = state['draft']
        group, ad = draft['adsets'][0], draft['ads'][0]
        draft['adsets'], draft['ads'] = [], []
        for i in range(6):
            g, a = copying.deepcopy(group), copying.deepcopy(ad)
            g['key'] = f'group-{i}'
            a['asset_id'] = f'local_asset-test-{i}'
            a['adset_key'] = g['key']
            a['media_kind'] = 'video'
            a['review']['audio_status'] = 'checked'
            a['video'] = {'duration_ms': 30000, 'width': 1080, 'height': 1920,
                          'technical_valid': True, 'thumbnail_asset_id': 'thumbnail-test',
                          'thumbnail_sha256': 'a' * 64}
            draft['adsets'].append(g)
            draft['ads'].append(a)
        launch._save(state)
        body = {'campaign_id': '120246379886090730',
                'budgets': {g['key']: 10000 for g in draft['adsets']},
                'genders': {g['key']: 'men' if i < 5 else 'women' for i, g in enumerate(draft['adsets'])},
                'confirm_same_angle': []}
        launch.finalize(body)
        proposal = launch.operation_report()['proposal']
        self.assertEqual(len(proposal['adsets']), 2)
        self.assertEqual([g['budget_minor'] for g in proposal['adsets']], [50000, 10000])
        self.assertEqual([sum(a['adset_key'] == g['key'] for a in proposal['ads']) for g in proposal['adsets']], [5, 1])
        self.assertIn('תכנים חדשים | גברים |', proposal['adsets'][0]['label'])
        self.assertTrue(proposal['adsets'][0]['label'].startswith('וידאו |'))
        # Repeating finalize starts from the original draft, without accumulating budgets.
        launch.finalize(body)
        self.assertEqual(launch.operation_report()['proposal']['adsets'][0]['budget_minor'], 50000)

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
