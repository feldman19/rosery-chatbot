import unittest
from copy import deepcopy
from pathlib import Path
from change_history_evidence import receipt_changes, load_saved_change_evidence, ACCOUNT, ACTION, BEFORE, AFTER
from management_report import _detail_inventory


class SavedChangeReceiptsTests(unittest.TestCase):
    def test_saved_creative_receipts_are_bounded_not_complete_history(self):
        root = Path(__file__).resolve().parents[1]
        from change_history_evidence import LEDGER
        if not all((root / p).is_file() for p in (LEDGER, BEFORE, AFTER)):
            self.skipTest('needs the private 2026-10-19 repair receipts in outputs/ (not in git)')
        events = load_saved_change_evidence(root)
        self.assertGreater(len(events), 0)
        for event in events:
            self.assertNotEqual(event['before_creative_id'], event['after_creative_id'])
            self.assertIsNone(event['exact_change_time'])
            self.assertFalse(event['latest_change_proven'])
            self.assertFalse(event['attribution_maturity_proven'])
        self.assertEqual(receipt_changes({'account':'other','action':ACTION,'before':BEFORE,'after':AFTER}, {}, {}, []), [])

    def test_active_edge_preserves_scope_and_rejects_conflicting_parent(self):
        ad = {'id':'a','account_id':ACCOUNT,'status':'ACTIVE','updated_time':'2026-10-19T09:00:00Z',
              'campaign':{'id':'c'},'adset':{'id':'s','status':'PAUSED','daily_budget':'12000','lifetime_budget':'0'}}
        data = {'active_ads':{'verified':True,'complete':True,'account_id':ACCOUNT,
                             'checked_at':'2026-10-20T09:00:00Z','ads':[ad]}}
        sets, ads, _ = _detail_inventory(data)
        self.assertEqual(sets['s']['status'], 'PAUSED')
        self.assertEqual(sets['s']['_observed_at'], '2026-10-20T09:00:00Z')
        self.assertEqual(ads['a']['campaign_id'], 'c')
        self.assertIn('active_ads', ads['a']['_source_path'])
        conflicting = deepcopy(ad);conflicting['id']='b';conflicting['campaign']['id']='other'
        data['active_ads']['ads'].append(conflicting)
        sets, ads, _ = _detail_inventory(data)
        self.assertEqual((sets,ads), ({},{}))


if __name__ == '__main__':
    unittest.main()
