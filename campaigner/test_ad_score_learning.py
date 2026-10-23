from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

import ad_score
from hypotheses import Hypotheses, gate, sign_test
from score_shadow import ScoreShadow


def ad(i, spend, purchases, revenue, atc, ic, clicks=200, impressions=15000, lpv=180):
    return {'id': str(1000 + i), 'name': f'ad {i}', 'adset_name': 'set', 'spend': spend, 'purchases': purchases,
            'attributed_revenue': revenue, 'landing_page_views': lpv,
            'funnel': {'add_to_cart': atc, 'initiate_checkout': ic, 'link_clicks': clicks, 'impressions': impressions}}


BASE_ADS = [ad(i, 300, 2, 600, 15, 8) for i in range(6)]


def report(until, ads3, decisions, day_rows=()):
    return {'windows': {'3': {'until': until, 'ads': ads3}, '7': {'ads': BASE_ADS},
                        '1': {'since': until, 'until': until, 'ads': list(day_rows)}},
            'recommendations': {'rule_engine': {'decisions': [{'entity_id': k, 'action': v} for k, v in decisions.items()]}}}


class AdScoreTests(unittest.TestCase):
    def setUp(self):
        self.base = ad_score.baseline(BASE_ADS)

    def test_spec_acceptance_cases(self):
        untested = ad_score.score(ad_score.metrics(ad(1, 60, 0, 0, 0, 0)), self.base)
        self.assertEqual(untested['verdict'], 'untested')  # ₪60, 0 purchases → not "kill"
        weak = ad_score.score(ad_score.metrics(ad(2, 250, 1, 300, 0, 0, clicks=40, impressions=10000)), self.base)
        self.assertLess(weak['score'], 50)  # one purchase, nothing else, CTR 0.4%
        keep = ad_score.score(ad_score.metrics(ad(3, 180, 0, 0, 4, 2)), self.base)
        self.assertEqual(keep['verdict'], 'continue')  # 4 carts, 2 checkouts at ₪180
        self.assertEqual(ad_score.score(ad_score.metrics(ad(3, 180, 0, 0, 4, 2)), self.base), keep)  # deterministic
        self.assertEqual(len(keep['reasons']), 2)

    def test_missing_purchases_are_unknown_not_zero(self):
        self.assertIsNone(ad_score.metrics({'spend': 100, 'purchases': None}))


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.shadow = ScoreShadow(Path(self.temp.name) / 's.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_disagreement_is_judged_on_the_next_eleven_days(self):
        strong_funnel = ad(7, 250, 1, 250, 20, 12)  # raw ROAS 1.0 but a strong funnel
        self.shadow.record(report('2026-10-01', [strong_funnel], {'1007': 'PAUSE'}))
        for n in range(2, 13):
            self.shadow.record(report(f'2026-10-{n:02d}', [], {}, [ad(7, 50, 1, 200, 3, 2)]))
        result = self.shadow.summary(date(2026, 10, 20))
        stop = [j for j in result['judged'] if j['topic'] == 'stop']
        self.assertEqual(len(stop), 1)
        self.assertEqual(stop[0]['follow_roas'], 4.0)
        self.assertEqual(stop[0]['outcome'], 'score_right')

    def test_paused_ad_without_follow_up_is_not_judged(self):
        self.shadow.record(report('2026-10-01', [ad(8, 250, 1, 250, 20, 12)], {'1008': 'PAUSE'}))
        result = self.shadow.summary(date(2026, 10, 20))
        self.assertEqual(result['not_judgeable'], 1)
        self.assertEqual(result['score_right'] + result['rules_right'], 0)


class GateTests(unittest.TestCase):
    def test_gate_needs_data_and_a_result_chance_does_not_explain(self):
        self.assertEqual(gate(6, 1)[0], 'testing')       # too few
        self.assertEqual(gate(9, 6)[0], 'testing')       # chance explains it
        self.assertEqual(gate(13, 2)[0], 'supported')
        self.assertEqual(gate(2, 13)[0], 'not_supported')
        self.assertAlmostEqual(sign_test(5, 5), 1.0)

    def test_journal_seed_and_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            h = Hypotheses(Path(d) / 'h.json', clock=lambda: datetime(2026, 10, 23, tzinfo=timezone.utc))
            self.assertEqual(h.list()[0]['status'], 'testing')
            self.assertEqual(h.update_evidence('ad_score_beats_3day_roas', 13, 2, 4, []), 'supported')
            self.assertEqual(h.list()[0]['evidence']['not_judgeable'], 4)


class OutsideCheckTests(unittest.TestCase):
    def test_claims_without_a_real_link_are_dropped_and_prompt_has_no_account_ids(self):
        import outside_check
        seen = {}
        def runner(text):
            seen['text'] = text
            return {'summary': 'ידוע חלקית', 'findings': [
                {'claim': 'א', 'source': 'Meta', 'url': 'https://www.facebook.com/business/help/1', 'relation': 'supports', 'note': ''},
                {'claim': 'ב', 'source': 'בלוג', 'url': 'http://example.com', 'relation': 'context', 'note': ''},
                {'claim': 'ג', 'source': 'x', 'url': '', 'relation': 'supports', 'note': ''}]}
        h = {'id': 'x', 'statement': 'השערה', 'test': 'בדיקה', 'backtest': {'ads': 64}, 'evidence': {'for': 1, 'against': 2}}
        result = outside_check.research(h, runner=runner)
        self.assertEqual([f['claim'] for f in result['findings']], ['א'])
        self.assertEqual(result['kind'], 'outside_context_not_proof')
        self.assertNotIn('393059510164294', seen['text'])
        self.assertIn('UNTRUSTED DATA', seen['text'])

    def test_outside_research_never_moves_the_status(self):
        with tempfile.TemporaryDirectory() as d:
            h = Hypotheses(Path(d) / 'h.json')
            h.set_outside('ad_score_beats_3day_roas', 'done', {'summary': 's', 'findings': [{'claim': 'c'}]})
            item = h.get('ad_score_beats_3day_roas')
            self.assertEqual((item['status'], item['sources'][0]['claim']), ('testing', 'c'))


if __name__ == '__main__':
    unittest.main()
