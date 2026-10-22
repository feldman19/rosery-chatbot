"""Synthetic Flashy adapter contract tests: no provider requests or real keys."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import flashy_direct as flashy

KEY='synthetic_key_for_tests_only_123456'

def mock_crypt(raw,decrypt=False):
    if decrypt:
        if not raw.startswith(b'MOCK:'):raise ValueError('Invalid envelope')
        return raw[5:][::-1]
    return b'MOCK:'+raw[::-1]


class FlashyDirectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.directory=Path(self.temp.name)
        self.response=Mock();self.response.__enter__=Mock(return_value=self.response);self.response.__exit__=Mock(return_value=False)
        self.response.read.return_value=b'{"data":{"id":11126}}'
        self.opener=Mock();self.opener.open.return_value=self.response
        self.build_patch=patch('flashy_direct.urllib.request.build_opener',return_value=self.opener)
        self.build=self.build_patch.start();self.crypt_patch=patch('flashy_direct.crypt',side_effect=mock_crypt);self.crypt=self.crypt_patch.start()
    def tearDown(self):
        self.crypt_patch.stop();self.build_patch.stop();self.temp.cleanup()

    def test_fixed_allowlisted_get_headers_and_timeout(self):
        for path in ('account','lists'):
            flashy.get(KEY,path)
            req=self.opener.open.call_args.args[0]
            self.assertEqual('https://api.flashy.app/'+path,req.full_url)
            self.assertEqual('GET',req.get_method());self.assertIsNone(req.data)
            self.assertEqual(KEY,req.get_header('X-api-key'));self.assertEqual(20,self.opener.open.call_args.kwargs['timeout'])
            self.response.read.assert_called_with(1048577)

    def test_arbitrary_destinations_rejected_before_network(self):
        for path in ('https://evil.invalid/account','//evil.invalid','account?key=x','account/../contacts','contacts','lists/123/contacts','/account','account#fragment','account%2fcontacts'):
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'Unverified Flashy path'):
                flashy.get(KEY,path)
        self.build.assert_not_called()

    def test_redirects_disabled(self):
        flashy.get(KEY,'account');handler=self.build.call_args.args[0]
        self.assertIsInstance(handler,flashy.NoRedirect)
        self.assertIsNone(handler.redirect_request(None,None,302,'redirect',{},'https://evil.invalid'))

    def test_oversized_response_rejected_sanitized(self):
        self.response.read.return_value=b'x'*1048577
        with self.assertRaisesRegex(ValueError,'^Flashy read unavailable$'):flashy.get(KEY,'account')
        self.response.read.assert_called_once_with(1048577)

    def test_exact_boundary_response_accepted(self):
        payload=b'{"data":{"id":"11126"}}';self.response.read.return_value=payload+b' '*(1048576-len(payload))
        self.assertEqual('11126',flashy.verified_account(flashy.get(KEY,'account')))

    def test_network_error_does_not_expose_key_or_url(self):
        self.opener.open.side_effect=RuntimeError(KEY+' https://secret.invalid')
        with self.assertRaises(ValueError) as caught:flashy.get(KEY,'account')
        self.assertEqual('Flashy read unavailable',str(caught.exception));self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(KEY,str(caught.exception))

    def test_invalid_json_is_sanitized(self):
        self.response.read.return_value=b'not JSON '+KEY.encode()
        with self.assertRaisesRegex(ValueError,'^Flashy read unavailable$'):flashy.get(KEY,'account')

    def test_invalid_credential_never_requests(self):
        for key in ('short','a'*257,'a'*20+'\n','a'*20+'?secret=x',None):
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'Invalid Flashy credential'):flashy.get(key,'account')
        self.build.assert_not_called()

    def test_verified_account_requires_exact_nested_identity(self):
        for response in ({'id':11126},{'data':{'id':11127}},{'data':{'id':'011126'}},{'data':{'id':True}},None,[],{'data':[]}):
            with self.subTest(response=response),self.assertRaisesRegex(ValueError,'identity not verified'):flashy.verified_account(response)
        self.assertEqual('11126',flashy.verified_account({'data':{'id':11126}}))
        self.assertEqual('11126',flashy.verified_account({'data':{'id':'11126'}}))

    def test_mismatch_does_not_save_or_replace_existing_credential(self):
        target=self.directory/'flashy-read.dpapi';target.write_bytes(b'existing-encrypted-secret')
        self.response.read.return_value=b'{"data":{"id":11127}}'
        with self.assertRaises(ValueError):flashy.configure(self.directory,KEY)
        self.assertEqual(b'existing-encrypted-secret',target.read_bytes());self.crypt.assert_not_called()
        self.assertFalse(target.with_suffix('.pending').exists())

    def test_success_saves_encrypted_key_without_key_in_return(self):
        result=flashy.configure(self.directory,KEY);target=self.directory/'flashy-read.dpapi'
        self.assertTrue(result['saved']);self.assertEqual('11126',result['account_id'])
        self.assertFalse(result['provider_read_only_scope']);self.assertFalse(result['registration_source_connected'])
        self.assertEqual(['GET'],result['adapter_methods']);self.assertNotIn(KEY,json.dumps(result))
        self.assertEqual(mock_crypt(KEY.encode()),target.read_bytes());self.assertNotIn(KEY.encode(),target.read_bytes())
        self.assertFalse(target.with_suffix('.pending').exists())

    def test_status_verifies_saved_key_but_never_exposes_it(self):
        flashy.configure(self.directory,KEY);result=flashy.status(self.directory)
        self.assertTrue(result['configured']);self.assertTrue(result['verified']);self.assertNotIn(KEY,json.dumps(result))
        self.assertEqual(KEY,self.opener.open.call_args.args[0].get_header('X-api-key'))

    def test_unconfigured_status_no_network_or_zero_claim(self):
        result=flashy.status(self.directory);self.assertFalse(result['configured']);self.assertFalse(result['verified']);self.build.assert_not_called()

if __name__=='__main__':unittest.main()
