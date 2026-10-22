import hashlib,json,shutil,subprocess,tempfile,unittest
from pathlib import Path
from creative_vision import CreativeVision,FIELDS
from image_validation import decode_image,review_image,thumbnail_image,_image_python,REVIEW_MAX_BYTES,SOURCE_MAX_BYTES
from image_validation import validate_upload_sources
from batch_limits import MAX_UPLOAD_IMAGE_BYTES


class LargeCreativeImages(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'drive-assets').mkdir()

    def test_actual_bright_source_review_preserves_original_binding(self):
        source=Path(__file__).resolve().parents[1]/'outputs/rosery-raw-jpg-color-2026-10-20/brighter-v2-all/IMG_0856-BRIGHT.jpg'
        if not source.exists():self.skipTest('Actual approved source is not installed on this machine')
        original=source.read_bytes();digest=hashlib.sha256(original).hexdigest()
        identity='actual_bright_0856'
        target=self.root/'drive-assets'/(identity+'-'+digest+'.bin');target.write_bytes(original)
        row=CreativeVision(self.root)._sources([{'id':identity,'sha256':digest,'name':source.name}])[0]
        self.assertGreater(len(original),5*1024*1024)
        self.assertGreater(row['width']*row['height'],20000000)
        self.assertTrue(row['review_derivative']);self.assertEqual(row['sha256'],digest)
        self.assertNotEqual(row['review_sha256'],digest)
        self.assertEqual(row['review_sha256'],hashlib.sha256(row['raw']).hexdigest())
        self.assertLessEqual(len(row['raw']),REVIEW_MAX_BYTES)
        preview=self.root/'review.jpg';preview.write_bytes(row['raw'])
        dims=decode_image(str(preview),row['review_sha256'])
        self.assertLessEqual(max(dims['width'],dims['height']),1568)
        self.assertEqual(target.read_bytes(),original);self.assertEqual(source.read_bytes(),original)
        self.assertEqual((row['width'],row['height']),(4284,5712))
        self.assertLessEqual(len(thumbnail_image(str(target),digest)),400*1024)
        (self.root/'creative-image-sharing.json').write_text(json.dumps({'approved':True,'scope':'selected_drive_creatives'}))
        captured=[]
        def local_runner(payload):
            captured.append(json.loads(payload))
            answer={k:'local fixture' for k in FIELDS}
            answer.update(asset_id=identity,sha256=digest,format=row['format'],paired_asset_id=None,confidence=.5,warnings=[])
            return {'assets':[answer]}
        reviewed=CreativeVision(self.root,local_runner).analyze([{'id':identity,'sha256':digest,'name':source.name}])
        self.assertEqual(reviewed['assets'][0]['sha256'],digest)
        provenance=reviewed['review_provenance'][0]
        self.assertEqual(provenance['review_sha256'],row['review_sha256'])
        import base64
        sent=next(b for b in captured[0]['message']['content'] if b['type']=='image')['source']
        self.assertEqual(hashlib.sha256(base64.b64decode(sent['data'])).hexdigest(),provenance['review_sha256'])
        self.assertIn('clothing and jewelry',captured[0]['message']['content'][0]['text'])
        # Trace execution's separate cache reader into the real multipart builder,
        # replacing only the HTTP opener. No credentials or account calls.
        from campaign_operations import CampaignOperations
        from campaign_operations_transport import CampaignTransport
        from unittest.mock import Mock,patch
        service=CampaignOperations.__new__(CampaignOperations);service.directory=self.root
        upload_raw,info=service._source({'asset_id':identity,'sha256':digest})
        self.assertEqual(upload_raw,original)
        self.assertEqual(info['dimensions'],{'width':4284,'height':5712,'format':'JPEG'})
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"images":{}}'
        opener=Mock();opener.open.return_value=Response()
        with patch('campaign_operations_transport.urllib.request.build_opener',return_value=opener):
            CampaignTransport().upload('synthetic-token',upload_raw,info['mime'],'source.jpg')
        body=opener.open.call_args.args[0].data
        sent_source=body.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n--',1)[0]
        self.assertEqual(sent_source,original)
        self.assertNotEqual(hashlib.sha256(sent_source).hexdigest(),provenance['review_sha256'])

    def test_wrong_source_digest_rejected(self):
        source=self.root/'source.jpg';source.write_bytes(b'not an image')
        with self.assertRaises(ValueError):review_image(str(source),'0'*64)

    def test_over_40_megapixels_rejected_before_review(self):
        source=self.root/'large.png'
        subprocess.run([_image_python(),'-I','-c','from PIL import Image;import sys;Image.new("RGB",(6400,6400)).save(sys.argv[1])',str(source)],check=True)
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):decode_image(str(source),digest)
        with self.assertRaises(ValueError):review_image(str(source),digest)

    def test_source_byte_bound_precedes_decoder(self):
        identity='oversized_source';digest='0'*64
        target=self.root/'drive-assets'/(identity+'-'+digest+'.bin')
        with target.open('wb') as stream:stream.truncate(SOURCE_MAX_BYTES+1)
        with self.assertRaisesRegex(ValueError,'Invalid local image'):
            CreativeVision(self.root)._sources([{'id':identity,'sha256':digest,'name':'large.jpg'}])

    def test_upload_limit_uses_actual_bytes_and_names_actionable_file(self):
        identity='oversized_source';digest='0'*64
        target=self.root/'drive-assets'/(identity+'-'+digest+'.bin')
        with target.open('wb') as stream:stream.truncate(MAX_UPLOAD_IMAGE_BYTES+1)
        asset={'id':identity,'sha256':digest,'name':'large-clothing.jpg','size':1}
        with self.assertRaisesRegex(ValueError,'large-clothing.jpg.*20 MiB'):
            validate_upload_sources(self.root,[asset])
        self.assertEqual(target.stat().st_size,MAX_UPLOAD_IMAGE_BYTES+1)
        self.assertEqual(SOURCE_MAX_BYTES,64*1024*1024)
        with target.open('r+b') as stream:stream.truncate(MAX_UPLOAD_IMAGE_BYTES)
        validate_upload_sources(self.root,[asset])

if __name__=='__main__':unittest.main()

