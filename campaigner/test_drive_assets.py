import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from drive_assets import read_assets,EMAIL,SCOPE,_download


class DriveAssetTests(unittest.TestCase):
    def test_multiple_small_native_documents_use_actual_bytes_not_reserved_maximum(self):
        from drive_assets import read_all_assets
        inv=self.inventory();files=[{'id':'abcdefghijkl'+str(i),'mimeType':'application/vnd.google-apps.document','modifiedTime':'now'} for i in range(5)]
        inv['sources'][0]['files']=files
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets._token',return_value='synthetic'),patch('drive_assets._metadata',side_effect=lambda token,identity,*a:next(x for x in files if x['id']==identity)),patch('drive_assets._download',return_value=b'test'):
            result=read_all_assets(d,inv)
            self.assertEqual(result['content_read_count'],5);self.assertTrue(result['complete'])

    def test_empty_collection_creates_manifest_without_read_calls(self):
        from drive_assets import read_all_assets
        inv=self.inventory();inv['sources'][0]['files']=[]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets.read_assets') as reader:
            result=read_all_assets(d,inv);reader.assert_not_called()
            self.assertTrue((Path(d)/'drive-assets'/'manifest.json').exists())
            self.assertEqual(result['files_total'],0)

    def test_global_deadline_and_remaining_bytes_pass_to_each_batch(self):
        from drive_assets import read_all_assets
        inv=self.inventory();inv['sources'][0]['files']=[{'id':'abcdefghijkl'+str(i),'mimeType':'text/plain','size':'4'} for i in range(21)]
        seen=[]
        def batch(directory,inventory,**bounds):
            seen.append(bounds)
            ids=[x['id'] for s in inventory['sources'] for x in s['files']]
            return {'assets':[{'id':x,'size':4,'content_read':True} for x in ids],'files_processed':len(ids)}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets.read_assets',side_effect=batch):read_all_assets(d,inv)
        self.assertEqual(seen[0]['deadline'],seen[1]['deadline'])
        self.assertEqual(seen[1]['max_total'],seen[0]['max_total']-80)

    def test_automatic_collection_advances_beyond_first_twenty(self):
        from drive_assets import read_all_assets
        inv=self.inventory(); inv['sources'][0]['files']=[{'id':'abcdefghijk'+str(i),'mimeType':'text/plain','size':'4'} for i in range(54)]
        calls=[]
        def batch(directory,inventory,**bounds):
            ids=[x['id'] for s in inventory['sources'] for x in s['files']];calls.append(ids)
            (Path(directory)/'drive-assets').mkdir(exist_ok=True)
            return {'assets':[{'id':x,'size':4,'content_read':True} for x in ids],'files_processed':len(ids)}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets.read_assets',side_effect=batch):
            result=read_all_assets(d,inv)
        self.assertEqual([len(x) for x in calls],[20,20,14]);self.assertEqual(result['content_read_count'],54)
        self.assertTrue(result['complete']);self.assertFalse(result['publication_ready'])

    def inventory(self):
        return {'verified':True,'complete':True,'email':EMAIL,'scopes':[SCOPE],
                'sources':[{'complete':True,'verified_identity':EMAIL,'files':[{'id':'abcdefghijklmno','name':'copy.txt','mimeType':'text/plain','modifiedTime':'now','size':'4'}]}]}

    def test_requires_authenticated_complete_inventory_before_credentials(self):
        for changed in ({'verified':False},{'complete':False},{'email':'other@example.test'}):
            inv={**self.inventory(),**changed}
            with patch('drive_assets._token') as token:
                with self.assertRaises(ValueError):read_assets('unused',inv)
                token.assert_not_called()

    def test_download_and_text_are_not_creative_review_or_publish(self):
        inv=self.inventory();item=inv['sources'][0]['files'][0]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets._token',return_value='synthetic'),patch('drive_assets._metadata',return_value=item),patch('drive_assets._download',return_value=b'test'):
            r=read_assets(d,inv)
            self.assertTrue(r['complete']);self.assertEqual(r['assets'][0]['text_excerpt'],'test')
            self.assertFalse(r['creative_reviewed']);self.assertFalse(r['publication_ready'])
            self.assertEqual(len(list((Path(d)/'drive-assets').glob('*.bin'))),1)

    def test_source_drift_never_downloads_or_marks_read(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets._token',return_value='synthetic'),patch('drive_assets._metadata',return_value={'id':'abcdefghijklmno','mimeType':'text/plain','modifiedTime':'changed','size':'4'}),patch('drive_assets._download') as dl:
            r=read_assets(d,self.inventory());dl.assert_not_called()
            self.assertFalse(r['complete']);self.assertFalse(r['assets'][0]['content_read'])

    def test_unbounded_or_unsupported_download_rejected_before_network(self):
        for metadata in ({'id':'bad/path','mimeType':'image/png','size':'2'}, {'id':'abcdefghijklmno','mimeType':'application/x-msdownload','size':'2'}, {'id':'abcdefghijklmno','mimeType':'video/mp4','size':'999999999'}):
            with patch('drive_assets.urllib.request.build_opener') as network:
                with self.assertRaises(ValueError):_download('synthetic',metadata,10**30)
                network.assert_not_called()

    def test_native_exports_obey_remaining_total_even_without_size(self):
        inv=self.inventory();files=[{'id':identity,'mimeType':'application/vnd.google-apps.document','modifiedTime':'now'} for identity in ('abcdefghijklmno','ponmlkjihgfedcb')]
        inv['sources'][0]['files']=files
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets.MAX_TOTAL',5),patch('drive_assets._token',return_value='synthetic'),patch('drive_assets._metadata',side_effect=lambda token,identity,*args:next(x for x in files if x['id']==identity)),patch('drive_assets._download',return_value=b'abcd'):
            report=read_assets(d,inv)
            self.assertFalse(report['complete']);self.assertEqual(report['content_read_count'],1)
            self.assertEqual(sum(p.stat().st_size for p in (Path(d)/'drive-assets').glob('*.bin')),4)

    def test_resource_keys_reach_all_content_requests_without_report_leak(self):
        inv=self.inventory();source=inv['sources'][0]
        source['source_url']='https://drive.google.com/drive/folders/abcdefghijklmnop?resourcekey=folder_key'
        source['files'][0]['resourceKey']='file_key';item=source['files'][0]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets._token',return_value='synthetic'),patch('drive_assets._metadata',return_value=item) as metadata,patch('drive_assets._download',return_value=b'test') as download:
            report=read_assets(d,inv)
            self.assertIn('abcdefghijklmno/file_key',download.call_args.args[3])
            self.assertTrue(all('folder_key' in call.args[2] for call in metadata.call_args_list))
            self.assertNotIn('file_key',str(report));self.assertNotIn('folder_key',str(report))

    def test_expired_batch_stops_before_next_provider_call(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d,patch('drive_assets._token',return_value='synthetic'),patch('drive_assets.time.monotonic',side_effect=[0,121]),patch('drive_assets._metadata') as metadata:
            report=read_assets(d,self.inventory());metadata.assert_not_called()
            self.assertFalse(report['complete'])
