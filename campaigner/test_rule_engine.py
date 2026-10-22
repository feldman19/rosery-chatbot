import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chat_rules_context import chat_rule_context
from creative_rules_check import check_creatives
from rule_engine import (HOLD, KEEP_TESTING, NO_DATA, PAUSE, SCALE, SUCCESS, UNTESTED,
                         evaluate_report, thresholds)
from rulebook import ACCOUNT, DIRECTORY, load_rulebook
from today_plan import build_today

NOW = datetime(2026, 10, 22, 9, tzinfo=timezone.utc)
BOOK = load_rulebook()


def ad(identity, spend, purchases, revenue, **extra):
    row = {'id': identity, 'name': 'מודעה ' + identity, 'adset_id': extra.pop('adset_id', 's1'),
           'adset_name': 'בדיקות 20.10', 'campaign_id': 'c1', 'campaign_name': 'ROSERY מכירות',
           'objective': 'OUTCOME_SALES', 'result_type': 'purchase', 'configured_active': True,
           'spend': spend, 'purchases': purchases, 'attributed_revenue': revenue,
           'funnel': {'add_to_cart': None, 'initiate_checkout': None, 'link_ctr': None}}
    row.update(extra)
    return row


def report(ads, adsets=None, fresh=True):
    window = {'since': '2026-10-19', 'until': '2026-10-21', 'days': 3, 'partial': False, 'ads': ads,
              'adsets': adsets if adsets is not None else [
                  {'id': 's1', 'daily_budget_minor': 15000, 'budget_known': True,
                   'change_evidence': {'status': 'entity_update_observed',
                                       'updated_time': (NOW - timedelta(days=10)).isoformat()}}]}
    return {'account_id': ACCOUNT, 'freshness': {'state': 'fresh' if fresh else 'stale'},
            'windows': {'3': window, '7': {'partial': False, 'ads': ads}}}


def decision(result, identity):
    return next(d for d in result['decisions'] if d['entity_id'] == identity)


class RulebookTests(unittest.TestCase):
    def test_every_rule_quotes_its_source(self):
        self.assertEqual(BOOK['status'], 'available')
        self.assertGreaterEqual(len(BOOK['rules']), 30)
        self.assertFalse(BOOK['live_action_allowed'])

    def test_changed_source_disables_rulebook(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'rules'
            shutil.copytree(DIRECTORY, target)
            path = target / '03-חוקי-ניתוח-ביצועים.md'
            path.write_text(path.read_text(encoding='utf-8').replace('2.3', '1.5'), encoding='utf-8')
            book = load_rulebook(target)
            self.assertEqual(book['status'], 'unavailable')
            self.assertEqual(evaluate_report(report([ad('a', 300, 0, 0)]), book, now=NOW)['status'], 'blocked')

    def test_rule_without_matching_quote_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'rules'
            shutil.copytree(DIRECTORY, target)
            data = json.loads((target / 'rosery-rulebook.json').read_text(encoding='utf-8'))
            data['rules'][0]['source_excerpt'] = 'סקייל +50% מותר'
            (target / 'rosery-rulebook.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(load_rulebook(target)['status'], 'unavailable')

    def test_other_tenant_gets_nothing(self):
        self.assertEqual(load_rulebook(business_id='other')['reason'], 'tenant_mismatch')


class EngineTests(unittest.TestCase):
    def run_one(self, row, **kwargs):
        return decision(evaluate_report(report([row]), BOOK, now=NOW, **kwargs), row['id'])

    def test_roas_ladder(self):
        # 2.0 fail, 2.8 hold, 3.5 success, 4.5 winner (default attribution raises fail to 2.6).
        self.assertEqual(self.run_one(ad('a', 300, 2, 600))['action'], PAUSE)
        self.assertEqual(self.run_one(ad('a', 300, 3, 840))['action'], HOLD)
        self.assertEqual(self.run_one(ad('a', 300, 3, 1050))['action'], SUCCESS)
        self.assertEqual(self.run_one(ad('a', 300, 4, 1350))['action'], SCALE)

    def test_boundaries_follow_table(self):
        self.assertEqual(self.run_one(ad('a', 300, 3, 900))['action'], HOLD)  # exactly 3 -> hold
        self.assertEqual(self.run_one(ad('a', 300, 3, 1110))['action'], SUCCESS)  # exactly 3.7 -> not winner

    def test_click_only_attribution_uses_owner_threshold(self):
        row = ad('a', 300, 2, 750)  # ROAS 2.5
        self.assertEqual(self.run_one(row)['action'], PAUSE)
        self.assertEqual(self.run_one(row, attribution_click_only=True)['action'], HOLD)

    def test_no_kill_below_200(self):
        result = self.run_one(ad('a', 180, 0, 0))
        self.assertEqual(result['action'], KEEP_TESTING)
        self.assertIn('200', result['why'])

    def test_spend_gate_means_untested(self):
        result = self.run_one(ad('a', 60, 0, 0))
        self.assertEqual(result['action'], UNTESTED)
        self.assertIn('לא כישלון', result['why'])

    def test_rescue_signals_extend_to_300(self):
        rescued = ad('a', 250, 0, 0, funnel={'add_to_cart': 3, 'initiate_checkout': 1, 'link_ctr': 0.01})
        self.assertEqual(self.run_one(rescued)['action'], KEEP_TESTING)
        by_ctr = ad('a', 250, 0, 0, funnel={'add_to_cart': 0, 'initiate_checkout': 0, 'link_ctr': 0.02})
        self.assertEqual(self.run_one(by_ctr)['action'], KEEP_TESTING)
        weak = ad('a', 250, 0, 0, funnel={'add_to_cart': 1, 'initiate_checkout': 0, 'link_ctr': 0.008})
        self.assertEqual(self.run_one(weak)['action'], PAUSE)
        half_known = ad('a', 250, 0, 0, funnel={'add_to_cart': 1, 'initiate_checkout': 0, 'link_ctr': None})
        self.assertTrue(any('חסרים' in t for t in self.run_one(half_known)['data_does_not_support']))
        over = ad('a', 320, 0, 0, funnel={'add_to_cart': 5, 'initiate_checkout': 2, 'link_ctr': 0.03})
        self.assertEqual(self.run_one(over)['action'], PAUSE)

    def test_unknown_rescue_signals_are_disclosed(self):
        result = self.run_one(ad('a', 250, 0, 0))
        self.assertEqual(result['action'], PAUSE)
        self.assertTrue(any('חסרים' in text for text in result['data_does_not_support']))

    def test_single_purchase_is_not_a_winner(self):
        result = self.run_one(ad('a', 220, 1, 1000))
        self.assertEqual(result['action'], SUCCESS)
        self.assertIn('single_purchase_not_verdict', [r['rule_id'] for r in result['rules']])

    def test_missing_data_is_not_zero(self):
        self.assertEqual(self.run_one(ad('a', 300, None, None))['action'], NO_DATA)
        self.assertEqual(self.run_one(ad('a', 300, 2, None))['action'], NO_DATA)

    def test_retargeting_doubles_thresholds(self):
        row = ad('a', 300, 4, 1350, campaign_name='ריטרגטינג DPA')  # ROAS 4.5
        result = self.run_one(row)
        self.assertTrue(result['retargeting'])
        self.assertEqual(result['action'], PAUSE)
        limits, _ = thresholds(BOOK, retargeting=True, click_only_attribution=True)
        self.assertEqual(limits['fail_below'], 4.6)

    def test_scale_is_plus_twenty_percent_on_adset(self):
        result = evaluate_report(report([ad('a', 300, 4, 1350)]), BOOK, now=NOW)
        scale = next(p for p in result['proposals'] if p['kind'] == 'scale_adset_budget')
        self.assertEqual(scale['entity_id'], 's1')
        self.assertEqual(scale['after']['daily_budget_minor'], 18000)
        self.assertTrue(scale['ready_for_approval'])

    def test_scale_blocked_inside_stabilization_window(self):
        sets = [{'id': 's1', 'daily_budget_minor': 15000, 'budget_known': True,
                 'change_evidence': {'status': 'entity_update_observed',
                                     'updated_time': (NOW - timedelta(days=1)).isoformat()}}]
        result = evaluate_report(report([ad('a', 300, 4, 1350)], sets), BOOK, now=NOW)
        scale = result['proposals'][0]
        self.assertIn('changed_within_stabilization_window', scale['blockers'])
        self.assertFalse(scale['ready_for_approval'])

    def test_unknown_budget_blocks_scale(self):
        result = evaluate_report(report([ad('a', 300, 4, 1350)], []), BOOK, now=NOW)
        self.assertIn('adset_daily_budget_unknown', result['proposals'][0]['blockers'])

    def test_pause_targets_ad_and_warns_before_darkening_adset(self):
        result = evaluate_report(report([ad('a', 300, 0, 0)]), BOOK, now=NOW)
        pause = result['proposals'][0]
        self.assertEqual((pause['kind'], pause['entity_type'], pause['after']), ('pause_ad', 'ad', {'status': 'PAUSED'}))
        self.assertTrue(pause['warnings'])
        two = evaluate_report(report([ad('a', 300, 0, 0), ad('b', 300, 4, 1350)]), BOOK, now=NOW)
        self.assertFalse(next(p for p in two['proposals'] if p['kind'] == 'pause_ad')['warnings'])

    def test_stale_data_never_ready_for_approval(self):
        result = evaluate_report(report([ad('a', 300, 0, 0)], fresh=False), BOOK, now=NOW)
        self.assertEqual(result['status'], 'stale_data')
        self.assertFalse(any(p['ready_for_approval'] for p in result['proposals']))

    def test_partial_window_blocks(self):
        data = report([ad('a', 300, 0, 0)])
        data['windows']['3']['partial'] = True
        self.assertEqual(evaluate_report(data, BOOK, now=NOW)['status'], 'blocked')

    def test_paused_ads_are_skipped_and_nothing_executes(self):
        result = evaluate_report(report([ad('a', 300, 0, 0, configured_active=False)]), BOOK, now=NOW)
        self.assertEqual(result['decisions'], [])
        self.assertFalse(result['live_action_allowed'])

    def test_link_uses_required_ads_manager_format(self):
        url = self.run_one(ad('123', 300, 0, 0))['ads_manager_url']
        self.assertTrue(url.startswith('https://www.facebook.com/adsmanager/manage/ads/edit?act=393059510164294'))
        self.assertTrue(url.endswith('selected_ad_ids=123'))

    def test_catalog_dependency_alert(self):
        rows = [ad('a', 950, 5, 3000, campaign_name='קטלוג'), ad('b', 50, 0, 0, campaign_name='קטלוג')]
        alerts = evaluate_report(report(rows), BOOK, now=NOW)['account_alerts']
        self.assertTrue(any(a['kind'] == 'catalog_single_ad_dependency' for a in alerts))


class TodayAndChatTests(unittest.TestCase):
    def test_today_puts_money_leaks_first_and_never_executes(self):
        rows = [ad('a', 300, 4, 1350), ad('b', 400, 0, 0), ad('c', 160, 0, 0)]
        today = build_today(evaluate_report(report(rows), BOOK, now=NOW))
        self.assertEqual([i['action'] for i in today['items']], [PAUSE, SCALE, KEEP_TESTING])
        self.assertIn('מ-₪150 ל-₪180 ליום', today['items'][1]['headline'])
        self.assertFalse(today['live_action_allowed'])

    def test_blocked_today_explains_why(self):
        today = build_today(evaluate_report({'account_id': 'x'}, BOOK, now=NOW))
        self.assertIn('אין היום המלצות', today['summary'])

    def test_chat_context_is_scoped_to_selected_ad(self):
        context = chat_rule_context(report([ad('a', 300, 0, 0), ad('b', 300, 4, 1350)]), 'b', rulebook=BOOK)
        self.assertEqual([d['entity_id'] for d in context['decisions']], ['b'])


class CreativeRuleTests(unittest.TestCase):
    def test_copy_rules(self):
        rows = [{'asset_id': 'x', 'name': 'וריאציה 2', 'headlines': ['שרשרת לגבר 🔥'],
                 'primary_texts': ['הבסט סלר שלנו מנירוסטה, 20% הנחה', 'SING UP FOR AN EXTRA 5% OFF',
                                   'משלוח חינם מעל ₪300'],
                 'call_to_action': 'LEARN_MORE', 'format': 'carousel'}]
        found = {i['rule_id'] for i in check_creatives(rows, BOOK)['issues']}
        self.assertTrue({'headline_no_emoji', 'no_superlatives', 'no_material_emphasis', 'known_asset_typo',
                         'store_facts', 'offer_in_headline', 'cta_shop_now', 'no_carousel',
                         'no_variation_label', 'full_copy_set'} <= found)

    def test_clean_copy_passes(self):
        rows = [{'asset_id': 'y', 'headlines': ['20% הנחה על שרשראות'] * 5,
                 'primary_texts': ['שרשרת שתלבש כל יום. משלוח חינם מעל ₪350.'] * 5, 'call_to_action': 'SHOP_NOW'}]
        result = check_creatives(rows, BOOK)
        self.assertEqual(result['status'], 'passed_text_checks')
        self.assertTrue(result['manual_checks'])


if __name__ == '__main__':
    unittest.main()
