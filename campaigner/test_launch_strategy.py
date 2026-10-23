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

    def test_several_angles_go_into_one_dated_adset_and_more_than_eight_is_split(self):
        assets = [dict(asset(i), media_kind='video') for i in range(11)]
        rows = [row(i, angle=f'זווית {i}') for i in range(11)]
        result = launch_strategy.plan(assets, rows, report([], []), self.rules, today=date(2026, 10, 23))
        sizes = sorted(sum(1 for v in result['assignment'].values() if v == g['key']) for g in result['adsets'])
        self.assertEqual(sizes, [3, 8])
        self.assertEqual(result['adsets'][0]['label'], 'וידאו | תכנים חדשים | 23/10')
        self.assertIn('כמה שפחות אד-סטים', result['adsets'][0]['rationale'])

    def test_mixed_angles_are_never_put_in_an_existing_adset(self):
        sets = [{'id': '111', 'name': 'צמידים ישנים', 'configured_active': True, 'campaign_id': 'c'}]
        ads = [{'id': 'a', 'adset_id': '111', 'name': 'צמיד', 'configured_active': True}]
        result = launch_strategy.plan([asset(1), asset(2)], [row(1), row(2, angle='אחר')], report(sets, ads), self.rules)
        self.assertEqual([g['target'] for g in result['adsets']], [{'mode': 'new'}])

    def test_one_adset_per_gender_from_the_analysis(self):
        assets = [dict(asset(i), media_kind='video') for i in range(3)]
        rows = [dict(row(0), audience_hypothesis='גברים צעירים'), dict(row(1), audience_hypothesis='נשים צעירות'),
                dict(row(2), audience_hypothesis='צעירים וצעירות')]
        result = launch_strategy.plan(assets, rows, report([], []), self.rules, today=date(2026, 10, 23))
        modes = sorted(g['targeting_mode'] for g in result['adsets'])
        self.assertEqual(modes, ['choose', 'copy_men', 'copy_women'])
        men = next(g for g in result['adsets'] if g['targeting_mode'] == 'copy_men')
        self.assertEqual(men['label'], 'וידאו | תכנים חדשים | גברים | 23/10')
        self.assertIn('יועתק בדיוק', men['audience']['reason'])

    def test_gender_reading(self):
        self.assertEqual([launch_strategy.gender_of(t) for t in ('גברים 25-40', 'נשים', 'גברים ונשים', None)], ['men', 'women', None, None])

    def test_unverified_rulebook_refuses(self):
        with self.assertRaises(ValueError):
            launch_strategy.plan([asset(1)], [row(1)], report([], []), {'status': 'unavailable'})


if __name__ == '__main__':
    unittest.main()
