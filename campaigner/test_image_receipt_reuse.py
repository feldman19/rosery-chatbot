"""Signed receipt reuse with controlled time and a synthetic provider only."""
import hashlib
import unittest
from test_operation_timings import OperationTimingTests

class ImageReceiptReuseTests(unittest.TestCase):
    def setUp(self):
        self.case=OperationTimingTests(methodName='runTest');self.case.setUp()
        # Initial creation is a separately approved, synthetic single-copy scope.
        self.first=self.case.run_attempt();self.service=self.case.service
        self.provider=self.case.provider
    def tearDown(self):self.case.tearDown()
    def next_attempt(self):
        f=self.case.fixture;f.proposal['ads'][0]['primary_text']+=' reviewed revision'
        p=self.service.preview({'action':'prepare_launch','expected_source':f.binding()})['operation']
        self.service.approve(p['operation_id'],p['digest']);self.case.clock.seconds=0
        return self.service.execute(p['operation_id'])
    def image_step(self,result):return next(s for s in self.service._get(result['job']['id'])['steps'] if s['kind']=='image')
    def mutate_old_receipt(self,mutate):
        job=self.service._get(self.first['job']['id']);step=next(s for s in job['steps'] if s['kind']=='image');mutate(job,step)
        with self.service.db() as db:self.service._save(db,job)
    def uploads(self):return len([x for x in self.provider.writes if x[0]=='image'])
    def test_same_bytes_reuse_verified_receipt_without_upload(self):
        result=self.next_attempt();step=self.image_step(result)
        self.assertEqual(result['job']['state'],'verified');self.assertEqual(self.uploads(),1)
        self.assertTrue(step['result']['reused'])
        self.assertEqual(step['result']['source_receipt']['operation_id'],self.first['job']['id'])
        self.assertEqual(result['job']['timings']['total_seconds'],28)
        self.assertEqual(result['job']['timings']['transport_kind'],'injected_not_meta')
    def test_changed_source_bytes_do_not_receive_previous_hash(self):
        f=self.case.fixture;raw=f.file.read_bytes()+b'\0';sha=hashlib.sha256(raw).hexdigest()
        (f.root/'drive-assets'/(f.asset+'-'+sha+'.bin')).write_bytes(raw);f.proposal['ads'][0]['sha256']=sha
        result=self.next_attempt();self.assertEqual(result['job']['state'],'verified')
        self.assertEqual(self.uploads(),2);self.assertFalse(self.image_step(result)['result']['reused'])
        self.assertNotEqual(self.image_step(result)['result']['hash'],self.image_step(self.first)['result']['hash'])
    def test_wrong_account_provenance_is_not_a_hit(self):
        self.mutate_old_receipt(lambda job,step:step['result']['upload_provenance'].update(account_id='999999'))
        result=self.next_attempt();self.assertEqual(self.uploads(),2)
        self.assertFalse(self.image_step(result)['result']['reused'])
    def test_name_and_hash_without_upload_provenance_are_not_a_hit(self):
        self.mutate_old_receipt(lambda job,step:step['result'].pop('upload_provenance'))
        result=self.next_attempt();self.assertEqual(self.uploads(),2)
        self.assertFalse(self.image_step(result)['result']['reused'])
    def test_wrong_library_account_stops_without_upload(self):
        old=self.image_step(self.first)['result']['hash'];self.provider.images[old]['account_id']='999999'
        result=self.next_attempt();self.assertEqual(result['job']['state'],'unknown')
        self.assertEqual(self.uploads(),1)
    def test_library_timeout_does_not_fallback_or_retry(self):
        def unavailable(*args):self.case.clock.advance(2);raise TimeoutError('private details')
        self.provider.image_info=unavailable
        result=self.next_attempt();self.assertEqual(result['job']['state'],'unknown');self.assertEqual(self.uploads(),1)
        again=self.service.execute(result['job']['id']);self.assertEqual(again['job']['state'],'unknown');self.assertEqual(self.uploads(),1)
    def test_definite_missing_image_is_not_reused(self):
        original=self.provider.image_info;calls=[]
        def missing_once(*args):
            calls.append(1)
            if len(calls)==1:self.case.clock.advance(2);return {'data':[]}
            return original(*args)
        self.provider.image_info=missing_once
        result=self.next_attempt();self.assertEqual(result['job']['state'],'verified')
        self.assertEqual(self.uploads(),2);self.assertFalse(self.image_step(result)['result']['reused'])
    def test_uncertain_prior_upload_blocks_even_new_content_revision(self):
        def uncertain(job,step):job['state']='unknown';step['state']='unknown'
        self.mutate_old_receipt(uncertain)
        result=self.next_attempt();self.assertEqual(result['job']['state'],'unknown');self.assertEqual(self.uploads(),1)

if __name__=='__main__':unittest.main()
