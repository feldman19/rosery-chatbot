from pathlib import Path
import tempfile,unittest,hashlib,json,hmac,struct,zlib
import subprocess
from unittest.mock import patch
from image_validation import decode_image
from launch_proposal import LaunchProposal
from launch_workflow import LaunchWorkflow

def png(width=1,height=1):
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00'))+chunk(b'IEND',b'')

class ProposalTests(unittest.TestCase):
 def test_decoder_process_failure_is_unavailable_without_media(self):
  for error in (subprocess.TimeoutExpired('decoder',15),OSError('private runtime detail')):
   with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
    store,request,draft,path=self.fixture(d);store.save(draft,request)
    decode_image.cache_clear()
    with patch('image_validation.subprocess.run',side_effect=error):
     self.assertFalse(store.report(request)['available'])
     with self.assertRaises(ValueError):store.media('abcdefghijkl',request)
 def fixture(self,d,raw=None):
  root=Path(d);(root/'drive-assets').mkdir(exist_ok=True);raw=png() if raw is None else raw;digest=hashlib.sha256(raw).hexdigest();identity='abcdefghijkl'
  path=root/'drive-assets'/(identity+'-'+digest+'.bin');path.write_bytes(raw)
  request={'id':'work','revision':1,'goal':'Sales launch','drive_urls':['https://drive.google.com/drive/folders/abcdefghijkl']}
  current=LaunchWorkflow(root).create({'brief':request['goal'],'drive_urls':request['drive_urls']})['request']
  evidence={'request_id':current['id'],'request_revision':current['revision'],'drive_urls':current['drive_urls'],'assets':{'assets':[{'id':identity,'sha256':digest,'content_read':True}]}}
  payload=json.dumps(evidence);(root/'launch-materials.json').write_text(json.dumps({'payload':payload,'signature':hmac.new(b'x'*32,payload.encode(),hashlib.sha256).hexdigest()}))
  draft={'ads':[{'asset_id':identity,'sha256':digest,'review':{'visual_checked':True}}]}
  return LaunchProposal(root,b'x'*32),request,draft,path
 def test_exact_request_and_hash_gate_preview(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   store,request,draft,path=self.fixture(d);store.save(draft,request)
   self.assertTrue(store.report(request)['available']);self.assertFalse(store.report(request)['proposal']['activation_available'])
   self.assertEqual(store.media('abcdefghijkl',request)[1],'image/png')
   self.assertFalse(store.report({**request,'revision':2})['available'])
   with self.assertRaises(ValueError):store.media('../secret',request)
   path.write_bytes(b'changed');self.assertFalse(store.report(request)['available'])
 def test_visual_review_and_signed_membership_required(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
   store,request,draft,path=self.fixture(d);draft['ads'][0]['review']['visual_checked']=False
   with self.assertRaises(ValueError):store.save(draft,request)
   draft['ads'][0]['review']['visual_checked']=True
   with self.assertRaises(ValueError):store.save(draft,{**request,'drive_urls':[]})
   (Path(d)/'launch-materials.json').unlink()
   with self.assertRaises(OSError):store.save(draft,request)
 def test_malformed_and_oversized_images_rejected(self):
  for raw in (b'\xff\xd8\xffsynthetic',png(100000,100000)):
   with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
    store,request,draft,path=self.fixture(d,raw)
    with self.assertRaises(ValueError):store.save(draft,request)
