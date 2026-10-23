import unittest
from unittest.mock import patch
import test_server


class AutomationHTTPTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_policy_routes_and_existing_stop(self):
        with patch('meta_write_connection.load', side_effect=AssertionError('No write credentials')) as loader:
            self.assertEqual(self.request('/api/automation-shadow', token=False)[0], 403)
            body = dict(action='pause_ad', mode='shadow', roas_threshold=2.1, interval_hours=2, shadow_days=14)
            self.assertEqual(self.request('/api/automation-shadow/policy', body, origin=False)[0], 403)
            code, saved = self.request('/api/automation-shadow/policy', body)
            self.assertEqual(code, 200)
            self.assertEqual(saved['effective_mode'], 'shadow')
            self.assertEqual(self.request('/api/automation-shadow')[1]['policy']['roas_threshold'], 2.1)
            self.assertEqual(self.request('/api/meta-execution/stop', {})[0], 200)
            self.assertEqual(self.request('/api/automation-shadow')[1]['effective_mode'], 'manual')
            self.assertEqual(self.request('/api/automation-shadow/policy', {**body, 'mode': 'auto'})[0], 400)
            self.assertEqual(self.request('/api/automation-shadow/policy', body)[0], 200)
            self.assertEqual(self.request('/api/automation-shadow/stop', {})[1]['effective_mode'], 'manual')
            loader.assert_not_called()

