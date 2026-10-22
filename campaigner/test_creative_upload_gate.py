"""Local oversized source verifies gate order; fake services, no model calls."""
import hashlib,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from creative_preparation import CreativePreparation
from batch_limits import MAX_UPLOAD_IMAGE_BYTES

class CreativeUploadGateTests(unittest.TestCase):
 def test_video_never_reaches_image_model_or_proposal(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
   request={'id':'video-test','revision':1,'goal':'clothing sales','drive_urls':['fake-link']}
   launch={'brief':request['goal'],'drive_urls':request['drive_urls']}
   materials=SimpleNamespace(refresh=lambda r:True,status=lambda r:{'binding_verified':True,'assets':{'complete':True,'assets':[{'id':'video12345678','mime_type':'video/mp4','content_read':True}]}})
   coordinator=CreativePreparation(directory,intake=SimpleNamespace(latest=lambda:request),workflow=SimpleNamespace(report=lambda s:{'request':launch}),materials=materials,proposal=SimpleNamespace(save=lambda *a:self.fail()),analyzer=lambda *a,**k:self.fail())
   coordinator._run(request)
   self.assertEqual(coordinator.status()['state'],'blocked');self.assertIn('וידאו',coordinator.status()['detail'])
 def test_oversized_source_blocks_before_analyzer(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
   root=Path(directory);(root/'drive-assets').mkdir()
   raw=b'\x00'*(MAX_UPLOAD_IMAGE_BYTES+1);digest=hashlib.sha256(raw).hexdigest();identity='syntheticlargeasset'
   (root/'drive-assets'/(identity+'-'+digest+'.bin')).write_bytes(raw)
   request={'id':'fake','revision':1,'goal':'clothing sales','drive_urls':['fake-link']}
   launch={'brief':request['goal'],'drive_urls':request['drive_urls']}
   asset={'id':identity,'sha256':digest,'name':'oversized.jpg','mime_type':'image/jpeg','content_read':True}
   materials=SimpleNamespace(refresh=lambda r:True,status=lambda r:{'binding_verified':True,'assets':{'complete':True,'assets':[asset]}})
   coordinator=CreativePreparation(root,intake=SimpleNamespace(latest=lambda:request),workflow=SimpleNamespace(report=lambda s:{'request':launch}),materials=materials,proposal=SimpleNamespace(save=lambda *a:self.fail('must not save')),analyzer=lambda assets:self.fail('must not analyze'))
   coordinator._run(request)
   self.assertEqual(coordinator.status()['state'],'blocked')
   self.assertIn('oversized.jpg',coordinator.status()['detail'])
