from datetime import datetime,timezone
import unittest
from automation_service import report
from connections import SHOP
from meta_direct import ACCOUNT,LAUNCH


class LiveEngineTests(unittest.TestCase):
    def setUp(self):self.now=datetime(2026,10,17,12,tzinfo=timezone.utc)
    def snapshot(self):
        return {'sources':{'meta':{'last_success_at':self.now.isoformat(),'data':{
            'account_id':ACCOUNT,'verified':True,'complete':True,'currency':'ILS','timezone':'Asia/Jerusalem',
            'since':'2026-10-10','until':'2026-10-17',
            'inventory':{'verified':True,'campaign':{'id':LAUNCH,'account_id':ACCOUNT,'status':'ACTIVE','daily_budget':'12000','updated_time':'2026-10-15T00:00:00+03:00'}},
            'rows':[{'ad_id':'1','campaign_id':LAUNCH,'date_start':'2026-10-16','date_stop':'2026-10-16','spend':'20','actions':[{'action_type':'omni_complete_registration','value':'4'}]}]}},
            'shopify':{'last_success_at':self.now.isoformat(),'data':{'verified':True,'shop':SHOP,'currency':'ILS'}}}}
    def test_live_reports_cannot_impersonate_purchase_cohort_or_authority(self):
        result=report(self.snapshot(),self.now)
        self.assertTrue(result['readiness'][0]['ready'])
        self.assertEqual(result['candidate']['status'],'blocked')
        self.assertIsNone(result['candidate']['action']);self.assertFalse(result['write_connected'])
        self.assertEqual(result['windows']['1']['spend_minor'],2000)
        self.assertEqual(result['windows']['1']['started_at'],'2026-10-16T00:00:00+03:00')
    def test_missing_spend_and_wrong_scope_do_not_become_zero_or_candidate(self):
        snap=self.snapshot();del snap['sources']['meta']['data']['rows'][0]['spend']
        self.assertEqual(report(snap,self.now)['windows'],{})
        snap=self.snapshot();snap['sources']['shopify']['data']['shop']='wrong'
        self.assertFalse(report(snap,self.now)['readiness'][0]['ready'])
    def test_empty_or_expired_connection_remains_blocked(self):
        for snap in ({},self.snapshot()):
            result=report(snap,datetime(2026,10,18,12,tzinfo=timezone.utc))
            self.assertEqual(result['candidate']['status'],'blocked');self.assertFalse(result['live_action_allowed'])
