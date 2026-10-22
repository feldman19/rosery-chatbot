from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import tempfile
import unittest
from pilot import pilot_report, employee_pilot


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'pilot.json'
        self.now=datetime(2026,10,16,14,10,tzinfo=timezone.utc)
        self.data={'schema_version':1,'tenant':'rosery','business':'ROSERY',
                   'identity':{'meta_account':'393059510164294','shop_domain':'21cc82-3.myshopify.com'},
                   'captured_at':self.now.isoformat(),'live_action_allowed':True,
                   'mode':'execute','autonomous_refresh':True,
                   'observations':[{'source_id':'meta_campaign','data':{'id':'120253309360290730','objective':'OUTCOME_LEADS','effective_status':'ACTIVE','omni_complete_registration':None}}]}
        self.save()

    def save(self): self.path.write_text(json.dumps(self.data),encoding='utf-8')

    def test_import_cannot_grant_execution(self):
        r=pilot_report(self.path,self.now)
        self.assertFalse(r['live_action_allowed']);self.assertFalse(r['autonomous_refresh'])
        self.assertEqual(r['mode'],'observe')
        self.assertFalse(r['strategy']['scale_allowed'])

    def test_missing_registration_is_not_zero(self):
        r=pilot_report(self.path,self.now)
        self.assertIsNone(r['observations'][0]['data']['omni_complete_registration'])
        self.assertIn('registration-proof',[x['id'] for x in r['exceptions']])

    def test_stale_and_future(self):
        r=pilot_report(self.path,self.now+timedelta(hours=3))
        self.assertEqual(r['freshness'],'stale_import')
        self.assertIn('refresh',[x['id'] for x in r['exceptions']])
        with self.assertRaises(ValueError):pilot_report(self.path,self.now-timedelta(seconds=1))

    def test_wrong_binding(self):
        self.data['identity']['meta_account']='other';self.save()
        with self.assertRaises(ValueError):pilot_report(self.path,self.now)

    def test_employee_projection_hides_internal_data(self):
        r=employee_pilot(pilot_report(self.path,self.now))
        for key in ('observations','facts','identity','exceptions','policy'):self.assertNotIn(key,r)
        self.assertFalse(r['live_action_allowed'])

    def test_no_fixture_not_fake_live(self):
        self.path.unlink()
        self.assertFalse(pilot_report(self.path,self.now)['available'])

    def test_business_projection_cannot_carry_payload(self):
        self.data['business']={'cost':'private'};self.save()
        self.assertEqual(employee_pilot(pilot_report(self.path,self.now))['business'],'ROSERY')

    def test_wrong_campaign_observation_denied(self):
        self.data['observations'][0]['data']['id']='foreign';self.save()
        with self.assertRaises(ValueError):pilot_report(self.path,self.now)

    def test_source_specific_scope_required(self):
        self.data['observations'][0]['scope']='21cc82-3.myshopify.com';self.save()
        with self.assertRaises(ValueError):pilot_report(self.path,self.now)

    def test_import_flags_not_verification(self):
        self.data['source_verified']=True
        self.data['policy']={'live_action_allowed':True}
        self.data['observations'][0]['data']['source_verified']=True;self.save()
        r=pilot_report(self.path,self.now)
        self.assertFalse(r['source_verified']);self.assertNotIn('policy',r)
        self.assertNotIn('source_verified',r['observations'][0]['data'])
