import unittest
from private_mobile_gateway import permitted,make_gateway

class MobileGatewayTests(unittest.TestCase):
 def allowed(self,method='GET',path='/',peer='100.64.0.2',host='100.64.0.1:8768',origin=None):
  return permitted(method,path,peer,host,origin,'100.64.0.1','100.64.0.2',8768)
 def test_pinned_phone_only_and_exact_host(self):
  self.assertTrue(self.allowed());self.assertFalse(self.allowed(peer='100.64.0.3'))
  self.assertFalse(self.allowed(peer='127.0.0.1'));self.assertFalse(self.allowed(host='evil.example'))
 def test_only_note_writes_and_same_origin(self):
  self.assertTrue(self.allowed('POST','/api/help/escalate',origin='http://100.64.0.1:8768'))
  self.assertFalse(self.allowed('POST','/api/help/escalate',origin='https://evil.example'))
  for path in ('/api/work/request','/api/meta-execution/configure','/api/tasks','/api/help/resolve'):
   self.assertFalse(self.allowed('POST',path,origin='http://100.64.0.1:8768'))
 def test_no_oauth_or_files_or_traversal(self):
  for path in ('/api/drive/oauth/callback?code=x','/.local/signing.key','/lab','/api/launch/proposal/media/../secret'):
   self.assertFalse(self.allowed(path=path))
 def test_never_bind_publicly(self):
  for address in ('0.0.0.0','192.168.1.10','127.0.0.1','8.8.8.8'):
   with self.assertRaises(ValueError):make_gateway(address,'100.64.0.2')
