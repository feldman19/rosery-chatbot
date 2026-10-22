import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import meta_write_connection as writer

class WriteConnectionTests(unittest.TestCase):
    APP_ID='999111222333'
    def bind(self,directory):
        (Path(directory)/'signing.key').write_bytes(b'x'*32)
        writer.bind_executor_app(directory,self.APP_ID)
    def replies(self,scopes=('ads_read','ads_management')):
        return [{'id':self.APP_ID},{'data':[{'permission':x,'status':'granted'} for x in scopes]},
                {'account_id':writer.ACCOUNT,'currency':'ILS'}]
    def test_valid_scope_does_not_grant_campaign_action(self):
        with patch.object(writer,'graph',side_effect=self.replies()):r=writer.verify('x'*40,self.APP_ID)
        self.assertTrue(r['verified']);self.assertFalse(r['campaign_action_authorized'])
    def test_missing_or_broader_scopes_rejected(self):
        for scopes in [('ads_read',),('ads_read','ads_management','business_management')]:
            with patch.object(writer,'graph',side_effect=self.replies(scopes)):
                with self.assertRaises(ValueError):writer.verify('x'*40,self.APP_ID)
    def test_wrong_account_and_app_rejected(self):
        for index,entry in [(0,{'id':'other'}),(2,{'account_id':'other','currency':'ILS'}),(2,{'account_id':writer.ACCOUNT,'currency':'USD'})]:
            responses=self.replies();responses[index]=entry
            with patch.object(writer,'graph',side_effect=responses):
                with self.assertRaises(ValueError):writer.verify('x'*40,self.APP_ID)
    def test_reporting_credential_never_replaced(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            directory=Path(temp);self.bind(directory);old=directory/'meta-report.dpapi';old.write_bytes(b'old-read')
            with patch.object(writer,'graph',side_effect=self.replies()),patch.object(writer,'crypt',side_effect=lambda b:b'encrypted:'+b):r=writer.configure(directory,'x'*40)
            self.assertEqual(old.read_bytes(),b'old-read');self.assertTrue((directory/'meta-execution.dpapi').read_bytes().startswith(b'encrypted:'))
            self.assertNotIn('x'*40,str(r));self.assertFalse(r['campaign_action_authorized'])
    def test_failed_verify_never_saves(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp,patch.object(writer,'graph',side_effect=ValueError('unavailable')):
            self.bind(temp)
            with self.assertRaises(ValueError):writer.configure(temp,'x'*40)
            self.assertFalse((Path(temp)/'meta-execution.dpapi').exists())
    def test_unconfigured_status_no_network(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp,patch.object(writer,'graph') as graph:
            self.assertFalse(writer.status(temp)['configured']);graph.assert_not_called()

    def test_reader_app_cannot_be_bound_as_executor(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            with self.assertRaises(ValueError):writer.bind_executor_app(temp,writer.READER_APP_ID)
        with patch.object(writer,'graph') as graph:
            with self.assertRaises(ValueError):writer.verify('x'*40,writer.READER_APP_ID)
            graph.assert_not_called()

    def test_third_app_token_rejected_and_tampered_binding_not_used(self):
        with patch.object(writer,'graph',return_value={'id':'444444444'}):
            with self.assertRaises(ValueError):writer.verify('x'*40,self.APP_ID)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            self.bind(temp);path=Path(temp)/'meta-executor-app.json'
            path.write_text(path.read_text().replace(self.APP_ID,'444444444'))
            with patch.object(writer,'graph') as graph:
                with self.assertRaises(ValueError):writer.configure(temp,'x'*40)
                graph.assert_not_called()
