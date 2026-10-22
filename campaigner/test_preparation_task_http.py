import unittest
import urllib.request,urllib.error
from unittest.mock import patch
import test_server
from preparation_task import PreparationActionError

class PreparationTaskHTTP(unittest.TestCase):
 setUp=test_server.ServerTests.setUp
 tearDown=test_server.ServerTests.tearDown
 request=test_server.ServerTests.request
 def save(self):
  return self.request('/api/preparation-task',{'action':'save','idempotency_key':'http_save_123','mode':'new','brief':'sales launch collection','drive_urls':['https://drive.google.com/drive/folders/1234567890123456'],'expected_request_id':None,'expected_request_revision':None})
 def test_actual_save_restore_import_duplicate_and_stale(self):
  self.assertEqual(self.request('/api/preparation-task',token=False)[0],403)
  code,report=self.save();self.assertEqual(code,200);request=report['request'];self.assertEqual(report['task']['request_id'],request['id'])
  self.assertEqual(self.save()[1]['task']['id'],report['task']['id'])
  body={'action':'import','idempotency_key':'http_import_123','request_id':request['id'],'request_revision':request['revision']}
  with patch('launch_materials.LaunchMaterials.status',return_value={'configured':True}),patch('launch_materials.LaunchMaterials.refresh',return_value=True) as read:
   self.assertEqual(self.request('/api/preparation-task',body)[0],200);self.assertEqual(self.request('/api/preparation-task',body)[0],200);read.assert_called_once()
  body.update(idempotency_key='http_stale_123',request_revision=999)
  self.assertEqual(self.request('/api/preparation-task',body)[0],409)
 def test_candidate_preview_requires_scoped_cookie_and_same_origin(self):
  media='/api/preparation-task/media/'+'a'*12+'~'+'b'*64
  with urllib.request.urlopen(self.base) as response:
   cookies=response.headers.get_all('Set-Cookie')
  cookie=next(v.split(';',1)[0] for v in cookies if v.startswith('campaigner_preparation_media='))
  with patch('server.PreparationTask.media',return_value=(b'PNG-BYTES','image/png')) as render:
   with self.assertRaises(urllib.error.HTTPError) as failure:urllib.request.urlopen(self.base+media)
   self.assertEqual(failure.exception.code,403)
   with urllib.request.urlopen(urllib.request.Request(self.base+media,headers={'Cookie':cookie,'Sec-Fetch-Site':'same-origin'})) as response:
    self.assertEqual(response.read(),b'PNG-BYTES');self.assertEqual(response.headers['Content-Type'],'image/png')
   render.assert_called_once()
   with self.assertRaises(urllib.error.HTTPError) as failure:urllib.request.urlopen(urllib.request.Request(self.base+media,headers={'Cookie':cookie,'Sec-Fetch-Site':'cross-site'}))
   self.assertEqual(failure.exception.code,403)
 def test_unknown_error_contract_no_raw_exception(self):
  with patch('server.PreparationTask.act',side_effect=PreparationActionError('Check status',status=503,code='outcome_unknown',ambiguous=True)):
   code,body=self.request('/api/preparation-task',{'action':'start'})
  self.assertEqual(code,503);self.assertEqual(body['code'],'outcome_unknown');self.assertTrue(body['ambiguous'])
 def test_thumbnail_uses_same_scoped_media_auth(self):
  path='/api/preparation-task/media/thumb/'+'a'*12+'~'+'b'*64
  with urllib.request.urlopen(self.base) as response:cookies=response.headers.get_all('Set-Cookie')
  cookie=next(v.split(';',1)[0] for v in cookies if v.startswith('campaigner_preparation_media='))
  with patch('server.PreparationTask.thumbnail',return_value=(b'JPEG-THUMB','image/jpeg')) as render:
   with self.assertRaises(urllib.error.HTTPError) as failure:urllib.request.urlopen(self.base+path)
   self.assertEqual(failure.exception.code,403)
   with urllib.request.urlopen(urllib.request.Request(self.base+path,headers={'Cookie':cookie,'Sec-Fetch-Site':'same-origin'})) as response:
    self.assertEqual(response.read(),b'JPEG-THUMB');self.assertEqual(response.headers['Content-Type'],'image/jpeg')
   render.assert_called_once()
 def test_review_not_faked_without_bound_proposal(self):
  _,report=self.save();r=report['request']
  with patch('preparation_loop.PreparationLoop.start') as start:
   code,_=self.request('/api/preparation-task',{'action':'review','idempotency_key':'http_review_123','request_id':r['id'],'request_revision':r['revision']})
  self.assertEqual(code,409);start.assert_not_called()

if __name__=='__main__':unittest.main()
