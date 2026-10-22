import hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from creative_vision import FIELDS
from creative_review_receipts import save_review_receipt
from creative_preparation import CreativePreparation


class ReviewReceiptsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  raw=b'fixture';sha=hashlib.sha256(raw).hexdigest();identity='receipt_test_asset'
  (self.root/'drive-assets').mkdir();(self.root/'drive-assets'/(identity+'-'+sha+'.bin')).write_bytes(raw)
  self.request={'id':'request1','revision':1,'goal':'clothing https://roseryjewelry.com/collections/all','drive_urls':['test']}
  self.assets=[{'id':identity,'sha256':sha,'name':'test.jpg','mime_type':'image/jpeg','content_read':True,'dimensions':{'width':1080,'height':1350}}]
  row={k:'fixture' for k in FIELDS};row.update(asset_id=identity,sha256=sha,format='feed',paired_asset_id=None,confidence=.8,warnings=[])
  self.analysis={'assets':[row],'checked_at':'2026-10-21T08:00:00+00:00','creative_reviewed':True,'review_kind':'model_advisory','raw':'excluded-base64','access_token':'excluded-secret'}

 def test_immutable_receipt_has_source_binding_not_raw_or_authority(self):
  identity=save_review_receipt(self.root,self.request,self.assets,self.analysis)
  path=self.root/'creative-review-receipts'/(identity+'.json');raw=path.read_bytes();value=json.loads(raw)
  self.assertEqual(hashlib.sha256(raw).hexdigest(),identity)
  self.assertNotIn(b'excluded',raw);self.assertFalse(value['execution_authority']);self.assertFalse(value['automatic_reuse'])
  self.assertEqual(value['sources'][0]['sha256'],self.assets[0]['sha256'])
  self.assertEqual(identity,save_review_receipt(self.root,self.request,self.assets,self.analysis))
  new=save_review_receipt(self.root,{**self.request,'revision':2},self.assets,self.analysis)
  self.assertNotEqual(new,identity);self.assertEqual(path.read_bytes(),raw)

 def test_pairing_failure_retains_analysis_receipt_and_does_not_save_proposal(self):
  request=self.request;launch={'brief':request['goal'],'drive_urls':request['drive_urls']};calls=[]
  materials=SimpleNamespace(refresh=lambda r:True,status=lambda r:{'binding_verified':True,'assets':{'complete':True,'assets':self.assets}})
  def analyzer(assets,**kwargs):calls.append(1);return self.analysis
  coordinator=CreativePreparation(self.root,intake=SimpleNamespace(latest=lambda:request),workflow=SimpleNamespace(report=lambda s:{'request':launch}),materials=materials,proposal=SimpleNamespace(save=lambda *a:self.fail('incomplete pair cannot save')),analyzer=analyzer)
  coordinator._run(request);state=coordinator.status()
  self.assertEqual(state['state'],'blocked');self.assertEqual(len(calls),1)
  self.assertEqual(state['review_stage'],'analysis_received')
  self.assertTrue((self.root/'creative-review-receipts'/(state['review_receipt_id']+'.json')).exists())

 def test_bad_source_binding_does_not_get_receipt(self):
  self.analysis['assets'][0]['sha256']='0'*64
  with self.assertRaises(ValueError):save_review_receipt(self.root,self.request,self.assets,self.analysis)
  self.assertFalse((self.root/'creative-review-receipts').exists())

if __name__=='__main__':unittest.main()
