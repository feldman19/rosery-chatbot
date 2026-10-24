import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from outcome_tracking import OutcomeDenied, OutcomeTracker
from server import make_server

ACCOUNT = '393059510164294'
D = date(2026, 10, 10)
NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def row(ad, adset, day, spend, purchases=None, revenue=None, actions=True):
    r = {'ad_id': ad, 'adset_id': adset, 'campaign_id': 'c1', 'date_start': day.isoformat(),
         'date_stop': day.isoformat(), 'spend': str(spend)}
    if actions:
        r['actions'] = [{'action_type': 'omni_purchase', 'value': str(purchases)}] if purchases else []
        r['action_values'] = [{'action_type': 'omni_purchase', 'value': str(revenue)}] if revenue else []
    return r


def entity(identity, status, updated=None, **extra):
    evidence = {'status': 'entity_update_observed', 'updated_time': updated} if updated else {'status': 'not_collected'}
    return {'id': identity, 'status': status, 'change_evidence': evidence, **extra}


def report(today, ads, adsets):
    return {'account_id': ACCOUNT, 'windows': {'1': {'until': (today - timedelta(days=1)).isoformat()},
                                              '7': {'ads': ads, 'adsets': adsets}}}


def data(today, rows):
    since, until = today - timedelta(days=7), today - timedelta(days=1)
    return {'account_id': ACCOUNT, 'timezone': 'Asia/Jerusalem', 'complete': True,
            'since': since.isoformat(), 'until': until.isoformat(),
            'rows': [r for r in rows if since.isoformat() <= r['date_start'] <= until.isoformat()]}


def pause_today(saving=110):
    return {'items': [{'kind': 'approve', 'action': 'PAUSE', 'proposal_id': 'pause:a1',
                       'headline': 'להשהות את "Ad One"', 'saving_per_day': saving}]}


def scale_today():
    return {'items': [{'kind': 'approve', 'action': 'SCALE', 'proposal_id': 'scale:s2',
                       'headline': 'להגדיל את "Set Two" מ-₪180 ל-₪216 ליום'}]}


class OutcomeTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tracker = OutcomeTracker(Path(self.temp.name) / 'outcomes.sqlite3')
        # a1 loses money before the change and is paused on D; a2 keeps running.
        self.rows = []
        for i in range(1, 15):
            for day in (D - timedelta(days=i), D + timedelta(days=i)):
                if day < D:
                    self.rows.append(row('a1', 's1', day, 110, 1, 80))
                self.rows.append(row('a2', 's1', day, 50, 1, 200))
        self.rows.append(row('a2', 's1', D, 50, 1, 200))

    def tearDown(self):
        self.temp.cleanup()

    def pause_report(self, today, paused):
        ad = entity('a1', 'PAUSED', '2026-10-10T11:20:00+00:00', adset_id='s1') if paused else entity('a1', 'ACTIVE', adset_id='s1')
        return report(today, [ad, entity('a2', 'ACTIVE', adset_id='s1')], [entity('s1', 'ACTIVE')])

    def open_screen(self, today, paused=True, rows=None):
        rep = self.pause_report(today, paused)
        self.tracker.refresh(data(today, self.rows if rows is None else rows), rep)
        return self.tracker.list(rep)['items']

    def test_marked_pause_goes_waiting_then_preliminary_then_final(self):
        offered = self.pause_report(D, paused=False)
        self.tracker.record_offers(pause_today(), offered)
        action = self.tracker.mark('pause:a1', offered)
        self.assertEqual(action['source'], 'owner_marked')
        self.assertEqual(self.tracker.mark('pause:a1', offered)['id'], action['id'])
        cards = self.open_screen(D + timedelta(days=1))
        self.assertEqual(cards[0]['phase'], 'waiting')
        self.assertEqual(cards[0]['days_left'], 3)
        self.assertEqual(cards[0]['change_day_source'], 'meta_updated_time')
        for offset in range(2, 4):
            self.open_screen(D + timedelta(days=offset))
        card = self.open_screen(D + timedelta(days=4))[0]
        self.assertEqual(card['phase'], 'preliminary')
        self.assertIn('רכישות מאוחרות', card['warnings'][0])
        self.assertIn('צפוי היה לחסוך ₪110 ליום', card['lines'][0])
        self.assertIn('₪110 → ₪0', card['lines'][1])
        for offset in range(5, 11):
            card = self.open_screen(D + timedelta(days=offset))[0]
        self.assertEqual(card['phase'], 'final')
        self.assertEqual(card['warnings'], [])
        adset = card['comparison']['adset']
        self.assertEqual((adset['before']['spend_per_day'], adset['after']['spend_per_day']), (160, 50))
        self.assertEqual((adset['before']['purchases'], adset['after']['purchases']), (6, 3))

    def test_missing_actions_stay_missing_not_zero(self):
        rows = [r for r in self.rows if not (r['ad_id'] == 'a2' and r['date_start'] == (D + timedelta(days=2)).isoformat())]
        rows.append(row('a2', 's1', D + timedelta(days=2), 50, actions=False))
        offered = self.pause_report(D, paused=False)
        self.tracker.record_offers(pause_today(), offered)
        self.tracker.mark('pause:a1', offered)
        for offset in range(1, 5):
            card = self.open_screen(D + timedelta(days=offset), rows=rows)[0]
        self.assertIsNone(card['comparison']['adset']['after']['purchases'])
        self.assertIn('רכישות 6 → חסר', card['lines'][2])

    def test_days_not_seen_while_open_are_incomplete(self):
        offered = self.pause_report(D, paused=False)
        self.tracker.record_offers(pause_today(), offered)
        self.tracker.mark('pause:a1', offered)
        # Screen opened only 9 days later: the days before the change already left Meta's window.
        card = self.open_screen(D + timedelta(days=9))[0]
        self.assertEqual(card['phase'], 'incomplete')
        self.assertNotIn('comparison', card)
        self.assertIn('חסרים ימים', ' '.join(card['lines']))

    def test_auto_detected_pause_asks_owner_and_no_removes_it(self):
        self.tracker.record_offers(pause_today(), self.pause_report(D, paused=False))
        cards = self.open_screen(D + timedelta(days=1))
        self.assertTrue(cards[0]['needs_answer'])
        self.assertEqual(cards[0]['source'], 'auto_detected')
        self.tracker.answer(cards[0]['id'], 'no')
        self.assertEqual(self.open_screen(D + timedelta(days=2)), [])
        with self.assertRaises(OutcomeDenied):
            self.tracker.answer(cards[0]['id'], 'yes')

    def test_auto_detected_then_confirmed_keeps_tracking(self):
        self.tracker.record_offers(pause_today(), self.pause_report(D, paused=False))
        card = self.open_screen(D + timedelta(days=1))[0]
        self.tracker.answer(card['id'], 'yes')
        card = self.open_screen(D + timedelta(days=2))[0]
        self.assertFalse(card['needs_answer'])
        self.assertEqual(card['phase'], 'waiting')

    def test_change_before_offer_is_not_detected(self):
        self.tracker.record_offers(pause_today(), self.pause_report(D, paused=False))
        rep = report(D + timedelta(days=1), [entity('a1', 'PAUSED', '2026-10-05T08:00:00+00:00', adset_id='s1')], [entity('s1', 'ACTIVE')])
        self.tracker.refresh(data(D + timedelta(days=1), self.rows), rep)
        self.assertEqual(self.tracker.list(rep)['items'], [])

    def test_mark_not_seen_in_meta_becomes_unconfirmed(self):
        offered = self.pause_report(D, paused=False)
        self.tracker.record_offers(pause_today(), offered)
        self.tracker.mark('pause:a1', offered)
        card = self.open_screen(D + timedelta(days=1), paused=False)[0]
        self.assertEqual((card['phase'], card['verification']), ('unverified', 'not_seen'))
        card = self.open_screen(D + timedelta(days=2), paused=False)[0]
        self.assertEqual(card['verification'], 'not_confirmed')
        self.assertIn('לא נראה', card['lines'][0])

    def test_adset_paused_instead_is_a_different_change(self):
        offered = self.pause_report(D, paused=False)
        self.tracker.record_offers(pause_today(), offered)
        self.tracker.mark('pause:a1', offered)
        rep = report(D + timedelta(days=1), [entity('a1', 'ACTIVE', adset_id='s1')], [entity('s1', 'PAUSED', '2026-10-10T09:00:00+00:00')])
        self.tracker.refresh(data(D + timedelta(days=1), self.rows), rep)
        card = self.tracker.list(rep)['items'][0]
        self.assertEqual(card['phase'], 'different_change')
        self.assertNotIn('comparison', card)

    def test_scale_budget_readback(self):
        before = report(D, [], [entity('s2', 'ACTIVE', daily_budget_minor=18000, budget_known=True)])
        self.tracker.record_offers(scale_today(), before)
        self.tracker.mark('scale:s2', before)
        wrong = report(D + timedelta(days=1), [], [entity('s2', 'ACTIVE', '2026-10-10T09:00:00+00:00', daily_budget_minor=25000, budget_known=True)])
        self.tracker.refresh(data(D + timedelta(days=1), []), wrong)
        card = self.tracker.list(wrong)['items'][0]
        self.assertEqual(card['phase'], 'different_change')
        self.assertIn('₪250', card['lines'][0])
        right = report(D + timedelta(days=2), [], [entity('s2', 'ACTIVE', '2026-10-10T09:00:00+00:00', daily_budget_minor=21600, budget_known=True)])
        self.tracker.refresh(data(D + timedelta(days=2), []), right)
        card = self.tracker.list(right)['items'][0]
        self.assertEqual((card['verification'], card['phase']), ('confirmed', 'waiting'))

    def test_second_change_in_same_adset_is_flagged_mixed(self):
        offered = self.pause_report(D, paused=False)
        self.tracker.record_offers({'items': pause_today()['items'] + [
            {'kind': 'approve', 'proposal_id': 'pause:a2', 'headline': 'להשהות את "Ad Two"', 'saving_per_day': 50}]}, offered)
        self.tracker.mark('pause:a1', offered)
        self.tracker.mark('pause:a2', offered)
        rep = report(D + timedelta(days=4), [entity('a1', 'PAUSED', '2026-10-10T11:20:00+00:00', adset_id='s1'),
                                             entity('a2', 'PAUSED', '2026-10-10T11:30:00+00:00', adset_id='s1')], [entity('s1', 'ACTIVE')])
        for offset in range(1, 5):
            today = D + timedelta(days=offset)
            rep['windows']['1']['until'] = (today - timedelta(days=1)).isoformat()
            self.tracker.refresh(data(today, self.rows), rep)
        cards = self.tracker.list(rep)['items']
        self.assertTrue(all(any('מעורבבת' in w for w in c['warnings']) for c in cards))

    def test_mark_requires_a_current_offer(self):
        with self.assertRaises(OutcomeDenied):
            self.tracker.mark('pause:a1', self.pause_report(D, paused=False))
        self.tracker.record_offers(pause_today(), self.pause_report(D, paused=False))
        with self.assertRaises(OutcomeDenied):
            self.tracker.mark('pause:a1', self.pause_report(D + timedelta(days=3), paused=False))

    def test_module_never_talks_to_meta(self):
        source = Path(__file__).with_name('outcome_tracking.py').read_text(encoding='utf-8')
        for word in ('urllib', 'requests', 'graph.facebook', 'http'):
            self.assertNotIn(word, source.replace('https://', ''))


class OutcomeHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaigner-test-', dir=Path(__file__).parent)
        self.server = make_server(0, self.temp.name, workspace='manager')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.token = re.search("const token='([^']+)'", urllib.request.urlopen(self.base).read().decode()).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, body=None, token=True):
        headers = {'X-Local-Session': self.token if token else 'bad'}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': self.base})
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_outcome_routes_are_local_session_exact_and_write_nothing_to_meta(self):
        self.assertEqual(self.request('/api/outcomes', token=False)[0], 403)
        code, value = self.request('/api/outcomes')
        self.assertEqual(code, 200)
        self.assertEqual((value['items'], value['provider_writes']), ([], 0))
        self.assertEqual(self.request('/api/outcomes/mark', {'proposal_id': 'pause:1', 'extra': 1})[0], 400)
        code, value = self.request('/api/outcomes/mark', {'proposal_id': 'pause:1'})
        self.assertEqual(code, 409)
        self.assertIn('רענן', value['error'])
        self.assertEqual(self.request('/api/outcomes/answer', {'action_id': 'x', 'answer': 'maybe'})[0], 409)

    def test_simple_screen_has_done_button_and_results_section(self):
        page = urllib.request.urlopen(self.base).read().decode()
        self.assertIn('ביצעתי', page)
        self.assertIn('id="history-title"', page)


if __name__ == '__main__':
    unittest.main()
