"""Real temporary HTTP contract for exact-bound legacy review start."""
import unittest
from unittest.mock import patch
import test_server


class LoopStartHTTP(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def binding(self):
        return {'request_id': 'request-one', 'request_revision': 1,
                'request_hash': 'a' * 64, 'artifact_hash': 'b' * 64,
                'source_hash': 'c' * 64}

    def test_empty_unbound_and_extra_fields_never_start(self):
        with patch('preparation_loop.PreparationLoop.start') as start:
            for body in ({}, {'binding': {}}, {'binding': self.binding(), 'extra': True}):
                self.assertEqual(self.request('/api/preparation-loop/start', body)[0], 400)
            start.assert_not_called()

    def test_exact_displayed_binding_forwarded_and_stale_rejected(self):
        binding = self.binding()
        with patch('preparation_loop.PreparationLoop.start', return_value={'id': 'job'}) as start, \
             patch('preparation_loop.PreparationLoop.start_worker') as worker:
            self.assertEqual(self.request('/api/preparation-loop/start', {'binding': binding})[0], 200)
            start.assert_called_once_with(expected_binding=binding)
            worker.assert_called_once()
        with patch('preparation_loop.PreparationLoop.start', side_effect=ValueError('changed evidence')), \
             patch('preparation_loop.PreparationLoop.start_worker') as worker:
            code, body = self.request('/api/preparation-loop/start', {'binding': binding})
            self.assertEqual(code, 409)
            self.assertFalse(body['ambiguous'])
            worker.assert_not_called()

    def test_queued_then_worker_failure_is_unknown(self):
        with patch('preparation_loop.PreparationLoop.start', return_value={'id': 'job'}), \
             patch('preparation_loop.PreparationLoop.start_worker', side_effect=RuntimeError('private details')):
            code, body = self.request('/api/preparation-loop/start', {'binding': self.binding()})
            self.assertEqual(code, 503)
            self.assertTrue(body['ambiguous'])
            self.assertNotIn('private details', str(body))


if __name__ == '__main__':
    unittest.main()
