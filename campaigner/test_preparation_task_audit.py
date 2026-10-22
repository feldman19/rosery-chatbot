"""Independent failure-boundary audit. Temporary SQLite only; no providers."""
import sqlite3
import hashlib
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from preparation_task import PreparationTask
from work_intake import WorkIntake
from launch_workflow import LaunchWorkflow
from preparation_loop import PreparationLoop


class PreparationFailureAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.intake = WorkIntake(self.tmp.name)
        self.workflow = LaunchWorkflow(self.tmp.name)
        self.calls = []
        self.materials = SimpleNamespace(
            status=lambda request: {'configured': True, 'binding_verified': True,
                                    'assets': {'complete': True}},
            refresh=lambda request: self.calls.append('import') or True)
        self.proposal = SimpleNamespace(report=lambda request: {'available': False})
        self.loop = SimpleNamespace(report=lambda: {'job': None})
        self.service = self.make()
        self.save_body = {'action': 'save', 'idempotency_key': 'audit-save-0001',
                          'mode': 'new', 'brief': 'פתיחת האתר לקולקציה החדשה והמשך מכירות',
                          'drive_urls': ['https://drive.google.com/drive/folders/1234567890123456'],
                          'expected_request_id': None, 'expected_request_revision': None}

    def make(self):
        return PreparationTask(self.tmp.name, intake=self.intake,
                               workflow=self.workflow, materials=self.materials,
                               proposal=self.proposal, loop=self.loop)

    def body(self, action='import'):
        request = self.intake.latest()
        return {'action': action, 'idempotency_key': 'audit-action-0001',
                'request_id': request['id'], 'request_revision': request['revision']}

    def assert_unknown(self, operation):
        with self.assertRaises(Exception) as raised:
            operation()
        error = raised.exception
        self.assertEqual(getattr(error, 'status', None), 503)
        self.assertEqual(getattr(error, 'code', None), 'outcome_unknown')
        self.assertIs(getattr(error, 'ambiguous', None), True)
        self.assertNotIn('sensitive upstream detail', str(error))

    def test_import_side_effect_then_value_error_is_unknown_and_never_replayed(self):
        self.service.act(self.save_body)
        body = self.body()
        def interrupted(request):
            self.calls.append('queued-before-error')
            raise ValueError('sensitive upstream detail must not become validation')
        self.materials.refresh = interrupted
        self.assert_unknown(lambda: self.service.act(body))
        self.service = self.make()
        self.materials.refresh = lambda request: self.calls.append('repeated')
        self.assert_unknown(lambda: self.service.act(body))
        self.assertEqual(self.calls, ['queued-before-error'])

    def test_saved_intake_followed_by_report_failure_is_unknown_not_repeated(self):
        def interrupted_report():
            raise ValueError('projection unavailable after commit')
        self.loop.report = interrupted_report
        self.assert_unknown(lambda: self.service.act(self.save_body))
        self.assertEqual(self.intake.latest()['revision'], 1)
        self.loop.report = lambda: {'job': None}
        self.service = self.make()
        self.assert_unknown(lambda: self.service.act(self.save_body))
        self.assertEqual(self.intake.latest()['revision'], 1)

    def test_pending_receipt_seen_by_second_instance_is_unknown(self):
        self.service.act(self.save_body)
        body = self.body()
        peer = self.make()
        def in_progress(request):
            self.calls.append('first-instance')
            self.assert_unknown(lambda: peer.act(body))
            return True
        self.materials.refresh = in_progress
        result = self.service.act(body)
        self.assertTrue(result['collection_started_or_queued'])
        self.assertEqual(self.calls, ['first-instance'])

    def test_stale_binding_does_not_create_a_task_or_import(self):
        self.service.act(self.save_body)
        body = self.body()
        self.intake.create({'goal': 'מכירת קולקציה אחרת', 'drive_urls': []})
        with self.assertRaises(Exception) as raised:
            self.service.act(body)
        self.assertEqual(getattr(raised.exception, 'status', None), 409)
        self.assertIs(getattr(raised.exception, 'ambiguous', None), False)
        self.assertEqual(self.calls, [])
        with closing(sqlite3.connect(self.service.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0], 1)

    def test_unexpected_preflight_error_does_not_expose_provider_detail(self):
        self.service.act(self.save_body)
        def broken_status(request):
            raise ValueError('provider url?access_token=sensitive-example')
        self.materials.status = broken_status
        with self.assertRaises(Exception) as raised:
            self.service.act(self.body())
        self.assertEqual(getattr(raised.exception, 'status', None), 409)
        self.assertNotIn('sensitive-example', str(raised.exception))
        self.assertEqual(self.calls, [])

    def test_request_change_during_import_preflight_does_not_queue_new_request(self):
        self.service.act(self.save_body)
        body = self.body()
        def changing_status(request):
            self.intake.create({'goal': 'מכירת קולקציה אחרת', 'drive_urls': []})
            return {'configured': True}
        self.materials.status = changing_status
        with self.assertRaises(Exception) as raised:
            self.service.act(body)
        self.assertEqual(getattr(raised.exception, 'status', None), 409)
        self.assertEqual(self.calls, [])

    def test_loop_rejects_exact_binding_drift_before_creating_job(self):
        loop = PreparationLoop(self.tmp.name, b'test-only-key')
        expected = {'request_id': 'request-one', 'request_revision': 1,
                    'request_hash': 'a' * 64, 'artifact_hash': 'b' * 64,
                    'source_hash': 'c' * 64}
        actual = {**expected, 'artifact_hash': 'd' * 64}
        loop.context = lambda: ({'id': 'request-one', 'revision': 1}, {}, actual)
        with self.assertRaises(ValueError):
            loop.start(expected_binding=expected)
        with loop.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_equal_clock_jobs_keep_new_creation_latest_even_after_old_update(self):
        loop = PreparationLoop(self.tmp.name, b'test-only-key', clock=lambda: 1234567)
        binding = {'request_id': 'request-one', 'request_revision': 1,
                   'request_hash': 'a' * 64, 'artifact_hash': 'b' * 64,
                   'source_hash': 'c' * 64}
        loop.context = lambda: ({'id': 'request-one', 'revision': 1}, {}, binding)
        first = loop.start()
        binding = {**binding, 'artifact_hash': 'd' * 64}
        second = loop.start()
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(loop.report()['job']['id'], second['id'])
        with loop.db() as db:
            first['blocked_reason'] = 'late history update'
            loop._save(db, first)
        self.assertEqual(loop.report()['job']['id'], second['id'])

    def test_candidate_cache_reparse_outside_data_root_rejected(self):
        self.service.act(self.save_body)
        raw = b'\x89PNG\r\n\x1a\ncontrolled-not-decoded'
        sha = hashlib.sha256(raw).hexdigest()
        identity = 'image1234567890'
        cache = Path(self.tmp.name) / 'drive-assets'
        cache.mkdir()
        (cache / (identity + '-' + sha + '.bin')).write_bytes(raw)
        self.materials.status = lambda request: {
            'configured': True, 'binding_verified': True,
            'assets': {'complete': True, 'assets': [
                {'id': identity, 'sha256': sha, 'mime_type': 'image/png',
                 'content_read': True, 'format_check': 'decoded',
                 'dimensions': {'width': 1, 'height': 1}}]}}
        original_resolve = type(cache).resolve
        def resolve(path, *args, **kwargs):
            if path == cache:
                return Path(self.tmp.name) / 'outside-cache'
            return original_resolve(path, *args, **kwargs)
        # Simulate Windows junction resolution without needing junction privileges.
        with patch.object(type(cache), 'resolve', resolve), \
             patch('image_validation.decode_image') as decoder:
            with self.assertRaisesRegex(ValueError, 'cache location'):
                self.service.media(identity + '~' + sha)
            decoder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
