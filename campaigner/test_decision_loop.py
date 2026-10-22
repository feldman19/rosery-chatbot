import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from decision_loop import DecisionLoop
from meta_direct import ACCOUNT, LAUNCH


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.loop=DecisionLoop(self.temp.name)
        self.now=datetime(2026,10,17,12,tzinfo=timezone.utc)
    def tearDown(self): self.temp.cleanup()
    def snapshot(self, now=None):
        now=now or self.now;day=now.date()
        return {'sources':{'meta':{'last_success_at':now.isoformat(),'data':{
            'verified':True,'complete':True,'account_id':ACCOUNT,'currency':'ILS',
            'timezone':'Asia/Jerusalem','timezone_offset_hours':3,'today':day.isoformat(),
            'since':(day-timedelta(days=7)).isoformat(),'until':day.isoformat(),
            'rows':[{'ad_id':'1','campaign_id':LAUNCH,'date_start':(day-timedelta(days=1)).isoformat(),
                'date_stop':(day-timedelta(days=1)).isoformat(),'spend':'20','actions':[{'action_type':'omni_complete_registration','value':'4'},
                                        {'action_type':'complete_registration','value':'4'}]},
                    {'ad_id':'2','campaign_id':LAUNCH,'date_start':day.isoformat(),'date_stop':day.isoformat(),'spend':'200'}]}}}}
    def test_automatic_assessment_persisted_deduplicated_and_not_permission(self):
        one=self.loop.record(self.snapshot(),{},self.now)
        two=self.loop.record(self.snapshot(),{},self.now)
        self.assertEqual(one['assessment_count'],1);self.assertEqual(two['assessment_count'],1)
        saved=DecisionLoop(self.temp.name).read()['assessments'][0]['payload']
        self.assertFalse(saved['live_action_allowed']);self.assertFalse(saved['execution_performed'])
        self.assertEqual(saved['windows'][0]['metrics']['spend_ils'],'20')
        self.assertEqual(saved['windows'][0]['metrics']['registrations_attributed'],'4')
    def test_partial_day_excluded_and_next_full_day_observed_not_causal(self):
        self.loop.record(self.snapshot(),{},self.now)
        first=self.loop.record(self.snapshot(self.now+timedelta(days=1)),{},self.now+timedelta(days=1))
        self.assertEqual(len(first['observations']),0)
        later=self.now+timedelta(days=2)
        result=self.loop.record(self.snapshot(later),{},later)
        self.assertEqual(len(result['observations']),1)
        observation=result['observations'][0]['payload']
        self.assertEqual(observation['metrics']['spend_ils'],'20')
        self.assertFalse(observation['causal_effect_verified']);self.assertFalse(observation['experiment_executed'])
    def test_invalid_or_stale_source_never_becomes_learning(self):
        for change in ({'currency':'USD'},{'account_id':'other'},{'complete':False},{'today':'2026-10-16'}):
            snap=self.snapshot();snap['sources']['meta']['data'].update(change)
            self.assertEqual(self.loop.record(snap,{},self.now)['assessment_count'],0)
        self.assertEqual(self.loop.record(self.snapshot(),{},self.now+timedelta(hours=1))['assessment_count'],0)
    def test_duplicate_grain_and_nonfinite_values_rejected(self):
        snap=self.snapshot();snap['sources']['meta']['data']['rows']*=2
        with self.assertRaises(ValueError):self.loop.record(snap,{},self.now)
        snap=self.snapshot();snap['sources']['meta']['data']['rows'][0]['spend']='NaN'
        with self.assertRaises(ValueError):self.loop.record(snap,{},self.now)
    def test_missing_coverage_is_unknown_not_zero(self):
        snap=self.snapshot();snap['sources']['meta']['data']['since']='2026-10-16'
        windows=self.loop.record(snap,{},self.now)['assessments'][0]['payload']['windows']
        self.assertIsNone(windows[1]['metrics']);self.assertFalse(windows[1]['covered'])
    def test_older_source_cannot_append_after_newer_even_on_restart(self):
        self.loop.record(self.snapshot(),{},self.now)
        old=self.snapshot(self.now-timedelta(minutes=1));old['sources']['meta']['data']['rows'][0]['spend']='80'
        self.assertEqual(DecisionLoop(self.temp.name).record(old,{},self.now)['assessment_count'],1)

    def test_missing_spend_rejected_not_zero(self):
        snap=self.snapshot();del snap['sources']['meta']['data']['rows'][0]['spend']
        with self.assertRaises(KeyError):self.loop.record(snap,{},self.now)
        self.assertEqual(self.loop.read()['assessment_count'],0)

    def test_tampered_assessment_not_shown_as_evidence(self):
        self.loop.record(self.snapshot(),{},self.now)
        with self.loop.db() as db:db.execute("UPDATE assessments SET payload='{}'")
        with self.assertRaises(ValueError):self.loop.read()
