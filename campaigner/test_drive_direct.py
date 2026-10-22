"""Synthetic-only Drive adapter tests. No user credentials or network access."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import drive_direct as drive

CLIENT='123456789012-synthetic-client.apps.googleusercontent.com'
CREDS={'type':'authorized_user','client_id':CLIENT,'client_secret':'synthetic-client-secret','refresh_token':'synthetic-refresh-token'}
FOLDER='abcdefghijk-folder';FILE='abcdefghijk-image';URL='https://drive.google.com/drive/folders/'+FOLDER

def fake_crypt(raw,decrypt=False):
 if decrypt:
  if not raw.startswith(b'MOCK:'):raise ValueError('bad envelope')
  return raw[5:][::-1]
 return b'MOCK:'+raw[::-1]

def auth_responses(email=drive.EMAIL,scope=drive.SCOPE,audience=CLIENT):return [{'access_token':'synthetic-access-token','expires_in':3600},{'scope':scope,'aud':audience},{'user':{'emailAddress':email}}]
def folder():return {'id':FOLDER,'name':'Source folder','mimeType':'application/vnd.google-apps.folder'}
def image(identity=FILE):return {'id':identity,'name':'Photo.jpg','mimeType':'image/jpeg','parents':[FOLDER],'size':'1234','imageMediaMetadata':{'width':1080,'height':1920,'location':{'latitude':1}}}

class DriveDirectTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.directory=Path(self.temp.name);drive._cache.clear()
  self.cp=patch('drive_direct.crypt',side_effect=fake_crypt);self.cp.start();self.jp=patch('drive_direct._json');self.read=self.jp.start();self.read.side_effect=auth_responses()
 def tearDown(self):self.jp.stop();self.cp.stop();drive._cache.clear();self.temp.cleanup()
 def configured(self):return drive.configure(self.directory,CREDS)
 def test_configure_exact_scope_account_and_encrypted_credentials(self):
  r=self.configured();self.assertTrue(r['verified']);self.assertEqual([drive.SCOPE],r['scopes']);raw=(self.directory/'drive-read.dpapi').read_bytes();self.assertTrue(raw.startswith(b'MOCK:'));self.assertNotIn(CREDS['refresh_token'].encode(),raw)
  self.assertNotIn(CREDS['client_secret'],json.dumps(r));self.assertNotIn('synthetic-access-token',json.dumps(r));self.assertFalse(r['content_read'])
 def test_wrong_scope_identity_or_audience_does_not_replace(self):
  target=self.directory/'drive-read.dpapi';target.write_bytes(b'old-encrypted-value')
  for responses in (auth_responses(scope=drive.SCOPE+' https://www.googleapis.com/auth/drive'),auth_responses(email='other@example.invalid'),auth_responses(audience='other-client')):
   self.read.side_effect=responses
   with self.assertRaises(ValueError):drive.configure(self.directory,CREDS)
   self.assertEqual(b'old-encrypted-value',target.read_bytes())
 def test_scope_missing_rejected(self):
  self.read.side_effect=auth_responses(scope='')
  with self.assertRaises(ValueError):self.configured()
 def test_arbitrary_oauth_endpoint_and_extra_credentials_rejected(self):
  for addition in ({'token_uri':'https://evil.invalid/token'},{'access_token':'browser-token'}):
   with self.assertRaises(ValueError):drive.configure(self.directory,{**CREDS,**addition})
  self.read.assert_not_called()
 def test_status_poll_no_read_or_decrypt(self):
  self.assertFalse(drive.status(self.directory)['configured']);self.configured();calls=self.read.call_count
  r=drive.status(self.directory);self.assertTrue(r['configured']);self.assertFalse(r['verified']);self.assertEqual(calls,self.read.call_count)
 def test_reloads_encrypted_credentials_and_renews_token(self):
  self.configured();drive._cache.clear();self.read.side_effect=auth_responses();self.assertTrue(drive.status(self.directory,verify=True)['verified'])
 def test_invalid_source_rejected_before_credential_load(self):
  with patch('drive_direct._token') as token:
   with self.assertRaises(ValueError):drive.request_sources(self.directory,[URL,'https://evil.invalid/a'])
   token.assert_not_called()
 def test_folder_pages_are_bounded_and_metadata_only(self):
  self.configured();self.read.reset_mock();self.read.side_effect=[folder(),{'files':[image()],'nextPageToken':'cursor-one'},{'files':[image('abcdefghijk-image2')]}]
  r=drive.list_folder(self.directory,URL);self.assertTrue(r['complete']);self.assertEqual(2,len(r['files']));self.assertFalse(r['content_read']);self.assertFalse(r['recursive'])
  self.assertNotIn('location',r['files'][0]['imageMediaMetadata']);self.assertEqual('cursor-one',self.read.call_args_list[2].args[1]['pageToken'])
  self.assertEqual("'"+FOLDER+"' in parents and trashed = false",self.read.call_args_list[1].args[1]['q'])
 def test_page_limit_and_incomplete_search_never_claim_complete(self):
  self.configured();self.read.side_effect=[folder(),{'files':[image()],'nextPageToken':'more'}]
  r=drive.list_folder(self.directory,URL,max_pages=1);self.assertFalse(r['complete'])
  self.read.side_effect=[folder(),{'files':[image()],'incompleteSearch':True}];self.assertFalse(drive.list_folder(self.directory,URL)['complete'])
 def test_repeated_cursor_or_duplicate_files_rejected(self):
  self.configured();self.read.side_effect=[folder(),{'files':[image()],'nextPageToken':'same'},{'files':[image('abcdefghijk-image2')],'nextPageToken':'same'}]
  with self.assertRaises(ValueError):drive.list_folder(self.directory,URL)
  self.read.side_effect=[folder(),{'files':[image(),image()]}]
  with self.assertRaises(ValueError):drive.list_folder(self.directory,URL)
 def test_unverified_folder_membership_rejected(self):
  self.configured();item=image();item['parents']=['different-folder'];self.read.side_effect=[folder(),{'files':[item]}]
  with self.assertRaises(ValueError):drive.list_folder(self.directory,URL)
 def test_file_sources_and_folder_sources_report_no_content_read(self):
  self.configured();self.read.side_effect=[image()]
  r=drive.request_sources(self.directory,['https://drive.google.com/file/d/'+FILE+'/view']);self.assertTrue(r['verified']);self.assertTrue(r['complete']);self.assertEqual(0,r['provider_writes']);self.assertFalse(r['content_read'])
 def test_removed_encrypted_connection_not_served_from_cache(self):
  self.configured();(self.directory/'drive-read.dpapi').unlink()
  with self.assertRaises(ValueError):drive.list_folder(self.directory,URL)
 def test_refresh_failure_status_sanitized(self):
  self.configured();drive._cache.clear();self.read.side_effect=RuntimeError(CREDS['refresh_token'])
  r=drive.status(self.directory,verify=True);self.assertFalse(r['verified']);self.assertNotIn(CREDS['refresh_token'],json.dumps(r))

class DriveTransportTests(unittest.TestCase):
 def test_fixed_origin_no_redirect_bound_get_and_refresh_only_post(self):
  response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{}';opener=Mock();opener.open.return_value=response
  with patch('drive_direct.urllib.request.build_opener',return_value=opener) as build:
   drive._json('about',{'fields':'user(emailAddress)'},token='synthetic')
   req=opener.open.call_args.args[0];self.assertEqual('GET',req.get_method());self.assertTrue(req.full_url.startswith(drive.API+'/about?'));self.assertEqual('Bearer synthetic',req.get_header('Authorization'))
   self.assertIsNone(build.call_args.args[0].redirect_request(None,None,302,'',{},'https://evil.invalid'));response.read.assert_called_with(2097153)
   drive._json('refresh',form={'grant_type':'refresh_token','client_id':CLIENT,'client_secret':'synthetic-secret','refresh_token':'synthetic-refresh'})
   req=opener.open.call_args.args[0];self.assertEqual('POST',req.get_method());self.assertEqual(drive.OAUTH,req.full_url)
 def test_arbitrary_operation_never_networks(self):
  with patch('drive_direct.urllib.request.build_opener') as build:
   for op in ('https://evil.invalid/drive','files/../account','files/abc?alt=media','files.create','permissions'):
    with self.assertRaises(ValueError):drive._json(op,token='synthetic')
   build.assert_not_called()
 def test_oversized_and_sensitive_transport_error_sanitized(self):
  response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'x'*2097153;opener=Mock();opener.open.return_value=response
  with patch('drive_direct.urllib.request.build_opener',return_value=opener):
   with self.assertRaisesRegex(ValueError,'^Google Drive read unavailable$'):drive._json('files',token='sensitive-token')
   opener.open.side_effect=RuntimeError('sensitive-token')
   with self.assertRaises(ValueError) as error:drive._json('files',token='sensitive-token')
   self.assertNotIn('sensitive-token',str(error.exception))
class RecursiveDriveTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.directory=Path(self.temp.name)
  self.tp=patch('drive_direct._token',return_value='synthetic-token');self.tp.start()
  self.folder_mime='application/vnd.google-apps.folder';self.root='root-folder-123';self.child='child-folder-123';self.file='image-file-123'
  self.metadata={self.root:{'id':self.root,'name':'Root','mimeType':self.folder_mime},self.child:{'id':self.child,'name':'Child','mimeType':self.folder_mime,'resourceKey':'child-key'},self.file:{'id':self.file,'name':'Image','mimeType':'image/jpeg'}}
  self.tree={self.root:[{**self.metadata[self.child],'parents':[self.root]}],self.child:[{**self.metadata[self.file],'parents':[self.child]}]}
  def reader(operation,params=None,**kwargs):
   if operation.startswith('files/'):return self.metadata[operation[6:]]
   if operation=='files':
    identity=params['q'].split("'")[1];return {'files':self.tree.get(identity,[])}
   raise AssertionError('Unexpected endpoint')
  self.jp=patch('drive_direct._json',side_effect=reader);self.read=self.jp.start()
 def tearDown(self):self.jp.stop();self.tp.stop();self.temp.cleanup()
 def url(self,identity=None):return 'https://drive.google.com/drive/folders/'+(identity or self.root)
 def test_nested_files_flatten_and_resource_keys_inherited(self):
  result=drive.request_sources(self.directory,[self.url()+'?resourcekey=root-key']);source=result['sources'][0]
  self.assertTrue(result['complete']);self.assertTrue(source['recursive']);self.assertEqual({self.child,self.file},{f['id'] for f in source['files']})
  self.assertEqual(self.root+'/root-key,'+self.child+'/child-key',source['resource_contexts'][self.file])
  self.assertEqual([self.root,self.child],source['folder_paths'][self.child])
  childcalls=[c for c in self.read.call_args_list if c.args[0]=='files' and self.child in c.args[1]['q']]
  self.assertIn(self.child+'/child-key',childcalls[0].kwargs['resource_key']);self.assertFalse(source['content_read'])
 def test_cycle_never_loops_or_claims_recursive_complete(self):
  self.tree[self.child]=[{**self.metadata[self.root],'parents':[self.child]}]
  r=drive.request_sources(self.directory,[self.url()]);self.assertFalse(r['complete']);self.assertIn('folder_cycle_detected',r['coverage_reasons']);self.assertLess(self.read.call_count,8)
 def test_depth_bound_is_explicit_and_no_deep_folder_read(self):
  self.tree={};last=self.root
  for n in range(1,7):
   identity='depth-folder-'+str(n);self.metadata[identity]={'id':identity,'name':identity,'mimeType':self.folder_mime};self.tree[last]=[{**self.metadata[identity],'parents':[last]}];last=identity
  r=drive.request_sources(self.directory,[self.url()]);self.assertFalse(r['complete']);self.assertIn('maximum_depth_reached',r['coverage_reasons']);self.assertFalse(r['sources'][0]['recursive'])
  requested=[c.args[0] for c in self.read.call_args_list];self.assertNotIn('files/depth-folder-5',requested)
 def test_folder_bound_is_global_explicit(self):
  self.tree[self.root]=[]
  for n in range(55):
   identity='wide-folder-'+str(n);self.metadata[identity]={'id':identity,'name':identity,'mimeType':self.folder_mime};self.tree[self.root].append({**self.metadata[identity],'parents':[self.root]})
  r=drive.request_sources(self.directory,[self.url()]);self.assertFalse(r['complete']);self.assertIn('maximum_folders_reached',r['coverage_reasons']);self.assertEqual(50,r['sources'][0]['folders_scanned'])
 def test_file_bound_rejects_silent_truncation(self):
  self.tree[self.root]=[]
  # Mock valid paginated pages to exercise the 2000-entry cap.
  def pages(operation,params=None,**kwargs):
   if operation.startswith('files/'):return self.metadata[self.root]
   page=int(params.get('pageToken','0'));items=[{'id':'asset-file-'+str(page*1000+n),'mimeType':'image/jpeg','parents':[self.root]} for n in range(1000)]
   return {'files':items,'nextPageToken':str(page+1)}
  self.read.side_effect=pages;r=drive.request_sources(self.directory,[self.url()]);self.assertFalse(r['complete']);self.assertLessEqual(len(r['sources'][0]['files']),2000);self.assertIn('folder_listing_incomplete',r['coverage_reasons'])
 def test_parent_membership_failure_preserves_root_count_and_reason(self):
  self.tree[self.child][0]['parents']=['outside-root-123'];r=drive.request_sources(self.directory,[self.url()])
  self.assertEqual(1,len(r['sources']));self.assertFalse(r['complete']);self.assertIn('folder_read_failed',r['coverage_reasons']);self.assertEqual(self.url(),r['sources'][0]['source_url'])
 def test_multiple_roots_errors_preserve_all_input_urls(self):
  urls=[self.url(),self.url('missing-root-123')];r=drive.request_sources(self.directory,urls)
  self.assertEqual(urls,[s['source_url'] for s in r['sources']]);self.assertFalse(r['complete']);self.assertEqual(['source_read_failed'],r['sources'][1]['coverage_reasons'])
 def test_deadline_returns_placeholder_for_every_unread_root(self):
  urls=[self.url(),self.url(self.child)]
  with patch('drive_direct.time.monotonic',side_effect=[0,121,122]):r=drive.request_sources(self.directory,urls)
  self.assertFalse(r['complete']);self.assertEqual(2,len(r['sources']));self.assertEqual(2,r['unread_source_count']);self.assertIn('deadline_reached',r['coverage_reasons']);self.read.assert_not_called()
 def test_duplicate_shared_folder_deduplicates_without_hiding_content(self):
  other='other-folder-123';self.metadata[other]={'id':other,'name':'Other','mimeType':self.folder_mime};shared={**self.metadata[self.child],'parents':[self.root,other]}
  self.tree[self.root]=[shared,{**self.metadata[other],'parents':[self.root]}];self.tree[other]=[shared]
  r=drive.request_sources(self.directory,[self.url()]);self.assertTrue(r['complete']);ids=[f['id'] for f in r['sources'][0]['files']];self.assertEqual(1,ids.count(self.file));self.assertEqual(1,ids.count(self.child))

if __name__=='__main__':unittest.main()
