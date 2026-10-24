"""Onboarding a new business with fake connections: plain-Hebrew steps, a rulebook the real loader accepts,
ROSERY's files untouched, and no live call anywhere."""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import business
from onboarding import LocalOnlyConnectors, NotConnected, Onboarding, proposed_thresholds

HERE = Path(__file__).resolve().parent


class FakeConnectors:
    def __init__(self, connected=True, currency='ILS'):
        self.connected, self.currency, self.calls = connected, currency, []

    def meta_status(self):
        self.calls.append('status')
        return {'connected': self.connected}

    def meta_accounts(self):
        self.calls.append('accounts')
        return [{'id': '555666777', 'name': 'חנות שתיים', 'currency': self.currency, 'timezone': 'Asia/Jerusalem'}]

    def meta_campaigns(self, account_id):
        self.calls.append(('campaigns', account_id))
        return [{'id': '120200000000000001', 'name': 'מכירות', 'objective': 'OUTCOME_SALES'}]

    def shopify_identity(self, domain):
        self.calls.append(('shop', domain))
        return {'domain': domain, 'currency': 'ILS'}


BUSINESS = {'name': 'חנות שתיים', 'what_you_sell': 'כלי בית מעוצבים', 'currency': 'ILS', 'timezone': 'Asia/Jerusalem', 'language': 'he', 'country': 'IL'}
ANSWERS = {'aov': 250, 'gross_margin': 0.6, 'copy_form': 'plural', 'store_facts': ['משלוח חינם מעל ₪300', 'החזרה עד 30 יום'],
           'free_shipping_from': 300, 'returns_days': 30, 'never_write': ['הכי זול בארץ']}


def walk(flow, shop='shop-two.myshopify.com'):
    flow.submit('business', BUSINESS)
    flow.submit('meta_connect', {})
    flow.submit('meta_account', {'account_id': '555666777'})
    flow.submit('meta_campaign', {'campaign_id': '120200000000000001'})
    flow.submit('shopify', {'domain': shop})
    return flow.submit('questionnaire', ANSWERS)


class OnboardingFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / 'shop-two'
        self.fake = FakeConnectors()
        self.flow = Onboarding(self.folder, self.fake)

    def tearDown(self):
        self.temp.cleanup()

    def test_steps_go_in_order_in_plain_hebrew(self):
        state = self.flow.status()
        self.assertEqual((state['step'], state['steps'][0]['state'], state['live_calls']), ('business', 'now', 0))
        self.assertTrue(all(re.search('[א-ת]', s['title']) for s in state['steps']))
        with self.assertRaises(ValueError) as caught:
            self.flow.submit('questionnaire', ANSWERS)  # not yet
        self.assertIn('פרטי העסק', str(caught.exception))
        self.assertEqual(walk(self.flow)['step'], 'review')

    def test_meta_not_connected_stops_at_the_connection_step(self):
        flow = Onboarding(Path(self.temp.name) / 'other', FakeConnectors(connected=False))
        flow.submit('business', BUSINESS)
        with self.assertRaises(NotConnected):
            flow.submit('meta_connect', {})

    def test_account_in_another_currency_is_refused(self):
        flow = Onboarding(Path(self.temp.name) / 'usd', FakeConnectors(currency='USD'))
        flow.submit('business', BUSINESS)
        flow.submit('meta_connect', {})
        with self.assertRaises(ValueError) as caught:
            flow.submit('meta_account', {'account_id': '555666777'})
        self.assertIn('לא ממירה מטבעות', str(caught.exception))

    def test_wrong_answers_are_refused_with_a_reason(self):
        for step, body in (('business', {**BUSINESS, 'currency': 'shekel'}), ('business', {**BUSINESS, 'extra': 1}),
                           ('business', {**BUSINESS, 'what_you_sell': ''})):
            with self.assertRaises(ValueError):
                self.flow.submit(step, body)
        walk_until = self.flow
        walk_until.submit('business', BUSINESS)
        walk_until.submit('meta_connect', {})
        with self.assertRaises(ValueError):
            walk_until.submit('meta_account', {'account_id': '999'})  # not in the list
        walk_until.submit('meta_account', {'account_id': '555666777'})
        walk_until.submit('meta_campaign', {'campaign_id': None})
        with self.assertRaises(ValueError):
            walk_until.submit('shopify', {'domain': 'https://shop-two.com'})
        walk_until.submit('shopify', {'domain': None})
        for bad in ({**ANSWERS, 'gross_margin': 60}, {**ANSWERS, 'copy_form': 'formal'}, {**ANSWERS, 'store_facts': ['a · b']},
                    {k: v for k, v in ANSWERS.items() if k != 'aov'}):
            with self.assertRaises(ValueError):
                walk_until.submit('questionnaire', bad)

    def test_review_shows_every_number_and_how_it_was_derived(self):
        walk(self.flow)
        review = self.flow.status()['review']
        t = review['thresholds']
        self.assertEqual((t['breakeven_roas'], t['breakeven_cpa']), (1.67, 150))  # 1/0.6 and 250×0.6
        self.assertTrue(t['fail_below'] < t['hold_up_to'] < t['winner_above'])
        self.assertIn('הצעה, לא עובדה', ' '.join(review['derived']))
        self.assertTrue(any('בלשון רבים' in r['text'] for r in review['rules']))
        with self.assertRaises(ValueError):
            self.flow.review({'fail_below': 9, 'hold_up_to': 3})  # thresholds must rise
        with self.assertRaises(ValueError):
            self.flow.review({'breakeven_cpa': 1})  # derived, not a choice

    def test_rosery_proportions_reproduce_rosery_s_own_thresholds(self):
        t = proposed_thresholds(315, 0.8)  # ROSERY's order value and margin
        self.assertEqual((t['fail_below'], t['hold_up_to'], t['winner_above'], t['default_window_fail_below']), (2.3, 3.0, 3.7, 2.6))
        self.assertEqual((t['min_spend'], t['min_daily_spend'], t['watch_from'], t['extended_limit']), (200, 35, 150, 300))
        self.assertEqual(t['breakeven_cpa'], 252)  # product margin only; ROSERY's own ₪190 also counts other costs

    def test_approval_writes_only_inside_the_new_folder_and_the_real_loader_accepts_the_rules(self):
        rosery_rules = {p.name: p.read_bytes() for p in (HERE / 'rules').iterdir()}
        walk(self.flow)
        result = self.flow.approve({'fail_below': 2.2})
        self.assertEqual(result['step'], 'done')
        b = business.load(self.folder / 'business.json')
        self.assertEqual((b.id, b.meta_account, b.shop, b.meta_sales_campaign, b.rules_path),
                         ('b555666777', '555666777', 'shop-two.myshopify.com', '120200000000000001', (self.folder / 'rules').resolve()))
        self.assertEqual({p.name: p.read_bytes() for p in (HERE / 'rules').iterdir()}, rosery_rules)  # ROSERY untouched
        code = ('import json,rulebook;b=rulebook.load_rulebook();'
                'print(json.dumps([b["status"],b.get("reason"),b.get("detail"),b["business_id"] if b["status"]=="available" else None,'
                'sorted(b["rules"]),b["rules"].get("roas_ladder",{}).get("params")]))')
        done = subprocess.run([sys.executable, '-B', '-c', code], cwd=HERE, capture_output=True, text=True, timeout=120,
                              env={**os.environ, business.ENV: str(self.folder / 'business.json')})
        self.assertEqual(done.returncode, 0, done.stderr)
        status, reason, detail, owner, ids, ladder = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual((status, reason, detail, owner), ('available', None, None, 'b555666777'))
        rosery_ids = sorted(r['id'] for r in json.loads((HERE / 'rules' / 'rosery-rulebook.json').read_text(encoding='utf-8'))['rules'])
        self.assertEqual(sorted(set(ids) - {'owner_forbidden'}), rosery_ids)  # every rule the code may read exists
        self.assertEqual(ladder['fail_below'], 2.2)  # the owner's change
        profile = json.loads((self.folder / 'profile.json').read_text(encoding='utf-8'))
        self.assertEqual((profile['aov'], profile['gross_margin']), (250, 0.6))

    def test_an_existing_data_folder_is_never_onboarded_over(self):
        existing = Path(self.temp.name) / 'rosery-data'
        existing.mkdir()
        (existing / 'live-reports.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            Onboarding(existing, self.fake)

    def test_without_connections_nothing_is_called(self):
        flow = Onboarding(Path(self.temp.name) / 'cloud', LocalOnlyConnectors())
        flow.submit('business', BUSINESS)
        self.assertEqual(flow.status()['meta'], {'connected': False})
        with self.assertRaises(NotConnected):
            flow.submit('meta_connect', {})


class OnboardingServerTests(unittest.TestCase):
    def setUp(self):
        from onboarding_server import make_onboarding_server
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_onboarding_server(0, Path(self.temp.name) / 'new', FakeConnectors())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.token = re.search("const token='([^']+)'", urllib.request.urlopen(self.base).read().decode()).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def call(self, path, body=None, token=None, origin=None):
        headers = {'X-Local-Session': token or self.token}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': origin or self.base})
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_whole_flow_over_http_with_session_and_origin_checks(self):
        self.assertEqual(self.call('/api/onboarding', token='bad')[0], 403)
        self.assertEqual(self.call('/api/onboarding/business', BUSINESS, origin='http://evil.example')[0], 403)
        code, value = self.call('/api/onboarding/business', {**BUSINESS, 'currency': 'x'})
        self.assertEqual(code, 400)
        self.assertRegex(value['error'], '[א-ת]')
        for path, body in (('/api/onboarding/business', BUSINESS), ('/api/onboarding/meta-connect', {}),
                           ('/api/onboarding/meta-account', {'account_id': '555666777'}), ('/api/onboarding/meta-campaign', {'campaign_id': None}),
                           ('/api/onboarding/shopify', {'domain': None}), ('/api/onboarding/questionnaire', ANSWERS)):
            code, value = self.call(path, body)
            self.assertEqual(code, 200, value)
        self.assertEqual(value['step'], 'review')
        code, preview = self.call('/api/onboarding/review', {'overrides': {'min_spend': 180}})
        self.assertEqual((code, preview['thresholds']['min_spend']), (200, 180))
        code, done = self.call('/api/onboarding/approve', {'overrides': {'min_spend': 180}})
        self.assertEqual((code, done['step'], done['business']['shop']), (200, 'done', None))

    def test_the_screen_script_parses(self):
        page = (HERE / 'onboarding.html').read_text(encoding='utf-8')
        script = '\n'.join(re.findall(r'<script>(.*?)</script>', page, re.S))
        done = subprocess.run(['node', '--check', '-'], input=script.encode('utf-8'), capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr.decode('utf-8'))
        self.assertNotIn('randomUUID', script)


if __name__ == '__main__':
    unittest.main()
