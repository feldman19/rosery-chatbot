from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from campaign_operations_transport import ProviderRejected, ProviderUnknown
from owner_actions import ActionBlocked, OwnerActions

AD, ADSET, CREATIVE = '120253138095700730', '120253138075350730', '120200000000000001'


class FakeMeta:
    def __init__(self):
        self.ads = {AD: {'id': AD, 'name': 'תוכן חדש 2', 'status': 'ACTIVE', 'adset_id': ADSET, 'creative': CREATIVE}}
        self.adsets = {ADSET: {'id': ADSET, 'name': 'וידאו', 'status': 'ACTIVE', 'daily_budget': '15000'}}
        self.creatives = {CREATIVE: {'id': CREATIVE, 'name': 'c', 'object_story_spec': {'page_id': '1', 'video_data': {'video_id': '9'}},
                                     'url_tags': 'utm_source=x', 'degrees_of_freedom_spec': {'creative_features_spec': {
                                         'image_animation': {'enroll_status': 'OPT_IN'}, 'inline_comment': {'enroll_status': 'OPT_IN'},
                                         'text_optimizations': {'enroll_status': 'OPT_OUT'}}}}}
        self.writes = []
        self.fail_next_write = None
        self.ignore_writes = False

    def read(self, token, node, fields):
        if node in self.ads:
            ad = dict(self.ads[node])
            ad['creative'] = dict(self.creatives[ad['creative']]) if 'creative{' in fields else None
            return {k: v for k, v in ad.items() if v is not None}
        return dict(self.adsets[node])

    def _write(self, kind, *args):
        self.writes.append((kind,) + args)
        if self.fail_next_write:
            error, self.fail_next_write = self.fail_next_write, None
            raise error

    def update(self, token, node, params):
        self._write('update', node, params)
        if self.ignore_writes:
            return {'success': True}
        target = self.ads.get(node) or self.adsets[node]
        target.update(params)
        return {'success': True}

    def create(self, token, edge, params):
        self._write('create', edge, params)
        new = str(int(max(self.creatives)) + 1)
        self.creatives[new] = {'id': new, **params}
        return {'id': new}

    def swap_creative(self, token, ad_id, creative_id):
        self._write('swap', ad_id, creative_id)
        self.ads[ad_id]['creative'] = creative_id
        return {'success': True}


def report(ready=True):
    return {'recommendations': {'rule_engine': {'proposals': [
        {'id': 'pause:' + AD, 'kind': 'pause_ad', 'entity_name': 'תוכן חדש 2', 'ready_for_approval': ready,
         'blockers': [] if ready else ['data_not_fresh'], 'warnings': []},
        {'id': 'scale:' + ADSET, 'kind': 'scale_adset_budget', 'ready_for_approval': True, 'blockers': [], 'warnings': [],
         'before': {'daily_budget_minor': 15000}, 'after': {'daily_budget_minor': 18000}}]}},
        'creative_drift': {'items': [{'ad_id': AD, 'name': 'תוכן חדש 2', 'creative_id': CREATIVE}]}}


class OwnerActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.meta = FakeMeta()
        self.now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        self.ready = True
        self.actions = self.make()

    def make(self):
        return OwnerActions(Path(self.temp.name), report=lambda: report(self.ready), transport=self.meta,
                            credentials=lambda: 'token', clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_writes_nothing_and_shows_exact_change(self):
        view = self.actions.preview('pause:' + AD)
        self.assertEqual(view['changes'], [{'field': 'סטטוס', 'from': 'פעילה', 'to': 'מושהית'}])
        self.assertEqual(self.meta.writes, [])

    def test_pause_confirm_writes_once_and_verifies(self):
        view = self.actions.preview('pause:' + AD)
        receipt = self.actions.confirm(view['confirm_token'])
        self.assertEqual(receipt['state'], 'verified')
        self.assertEqual(self.meta.writes, [('update', AD, {'status': 'PAUSED'})])
        self.assertEqual(list(self.actions.verified_changes()), ['pause:' + AD])
        with self.assertRaises(ActionBlocked):
            self.actions.confirm(view['confirm_token'])  # single use
        self.assertEqual(len(self.meta.writes), 1)

    def test_expired_token_writes_nothing(self):
        view = self.actions.preview('pause:' + AD)
        self.now += timedelta(minutes=11)
        with self.assertRaises(ActionBlocked):
            self.actions.confirm(view['confirm_token'])
        self.assertEqual(self.meta.writes, [])

    def test_state_changed_after_preview_writes_nothing(self):
        view = self.actions.preview('pause:' + AD)
        self.meta.ads[AD]['status'] = 'PAUSED'
        with self.assertRaises(ActionBlocked):
            self.actions.confirm(view['confirm_token'])
        self.assertEqual(self.meta.writes, [])
        self.assertEqual(self.actions.status()['receipts'][0]['state'], 'blocked')

    def test_not_ready_proposal_cannot_be_previewed(self):
        self.ready = False
        with self.assertRaises(ActionBlocked):
            self.actions.preview('pause:' + AD)

    def test_scale_is_bounded_and_checks_current_budget(self):
        view = self.actions.preview('scale:' + ADSET)
        self.assertEqual(view['changes'][0]['from'], '₪150')
        self.assertEqual(view['changes'][0]['to'], '₪180')
        self.assertEqual(self.actions.confirm(view['confirm_token'])['state'], 'verified')
        self.assertEqual(self.meta.adsets[ADSET]['daily_budget'], '18000')
        self.meta.adsets[ADSET]['daily_budget'] = '15000'
        self.meta.writes.clear()
        view = self.actions.preview('scale:' + ADSET)
        self.meta.adsets[ADSET]['daily_budget'] = '16000'
        with self.assertRaises(ActionBlocked):
            self.actions.confirm(view['confirm_token'])
        self.assertEqual(self.meta.writes, [])

    def test_unknown_outcome_is_not_retried(self):
        view = self.actions.preview('pause:' + AD)
        self.meta.fail_next_write = ProviderUnknown('x')
        receipt = self.actions.confirm(view['confirm_token'])
        self.assertEqual(receipt['state'], 'unknown')
        self.assertEqual(len(self.meta.writes), 1)

    def test_definite_rejection_is_rejected(self):
        view = self.actions.preview('pause:' + AD)
        self.meta.fail_next_write = ProviderRejected(details={'error_user_msg': 'no'})
        self.assertEqual(self.actions.confirm(view['confirm_token'])['state'], 'rejected')

    def test_readback_mismatch_is_not_verified(self):
        self.meta.ignore_writes = True
        view = self.actions.preview('pause:' + AD)
        self.assertEqual(self.actions.confirm(view['confirm_token'])['state'], 'not_verified')

    def test_restart_marks_inflight_unknown(self):
        view = self.actions.preview('pause:' + AD)
        receipt = self.actions.confirm(view['confirm_token'])
        receipt['state'] = 'inflight'
        self.actions._save(receipt)
        self.assertEqual(self.make().status()['receipts'][0]['state'], 'unknown')

    def test_enhancements_off_copies_creative_keeps_comments_and_undo_restores(self):
        view = self.actions.preview('drift:' + AD)
        self.assertEqual([c['field'] for c in view['changes']], ['אנימציה לתמונה'])
        receipt = self.actions.confirm(view['confirm_token'])
        self.assertEqual(receipt['state'], 'verified', receipt.get('detail'))
        created = next(w for w in self.meta.writes if w[0] == 'create')[2]
        features = created['degrees_of_freedom_spec']['creative_features_spec']
        self.assertEqual(features['image_animation'], {'enroll_status': 'OPT_OUT'})
        self.assertEqual(features['inline_comment'], {'enroll_status': 'OPT_IN'})
        self.assertEqual(created['object_story_spec'], self.meta.creatives[CREATIVE]['object_story_spec'])
        self.assertEqual(created['url_tags'], 'utm_source=x')
        undo = self.actions.preview('undo:' + receipt['id'])
        self.assertEqual(self.actions.confirm(undo['confirm_token'])['state'], 'verified')
        self.assertEqual(self.meta.ads[AD]['creative'], CREATIVE)
        with self.assertRaises(ActionBlocked):
            self.actions.preview('undo:' + receipt['id'])

    def test_pause_undo_resumes(self):
        receipt = self.actions.confirm(self.actions.preview('pause:' + AD)['confirm_token'])
        undo = self.actions.preview('undo:' + receipt['id'])
        self.assertEqual(undo['changes'][0]['to'], 'פעילה')
        self.assertEqual(self.actions.confirm(undo['confirm_token'])['state'], 'verified')
        self.assertEqual(self.meta.ads[AD]['status'], 'ACTIVE')

    def test_text_options_creative_is_copied_with_its_texts(self):
        self.meta.creatives[CREATIVE]['asset_feed_spec'] = {'bodies': [{'text': 'א', 'adlabels': [{'id': '1'}]}, {'text': 'ב'}],
                                                            'titles': [{'text': 'כותרת'}], 'optimization_type': 'DEGREES_OF_FREEDOM'}
        receipt = self.actions.confirm(self.actions.preview('drift:' + AD)['confirm_token'])
        self.assertEqual(receipt['state'], 'verified')
        created = next(w for w in self.meta.writes if w[0] == 'create')[2]
        self.assertEqual(created['asset_feed_spec'], {'optimization_type': 'DEGREES_OF_FREEDOM', 'bodies': [{'text': 'א'}, {'text': 'ב'}],
                                                      'titles': [{'text': 'כותרת'}]})

    def test_media_feed_creative_is_refused(self):
        self.meta.creatives[CREATIVE]['asset_feed_spec'] = {'images': [{'hash': 'x'}], 'bodies': [{'text': 'א'}]}
        with self.assertRaises(ActionBlocked):
            self.actions.preview('drift:' + AD)

    def test_catalog_creative_is_refused(self):
        self.meta.creatives[CREATIVE]['product_set_id'] = '77777'
        with self.assertRaises(ActionBlocked):
            self.actions.preview('drift:' + AD)

    def test_unknown_target_refused(self):
        for bad in ('delete:' + AD, 'pause:abc', 'scale:', None):
            with self.assertRaises(ActionBlocked):
                self.actions.preview(bad)


if __name__ == '__main__':
    unittest.main()
