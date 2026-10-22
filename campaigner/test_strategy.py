import copy
import unittest
from datetime import datetime,timedelta,timezone
from strategy import evaluate

class StrategyTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,10,17,12,tzinfo=timezone.utc)
  self.profile=dict(tenant_id='synthetic',account_id='synthetic',entity_id='synthetic',currency='ILS',timezone='UTC')
  self.policy=dict(cap_minor=10000,max_test_loss_minor=5000,budget_step_minor=1000,min_scale_registrations=3,review_after_hours=24,profit_margin=.2,allowed_actions=['pause','set_budget'])
  self.e={k:{'verified':True} for k in ('costs','measurement','provenance','maturity','stock','offer')}
  self.e.update(observed_at=self.now.isoformat(),valid_until=(self.now+timedelta(minutes=15)).isoformat(),entity=dict(status='ACTIVE',budget_minor=6000,budget_type='daily',currency='ILS',last_changed_at=(self.now-timedelta(days=9)).isoformat()),cohort=dict(verified=True,subscribers=1000,purchasers=200),windows={str(d):dict(full_days=True,spend_minor=1000*d,registrations=10*d,started_at=(self.now-timedelta(days=d,hours=12)).isoformat(),ended_at=(self.now-timedelta(hours=12)).isoformat()) for d in (1,3,7)})
  self.e['entity'].update({k:self.profile[k] for k in ('tenant_id','account_id','entity_id')})
  self.e['costs']['contribution_lower_minor']=15000
  self.e['measurement']['unique_registrations']=True
  self.e['provenance']['refs']=['sha256:'+'a'*64]
  self.e['maturity'].update(attribution_complete=True,comparable_cohort=True,observed_at=self.now.isoformat(),valid_until=(self.now+timedelta(minutes=15)).isoformat(),attributed_through=(self.now-timedelta(hours=12)).isoformat())
  self.e['stock']['available']=True;self.e['offer']['current']=True
 def run_strategy(self):return evaluate(self.profile,self.e,self.policy,self.now)
 def test_scale(self):
  saved=copy.deepcopy(self.e);r=self.run_strategy()
  self.assertEqual(('propose','set_budget',7000),(r['status'],r['action'],r['after']['budget_minor']))
  self.assertEqual(saved,self.e);self.assertFalse(r['live_action_allowed']);self.assertFalse(r['permission_granted'])
  self.assertLess(r['economics']['conservative_cpl_minor'],2400)
 def test_gates(self):
  for key in ('costs','measurement','provenance','maturity','stock','offer'):
   self.e[key]['verified']=False;self.assertEqual('blocked',self.run_strategy()['status']);self.e[key]['verified']=True
 def test_envelope(self):
  self.policy.pop('cap_minor');self.assertEqual('blocked',self.run_strategy()['status'])
 def test_partial(self):
  self.e['windows']['1']['full_days']=False;self.assertEqual('blocked',self.run_strategy()['status'])
 def test_maturity(self):
  self.e['maturity']['attribution_complete']=False;self.assertEqual('blocked',self.run_strategy()['status'])
 def test_unique(self):
  self.e['measurement']['unique_registrations']=False;self.assertEqual('blocked',self.run_strategy()['status'])
 def test_expired(self):
  self.e['valid_until']=(self.now-timedelta(seconds=1)).isoformat();self.assertEqual('blocked',self.run_strategy()['status'])
 def test_currency(self):
  self.profile['currency']='USD';self.assertEqual('blocked',self.run_strategy()['status'])
 def test_no_cohort_purchases(self):
  self.e['cohort']['purchasers']=0;self.assertEqual('hold',self.run_strategy()['status'])
 def test_pause(self):
  for w in self.e['windows'].values():w['registrations']=0
  r=self.run_strategy();self.assertEqual(('propose','pause','PAUSED'),(r['status'],r['action'],r['after']['status']))
 def test_action_envelope(self):
  self.policy['allowed_actions']=[];self.assertEqual('hold',self.run_strategy()['status'])
 def test_cap(self):
  self.policy['cap_minor']=6500;self.assertEqual(6500,self.run_strategy()['after']['budget_minor'])
  self.policy['cap_minor']=5000;self.assertEqual('hold',self.run_strategy()['status'])
 def test_paused(self):
  self.e['entity']['status']='PAUSED';self.assertEqual('hold',self.run_strategy()['status'])
 def test_recent_change(self):
  self.e['entity']['last_changed_at']=self.now.isoformat();self.assertEqual('blocked',self.run_strategy()['status'])
 def test_small_sample(self):
  large=self.run_strategy()['economics']['conservative_cpl_minor'];self.e['cohort'].update(subscribers=10,purchasers=2)
  self.assertLess(self.run_strategy()['economics']['conservative_cpl_minor'],large)
 def test_refs(self):
  self.e['provenance']['refs']=['url'];self.assertEqual('blocked',self.run_strategy()['status'])
 def test_stock_and_comparability(self):
  self.e['stock']['available']=False;self.assertEqual('hold',self.run_strategy()['status'])
  self.e['stock']['available']=True;self.e['maturity']['comparable_cohort']=False;self.assertEqual('hold',self.run_strategy()['status'])
 def test_entity_mismatch(self):
  self.e['entity']['account_id']='other';self.assertEqual('blocked',self.run_strategy()['status'])
 def test_window_duration(self):
  self.e['windows']['1']['started_at']=self.e['windows']['1']['ended_at'];self.assertEqual('blocked',self.run_strategy()['status'])
 def test_change_inside_window(self):
  self.e['entity']['last_changed_at']=(self.now-timedelta(days=2)).isoformat();self.assertEqual('blocked',self.run_strategy()['status'])
 def test_inconsistent_totals(self):
  self.e['windows']['1']['spend_minor']=999999;self.assertEqual('blocked',self.run_strategy()['status'])
 def test_utc_windows_do_not_fit_jerusalem(self):
  self.profile['timezone']='Asia/Jerusalem';self.assertEqual('blocked',self.run_strategy()['status'])
 def test_missing_timezone_blocks(self):
  self.profile.pop('timezone');self.assertEqual('blocked',self.run_strategy()['status'])
 def test_midday_full_duration_blocks(self):
  for w in self.e['windows'].values():
   w['started_at']=(datetime.fromisoformat(w['started_at'])+timedelta(hours=1)).isoformat();w['ended_at']=(datetime.fromisoformat(w['ended_at'])+timedelta(hours=1)).isoformat()
  self.assertEqual('blocked',self.run_strategy()['status'])
 def test_stale_closed_windows_block(self):
  for w in self.e['windows'].values():
   w['started_at']=(datetime.fromisoformat(w['started_at'])-timedelta(days=1)).isoformat();w['ended_at']=(datetime.fromisoformat(w['ended_at'])-timedelta(days=1)).isoformat()
  self.assertEqual('blocked',self.run_strategy()['status'])
 def test_misaligned_nested_ends_block(self):
  w=self.e['windows']['3'];w['started_at']=(datetime.fromisoformat(w['started_at'])-timedelta(days=1)).isoformat();w['ended_at']=(datetime.fromisoformat(w['ended_at'])-timedelta(days=1)).isoformat()
  self.assertEqual('blocked',self.run_strategy()['status'])
 def test_sample_case_arbitrary_hour_aligns(self):
  from strategy import sample_case
  case=sample_case(self.now+timedelta(hours=3));self.assertEqual('propose',evaluate(**case)['status'])
 def test_stale_maturity_proof_blocks(self):
  self.e['maturity']['valid_until']=(self.now-timedelta(seconds=1)).isoformat();self.assertEqual('blocked',self.run_strategy()['status'])
 def test_incomplete_maturity_coverage_blocks(self):
  self.e['maturity']['attributed_through']=(self.now-timedelta(days=2)).isoformat();self.assertEqual('blocked',self.run_strategy()['status'])
 def test_unknown_timezone_blocks(self):
  self.profile['timezone']='Not/AZone';self.assertEqual('blocked',self.run_strategy()['status'])
 def test_jerusalem_calendar_days_supported(self):
  from strategy import sample_case
  c=sample_case(self.now,timezone_name='Asia/Jerusalem');self.assertEqual('propose',evaluate(**c)['status'])
 def test_dst_23_hour_day(self):
  from strategy import sample_case
  c=sample_case(datetime(2026,3,28,10,tzinfo=timezone.utc),timezone_name='Asia/Jerusalem')
  w=c['evidence']['windows']['1'];elapsed=datetime.fromisoformat(w['ended_at'])-datetime.fromisoformat(w['started_at'])
  self.assertEqual(23,elapsed.total_seconds()/3600);self.assertEqual('propose',evaluate(**c)['status'])
 def test_dst_25_hour_day(self):
  from strategy import sample_case
  c=sample_case(datetime(2026,10,26,10,tzinfo=timezone.utc),timezone_name='Asia/Jerusalem')
  w=c['evidence']['windows']['1'];elapsed=datetime.fromisoformat(w['ended_at'])-datetime.fromisoformat(w['started_at'])
  self.assertEqual(25,elapsed.total_seconds()/3600);self.assertEqual('propose',evaluate(**c)['status'])
 def test_jerusalem_nonmidnight_rejected(self):
  from strategy import sample_case
  c=sample_case(self.now,timezone_name='Asia/Jerusalem');w=c['evidence']['windows']['1']
  w['started_at']=(datetime.fromisoformat(w['started_at'])+timedelta(hours=1)).isoformat()
  self.assertEqual('blocked',evaluate(**c)['status'])
if __name__=='__main__':unittest.main()
