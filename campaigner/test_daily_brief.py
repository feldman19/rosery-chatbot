import unittest

from daily_brief import build


def report(items=(), status='ready'):
    return {'windows': {'1': {'metrics': {'store_revenue': 3270, 'spend': 806.16, 'store_orders': 13}},
                        '7': {'days': 7, 'metrics': {'store_revenue': 25617.6}}},
            'recommendations': {'today': {'status': status, 'items': list(items)}}}


class DailyBriefTests(unittest.TestCase):
    def test_numbers_and_todo(self):
        text = build(report([{'kind': 'approve', 'headline': 'להשהות את "C14"'}, {'kind': 'info', 'headline': 'x'}]))
        self.assertIn('מכירות בחנות: ₪3,270 (▼ 11% מהממוצע)', text)
        self.assertIn('הוצאה במטא: ₪806 · 4.06 מכירות לכל ₪1', text)
        self.assertIn('מחכים לך היום (1):', text)
        self.assertNotIn('• x', text)

    def test_nothing_to_do_and_done(self):
        text = build(report(), [{'state': 'verified', 'title': 'לעצור את המודעה', 'entity_name': 'C14'},
                                {'state': 'unknown', 'title': 'x', 'entity_name': 'y'}])
        self.assertIn('אין מה לשנות היום', text)
        self.assertIn('• לעצור את המודעה: C14', text)
        self.assertNotIn('• x', text)

    def test_blocked_and_empty(self):
        self.assertIn('חסרים נתונים', build(report(status='blocked')))
        self.assertTrue(build({}).split('\n',1)[1].startswith('בוקר טוב'))


if __name__ == '__main__':
    unittest.main()
