"""Controlled clock and synthetic provider only. No credentials or network."""
import unittest
from copy import deepcopy
from campaign_operations import CampaignOperations
from test_campaign_operations import CampaignOperationsTests, BatchProvider

class ControlledClock:
    def __init__(self):self.seconds=0.0
    def __call__(self):return self.seconds
    def advance(self,seconds):self.seconds+=seconds

class TimedProvider(BatchProvider):
    def __init__(self,clock,fail=None):super().__init__();self.timer=clock;self.fail=fail;self.calls=0
    def read(self,*args):self.timer.advance(2);return super().read(*args)
    def upload(self,*args):self.timer.advance(3);return super().upload(*args)
    def image_info(self,*args):self.timer.advance(2);return super().image_info(*args)
    def batch(self,*args):
        self.timer.advance(5);self.calls+=1
        if self.calls==2 and self.fail=='timeout':raise TimeoutError('private response not retained')
        if self.calls==2 and self.fail=='rejected':return [{'code':400,'body':'{"error":{"code":100}}'}]
        return super().batch(*args)

class OperationTimingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=CampaignOperationsTests(methodName='runTest');self.fixture.setUp()
        self.clock=ControlledClock()
    def tearDown(self):self.fixture.tearDown()
    def run_attempt(self,fail=None):
        f=self.fixture;self.provider=TimedProvider(self.clock,fail)
        def credentials():self.clock.advance(1);return 'synthetic-token'
        self.service=CampaignOperations(f.root,f.key,lambda:{'available':True,'proposal':deepcopy(f.proposal),'source_binding':f.binding()},
            credential_loader=credentials,transport=self.provider,clock=lambda:f.now,monotonic=self.clock)
        op=self.service.preview({'action':'prepare_launch','expected_source':f.binding()})['operation']
        self.service.approve(op['operation_id'],op['digest']);self.clock.seconds=0
        return self.service.execute(op['operation_id'])
    def test_exact_phase_durations_and_missing_unmeasured_stages(self):
        result=self.run_attempt();timing=result['job']['timings']
        self.assertEqual(result['job']['state'],'verified')
        self.assertEqual({k:v['seconds'] for k,v in timing['phases'].items()},
            {'preflight':5,'submission':18,'receipt_readback':8})
        self.assertEqual(timing['total_seconds'],31)
        self.assertEqual(timing['transport_kind'],'injected_not_meta')
        self.assertTrue(all(v is None for v in timing['missing'].values()))
        self.assertEqual(timing['operation_id'],result['job']['id'])
        self.assertEqual(self.service._get(result['job']['id'])['timings'],timing)
    def test_timeout_persists_measurements_and_never_resubmits(self):
        result=self.run_attempt('timeout');timing=result['job']['timings']
        self.assertEqual(result['job']['state'],'unknown')
        self.assertEqual(timing['total_seconds'],22)
        self.assertEqual(timing['phases']['submission']['seconds'],13)
        self.assertEqual(timing['outcome'],'unknown');self.assertTrue(timing['complete'])
        again=self.service.execute(result['job']['id'])
        self.assertEqual(again['job']['timings'],timing);self.assertEqual(self.provider.calls,2)
        self.assertTrue(any(s['state']=='unknown' for s in again['job']['steps']))
        # A fresh service must retain both the uncertainty and its measurement.
        f=self.fixture
        restarted=CampaignOperations(f.root,f.key,lambda:{'available':True,'proposal':deepcopy(f.proposal),'source_binding':f.binding()},
            credential_loader=lambda:'synthetic-token',transport=self.provider,clock=lambda:f.now,monotonic=self.clock)
        self.assertEqual(restarted.execute(result['job']['id'])['job']['timings'],timing)
        self.assertEqual(self.provider.calls,2)
    def test_partial_rejection_retains_timing_and_known_result(self):
        result=self.run_attempt('rejected')
        self.assertEqual(result['job']['state'],'partial')
        self.assertEqual(result['job']['timings']['total_seconds'],22)
        self.assertTrue(any(s.get('provider_id') for s in result['job']['steps']))
        self.assertEqual(self.service._get(result['job']['id'])['timings']['outcome'],'partial')

if __name__=='__main__':unittest.main()
