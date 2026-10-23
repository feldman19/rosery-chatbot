from datetime import date, datetime, timezone
import json
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

    def test_judged_when_follow_up_spend_reaches_600_but_never_before_7_days(self):
        strong_funnel = ad(7, 250, 1, 250, 20, 12)  # raw ROAS 1.0 but a strong funnel
        self.shadow.record(report('2026-10-01', [strong_funnel], {'1007': 'PAUSE'}))
        for n in range(2, 12):  # ₪200 a day: ₪600 after 3 days, but judged only after the 7th
            self.shadow.record(report(f'2026-10-{n:02d}', [], {}, [ad(7, 200, 2, 800, 3, 2)]))
        self.assertEqual(self.shadow.summary(date(2026, 10, 6))['judged'], [])  # 4 days: too early
        stop = [j for j in self.shadow.summary(date(2026, 10, 20))['judged'] if j['topic'] == 'stop']
        self.assertEqual((len(stop), stop[0]['follow_days'], stop[0]['follow_spend']), (1, 7, 1400))
        self.assertEqual((stop[0]['follow_roas'], stop[0]['outcome']), (4.0, 'score_right'))

    def test_slow_ad_waits_for_600_and_gives_up_after_21_days(self):
        self.shadow.record(report('2026-10-01', [ad(12, 250, 1, 250, 20, 12)], {'1012': 'PAUSE'}))
        for n in range(2, 23):  # ₪20 a day never reaches ₪600
            self.shadow.record(report(f'2026-10-{n:02d}', [], {}, [ad(12, 20, 0, 0, 1, 0)]))
        self.assertEqual(self.shadow.summary(date(2026, 10, 15))['judged'], [])  # still waiting
        result = self.shadow.summary(date(2026, 10, 30))
        self.assertEqual((result['not_judgeable'], result['score_right'] + result['rules_right']), (1, 0))

    def test_paused_ad_without_follow_up_is_not_judged(self):
        self.shadow.record(report('2026-10-01', [ad(8, 250, 1, 250, 20, 12)], {'1008': 'PAUSE'}))
        for n in range(2, 23):  # the account's days were read; the paused ad simply had no delivery
            self.shadow.record(report(f'2026-10-{n:02d}', [], {}, []))
        result = self.shadow.summary(date(2026, 10, 30))
        self.assertEqual(result['not_judgeable'], 1)
        self.assertEqual(result['score_right'] + result['rules_right'], 0)


    def test_one_profitable_day_and_ten_missing_days_is_not_a_result(self):
        self.shadow.record(report('2026-10-01', [ad(9, 250, 1, 250, 20, 12)], {'1009': 'PAUSE'}))
        self.shadow.record(report('2026-10-02', [], {}, [ad(9, 150, 2, 900, 3, 2)]))
        result = self.shadow.summary(date(2026, 10, 20))
        self.assertEqual((result['score_right'], result['rules_right'], result['not_judgeable']), (0, 0, 0))

    def test_an_ad_disagreeing_on_stop_and_scale_counts_once(self):
        hot = ad(10, 450, 6, 2700, 40, 25)  # the score may call it a winner while the rules say pause
        self.shadow.record(report('2026-10-01', [hot], {'1010': 'PAUSE'}))
        for n in range(2, 13):
            self.shadow.record(report(f'2026-10-{n:02d}', [], {}, [ad(10, 100, 1, 400, 3, 2)]))
        judged = self.shadow.summary(date(2026, 10, 20))['judged']
        self.assertEqual(len([j for j in judged if j['ad_id'] == '1010']), 1)


class VariantNoCtrTests(unittest.TestCase):
    """Hypothesis ctr_weight_not_needed: the variant without CTR is recorded and judged beside the main score."""
    LOW_CTR_HOT = (450, 6, 2700, 40, 25, 30, 30000)  # strong sales and funnel, CTR 0.1%: main says success, variant winner

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.shadow = ScoreShadow(Path(self.temp.name) / 's.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_main_weights_unchanged_and_variant_ignores_ctr(self):
        self.assertEqual(ad_score.WEIGHTS, {'roas': 45, 'ic': 20, 'atc': 15, 'lpv': 10, 'ctr': 10})
        self.assertEqual(ad_score.VARIANT_NO_CTR, {'roas': 45, 'ic': 25, 'atc': 20, 'lpv': 10, 'ctr': 0})
        base = ad_score.baseline(BASE_ADS)
        low, high = (ad_score.score(ad_score.metrics(ad(1, 300, 2, 600, 15, 8, impressions=n)), base, ad_score.VARIANT_NO_CTR) for n in (2000, 90000))
        self.assertEqual(low['score'], high['score'])

    def follow(self, i, days, spend, revenue):
        for n in range(2, 2 + days):
            self.shadow.record(report(f'2026-10-{n:02d}', [], {}, [ad(i, spend, 1, revenue, 3, 2)]))

    def test_variant_recorded_beside_the_main_score_and_decision_kept(self):
        self.shadow.record(report('2026-10-01', [ad(20, *self.LOW_CTR_HOT[:5], clicks=30, impressions=30000)], {'1020': 'HOLD'}))
        import sqlite3, json
        with sqlite3.connect(self.shadow.path) as db:
            call = json.loads(db.execute('SELECT payload FROM calls').fetchone()[0])
        self.assertEqual((call['rules'], call['score']['verdict'], call['variant']['verdict']), ('HOLD', 'success', 'winner'))
        self.assertEqual(self.shadow.summary(date(2026, 10, 2))['variant']['today_disagreements'], 1)

    def test_disagreement_judged_after_600_spend_and_7_days(self):
        self.shadow.record(report('2026-10-01', [ad(21, *self.LOW_CTR_HOT[:5], clicks=30, impressions=30000)], {'1021': 'HOLD'}))
        self.follow(21, 10, 100, 400)  # ROAS 4 later: "scale" was right → the variant was right
        self.assertEqual(self.shadow.summary(date(2026, 10, 6))['variant']['judged'], [])
        v = self.shadow.summary(date(2026, 10, 20))['variant']
        self.assertEqual((v['variant_right'], v['main_right']), (1, 0))
        self.assertEqual((v['judged'][0]['follow_days'], v['judged'][0]['follow_spend']), (7, 700))
        # The rulebook-vs-score hypothesis does not count this ad: rules HOLD and main "success" are not stop/scale.
        self.assertEqual(self.shadow.summary(date(2026, 10, 20))['judged'], [])

    def test_unread_days_stay_unknown_and_unjudged(self):
        self.shadow.record(report('2026-10-01', [ad(22, *self.LOW_CTR_HOT[:5], clicks=30, impressions=30000)], {'1022': 'HOLD'}))
        self.follow(22, 2, 400, 1600)
        v = self.shadow.summary(date(2026, 10, 25))['variant']
        self.assertEqual((v['variant_right'], v['main_right'], v['not_judgeable']), (0, 0, 0))

    def test_calls_from_before_the_variant_are_skipped(self):
        import sqlite3, json
        with sqlite3.connect(self.shadow.path) as db:
            db.execute('INSERT INTO calls VALUES (?,?,?)', ('1', '2026-10-01', json.dumps(
                {'name': 'old', 'rules': 'PAUSE', 'score': {'verdict': 'hold'}, 'agree': False})))
        self.assertEqual(self.shadow.judge_variant(date(2026, 10, 30)), [])

    def test_hypothesis_seeded_into_an_existing_journal_and_gated(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'h.json'
            path.write_text(json.dumps([{'id': 'ad_score_beats_3day_roas', 'status': 'supported', 'evidence': {'for': 13}}]), encoding='utf-8')
            h = Hypotheses(path)
            self.assertEqual(h.get('ad_score_beats_3day_roas')['status'], 'supported')  # history kept
            self.assertEqual(h.get('ctr_weight_not_needed')['status'], 'testing')
            self.assertEqual(h.update_evidence('ctr_weight_not_needed', 7, 2, 0, []), 'testing')   # too few
            self.assertEqual(h.update_evidence('ctr_weight_not_needed', 9, 6, 0, []), 'testing')   # chance explains it
            self.assertEqual(h.update_evidence('ctr_weight_not_needed', 13, 2, 3, []), 'supported')
            self.assertEqual(h.update_evidence('ctr_weight_not_needed', 2, 13, 3, []), 'not_supported')
            self.assertEqual(len(h.list()), 2)


class MissingFunnelTests(unittest.TestCase):
    def test_missing_funnel_gives_no_verdict_and_baseline_ignores_unknowns(self):
        row = ad(11, 450, 0, 0, 0, 0)
        row['funnel'] = {'add_to_cart': None, 'initiate_checkout': None, 'link_clicks': 100, 'impressions': 9000}
        result = ad_score.score(ad_score.metrics(row), ad_score.baseline(BASE_ADS))
        self.assertEqual((result['verdict'], result['score']), ('unknown', None))
        base = ad_score.baseline(BASE_ADS + [dict(row, purchases=3, attributed_revenue=600)])
        self.assertAlmostEqual(base['atc_to_purchase'], 12 / 90)


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
