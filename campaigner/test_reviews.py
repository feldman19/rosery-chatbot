import json
import tempfile
import unittest
from pathlib import Path
from reviews import review_history


class ReviewTests(unittest.TestCase):
    def test_missing_is_unavailable_not_success(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            self.assertFalse(review_history(Path(tmp)/'app')['available'])

    def test_binding_and_no_fake_execution(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp); (root/'outputs').mkdir()
            p=root/'outputs'/'rosery-daily-review-2026-10-17.json'
            d={'account':'393059510164294','shop':'21cc82-3.myshopify.com','readOnly':True,
               'checkedDate':'2026-10-17','fullDaysUntil':'2026-10-16',
               'launch':{'id':'120253309360290730','yesterday':{'spendILS':0,'attributedRegistrations':None}},
               'decision':{'actionsExecuted':[]}}
            p.write_text(json.dumps(d))
            r=review_history(root/'app');self.assertTrue(r['available'])
            self.assertIsNone(r['entries'][0]['registrations'])
            self.assertFalse(r['live_action_allowed'])
            d['decision']['actionsExecuted']=['write'];p.write_text(json.dumps(d))
            with self.assertRaises(ValueError):review_history(root/'app')
            d['decision']['actionsExecuted']=[];d['account']='wrong';p.write_text(json.dumps(d))
            with self.assertRaises(ValueError):review_history(root/'app')
