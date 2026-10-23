from datetime import date
import unittest

import launch_strategy
from rulebook import load_rulebook


def report(adsets, ads):
    return {'windows': {'7': {'adsets': adsets, 'ads': ads}}}


def asset(i, name='file.png'):
    return {'id': f'local_{i:032d}', 'name': name}


def row(i, product='צמיד בגוון זהב', angle='מתנה לחג'):
    return {'asset_id': f'local_{i:032d}', 'observed_product': product, 'angle': angle}


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rulebook()

    def test_default_is_a_new_dated_adset_with_rule_citation(self):
        result = launch_strategy.plan([asset(1), asset(2)], [row(1), row(2)], report([], []), self.rules, today=date(2026, 10, 23))
        self.assertEqual(len(result['adsets']), 1)
        group = result['adsets'][0]
        self.assertEqual(group['target'], {'mode': 'new'})
        self.assertEqual(group['label'], 'צמידים | מתנה לחג | 23/10')
        self.assertIn('אד-סט חדש מתוארך', group['rationale'])
        self.assertIn('5–8', group['rationale'])
        self.assertIn('new_creatives_new_dated_adset', result['citations'])

    def test_existing_adset_only_under_the_rule_and_with_owner_question(self):
        sets = [{'id': '111', 'name': 'צמידים ישנים', 'configured_active': True, 'campaign_id': 'c'}]
        ads = [{'id': f'a{i}', 'adset_id': '111', 'name': 'צמיד שעווה', 'configured_active': True} for i in range(3)]
        result = launch_strategy.plan([asset(1)], [row(1)], report(sets, ads), self.rules)
        group = result['adsets'][0]
        self.assertEqual(group['target'], {'mode': 'existing', 'adset_id': '111', 'rule_cited': 'existing_adset_reuse'})
        self.assertFalse(group['budget_required'])
        self.assertIn('existing_adset_reuse', result['citations'])
        self.assertIn('זווית', result['owner_questions'][0])

    def test_full_or_unknown_or_other_product_adsets_are_not_reused(self):
        sets = [{'id': '111', 'name': 'צמידים', 'configured_active': True, 'campaign_id': 'c'},
                {'id': '222', 'name': 'שרשראות', 'configured_active': True, 'campaign_id': 'c'},
                {'id': '333', 'name': 'צמידים נוספים', 'configured_active': True, 'campaign_id': 'c'}]
        ads = ([{'id': f'a{i}', 'adset_id': '111', 'name': 'צמיד', 'configured_active': True} for i in range(5)] +
               [{'id': 'b', 'adset_id': '222', 'name': 'שרשרת', 'configured_active': True},
                {'id': 'c', 'adset_id': '333', 'name': 'צמיד', 'configured_active': None}])
        result = launch_strategy.plan([asset(1)], [row(1)], report(sets, ads), self.rules)
        self.assertEqual(result['adsets'][0]['target'], {'mode': 'new'})

    def test_more_than_eight_is_split_and_products_are_separated(self):
        assets = [asset(i) for i in range(10)] + [asset(20)]
        rows = [row(i) for i in range(10)] + [row(20, product='שרשרת', angle='יומיומי')]
        result = launch_strategy.plan(assets, rows, report([], []), self.rules)
        sizes = sorted(sum(1 for v in result['assignment'].values() if v == g['key']) for g in result['adsets'])
        self.assertEqual(sizes, [1, 2, 8])

    def test_unverified_rulebook_refuses(self):
        with self.assertRaises(ValueError):
            launch_strategy.plan([asset(1)], [row(1)], report([], []), {'status': 'unavailable'})


if __name__ == '__main__':
    unittest.main()
