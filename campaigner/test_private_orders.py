"""Private order-cache contract tests; synthetic identities and no network calls.

The reversible mock crypt verifies use of the encryption boundary, not DPAPI
cryptographic strength, which must be verified separately on Windows.
"""
import copy
import json
import tempfile
import threading
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock,patch
from private_orders import PrivateOrders


def fake_crypt(raw,decrypt=False):
    if decrypt:
        if not raw.startswith(b'MOCK:'):raise ValueError('Bad encrypted envelope')
        return raw[5:][::-1]
    return b'MOCK:'+raw[::-1]


def fixture():
    return {'binding':{'source_verified':True,'shop':'sandbox-shop.myshopify.com'},
            'coverage':{'complete':True,'error':None,'since':'2026-10-01','until':'2026-10-16'},
            'orders':[{'order_id':'synthetic-order-123','customer_id':'synthetic-customer-456','email':'synthetic-person@example.invalid','phone':'+000123456','identity_digest':'synthetic-private-digest'}],
            'excluded':[{'order_id':'synthetic-excluded-789','reason':'test-order'}]}


class PrivateOrdersTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.directory=Path(self.temp.name)
        self.patcher=patch('private_orders.crypt',side_effect=fake_crypt);self.crypt=self.patcher.start()
        self.reader=Mock(return_value=fixture())
    def tearDown(self):
        self.patcher.stop();self.temp.cleanup()
    def cache(self,**kwargs):return PrivateOrders(self.directory,reader=self.reader,**kwargs)

    def test_success_encrypts_persists_reloads_without_new_provider_read(self):
        cache=self.cache();self.assertTrue(cache.refresh())
        self.assertEqual(fixture(),cache.data)
        raw=cache.path.read_bytes();self.assertTrue(raw.startswith(b'MOCK:'))
        self.assertNotIn(b'synthetic-person@example.invalid',raw)
        self.assertFalse(cache.path.with_suffix('.pending').exists())
        restored=self.cache();self.assertEqual(cache.data,restored.data)
        self.assertEqual(cache.last_success,restored.last_success)
        self.assertEqual(1,self.reader.call_count)
        self.assertTrue(restored.summary()['complete'])
        self.assertEqual(1,restored.summary()['matchable_orders'])

    def test_summary_is_allowlisted_and_never_exports_customer_data(self):
        cache=self.cache();cache.refresh();summary=cache.summary();serialized=json.dumps(summary)
        self.assertEqual({'configured','refreshing','last_success_at','last_attempt_at','error','interval_seconds','source_verified','complete','matchable_orders','excluded_orders','since','until','storage','live_action_allowed'},set(summary))
        for private in ('synthetic-person@example.invalid','synthetic-order-123','synthetic-customer-456','+000123456','synthetic-private-digest','synthetic-excluded-789'):
            self.assertNotIn(private,serialized)
        self.assertFalse(summary['live_action_allowed'])
        self.assertEqual((1,1),(summary['matchable_orders'],summary['excluded_orders']))

    def test_empty_unconfigured_cache_does_not_claim_zero_purchases(self):
        summary=self.cache().summary()
        self.assertIsNone(summary['matchable_orders']);self.assertIsNone(summary['excluded_orders'])
        self.assertFalse(summary['complete']);self.assertFalse(summary['source_verified'])
        self.assertFalse(summary['configured'])

    def test_incomplete_result_preserves_last_good_memory_and_disk(self):
        cache=self.cache();cache.refresh();saved=copy.deepcopy(cache.data);raw=cache.path.read_bytes();at=cache.last_success
        failed=fixture();failed['coverage']['complete']=False;failed['orders']=[];self.reader.return_value=failed
        self.assertTrue(cache.refresh(force=True))
        self.assertEqual(saved,cache.data);self.assertEqual(raw,cache.path.read_bytes());self.assertEqual(at,cache.last_success)
        self.assertIsNotNone(cache.summary()['error']);self.assertEqual(1,cache.summary()['matchable_orders'])
        self.assertEqual(saved,self.cache().data)

    def test_coverage_error_rejected_even_with_complete_flag(self):
        cache=self.cache();cache.refresh();raw=cache.path.read_bytes()
        failed=fixture();failed['coverage']['error']='truncated';self.reader.return_value=failed
        cache.refresh(force=True);self.assertEqual(raw,cache.path.read_bytes());self.assertIsNotNone(cache.error)

    def test_exception_is_sanitized_and_next_success_clears_error(self):
        cache=self.cache();self.reader.side_effect=RuntimeError('SECRET_TOKEN synthetic-person@example.invalid')
        cache.refresh();summary=json.dumps(cache.summary());self.assertNotIn('SECRET_TOKEN',summary);self.assertNotIn('synthetic-person',summary)
        self.assertIsNone(cache.data);self.assertIsNotNone(cache.error)
        self.reader.side_effect=None;cache.refresh(force=True);self.assertIsNone(cache.error);self.assertIsNotNone(cache.data)

    def test_identity_secret_stable_private_and_passed_only_to_reader(self):
        cache=self.cache();first=cache.identity_secret();second=self.cache().identity_secret()
        self.assertEqual(32,len(first));self.assertEqual(first,second)
        self.assertNotEqual(first,cache.key_path.read_bytes())
        cache.refresh();self.reader.assert_called_once_with(self.directory,first)
        self.assertNotIn(first.hex(),json.dumps(cache.summary()))

    def test_singleflight_even_when_second_refresh_forced(self):
        entered=threading.Event();release=threading.Event()
        def slow_reader(*_):
            entered.set()
            if not release.wait(3):raise RuntimeError('Test release timeout')
            return fixture()
        self.reader.side_effect=slow_reader;cache=self.cache();answers=[]
        worker=threading.Thread(target=lambda:answers.append(cache.refresh()))
        worker.start()
        try:
            self.assertTrue(entered.wait(2));self.assertTrue(cache.summary()['refreshing'])
            self.assertFalse(cache.refresh(force=True));self.assertEqual(1,self.reader.call_count)
        finally:
            release.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual([True],answers)
        self.assertFalse(cache.summary()['refreshing'])

    def test_interval_suppresses_extra_provider_reads_but_force_is_explicit(self):
        cache=self.cache(interval_seconds=1800);self.assertTrue(cache.refresh())
        self.assertFalse(cache.refresh());self.assertEqual(1,self.reader.call_count)
        cache.last_attempt=(datetime.now(timezone.utc)-timedelta(seconds=1801)).isoformat()
        self.assertTrue(cache.refresh());self.assertEqual(2,self.reader.call_count)
        self.assertTrue(cache.refresh(force=True));self.assertEqual(3,self.reader.call_count)

    def test_failure_also_obeys_cadence_no_retry_storm(self):
        self.reader.side_effect=RuntimeError('Provider failure');cache=self.cache()
        self.assertTrue(cache.refresh());self.assertFalse(cache.refresh());self.assertEqual(1,self.reader.call_count)

    def test_corrupt_encrypted_cache_ignored_and_replaced_after_success(self):
        (self.directory/'cohort-orders.dpapi').write_bytes(b'corrupt')
        cache=self.cache();self.assertIsNone(cache.data)
        cache.refresh();self.assertEqual(fixture(),self.cache().data)

if __name__=='__main__':unittest.main()

