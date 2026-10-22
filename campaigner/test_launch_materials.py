from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from launch_materials import LaunchMaterials


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.request={'id':'request','revision':1,'drive_urls':['https://drive.google.com/drive/folders/abcdefghijk']}
    def tearDown(self):self.temp.cleanup()
    def cached_fixture(self):
        import hashlib
        from test_launch_proposal import png
        service=LaunchMaterials(self.temp.name,b'x'*32,reader=lambda *a:self.fail('provider read forbidden'))
        raw=png();sha=hashlib.sha256(raw).hexdigest();identity='cachedimage12345'
        target=Path(self.temp.name)/'drive-assets';target.mkdir(exist_ok=True);path=target/(identity+'-'+sha+'.bin');path.write_bytes(raw)
        value={'request_id':self.request['id'],'request_revision':1,'drive_urls':self.request['drive_urls'],'checked_at':'2026-10-18T08:00:00+00:00','verified':True,'assets':{'complete':True,'content_read_count':1,'creative_reviewed':False,'assets':[{'id':identity,'sha256':sha,'content_read':True,'mime_type':'image/png','format_check':'unverified','creative_reviewed':False}]}}
        raw_value=json.dumps(value);service.path.write_text(json.dumps({'payload':raw_value,'signature':service._sign(raw_value)}),encoding='utf-8');return service,path,value
    def test_cached_validation_preserves_source_read_truth_and_no_visual_claim(self):
        service,path,old=self.cached_fixture();result=service.revalidate_cached(self.request)
        self.assertEqual(result['decoded'],1);self.assertEqual(result['provider_reads'],0);self.assertFalse(result['visual_review'])
        with patch('launch_materials.drive_status',return_value={'configured':True}):value=service.status(self.request)
        self.assertTrue(value['binding_verified']);self.assertEqual(value['checked_at'],old['checked_at']);self.assertTrue(value['assets']['complete']);self.assertFalse(value['assets']['creative_reviewed']);self.assertFalse(value['assets']['assets'][0]['creative_reviewed']);self.assertEqual(value['assets']['assets'][0]['dimensions'],{'width':1,'height':1})
    def test_cached_validation_rejects_stale_binding_without_write(self):
        service,path,_=self.cached_fixture();before=service.path.read_bytes()
        with self.assertRaises(ValueError):service.revalidate_cached({**self.request,'revision':2})
        self.assertEqual(before,service.path.read_bytes())
    def test_cached_tamper_stays_unverified_without_fake_read(self):
        service,path,_=self.cached_fixture();path.write_bytes(b'changed')
        self.assertEqual(service.revalidate_cached(self.request)['failed'],1)
        value=json.loads(json.loads(service.path.read_text())['payload']);self.assertEqual(value['assets']['assets'][0]['format_check'],'unverified');self.assertTrue(value['assets']['assets'][0]['content_read'])
    def test_changed_evidence_during_decode_not_overwritten(self):
        service,path,_=self.cached_fixture()
        def decoder(*args):service.path.write_text('changed');return {'width':1,'height':1}
        with patch('image_validation.decode_image',side_effect=decoder):
            with self.assertRaises(ValueError):service.revalidate_cached(self.request)
        self.assertEqual(service.path.read_text(),'changed')

    def test_no_sources_never_reads(self):
        service=LaunchMaterials(self.temp.name,b'x'*32,reader=lambda *a:self.fail('unexpected read'))
        self.assertFalse(service.refresh({**self.request,'drive_urls':[]}))
    def test_new_revision_queues_and_runs_after_current_read(self):
        import time
        entered=threading.Event();release=threading.Event();calls=[]
        def reader(directory,urls):
            calls.append(urls)
            if len(calls)==1:entered.set();release.wait(3)
            return {'verified':True,'complete':True}
        service=LaunchMaterials(self.temp.name,b'x'*32,reader,lambda *a:{'complete':True})
        service.refresh(self.request);self.assertTrue(entered.wait(2))
        newer={**self.request,'revision':2,'drive_urls':['https://drive.google.com/drive/folders/newerabcdefg']}
        self.assertTrue(service.refresh(newer));self.assertEqual(service.status(newer)['job']['state'],'queued')
        self.assertFalse(service.status(newer)['binding_verified']);release.set()
        for _ in range(200):
            if not service.running:break
            time.sleep(.01)
        self.assertEqual(len(calls),2);self.assertTrue(service.status(newer)['binding_verified'])
        self.assertFalse(service.status(self.request)['binding_verified'])

    def test_restart_reports_interruption_instead_of_fake_running(self):
        (Path(self.temp.name)/'launch-material-job.json').write_text(json.dumps({'state':'running'}))
        service=LaunchMaterials(self.temp.name,b'x'*32)
        self.assertEqual(service.status(self.request)['job']['state'],'interrupted')
    def test_signed_exact_binding_and_double_read_suppression(self):
        entered=threading.Event();release=threading.Event();done=threading.Event()
        def read(*args):entered.set();release.wait(3);return {'verified':True,'complete':True}
        def assets(*args):done.set();return {'content_read_count':0}
        service=LaunchMaterials(self.temp.name,b'x'*32,read,assets)
        self.assertTrue(service.refresh(self.request));self.assertTrue(entered.wait(2))
        self.assertFalse(service.refresh(self.request));release.set();self.assertTrue(done.wait(2))
        # Wait for the actual atomic report save, not merely the reader completion.
        import time
        for _ in range(100):
            if not service.running:break
            time.sleep(.01)
        self.assertTrue(service.status(self.request)['binding_verified'])
        self.assertFalse(service.status({**self.request,'revision':2})['binding_verified'])
        self.assertFalse(service.status({**self.request,'drive_urls':[]})['binding_verified'])
        envelope=json.loads(service.path.read_text());envelope['payload']=envelope['payload'].replace('request','other')
        service.path.write_text(json.dumps(envelope))
        self.assertFalse(service.status(self.request)['binding_verified'])
