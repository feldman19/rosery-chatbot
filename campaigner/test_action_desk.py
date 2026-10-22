import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from action_desk import ActionDesk
from meta_direct import ACCOUNT,LAUNCH


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.desk=ActionDesk(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def find(self,result,key):return next(x for x in result['findings'] if x['payload']['key']==key)
    def update(self,item,action,note=''):
        return self.desk.update({'id':item['id'],'expected_revision':item['revision'],'action':action,'note':note})
    def snapshot(self):
        return {'checked_at':datetime.now(timezone.utc).isoformat(),'sources':{'meta':{'last_success_at':datetime.now(timezone.utc).isoformat(),'data':{'account_id':ACCOUNT,'currency':'ILS','verified':True,'complete':True,'today':'2026-10-17','rows':[],
          'inventory':{'verified':True,'campaign':{'id':LAUNCH,'account_id':ACCOUNT},'adsets':[{'id':'123','effective_status':'ACTIVE','promoted_object':{'custom_event_type':'PURCHASE'}}]}}}}}

    def test_review_is_durable_not_verification_or_authority(self):
        item=self.find(self.desk.sync({},{}),'measurement');self.update(item,'reviewed','בדקתי את התיעוד, אין עדיין התאמה')
        result=ActionDesk(self.temp.name).sync({},{})
        self.assertEqual(self.find(result,'measurement')['state'],'reviewed')
        self.assertFalse(result['report']['measurement_verified']);self.assertFalse(result['live_action_allowed'])
        self.assertTrue(any(e['event']=='operator_reviewed' for e in result['history']))

    def test_duplicate_sync_does_not_duplicate_findings_or_reset_review(self):
        first=self.desk.sync({},{});item=self.find(first,'measurement');self.update(item,'start')
        second=self.desk.sync({},{});third=self.desk.sync({},{})
        self.assertEqual(len(second['findings']),len(third['findings']))
        self.assertEqual(self.find(third,'measurement')['state'],'in_review')
        self.assertEqual(len(second['history']),len(third['history']))

    def test_revision_conflict_and_unsupported_approval(self):
        item=self.find(self.desk.sync({},{}),'measurement');self.update(item,'start')
        with self.assertRaises(ValueError):self.update(item,'snooze')
        with self.assertRaises(ValueError):self.update(item,'approve')

    def test_snooze_expiration_reopens(self):
        item=self.find(self.desk.sync({},{}),'measurement');self.update(item,'snooze')
        with self.desk.connect() as db:db.execute('UPDATE findings SET snoozed_until=? WHERE id=?',('2000-01-01T00:00:00+00:00',item['id']))
        self.assertEqual(self.find(self.desk.sync({},{}),'measurement')['state'],'open')

    def test_missing_inventory_never_resolves_previously_detected_event(self):
        self.assertEqual(self.find(self.desk.sync(self.snapshot(),{}),'event-123')['state'],'open')
        self.assertEqual(self.find(self.desk.sync({},{}),'event-123')['state'],'open')

    def test_actual_condition_clear_is_audited(self):
        source=self.snapshot();self.desk.sync(source,{})
        source['sources']['meta']['data']['inventory']['adsets'][0]['promoted_object']['custom_event_type']='COMPLETE_REGISTRATION'
        result=self.desk.sync(source,{})
        self.assertEqual(self.find(result,'event-123')['state'],'resolved')
        self.assertTrue(any(e['event']=='condition_cleared' for e in result['history']))

    def test_review_needs_note(self):
        item=self.find(self.desk.sync({},{}),'measurement')
        with self.assertRaises(ValueError):self.update(item,'reviewed','')

    def test_old_snapshot_cannot_override_new(self):
        newer=self.snapshot();newer['checked_at']='2026-10-17T15:00:00+00:00'
        self.desk.sync(newer,{})
        older=self.snapshot();older['checked_at']='2026-10-17T14:00:00+00:00'
        older['sources']['meta']['data']['inventory']['adsets'][0]['promoted_object']['custom_event_type']='COMPLETE_REGISTRATION'
        self.assertEqual(self.find(self.desk.sync(older,{}),'event-123')['state'],'open')

    def test_changed_semantic_evidence_reopens_review(self):
        source=self.snapshot();item=self.find(self.desk.sync(source,{}),'event-123')
        self.update(item,'reviewed','Checked source settings')
        source['sources']['meta']['data']['inventory']['adsets'][0]['daily_budget']='14000'
        revised=self.find(self.desk.sync(source,{}),'event-123')
        self.assertEqual(revised['state'],'open');self.assertGreater(revised['revision'],item['revision'])

    def test_source_watermark_survives_new_instance(self):
        newer=self.snapshot();newer['checked_at']='2026-10-17T15:00:00+00:00'
        newer['sources']['meta']['last_success_at']=datetime.now(timezone.utc).isoformat()
        self.desk.sync(newer,{})
        older=self.snapshot();older['checked_at']='2026-10-17T16:00:00+00:00'
        older['sources']['meta']['last_success_at']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        older['sources']['meta']['data']['inventory']['adsets'][0]['promoted_object']['custom_event_type']='COMPLETE_REGISTRATION'
        result=ActionDesk(self.temp.name).sync(older,{})
        self.assertEqual(self.find(result,'event-123')['state'],'open')
