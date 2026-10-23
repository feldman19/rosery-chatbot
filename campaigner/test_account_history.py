import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

import meta_direct
from account_history import AccountHistory, _live_rows, chat_context, patterns, rank, traits
from rulebook import load_rulebook
from server import make_server

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def purchase(count, value):
    return {'actions': [{'action_type': 'omni_purchase', 'value': str(count)}],
            'action_values': [{'action_type': 'omni_purchase', 'value': str(value)}]}


def live_row(ad, spend, count=None, value=None, present=True, name=None):
    row = {'ad_id': ad, 'ad_name': name or 'Ad ' + ad, 'campaign_name': 'C', 'spend': str(spend), 'actions_present': present}
    if present and count is not None:
        row.update(purchase(count, value))
    return row


def history_file(rows, creatives=None, checked=NOW):
    return {'verified': True, 'checked_at': checked.isoformat(), 'windows': {'all': {'rows': rows}, '90': {'rows': rows[:2]}},
            'creatives': creatives or {}, 'creatives_failed': 0}


def ranked(spend, purchases, revenue, name='x', creative=None):
    return {'ad_id': name, 'name': name, 'spend': spend, 'purchases': purchases, 'revenue': revenue,
            'traits': traits(name, creative)}


class TraitTests(unittest.TestCase):
    def test_format_from_creative(self):
        self.assertEqual(traits('a', {'video_id': '1'})['format'], 'video')
        self.assertEqual(traits('a', {'object_story_spec': {'link_data': {'child_attachments': [{}]}}})['format'], 'carousel')
        self.assertEqual(traits('a', {'product_set_id': '9'})['format'], 'catalog')
        self.assertEqual(traits('a', {'object_story_spec': {'link_data': {'image_hash': 'h'}}})['format'], 'image')

    def test_text_traits_and_sources(self):
        t = traits('שרשרת זהב', {'body': '20% הנחה על כל השרשראות ✨\nרק היום ₪199', 'call_to_action_type': 'SHOP_NOW'})
        self.assertEqual((t['has_discount'], t['has_price'], t['emoji'], t['cta'], t['category']), (True, True, True, 'SHOP_NOW', 'שרשראות'))
        self.assertEqual(t['hook'], '20% הנחה על כל השרשראות ✨')
        self.assertEqual(t['source'], 'creative')

    def test_name_only_is_unknown_not_no(self):
        t = traits('ביקורת לקוח R01')
        self.assertTrue(t['testimonial'])
        self.assertIsNone(t['has_discount'])
        self.assertIsNone(traits('מודעה רגילה')['testimonial'])


class RankingTests(unittest.TestCase):
    def test_missing_actions_stay_unknown_and_present_without_purchase_is_zero(self):
        rows = _live_rows({'rows': [live_row('1', 300, present=False), live_row('2', 300)]})
        self.assertIsNone(rows[0]['purchases'])
        self.assertEqual((rows[1]['purchases'], rows[1]['revenue']), (0.0, 0.0))

    def test_thresholds_and_order(self):
        rows = [ranked(199, 5, 2000, 'low-spend'), ranked(500, 2, 3000, 'two-purchases'), ranked(500, None, None, 'unknown'),
                ranked(400, 4, 1200, 'b'), ranked(300, 3, 1500, 'a')]
        eligible, top, bottom = rank(rows, breakeven_cpa=190)
        self.assertEqual([r['name'] for r in eligible], ['b', 'a'])
        self.assertEqual([r['name'] for r in top], ['a', 'b'])
        self.assertEqual(top[0]['cpa'], 100)
        self.assertTrue(top[0]['below_breakeven'])

    def test_groups_need_five_ads_and_ten_purchases(self):
        video = [ranked(100, 2, 500, f'v{i}', {'video_id': '1'}) for i in range(5)]
        image = [ranked(100, 3, 200, f'i{i}', {'image_hash': 'h'}) for i in range(4)]
        result = patterns(video + image + [ranked(100, None, None, 'u')])
        fmt = next(d for d in result['dimensions'] if d['dimension'] == 'פורמט')
        values = {v['value']: v for v in fmt['values']}
        self.assertEqual((values['וידאו']['roas'], values['וידאו']['enough_data']), (5.0, True))
        self.assertEqual((values['תמונה']['roas'], values['תמונה']['enough_data']), (None, False))
        self.assertEqual(result['compared_ads'], 9)
        self.assertEqual(result['baseline_roas'], round(3300 / 900, 2))


class AccountHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_summary_from_daily_read(self):
        rows = [live_row('1', 600, 6, 3000), live_row('2', 400, 3, 600), live_row('3', 900, present=False)]
        (self.dir / 'account-history.json').write_text(json.dumps(history_file(rows, {'1': {'video_id': 'v', 'body': 'מבצע!'}})), encoding='utf-8')
        summary = AccountHistory(self.dir).summary(load_rulebook())
        window = summary['windows']['all']
        self.assertEqual(summary['source'], 'meta_history_read')
        self.assertEqual(window['top'][0]['name'], 'Ad 1')
        self.assertEqual(window['top'][0]['traits']['פורמט'], 'וידאו')
        self.assertEqual(window['coverage']['ads_purchases_unknown'], 1)
        self.assertEqual(window['coverage']['spend_purchases_unknown'], 900)
        self.assertEqual(summary['thresholds']['breakeven_cpa'], 190)
        self.assertIn('90', summary['windows'])

    def test_falls_back_to_stored_memory_snapshot(self):
        db = sqlite3.connect(self.dir / 'decision-memory.sqlite3')
        db.execute('CREATE TABLE memory_snapshots(id TEXT, business TEXT, account TEXT, captured_at TEXT, payload TEXT)')
        payload = {'rows': [{'id': '1', 'name': 'וידאו ביקורת', 'campaign_name': 'C', 'spend': 500, 'purchases': 5, 'revenue': 2500, 'format': 'video'},
                            {'id': '2', 'name': 'x', 'campaign_name': 'C', 'spend': 300, 'purchases': None, 'revenue': None, 'format': 'media_not_resolved'}],
                   'limits': ['All history available through the connector.']}
        db.execute('INSERT INTO memory_snapshots VALUES(?,?,?,?,?)', ('s', 'rosery', '393059510164294', '2026-10-20T16:33:51+00:00', json.dumps(payload)))
        db.commit(); db.close()
        summary = AccountHistory(self.dir).summary()
        self.assertEqual(summary['source'], 'decision_memory_snapshot')
        self.assertEqual(summary['windows']['all']['top'][0]['traits']['פורמט'], 'וידאו')
        self.assertEqual(summary['windows']['all']['coverage']['ads_purchases_unknown'], 1)
        self.assertNotIn('90', summary['windows'])

    def test_no_history_is_unavailable(self):
        self.assertEqual(AccountHistory(self.dir).summary()['status'], 'unavailable')

    def test_refresh_once_a_day_and_failure_keeps_last_copy(self):
        calls = []
        def reader(_):
            calls.append(1)
            return history_file([live_row('1', 600, 6, 3000)], checked=NOW)
        history = AccountHistory(self.dir, reader=reader)
        self.assertTrue(history.refresh_if_stale(NOW))
        self.assertFalse(history.refresh_if_stale(NOW + timedelta(hours=2)))
        def broken(_):
            raise ValueError('token=SECRET')
        history.reader = broken
        self.assertFalse(history.refresh_if_stale(NOW + timedelta(hours=30)))
        self.assertNotIn('SECRET', history.last_error)
        self.assertEqual(history.summary()['windows']['all']['top'][0]['name'], 'Ad 1')
        self.assertEqual(len(calls), 1)

    def test_chat_context_fits_budget(self):
        rows = [live_row(str(i), 300 + i, 5, 2000 + i * 10, name='מודעה עם שם ארוך מאוד ' * 3 + str(i)) for i in range(60)]
        creatives = {str(i): {'video_id': 'v', 'body': 'טקסט פתיחה ארוך מאוד של המודעה ' * 4} for i in range(60)}
        (self.dir / 'account-history.json').write_text(json.dumps(history_file(rows, creatives)), encoding='utf-8')
        block = chat_context(AccountHistory(self.dir).summary(load_rulebook()))
        self.assertLessEqual(len(json.dumps(block, ensure_ascii=False)), 7000)
        self.assertTrue(block['windows']['all']['top'])


class MetaHistoryReadTests(unittest.TestCase):
    def test_read_merges_streams_and_only_uses_allowed_get_paths(self):
        calls = []
        def fake_graph(token, path, params=None):
            calls.append((path, params.get('fields', '')))
            if path.endswith('/insights') and 'spend' in params['fields']:
                return {'data': [{'ad_id': '1', 'ad_name': 'A', 'spend': '500'}, {'ad_id': '2', 'ad_name': 'B', 'spend': '50'}]}
            if path.endswith('/insights'):
                return {'data': [{'ad_id': '1', **purchase(4, 2000)}]}
            return {'data': [{'id': '1', 'name': 'A', 'creative': {'id': 'c', 'video_id': 'v'}}]}
        with patch.object(meta_direct, 'graph', fake_graph), patch.object(meta_direct, 'get_token', return_value='t'), \
                patch.object(meta_direct, 'identity', return_value={'timezone_offset_hours_utc': 3}):
            result = meta_direct.read_account_history('unused')
        rows = {r['ad_id']: r for r in result['windows']['all']['rows']}
        self.assertTrue(rows['1']['actions_present'])
        self.assertFalse(rows['2']['actions_present'])
        self.assertEqual(result['creatives']['1']['video_id'], 'v')
        self.assertEqual(result['creatives_requested'], 1)
        self.assertEqual({p for p, _ in calls}, {'act_393059510164294/insights', 'act_393059510164294/ads'})

    def test_actions_without_delivery_are_counted_not_merged(self):
        def fake_graph(token, path, params=None):
            if path.endswith('/insights') and 'spend' in params['fields']:
                return {'data': [{'ad_id': '1', 'spend': '500'}]}
            if path.endswith('/insights'):
                return {'data': [{'ad_id': '1', **purchase(1, 100)}, {'ad_id': '7', **purchase(2, 300)}]}
            return {'data': []}
        with patch.object(meta_direct, 'graph', fake_graph), patch.object(meta_direct, 'get_token', return_value='t'), \
                patch.object(meta_direct, 'identity', return_value={'timezone_offset_hours_utc': 3}):
            result = meta_direct.read_account_history('unused')
        self.assertEqual([r['ad_id'] for r in result['windows']['90']['rows']], ['1'])
        self.assertEqual(result['windows']['90']['actions_without_delivery'], 1)

    def test_duplicate_ad_rows_fail_closed(self):
        def fake_graph(token, path, params=None):
            return {'data': [{'ad_id': '1', 'spend': '5'}, {'ad_id': '1', 'spend': '5'}]}
        with patch.object(meta_direct, 'graph', fake_graph), patch.object(meta_direct, 'get_token', return_value='t'), \
                patch.object(meta_direct, 'identity', return_value={'timezone_offset_hours_utc': 3}):
            with self.assertRaises(ValueError):
                meta_direct.read_account_history('unused')

    def test_module_has_no_write_surface(self):
        source = Path(__file__).with_name('account_history.py').read_text(encoding='utf-8')
        for word in ('urllib', 'requests', 'graph.facebook', 'method=', "'POST'"):
            self.assertNotIn(word, source)


class ChatContextHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaigner-test-', dir=Path(__file__).parent)
        rows = [live_row(str(i), 300 + i, 5, 2000, name='Ad ' + str(i)) for i in range(40)]
        Path(self.temp.name, 'account-history.json').write_text(json.dumps(history_file(rows)), encoding='utf-8')
        self.server = make_server(0, self.temp.name, workspace='manager')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.page = urllib.request.urlopen(self.base).read().decode()
        self.token = re.search("const token='([^']+)'", self.page).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_account_question_gets_history_within_limit(self):
        seen = {}
        def capture(self_, question, request_id, context):
            seen['context'] = context
            return {'id': request_id, 'state': 'running'}
        with patch('server.ClaudeChat.submit', capture):
            req = urllib.request.Request(self.base + '/api/claude-chat', data=json.dumps(
                {'question': 'מה עבד לאורך זמן?', 'request_id': '220ce122-8159-4b49-a03b-217d5fc8312c', 'ad_id': None}).encode(),
                headers={'X-Local-Session': self.token, 'Content-Type': 'application/json', 'Origin': self.base})
            urllib.request.urlopen(req).read()
        history = seen['context']['account_history']
        self.assertEqual(history['status'], 'ready')
        self.assertEqual(len(history['windows']['all']['top']) > 0, True)
        self.assertLessEqual(len(json.dumps(seen['context'], ensure_ascii=False, sort_keys=True)), 18000)

    def test_ask_tab_suggests_history_questions(self):
        self.assertIn('איזו מודעה הכי משתלמת לאורך זמן?', self.page)


if __name__ == '__main__':
    unittest.main()
