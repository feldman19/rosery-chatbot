"""Local transport-only per-dispatch authority and stop tests."""
import threading
import unittest
from datetime import timedelta
import test_campaign_operations as fixtures


class DispatchTests(unittest.TestCase):
    setUp=fixtures.CampaignOperationsTests.setUp
    tearDown=fixtures.CampaignOperationsTests.tearDown
    binding=fixtures.CampaignOperationsTests.binding
    new_service=fixtures.CampaignOperationsTests.new_service
    preview=fixtures.CampaignOperationsTests.preview
    approve=fixtures.CampaignOperationsTests.approve
    execute=fixtures.CampaignOperationsTests.execute

    def test_cancel_during_io_returns_before_release_and_prevents_next_post(self):
        entered=threading.Event();release=threading.Event();results=[]
        original=self.provider.create
        def slow(*args):
            entered.set()
            if not release.wait(3):raise TimeoutError('test release absent')
            return original(*args)
        self.provider.create=slow
        p=self.preview();self.approve(p)
        worker=threading.Thread(target=lambda:results.append(self.service.execute(p['operation_id'])))
        worker.start();self.assertTrue(entered.wait(2))
        try:
            cancel_result=[]
            canceller=threading.Thread(target=lambda:cancel_result.append(self.service.cancel(p['operation_id'],p['digest'])))
            canceller.start();canceller.join(1)
            self.assertFalse(canceller.is_alive(),'cancel must not wait for execute lock')
            self.assertTrue(cancel_result[0]['stop_requested'])
            status=self.service.status()
            self.assertTrue(next(x for x in status['jobs'] if x['id']==p['operation_id'])['stop_requested'])
            self.assertTrue(next(x for x in status['proposals'] if x['operation_id']==p['operation_id'])['stop_requested'])
        finally:
            release.set();worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.provider.writes),1)
        self.assertEqual(results[0]['operation']['state'],'partial')
        self.assertEqual(results[0]['job']['steps'][0]['state'],'verified')
        self.assertTrue(all(s['state']=='pending' for s in results[0]['job']['steps'][1:]))
        self.assertEqual(self.new_service()._get(p['operation_id'])['state'],'partial')
        self.assertTrue(self.new_service().status()['jobs'][0]['stop_requested'])

    def test_stop_readmodel_rejects_forged_signal(self):
        p=self.preview()
        self.assertFalse(self.service.status()['jobs'][0]['stop_requested'])
        with self.service.db() as db:
            db.execute('INSERT INTO operation_stops VALUES(?,?,?)',(p['operation_id'],'{}','invalid'))
        with self.assertRaises(ValueError):self.service.status()

    def test_expiry_after_upload_stops_next_create(self):
        original=self.provider.upload
        def upload(*args):
            result=original(*args);self.now+=timedelta(hours=1);return result
        self.provider.upload=upload
        p=self.preview();result=self.execute(p)
        self.assertEqual([k for k,p in self.provider.writes],['adsets','image'])
        self.assertEqual(result['operation']['state'],'partial')
        self.assertEqual(result['job']['steps'][1]['state'],'verified')
        self.assertEqual(result['job']['steps'][2]['state'],'pending')

    def test_revision_change_after_upload_stops_next_create(self):
        original=self.provider.upload
        def upload(*args):
            result=original(*args);self.proposal['request_revision']+=1;return result
        self.provider.upload=upload
        result=self.execute(self.preview())
        self.assertEqual([k for k,p in self.provider.writes],['adsets','image'])
        self.assertEqual(result['operation']['state'],'partial')

    def test_batch_stop_prevents_next_wave_not_inflight_items(self):
        self.provider=fixtures.BatchProvider();self.service=self.new_service()
        original=self.provider.batch
        def batch(*args):
            result=original(*args);self.service.cancel(p['operation_id'],p['digest']);return result
        self.provider.batch=batch
        p=self.preview();result=self.execute(p)
        self.assertEqual(len(self.provider.batches),1)
        self.assertEqual(len(self.provider.writes),1)
        self.assertEqual(result['operation']['state'],'partial')
        self.assertEqual(result['job']['steps'][0]['state'],'verified')

    def test_stop_cannot_convert_transport_timeout_into_not_attempted(self):
        original=self.provider.create
        def ambiguous(*args):
            original(*args);self.service.cancel(p['operation_id'],p['digest']);raise TimeoutError('unknown response')
        self.provider.create=ambiguous
        p=self.preview();result=self.execute(p)
        self.assertEqual(result['operation']['state'],'unknown')
        self.assertEqual(result['job']['steps'][0]['state'],'unknown')
        self.assertEqual(len(self.provider.writes),1)

    def test_worker_stop_after_first_readback_prevents_more_posts(self):
        self.service.stop_event=threading.Event()
        original=self.provider.create
        def create(*args):
            result=original(*args);self.service.stop();return result
        self.provider.create=create
        result=self.execute(self.preview())
        self.assertEqual(len(self.provider.writes),1)
        self.assertEqual(result['operation']['state'],'partial')


if __name__=='__main__':unittest.main()
