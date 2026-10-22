import unittest,json,tempfile,time,threading,http.client
from pathlib import Path
from mobile_web import make_server,MobileAccess,valid_operation_body


class MobileOperationTests(unittest.TestCase):
 def test_exact_body_only(self):
  base='/api/campaign-operations/'
  self.assertTrue(valid_operation_body(base+'preview',{'action':'prepare_launch'}))
  self.assertTrue(valid_operation_body(base+'preview',{'action':'prepare_launch','format_mode':'dedicated_feed_story_single_copy'}))
  self.assertFalse(valid_operation_body(base+'preview',{'action':'prepare_launch','format_mode':'unproven_five_copy'}))
  self.assertTrue(valid_operation_body(base+'preview',{'action':'set_budget','entity_type':'adset','entity_id':'123456','budget_minor':12000}))
  self.assertFalse(valid_operation_body(base+'preview',{'action':'set_budget','entity_type':'adset','entity_id':'123456','budget_minor':True}))
  self.assertFalse(valid_operation_body(base+'preview',{'action':'activate','entity_type':'campaign','entity_id':'123456','account_id':'other'}))
  self.assertFalse(valid_operation_body(base+'execute',{'operation_id':'a'*32,'approved':True}))
  self.assertTrue(valid_operation_body(base+'approve',{'operation_id':'a'*32,'digest':'b'*64}))
  self.assertFalse(valid_operation_body(base+'approve',{'operation_id':'a'*32,'digest':'bad'}))

 def test_relay_requires_explicit_configuration_cookie_and_origin(self):
  class Backend:
   def __init__(self):self.calls=[]
   def request(self,path,body=None):self.calls.append((path,body));return b'{"accepted":true}','application/json'
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   config=Path(d,'mobile-origin.json');config.write_text(json.dumps({'origin':'https://example.trycloudflare.com'}))
   gate=MobileAccess('x'*43,time.time()+100);session=gate.login('x'*43);backend=Backend()
   server=make_server(d,0,gate,backend);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
   def req(path,body=None,authenticated=True,origin='https://example.trycloudflare.com'):
    c=http.client.HTTPConnection('127.0.0.1',server.server_port)
    headers={'Host':'example.trycloudflare.com','X-Forwarded-Proto':'https','Origin':origin,'Content-Type':'application/json'}
    if authenticated:headers['Cookie']='rosery_mobile='+session
    c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
    r=c.getresponse();code=r.status;raw=r.read();c.close();return code,json.loads(raw)
   try:
    self.assertFalse(req('/api/remote-capabilities')[1]['can_manage_campaigns'])
    self.assertEqual(req('/api/campaign-operations/preview',{'action':'prepare_launch'})[0],403)
    self.assertEqual(backend.calls,[])
    config.write_text(json.dumps({'origin':'https://example.trycloudflare.com','remote_execution_enabled':True}))
    self.assertEqual(req('/api/campaign-operations/preview',{'action':'prepare_launch'},authenticated=False)[0],401)
    self.assertEqual(req('/api/campaign-operations/preview',{'action':'prepare_launch'},origin='https://evil.example')[0],403)
    self.assertEqual(req('/api/campaign-operations/execute',{'operation_id':'a'*32,'approved':True})[0],400)
    self.assertEqual(backend.calls,[])
    self.assertEqual(req('/api/campaign-operations/preview',{'action':'prepare_launch'})[0],200)
    self.assertEqual(backend.calls,[('/api/campaign-operations/preview',b'{"action": "prepare_launch"}')])
    self.assertEqual(req('/api/work/request',{'action':'activate'})[0],403)
    self.assertEqual(req('/api/campaign-operations')[0],200)
   finally:server.shutdown();server.server_close();worker.join()

if __name__=='__main__':unittest.main()
