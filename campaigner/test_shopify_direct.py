import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from shopify_direct import crypt,configure,exchange,read_reports,CLIENT_ID,SHOP


class DirectTests(unittest.TestCase):
    def test_windows_encryption_roundtrip(self):
        encrypted=crypt(b'test-value-not-a-key')
        self.assertNotIn(b'test-value-not-a-key',encrypted)
        self.assertEqual(crypt(encrypted,True),b'test-value-not-a-key')

    def test_broader_scope_is_rejected(self):
        with patch('shopify_direct.request_json',return_value={'scope':'read_reports,write_orders','access_token':'test','expires_in':100}):
            with self.assertRaises(ValueError):exchange('not-a-real-key')

    def test_wrong_identity_never_saves(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            with patch('shopify_direct.exchange',return_value=('test',100)),patch('shopify_direct.graph',return_value={'shop':{'myshopifyDomain':'other.myshopify.com'}}):
                with self.assertRaises(ValueError):configure(temp,CLIENT_ID,'x'*32)
            self.assertEqual(list(Path(temp).iterdir()),[])

    def test_report_uses_provider_days_and_fixed_aggregate_query(self):
        responses=[{'shop':{'myshopifyDomain':SHOP,'currencyCode':'ILS','ianaTimezone':'Asia/Jerusalem'}},
                   {'shopifyqlQuery':{'parseErrors':[],'tableData':{'columns':[],'rows':[{'day':'2026-10-16'}]}}},OSError()]
        with patch('shopify_direct.get_token',return_value='fake'),patch('shopify_direct.graph',side_effect=responses) as graph:
            r=read_reports('unused')
            self.assertEqual(r['timezone'],'Asia/Jerusalem');self.assertIsNotNone(r['partial_date']);self.assertTrue(r['partial']);self.assertTrue(r['verified']);self.assertEqual(r['until'],'2026-10-16')
            self.assertIn('UNTIL today',graph.call_args_list[1].args[2]['q'])
