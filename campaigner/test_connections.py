import io,json,unittest,urllib.error
from connections import connection_status,NoRedirect,SHOP


class ConnectionTests(unittest.TestCase):
    def test_missing_is_not_connected(self):
        r=connection_status({});self.assertFalse(r['shopify']['verified']);self.assertFalse(r['independent_collection'])

    def test_rejected_credentials_never_leak(self):
        def reject(*args,**kwargs):raise urllib.error.HTTPError('https://example',401,'secret',None,None)
        r=connection_status({'SHOPIFY_TOKEN':'private-secret'},reject)
        self.assertEqual(r['shopify']['state'],'credential_rejected');self.assertNotIn('private-secret',json.dumps(r))

    def test_identity_is_required_and_fixed_endpoint(self):
        def opened(request,**kwargs):
            self.assertEqual(request.full_url,f'https://{SHOP}/admin/api/2026-10/graphql.json')
            self.assertNotIn('mutation',json.loads(request.data)['query'])
            return io.BytesIO(json.dumps({'data':{'shop':{'myshopifyDomain':SHOP,'currencyCode':'ILS'},'currentAppInstallation':{'accessScopes':[{'handle':'read_reports'}]}}}).encode())
        r=connection_status({'SHOPIFY_TOKEN':'secret'},opened)
        self.assertTrue(r['shopify']['verified']);self.assertFalse(r['shopify']['analytics_verified'])

    def test_redirect_never_forwards_credential(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://other'))
