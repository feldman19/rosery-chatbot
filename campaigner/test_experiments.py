import unittest
from dataclasses import replace
from datetime import date, timedelta

from experiments import Arm, Plan, evaluate, rank_candidates


def plan():
    return Plan('האם עדויות מוכרות יותר?', 'סוג קריאייטיב',
                Arm('מוצר', 'product', tuple('a' + str(i) for i in range(5))),
                Arm('עדות', 'testimonial', tuple('b' + str(i) for i in range(5))),
                10000, '2026-01-01').transition('prepared').transition('running')


def data(days=21, purchases=(1, 1), spend=10000, checkouts=(2, 2)):
    return {arm: [{'date': (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                   'spend_minor': spend, 'purchases': purchases[j], 'checkouts': checkouts[j]}
                  for i in range(days)] for j, arm in enumerate(('A', 'B'))}


class ExperimentTests(unittest.TestCase):
    def test_winner(self):
        result = evaluate(plan(), data(purchases=(1, 6)), '2026-01-21')
        self.assertEqual((result['state'], result['winner']), ('decided', 'B'))
        self.assertGreater(result['lift_interval_90'][0], 0)
        self.assertEqual(evaluate(plan(), data(purchases=(6, 1)), '2026-01-21')['winner'], 'A')

    def test_equal_inconclusive(self):
        result = evaluate(plan(), data(), '2026-01-21')
        self.assertEqual(result['state'], 'inconclusive')
        self.assertLess(result['lift_interval_90'][0], 0)
        self.assertGreater(result['lift_interval_90'][1], 0)

    def test_purchase_and_spend_gate(self):
        for rows in (data(purchases=(0, 4)), data(purchases=(1, 6), spend=100)):
            result = evaluate(plan(), rows, '2026-01-21')
            self.assertEqual(result['state'], 'inconclusive')
            self.assertIsNone(result['winner'])

    def test_harm(self):
        result = evaluate(plan(), data(4, (0, 1), checkouts=(0, 2)), '2026-01-04')
        self.assertEqual(result['state'], 'stopped_for_harm')
        self.assertIsNone(result['winner'])

    def test_harm_threshold_and_unknown_checkout(self):
        rows = data(4, (0, 1), spend=9999, checkouts=(0, 2))
        self.assertEqual(evaluate(plan(), rows, '2026-01-04')['state'], 'running')
        rows = data(4, (0, 1), checkouts=(0, 2))
        rows['A'][0]['checkouts'] = None
        self.assertIsNone(evaluate(plan(), rows, '2026-01-04')['probability_b_gt_a'])

    def test_explicit_zero_is_known(self):
        result = evaluate(plan(), data(purchases=(0, 0), checkouts=(0, 0)), '2026-01-21')
        self.assertEqual(result['state'], 'inconclusive')
        self.assertIsNotNone(result['lift_interval_90'])

    def test_missing_or_invalid_day_blocks(self):
        for mode in ('missing', 'both_missing', 'unknown', 'duplicate', 'negative', 'nan'):
            rows = data()
            if mode in ('missing', 'both_missing'):
                rows['A'].pop(3)
                if mode == 'both_missing':
                    rows['B'].pop(3)
            elif mode == 'duplicate':
                rows['A'][1]['date'] = rows['A'][0]['date']
            else:
                rows['A'][0]['purchases'] = {'unknown': None, 'negative': -1, 'nan': float('nan')}[mode]
            result = evaluate(plan(), rows, '2026-01-21')
            self.assertEqual(result['state'], 'running')
            self.assertIsNone(result['probability_b_gt_a'])

    def test_determinism(self):
        self.assertEqual(evaluate(plan(), data(), '2026-01-21'), evaluate(plan(), data(), '2026-01-21'))

    def test_state_machine_and_edit(self):
        running = plan()
        with self.assertRaises(ValueError):
            running.transition('prepared')
        for state in ('decided', 'inconclusive', 'interrupted', 'stopped_for_harm'):
            terminal = running.transition(state)
            with self.assertRaises(ValueError):
                terminal.transition('running')
        changed = running.edit_arm('a', replace(running.a, label='תיאור חדש'))
        self.assertEqual(changed.state, 'interrupted')
        self.assertEqual(running.state, 'running')
        self.assertEqual(running.total_cost_minor, 420000)

    def test_min_days(self):
        result = evaluate(plan(), data(6, (1, 20)), '2026-01-06')
        self.assertEqual(result['state'], 'running')

    def test_plan_validation(self):
        for changes in ({'daily_budget_minor': 1.5}, {'min_days': 22}, {'start_date': 'bad'}, {'question': ''}):
            with self.assertRaises(ValueError):
                replace(plan(), **changes)
        with self.assertRaises(ValueError):
            Arm('חסר', 'video', ('1',))
        with self.assertRaises(ValueError):
            replace(plan(), b=plan().a)

    def test_ranking_refuses_missing_arm(self):
        idea = {'question': 'איזה סוג עדיף?', 'a_kind': 'product', 'b_kind': 'testimonial'}
        creatives = {'product': list('abcde'), 'testimonial': list('fghi')}
        result = rank_candidates([idea], {}, [], creatives, 200)
        self.assertIsNotNone(result[0]['refusal_reason'])
        self.assertEqual(result[0]['estimated_cost_minor'], 200000)
        self.assertIsNone(rank_candidates([idea], {}, [], creatives)[0]['estimated_cost_minor'])

    def test_history_shape_and_ranking(self):
        history = {'windows': {'all': {'patterns': {'dimensions': [
            {'dimension': 'פורמט', 'values': [{'value': 'וידאו', 'enough_data': False},
                                            {'value': 'תמונה', 'enough_data': True}]}]}}}}
        creatives = {'וידאו': list('abcde'), 'תמונה': list('fghij')}
        result = rank_candidates([], history, [], creatives, 100)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]['refusal_reason'])
        self.assertEqual(result[0]['estimated_cost_minor'], 120000)
        self.assertEqual(result, rank_candidates([], history, [], creatives, 100))
        ideas = [{'question': str(i), 'expected_value': i, 'a_kind': 'וידאו', 'b_kind': 'תמונה'} for i in range(5)]
        ranked = rank_candidates([], {}, ideas, creatives, 100)
        self.assertEqual([x['question'] for x in ranked], ['4', '3', '2'])


class RuledOutCandidatesTests(unittest.TestCase):
    def test_dimensions_and_values_the_rules_fix_are_not_proposed(self):
        history = {'windows': {'all': {'patterns': {'dimensions': [
            {'dimension': 'כפתור פעולה', 'values': [{'value': 'SHOP_NOW', 'enough_data': True}, {'value': 'BOOK_TRAVEL', 'enough_data': False}]},
            {'dimension': 'פורמט', 'values': [{'value': 'וידאו', 'enough_data': True}, {'value': 'קרוסלה', 'enough_data': False},
                                              {'value': 'תמונה', 'enough_data': False}]}]}}}}
        from experiments import rank_candidates
        questions = [p['question'] for p in rank_candidates([], history, [], {}, account_cpa=78)]
        self.assertFalse(any('BOOK_TRAVEL' in q or 'קרוסלה' in q for q in questions))
        self.assertTrue(any('תמונה' in q for q in questions))


if __name__ == '__main__':
    unittest.main()
