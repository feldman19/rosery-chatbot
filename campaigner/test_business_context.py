import unittest,tempfile,json
from pathlib import Path
from business_context import load_rosery_dossier
from strategy_desk import StrategyDesk

class BusinessContextTest(unittest.TestCase):
 def test_strategy_report_reads_central_profile_again(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as td:
   d=StrategyDesk(td,dossier_loader=lambda:load_rosery_dossier(include_owner_financials=True))
   r=d.report({});self.assertIn('fixed_horizon_cohorts',r['business_dossier']['owner_evidence']);self.assertFalse(r['live_action_allowed']);self.assertEqual([],r['analysis']['numerical_recommendations'])
 def test_real_profile_is_linked_and_not_authority(self):
  d=load_rosery_dossier(include_owner_financials=True)
  self.assertEqual(d['business_id'],'rosery');self.assertIn('fixed_horizon_cohorts',d['owner_evidence']);self.assertIsNone(d['automatic_spend_limits']);self.assertFalse(d['live_action_allowed'])
 def test_employee_omits_owner_financial_evidence(self):
  d=load_rosery_dossier();self.assertNotIn('owner_evidence',d)
 def test_other_tenant_never_receives_rosery_financials(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as td:
   root=Path(td);p=root/'businesses/rosery/profile.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'business_id':'other'}))
   d=load_rosery_dossier(root,include_owner_financials=True);self.assertNotIn('owner_evidence',d);self.assertTrue(d['missing'])
if __name__=='__main__':unittest.main()
