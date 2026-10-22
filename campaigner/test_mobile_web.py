import unittest,tempfile,json,time,http.client,threading
from pathlib import Path
from mobile_web import MobileAccess,make_server
class MobileAccessTests(unittest.TestCase):
 def test_aggregate_management_report_can_be_read_but_not_written_remotely(self):
  class Backend:
   def request(self,path,body=None):
    if path!='/api/management-report' or body is not None:raise AssertionError('Unexpected report request')
    return json.dumps({'windows':{'7':{'metrics':{'spend':120},'campaigns':[]}}}).encode(),'application/json'
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   Path(d,'mobile-origin.json').write_text(json.dumps({'origin':'https://example.trycloudflare.com'}))
   access=MobileAccess('x'*43,time.time()+100);session=access.login('x'*43)
   server=make_server(d,0,access,Backend());thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   def req(method):
    c=http.client.HTTPConnection('127.0.0.1',server.server_port)
    c.request(method,'/api/management-report',b'{}' if method=='POST' else None,{'Host':'example.trycloudflare.com','X-Forwarded-Proto':'https','Cookie':'rosery_mobile='+session,'Origin':'https://example.trycloudflare.com','Content-Type':'application/json'})
    r=c.getresponse();code=r.status;raw=r.read();c.close();return code,raw
   try:
    code,raw=req('GET');self.assertEqual(code,200);self.assertEqual(json.loads(raw)['windows']['7']['metrics']['spend'],120)
    self.assertEqual(req('POST')[0],403)
   finally:server.shutdown();server.server_close();thread.join()

 def test_remembered_device_survives_restart_and_logout(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   file=Path(d,'sessions.dpapi')
   a=MobileAccess('x'*43,100,None,file,30*24*3600)
   token=a.login('x'*43,50)
   b=MobileAccess('unused',100,None,file,30*24*3600)
   self.assertTrue(b.valid('rosery_mobile='+token,100000))
   self.assertFalse(b.valid('rosery_mobile='+token,50+30*24*3600))
   self.assertNotIn(token,a.sessions)
   b.revoke(token)
   c=MobileAccess('unused',100,None,file,30*24*3600)
   self.assertFalse(c.valid('rosery_mobile='+token,51))
 def test_consumed_link_stays_consumed_after_restart(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   first=MobileAccess('x'*43,100,d);self.assertTrue(first.login('x'*43,50))
   restarted=MobileAccess('x'*43,100,d);self.assertIsNone(restarted.login('x'*43,51))
   fresh=MobileAccess('y'*43,100,d);self.assertTrue(fresh.login('y'*43,51))
 def test_one_use_expiry_and_session(self):
  gate=MobileAccess('x'*43,100)
  self.assertIsNone(gate.login('bad',50));session=gate.login('x'*43,50)
  self.assertTrue(gate.valid('rosery_mobile='+session,51));self.assertFalse(gate.valid('rosery_mobile='+session,30000))
  self.assertIsNone(gate.login('x'*43,51));self.assertIsNone(MobileAccess('x'*43,100).login('x'*43,101))
 def test_remote_data_requires_cookie_and_writes_blocked(self):
  class Backend:
   def request(self,*args):raise AssertionError('Unauthorized backend access')
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   Path(d,'mobile-origin.json').write_text(json.dumps({'origin':'https://example.trycloudflare.com'}))
   server=make_server(d,0,MobileAccess('x'*43,time.time()+100),Backend());thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   def req(method,path,body=None,extra=None):
    c=http.client.HTTPConnection('127.0.0.1',server.server_port);headers={'Host':'example.trycloudflare.com','X-Forwarded-Proto':'https',**(extra or {})};c.request(method,path,body,headers);r=c.getresponse();code=r.status;data=r.read();cookie=r.getheader('Set-Cookie');c.close();return code,data,cookie
   try:
    self.assertEqual(req('GET','/api/status')[0],401)
    self.assertEqual(req('POST','/api/tasks',b'{}')[0],403)
    self.assertEqual(req('POST','/auth',b'{}')[0],403)
    code,_,cookie=req('POST','/auth',json.dumps({'key':'x'*43}),{'Origin':'https://example.trycloudflare.com','Content-Type':'application/json'})
    self.assertEqual(code,200);self.assertIn('HttpOnly; Secure; SameSite=Strict',cookie)
    self.assertEqual(req('GET','/.local/signing.key',extra={'Cookie':cookie})[0],404)
    self.assertEqual(req('POST','/api/work/request',b'{}',{'Cookie':cookie,'Origin':'https://example.trycloudflare.com'})[0],403)
   finally:server.shutdown();server.server_close();thread.join()
 def test_authenticated_summary_filters_backend_and_note_origin(self):
  class Backend:
   def __init__(self):self.calls=[]
   def request(self,path,body=None):
    self.calls.append((path,body))
    return json.dumps({'goal':'launch','current_state':'draft','private_orders':['not exposed']}).encode(),'application/json'
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   Path(d,'mobile-origin.json').write_text(json.dumps({'origin':'https://example.trycloudflare.com'}))
   access=MobileAccess('x'*43,time.time()+100);session=access.login('x'*43);backend=Backend()
   server=make_server(d,0,access,backend);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   def req(method,path,body=None,origin='https://example.trycloudflare.com'):
    c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request(method,path,body,{'Host':'example.trycloudflare.com','X-Forwarded-Proto':'https','Origin':origin,'Cookie':'rosery_mobile='+session,'Content-Type':'application/json'})
    r=c.getresponse();code=r.status;raw=r.read();c.close();return code,raw
   try:
    code,raw=req('GET','/api/status');self.assertEqual(code,200);self.assertNotIn(b'private_orders',raw)
    self.assertEqual(req('POST','/api/notes',b'{"question":"test"}',origin='https://evil.example')[0],403)
    self.assertEqual(req('POST','/api/notes',b'{"question":"test","budget":100}')[0],400)
    self.assertEqual(len(backend.calls),1)
   finally:server.shutdown();server.server_close();thread.join()
