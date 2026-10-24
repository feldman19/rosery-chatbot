import unittest
from unittest.mock import patch
import test_server

class SelfCheckHTTPTests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request

    def test_owner_session_and_no_collection_from_http(self):
        with patch('self_check.SelfCheck.run',side_effect=AssertionError('No collection from HTTP')):
            self.assertEqual(self.request('/api/self-check',token=False)[0],403)
            code,result=self.request('/api/self-check')
            self.assertEqual(code,200)
            self.assertEqual(result['status'],'warn')
            self.assertEqual(self.request('/api/self-check',{})[0],404)

    def test_saved_status_and_brief(self):
        with patch('self_check.SelfCheck.latest',return_value={'status':'fail','checks':[{'id':'meta_spend','status':'fail','reason':'פער בנתונים'}]}):
            self.assertEqual(self.request('/api/self-check')[1]['status'],'fail')
            code,result=self.request('/api/daily-brief')
            self.assertEqual(code,200)
            self.assertTrue(result['text'].startswith('בדיקה עצמית: פער בנתונים'))
