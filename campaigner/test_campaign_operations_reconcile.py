"""Synthetic transport read-only recovery. No credentials or real provider I/O."""
import unittest
from copy import deepcopy
from datetime import timedelta
from test_campaign_operations import CampaignOperationsTests


class ReconcileTests(unittest.TestCase):
    setUp=CampaignOperationsTests.setUp
    tearDown=CampaignOperationsTests.tearDown
    binding=CampaignOperationsTests.binding
    new_service=CampaignOperationsTests.new_service
    preview=CampaignOperationsTests.preview
    approve=CampaignOperationsTests.approve
    execute=CampaignOperationsTests.execute

    def failed_read(self):
        p=self.preview();self.provider.readback_fail=True
        result=self.execute(p);self.assertEqual(result['operation']['state'],'unknown')
        self.provider.readback_fail=False
        return p

    def test_saved_id_recovers_after_reopen_without_creating_remaining(self):
        p=self.failed_read();writes=deepcopy(self.provider.writes)
        self.now+=timedelta(days=1);self.proposal['request_revision']+=1
        self.service=self.new_service()
        result=self.service.reconcile(p['operation_id'],p['digest'])
        self.assertEqual(result['operation']['state'],'partial')
        self.assertEqual(result['job']['steps'][0]['state'],'verified')
        self.assertTrue(all(s['state']=='pending' for s in result['job']['steps'][1:]))
        self.assertEqual(self.provider.writes,writes)
        self.service.execute(p['operation_id'])
        self.assertEqual(self.provider.writes,writes)
        self.assertEqual(self.new_service()._get(p['operation_id'])['state'],'partial')

    def test_missing_id_stays_unknown_no_new_write(self):
        p=self.preview();self.provider.fail_at=1;self.execute(p)
        writes=deepcopy(self.provider.writes)
        result=self.service.reconcile(p['operation_id'],p['digest'])
        self.assertEqual(result['operation']['state'],'unknown')
        self.assertEqual(self.provider.writes,writes)

    def test_wrong_account_and_settings_stay_unknown(self):
        p=self.failed_read();identity=self.service._get(p['operation_id'])['steps'][0]['provider_id']
        original=deepcopy(self.provider.nodes[identity]);writes=deepcopy(self.provider.writes)
        for changes in ({'account_id':'999999'},{'daily_budget':'99999'},{'status':'ACTIVE'},{'targeting':{}}):
            self.provider.nodes[identity]={**original,**changes}
            result=self.service.reconcile(p['operation_id'],p['digest'])
            self.assertEqual(result['operation']['state'],'unknown')
        self.assertEqual(self.provider.writes,writes)

    def test_complete_saved_receipts_reverify_all_fields_and_dependencies(self):
        p=self.preview();self.execute(p);writes=deepcopy(self.provider.writes)
        before=self.service._get(p['operation_id'])['timings']
        result=self.new_service().reconcile(p['operation_id'],p['digest'])
        self.assertEqual(result['operation']['state'],'verified')
        self.assertEqual(result['job']['timings'],before)
        self.assertEqual(self.provider.writes,writes)
        creative=next(s for s in result['job']['steps'] if s['kind']=='adcreatives')
        self.provider.nodes[creative['provider_id']]['object_story_spec']['link_data']['message']='changed'
        result=self.service.reconcile(p['operation_id'],p['digest'])
        self.assertEqual(result['operation']['state'],'verified')
        self.assertEqual(result['job']['steps'][-1]['state'],'verified')
        self.assertEqual(result['reconciliation']['current_state'],'drift')
        self.assertEqual(result['reconciliation']['observations'][-1]['current_state'],'match')

    def test_historical_receipt_survives_drift_error_and_reopen(self):
        p=self.preview();self.execute(p)
        original=deepcopy(self.service._get(p['operation_id'])['steps']);writes=deepcopy(self.provider.writes)
        self.provider.nodes[original[0]['provider_id']]['daily_budget']='7777'
        reads=[];read=self.provider.read
        def capture(token,node,fields):
            reads.append(node);return read(token,node,fields)
        self.provider.read=capture
        result=self.service.reconcile(p['operation_id'],p['digest'])
        self.assertEqual(result['job']['steps'],original)
        self.assertEqual(result['execution_evidence']['steps'],original)
        self.assertEqual(result['reconciliation']['current_state'],'drift')
        self.assertIn(original[-1]['provider_id'],reads)
        self.provider.readback_fail=True
        result=self.new_service().reconcile(p['operation_id'],p['digest'])
        self.assertEqual(result['job']['steps'],original)
        self.assertEqual(result['execution_evidence']['steps'],original)
        self.assertEqual(result['reconciliation']['current_state'],'unavailable')
        self.assertEqual(len(result['reconciliation_history']),2)
        saved=self.new_service()._get(p['operation_id'])
        self.assertEqual(saved['execution_evidence']['steps'],original)
        self.assertEqual(len(saved['reconciliation_history']),2)
        self.assertEqual(self.provider.writes,writes)

    def test_digest_and_step_tampering_are_blocked_before_io(self):
        p=self.failed_read()
        with self.assertRaises(ValueError):self.service.reconcile(p['operation_id'],'a'*64)
        with self.service.db() as db:
            job=self.service._load(db,p['operation_id']);job['steps'][0]['params']['name']='tampered';self.service._save(db,job)
        with self.assertRaises(ValueError):self.service.reconcile(p['operation_id'],p['digest'])

    def test_read_failure_preserves_unknown_and_never_resubmits(self):
        p=self.failed_read();self.provider.readback_fail=True;writes=deepcopy(self.provider.writes)
        result=self.service.reconcile(p['operation_id'],p['digest'])
        self.assertEqual(result['operation']['state'],'unknown')
        self.assertNotIn('private provider body',str(result));self.assertEqual(self.provider.writes,writes)

    def test_saved_image_hash_recovers_without_upload_retry(self):
        original=self.provider.image_info
        def unavailable(*args):raise TimeoutError('private')
        p=self.preview();self.provider.image_info=unavailable;self.execute(p)
        self.provider.image_info=original;writes=deepcopy(self.provider.writes)
        result=self.new_service().reconcile(p['operation_id'],p['digest'])
        self.assertEqual(result['operation']['state'],'partial')
        image=next(s for s in result['job']['steps'] if s['kind']=='image')
        self.assertEqual(image['state'],'verified')
        self.assertFalse(image['result']['remote_byte_equality_verified'])
        self.assertEqual(self.provider.writes,writes)


if __name__=='__main__':unittest.main()
