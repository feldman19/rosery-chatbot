import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from creative_vision import CreativeVision,FIELDS

class VisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'drive-assets').mkdir()
        (self.root/'creative-image-sharing.json').write_text(json.dumps({'approved':True,'scope':'selected_drive_creatives'}))
        self.raw=b'\x89PNG\r\n\x1a\nsynthetic';self.sha=hashlib.sha256(self.raw).hexdigest();self.identity='abcdefghijklmnop'
        self.path=self.root/'drive-assets'/(self.identity+'-'+self.sha+'.bin');self.path.write_bytes(self.raw)
        self.asset={'id':self.identity,'sha256':self.sha,'name':'test.png','mime_type':'image/png'}
        self.decode=patch('creative_vision.decode_image',return_value={'width':1080,'height':1350,'format':'PNG'});self.decode.start();self.addCleanup(self.decode.stop)
    def row(self):
        row={k:'draft' for k in FIELDS};row.update(asset_id=self.identity,sha256=self.sha,format='feed',paired_asset_id=None,confidence=.8,warnings=[])
        return row
    def test_actual_image_block_and_bound_result(self):
        captured=[]
        def runner(payload):captured.append(json.loads(payload));return json.dumps({'assets':[self.row()]})
        result=CreativeVision(self.root,runner).analyze([self.asset])
        blocks=captured[0]['message']['content'];image=next(x for x in blocks if x['type']=='image')
        import base64
        self.assertEqual(base64.b64decode(image['source']['data']),self.raw)
        self.assertTrue(result['creative_reviewed']);self.assertFalse(result['publication_ready']);self.assertEqual(result['provider_writes'],0)
    def test_consent_also_required_for_injected_runner(self):
        (self.root/'creative-image-sharing.json').unlink()
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:self.fail()).analyze([self.asset])
    def test_changed_bytes_never_transmitted(self):
        self.path.write_bytes(b'changed')
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:self.fail()).analyze([self.asset])
    def test_mime_mismatch_rejected(self):
        self.asset['mime_type']='image/jpeg'
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:self.fail()).analyze([self.asset])
    def test_wrong_hash_in_answer_rejected(self):
        row=self.row();row['sha256']='0'*64
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:{'assets':[row]}).analyze([self.asset])
    def test_missing_asset_rejected(self):
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:{'assets':[]}).analyze([self.asset])
    def test_extra_action_field_rejected(self):
        row=self.row();row['activate']=True
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:{'assets':[row]}).analyze([self.asset])
    def test_model_cannot_override_dimension_format(self):
        row=self.row();row['format']='story'
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:{'assets':[row]}).analyze([self.asset])
    def test_unselected_pair_rejected(self):
        row=self.row();row['paired_asset_id']='other_drive_identity'
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:{'assets':[row]}).analyze([self.asset])
    def test_count_duplicate_and_filename_bounds(self):
        for assets in ([],[self.asset]*11,[self.asset,self.asset],[dict(self.asset,name='../\nsecret')]):
            with self.subTest(assets=len(assets)),self.assertRaises(ValueError):CreativeVision(self.root,lambda p:self.fail()).analyze(assets)
    def test_plain_failure_text_not_review(self):
        with self.assertRaises(ValueError):CreativeVision(self.root,lambda p:'Could not process image').analyze([self.asset])
    def test_owner_goal_is_distinct_context_for_same_images(self):
        captured=[]
        vision=CreativeVision(self.root,lambda payload:captured.append(json.loads(payload)) or {'assets':[self.row()]})
        for goal in ('Promote clothes to new shoppers','Present gift ideas to returning shoppers'):
            vision.analyze([self.asset],owner_brief=goal)
        first=captured[0]['message']['content'];second=captured[1]['message']['content']
        self.assertNotEqual(first[1],second[1]);self.assertIn('Promote clothes',first[1]['text'])
        self.assertEqual([b for b in first if b['type']=='image'],[b for b in second if b['type']=='image'])
    def test_brief_with_explicit_secret_or_money_is_not_sent(self):
        for brief in ('Use api_key=secret','Budget $100','Contact owner@example.com'):
            with self.subTest(brief=brief),self.assertRaises(ValueError):CreativeVision(self.root,lambda p:self.fail()).analyze([self.asset],owner_brief=brief)




    def test_unpaired_review_reaches_actionable_bound_status(self):
        from types import SimpleNamespace
        from creative_preparation import CreativePreparation
        request={'id':'pair-request','revision':2,'goal':'clothing sales','drive_urls':['fake-link']}
        launch={'brief':request['goal'],'drive_urls':request['drive_urls']}
        asset={**self.asset,'content_read':True,'dimensions':{'width':1080,'height':1350}}
        vision=CreativeVision(self.root,lambda payload:{'assets':[self.row()],'proposed_adsets':[]})
        materials=SimpleNamespace(refresh=lambda r:True,status=lambda r:{'binding_verified':True,'assets':{'complete':True,'assets':[asset]}})
        coordinator=CreativePreparation(self.root,intake=SimpleNamespace(latest=lambda:request),workflow=SimpleNamespace(report=lambda s:{'request':launch}),materials=materials,proposal=SimpleNamespace(save=lambda *a:self.fail('Unpaired source must never save proposal')),analyzer=lambda assets,owner_brief:vision.analyze(assets,owner_brief=owner_brief))
        coordinator._run(request)
        status=coordinator.status()
        self.assertEqual(status['state'],'blocked');self.assertIn('source_issue',status,status);self.assertEqual(status['source_issue']['missing_format'],'story')
        self.assertEqual(status['source_issue']['sha256'],self.sha);self.assertEqual(status['source_issue']['request_revision'],2)
        self.assertTrue(status['review_receipt_id']);self.assertEqual(status['provider_writes'],0)

if __name__=='__main__':unittest.main()
