import json
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from creative_drift import CreativeDrift
from rulebook import load_rulebook
from server import make_server

ACCOUNT = '393059510164294'
T1 = datetime(2026, 10, 23, 8, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 28, 8, tzinfo=timezone.utc)


def spec(**on):
    features = {name: {'enroll_status': 'OPT_OUT'} for name in ('music', 'standard_enhancements', 'image_touchups', 'inline_comment')}
    features.update({name: {'enroll_status': status} for name, status in on.items()})
    return {'creative_features_spec': features}


def ad(ad_id, dof='missing', creative_id='c1'):
    creative = {'id': creative_id}
    if dof != 'missing':
        creative['degrees_of_freedom_spec'] = dof
    return {'id': ad_id, 'name': 'Ad ' + ad_id, 'adset': {'name': 'Set A'}, 'creative': creative}


def data(*ads, verified=True):
    return {'active_ads': {'verified': verified, 'complete': True, 'account_id': ACCOUNT, 'ads': list(ads),
                           'checked_at': '2026-10-23T08:00:00+00:00'}}


class CreativeDriftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.drift = CreativeDrift(Path(self.temp.name) / 'drift.sqlite3')
        self.rules = load_rulebook()

    def tearDown(self):
        self.temp.cleanup()

    def test_all_off_is_clean_and_relevant_comments_are_allowed(self):
        result = self.drift.check(data(ad('1', spec(inline_comment='OPT_IN'))), self.rules, T1)
        self.assertEqual((result['status'], result['items']), ('ready', []))
        self.assertEqual(result['allowed_on'], ['inline_comment'])

    def test_feature_turned_on_later_is_reported_with_rule_quote(self):
        self.drift.check(data(ad('1', spec())), self.rules, T1)
        result = self.drift.check(data(ad('1', spec(music='OPT_IN'))), self.rules, T2)
        item = result['items'][0]
        self.assertEqual(item['features'][0]['feature'], 'music')
        self.assertEqual(item['features'][0]['kind'], 'turned_on')
        self.assertEqual(item['features'][0]['since'], T2.isoformat())
        self.assertEqual(item['features'][0]['label'], 'מוזיקה')
        self.assertIn('Advantage+', result['rule']['quote'])
        self.assertIn('selected_ad_ids=1', item['link'])

    def test_new_feature_added_already_on_is_flagged(self):
        self.drift.check(data(ad('1', spec())), self.rules, T1)
        result = self.drift.check(data(ad('1', spec(product_browsing='OPT_IN'))), self.rules, T2)
        feature = result['items'][0]['features'][0]
        self.assertEqual((feature['feature'], feature['kind'], feature['label']), ('product_browsing', 'new_on', 'עיון במוצרים'))

    def test_on_at_first_read_is_still_against_the_rule(self):
        result = self.drift.check(data(ad('1', spec(standard_enhancements='OPT_IN'))), self.rules, T1)
        self.assertEqual(result['items'][0]['features'][0]['kind'], 'on_at_first_read')

    def test_missing_spec_is_unknown_not_clean(self):
        result = self.drift.check(data(ad('1'), ad('2', None)), self.rules, T1)
        self.assertEqual(result['items'], [])
        self.assertEqual([u['ad_id'] for u in result['unknown']], ['1', '2'])

    def test_unverified_inventory_is_unavailable(self):
        result = self.drift.check(data(ad('1', spec(music='OPT_IN')), verified=False), self.rules, T1)
        self.assertEqual((result['status'], result['items']), ('unavailable', []))

    def test_turned_off_in_meta_clears_the_card(self):
        self.drift.check(data(ad('1', spec(music='OPT_IN'))), self.rules, T1)
        self.assertEqual(self.drift.check(data(ad('1', spec())), self.rules, T2)['items'], [])

    def test_keep_hides_only_that_state(self):
        self.drift.check(data(ad('1', spec(music='OPT_IN'))), self.rules, T1)
        kept = self.drift.keep('1', data(ad('1', spec(music='OPT_IN'))))
        self.assertEqual((kept['kept'], kept['provider_writes']), (['music'], 0))
        self.assertEqual(self.drift.check(data(ad('1', spec(music='OPT_IN'))), self.rules, T2)['items'], [])
        later = self.drift.check(data(ad('1', spec(music='OPT_IN', image_touchups='OPT_IN'))), self.rules, T2)
        self.assertEqual([f['feature'] for f in later['items'][0]['features']], ['image_touchups'])
        with self.assertRaises(ValueError):
            self.drift.keep('9', data(ad('1', spec())))

    def test_kept_feature_turned_off_then_on_again_is_shown(self):
        self.drift.check(data(ad('1', spec(music='OPT_IN'))), self.rules, T1)
        self.drift.keep('1', data(ad('1', spec(music='OPT_IN'))))
        self.assertEqual(self.drift.check(data(ad('1', spec())), self.rules, T2)['items'], [])
        again = self.drift.check(data(ad('1', spec(music='OPT_IN'))), self.rules, T2)
        self.assertEqual([f['feature'] for f in again['items'][0]['features']], ['music'])
        self.assertEqual(again['items'][0]['features'][0]['kind'], 'turned_on')

    def test_unknown_feature_status_is_reported_not_dropped(self):
        weird = spec()
        weird['creative_features_spec']['music'] = {'enroll_status': 'SOMETHING_NEW'}
        item = self.drift.check(data(ad('1', weird)), self.rules, T1)['items'][0]
        self.assertEqual((item['features'], [u['feature'] for u in item['unclear']]), ([], ['music']))

    def test_without_verified_rulebook_no_rule_claim(self):
        result = self.drift.check(data(ad('1', spec(music='OPT_IN'))), {'status': 'unavailable'}, T1)
        self.assertEqual((result['rule'], result['rule_status']), (None, 'unavailable'))
        self.assertEqual(len(result['items']), 1)

    def test_first_read_wording_does_not_claim_who_changed_it(self):
        feature = self.drift.check(data(ad('1', spec(music='OPT_IN'))), self.rules, T1)['items'][0]['features'][0]
        self.assertEqual(feature['kind_label'], 'נצפה מופעל בקריאה הראשונה')
        self.assertNotIn('מטא', feature['kind_label'])

    def test_swapped_creative_is_reported(self):
        self.drift.check(data(ad('1', spec(), 'c1')), self.rules, T1)
        item = self.drift.check(data(ad('1', spec(), 'c2')), self.rules, T2)['items'][0]
        self.assertEqual(item['creative_replaced']['before'], 'c1')
        self.drift.keep('1', data(ad('1', spec(), 'c2')))
        self.assertEqual(self.drift.check(data(ad('1', spec(), 'c2')), self.rules, T2)['items'], [])

    def test_readbacks_are_the_starting_state(self):
        readback = {'id': '1', 'status': 'ACTIVE', 'creative': {'id': 'c1', 'degrees_of_freedom_spec': spec(standard_enhancements='OPT_IN')}}
        listed = {'ads': [{'ad_id': '2', 'creative': {'id': 'c9', 'degrees_of_freedom_spec': spec()}}]}
        self.drift.import_readbacks([('a-readback-2026-10-19.json', readback), ('b-readback-2026-10-19.json', listed)])
        result = self.drift.check(data(ad('1', spec(standard_enhancements='OPT_IN')), ad('2', spec(music='OPT_IN'), 'c9')), self.rules, T2)
        kinds = {i['ad_id']: i['features'][0]['kind'] for i in result['items']}
        self.assertEqual(kinds, {'1': 'on_at_first_read', '2': 'turned_on'})

    def test_module_never_talks_to_meta(self):
        source = Path(__file__).with_name('creative_drift.py').read_text(encoding='utf-8')
        for word in ('urllib', 'requests', 'graph.facebook', 'import http'):
            self.assertNotIn(word, source)

    def test_meta_reads_request_the_enhancement_field(self):
        source = Path(__file__).with_name('meta_direct.py').read_text(encoding='utf-8')
        self.assertEqual(source.count('degrees_of_freedom_spec}'), 2)


class CreativeDriftHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaigner-test-', dir=Path(__file__).parent)
        self.server = make_server(0, self.temp.name, workspace='manager')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.page = urllib.request.urlopen(self.base).read().decode()
        self.token = re.search("const token='([^']+)'", self.page).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def post(self, body):
        req = urllib.request.Request(self.base + '/api/creative-drift/keep', data=json.dumps(body).encode(),
                                     headers={'X-Local-Session': self.token, 'Content-Type': 'application/json', 'Origin': self.base})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_keep_route_is_exact_and_local(self):
        self.assertEqual(self.post({'ad_id': '1', 'extra': 1})[0], 400)
        self.assertEqual(self.post({'ad_id': 'abc'})[0], 400)
        self.assertEqual(self.post({'ad_id': '123'})[0], 409)

    def test_simple_screen_has_drift_section(self):
        self.assertIn('שיפורי Advantage+ מופעלים לבדיקה', self.page)
        self.assertNotIn('מטא שינתה משהו בלי אישור', self.page)
        self.assertIn('לכיבוי במטא', self.page)


if __name__ == '__main__':
    unittest.main()
