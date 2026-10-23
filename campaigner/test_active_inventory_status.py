import unittest

from management_report import ACCOUNT, _active_ad_ids


class ActiveInventoryStatusTests(unittest.TestCase):
    def test_complete_read_gives_ids(self):
        data = {'active_ads': {'verified': True, 'complete': True, 'account_id': ACCOUNT, 'ads': [{'id': '1'}, {'id': '2'}]}}
        self.assertEqual(_active_ad_ids(data), {'1', '2'})

    def test_incomplete_or_bad_read_says_nothing(self):
        for active in ({'verified': True, 'complete': False, 'account_id': ACCOUNT, 'ads': []},
                       {'verified': False, 'complete': True, 'account_id': ACCOUNT, 'ads': []},
                       {'verified': True, 'complete': True, 'account_id': 'other', 'ads': []},
                       {'verified': True, 'complete': True, 'account_id': ACCOUNT, 'ads': [{'name': 'x'}]}):
            self.assertIsNone(_active_ad_ids({'active_ads': active}))
        self.assertIsNone(_active_ad_ids({}))


if __name__ == '__main__':
    unittest.main()
