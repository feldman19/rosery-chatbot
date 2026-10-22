from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib,json,tempfile,unittest
from registration import registration_report,answer_registration,ACCOUNT,CAMPAIGN,ADSET,PIXEL

class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work',prefix='r');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'app';(self.root/'.local').mkdir(parents=True)
        (self.root.parent/'outputs').mkdir()
        self.now=datetime.now(timezone.utc)
        self.m={'tenant':'rosery','account_id':ACCOUNT,'campaign_id':CAMPAIGN,'captured_at':self.now.isoformat(),
                'campaign':{'id':CAMPAIGN,'daily_budget':{'value':'120','unit':'ILS'},'amount_spent':{'value':'74.97','unit':'ILS'},'omni_complete_registration':'9'},
                'adset':{'id':ADSET,'campaign_id':CAMPAIGN,'optimization_goal':'OFFSITE_CONVERSIONS','promoted_object':{'pixel_id':PIXEL,'custom_event_type':'COMPLETE_REGISTRATION'}},
                'ads':[],'window':{'since':'2026-10-15','until':'2026-10-16','partial_last_day':True}}
        self.s={'tenant':'rosery','domain':'roseryjewelry.com','list_id':'40428','captured_at':self.now.isoformat(),'public_static_page_verified':True,'list_contacts':144,'popup_conversions':151}
        self.h={'campaign_id':CAMPAIGN,'dataset_id':PIXEL,'test_result':{'website_success':True,'flashy_contact_subscription_verified':True,'meta_registration_browser_requests_observed_in_controlled_submission':1}}
        self.save()
    def save(self):
        payloads={'.local/registration-meta.json':self.m,'.local/registration-site.json':self.s,
                  '../outputs/rosery-launch-registration-tracking-2026-10-16.json':self.h,
                  '../outputs/rosery-registration-scale-recheck-2026-10-16.json':{}}
        files={}
        for name,payload in payloads.items():
            raw=json.dumps(payload).encode();(self.root/name).write_bytes(raw);files[name]=hashlib.sha256(raw).hexdigest()
        (self.root/'.local/registration-manifest.json').write_text(json.dumps({'tenant':'rosery','account_id':ACCOUNT,'campaign_id':CAMPAIGN,'files':files}))
    def test_review_not_full_measurement_or_permission(self):
        r=registration_report(self.root,self.now)
        self.assertEqual(r['completed_checks'],3);self.assertEqual(r['historical_reviews'],1)
        self.assertFalse(r['end_to_end_verified']);self.assertIsNone(r['proposal']);self.assertFalse(r['live_action_allowed'])
        self.assertEqual(r['metrics']['attributed_cpl_ils'],'8.33')
    def test_tamper_and_missing_evidence_fail_closed(self):
        (self.root/'.local/registration-meta.json').write_text('{}')
        with self.assertRaises(ValueError):registration_report(self.root,self.now)
    def test_foreign_scope_denied(self):
        self.m['campaign']['id']='other';self.save()
        with self.assertRaises(ValueError):registration_report(self.root,self.now)
    def test_stale_reviews_not_current_pass(self):
        r=registration_report(self.root,self.now+timedelta(hours=3))
        self.assertEqual(r['completed_checks'],0);self.assertEqual(r['freshness'],'stale')
    def test_future_evidence_denied(self):
        with self.assertRaises(ValueError):registration_report(self.root,self.now-timedelta(seconds=1))
    def test_truthy_is_not_a_pass(self):
        self.s['public_static_page_verified']='false';self.s['list_contacts']=-1;self.save()
        checks={c['id']:c for c in registration_report(self.root,self.now)['checks']}
        self.assertEqual(checks['landing']['state'],'missing');self.assertEqual(checks['storage']['state'],'missing')
    def test_missing_registration_not_zero(self):
        self.m['campaign']['omni_complete_registration']=None;self.save()
        r=registration_report(self.root,self.now)
        self.assertIsNone(r['metrics']['registrations']);self.assertIsNone(r['metrics']['attributed_cpl_ils'])
    def test_answer_is_context_not_campaign_approval(self):
        r=registration_report(self.root,self.now)
        saved=answer_registration(self.root,self.root/'.local',r['revision'],'observed_budget_intended')
        self.assertFalse(saved['live_permission'])
        r=registration_report(self.root)
        self.assertEqual(r['state'],'blocked_evidence');self.assertFalse(r['approval_available'])
    def test_wrong_revision_or_action_answer_denied(self):
        with self.assertRaises(ValueError):answer_registration(self.root,self.root/'.local','wrong','observed_budget_intended')
        with self.assertRaises(ValueError):answer_registration(self.root,self.root/'.local','wrong','activate')
    def test_stale_evidence_denies_answer(self):
        self.m['captured_at']=(self.now-timedelta(hours=3)).isoformat();self.save()
        r=registration_report(self.root,self.now)
        with self.assertRaises(ValueError):answer_registration(self.root,self.root/'.local',r['revision'],'observed_budget_intended')
    def test_no_case_is_not_fake_success(self):
        (self.root/'.local/registration-manifest.json').unlink()
        self.assertFalse(registration_report(self.root,self.now)['available'])
