"""The day picker's data: one entry per day of the stored read; unknown days stay unknown, never zero."""
from copy import deepcopy
import unittest

from management_report import build_report
import test_management_report as base


class DailyReportTests(unittest.TestCase):
    setUp = base.ManagementReportTests.setUp

    def report(self):
        return build_report(self.snapshot, now=self.now)

    def test_seven_full_days_then_today_so_far(self):
        daily = self.report()['daily']
        self.assertEqual([d['date'] for d in daily], [f'2026-10-{n}' for n in range(11, 19)])
        self.assertEqual([d['partial'] for d in daily], [False] * 7 + [True])
        day = daily[-2]  # 17.10, yesterday
        self.assertEqual((day['metrics']['spend'], day['metrics']['purchases'], day['metrics']['attributed_revenue'], day['metrics']['roas']),
                         (10, 1, 50, 5))
        self.assertEqual((day['metrics']['store_orders'], day['metrics']['store_revenue']), (3, 30))
        self.assertEqual(day['ads'], [{'id': 'ad1', 'name': 'ad1', 'spend': 10, 'purchases': 1, 'attributed_revenue': 50, 'roas': 5}])

    def test_yesterday_matches_the_one_day_window(self):
        result = self.report()
        one, yesterday = result['windows']['1']['metrics'], result['daily'][-2]['metrics']
        for key in ('spend', 'purchases', 'attributed_revenue', 'store_orders', 'store_revenue'):
            self.assertEqual(one[key], yesterday[key], key)

    def test_today_with_rows_is_shown_so_far_and_store_without_today_is_unknown(self):
        row = deepcopy(self.snapshot['sources']['meta']['data']['rows'][0])
        row.update(date_start='2026-10-18', date_stop='2026-10-18', spend='4')
        self.snapshot['sources']['meta']['data']['rows'].append(row)
        today = self.report()['daily'][-1]
        self.assertEqual(today['metrics']['spend'], 4)
        self.assertIsNone(today['metrics']['store_revenue'])  # the store read ends yesterday: unknown, not zero
        self.assertTrue(today['notes'])

    def test_a_day_without_a_full_read_is_unknown_not_zero(self):
        self.snapshot['sources']['meta']['data']['since'] = '2026-10-14'
        daily = self.report()['daily']
        early = daily[0]
        self.assertIsNone(early['metrics']['spend'])
        self.assertEqual(early['ads'], [])
        self.assertTrue(early['notes'])
        self.assertEqual(daily[-2]['metrics']['spend'], 10)

    def test_an_unverified_read_gives_no_numbers(self):
        self.snapshot['sources']['meta']['data']['complete'] = False
        for day in self.report()['daily']:
            self.assertIsNone(day['metrics']['spend'])


if __name__ == '__main__':
    unittest.main()
