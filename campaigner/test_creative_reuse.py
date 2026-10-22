"""Synthetic receipts only; no real provider or credentials."""
from copy import deepcopy
import unittest
from campaign_operations import ACCOUNT,PARENT,digest
from creative_reuse import historical_source_reuse
import test_campaign_operations as fixtures


class CreativeReuseTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CampaignOperationsTests();self.fixture.setUp()
        f=self.fixture;p=f.preview();f.execute(p)
        self.job=f.service._get(p['operation_id'])
        # Explicitly simulate the production receipt shape. Never live evidence.
        self.job['timings']['transport_kind']='meta_http'
        for step in self.job['steps']:
            if step['kind']=='image':step['result']['upload_provenance']['transport_kind']='meta_http'
        self.files=deepcopy(self.job['files']);self.files[0]['id']='new_source_identity'

    def tearDown(self):self.fixture.tearDown()
    def warnings(self,job=None,files=None):
        return historical_source_reuse([self.job if job is None else job],self.files if files is None else files,ACCOUNT,PARENT)

    def test_same_bytes_new_id_warns_with_verified_ad_not_delivery_claim(self):
        result=self.warnings();self.assertEqual(len(result),1)
        self.assertEqual(result[0]['asset_id'],'new_source_identity')
        self.assertEqual(result[0]['ad_id'],next(s['result']['id'] for s in self.job['steps'] if s['kind']=='ads'))
        self.assertFalse(result[0]['current_presence_verified']);self.assertEqual(result[0]['verified_at'],self.job['finished_at'])

    def test_partial_image_only_wrong_account_tampering_and_injected_ignored(self):
        for mutation in ('partial','image_only','account','digest','creative','image_hash','injected','source_size'):
            with self.subTest(mutation=mutation):
                job=deepcopy(self.job)
                if mutation=='partial':job['state']='partial'
                elif mutation=='image_only':job['steps']=[s for s in job['steps'] if s['kind']=='image']
                elif mutation=='account':job['manifest']['account_id']='12345';job['digest']=digest(job['manifest'])
                elif mutation=='digest':job['manifest']['files'][0]['name']='changed'
                elif mutation=='creative':next(s for s in job['steps'] if s['kind']=='ads')['result']['verified_fields']['creative']['id']='99999'
                elif mutation=='image_hash':next(s for s in job['steps'] if s['kind']=='image')['result']['hash']='different'
                elif mutation=='source_size':job['manifest']['files'][0]['size']+=1;job['digest']=digest(job['manifest'])
                else:job['timings']['transport_kind']='injected_not_meta'
                self.assertEqual(self.warnings(job),[])

    def test_changed_bytes_unselected_file_and_reused_library_only_ignored(self):
        files=deepcopy(self.files);files[0]['sha256']='f'*64
        self.assertEqual(self.warnings(files=files),[]);self.assertEqual(self.warnings(files=[]),[])
        image=next(s for s in self.job['steps'] if s['kind']=='image');image['result']['reused']=True
        self.assertEqual(self.warnings(),[])

    def test_preview_attaches_warning_without_changing_approval_or_writing(self):
        f=self.fixture
        with f.service.db() as db:f.service._save(db,self.job)
        f.proposal['request_revision']=2;f.proposal['id']='new_proposal'
        writes=len(f.provider.writes);preview=f.preview()
        self.assertEqual(len(preview['historical_source_reuse']),1)
        self.assertEqual(preview['state'],'prepared');self.assertEqual(preview['approval_status'],'pending')
        self.assertNotIn('historical_source_reuse',preview['manifest'])
        self.assertEqual(len(f.provider.writes),writes)

    def test_malformed_legacy_receipts_skip_without_hiding_next_valid_record(self):
        for field in ('manifest','timings','steps','result','params','proof','files'):
            for invalid in (None,[],42,'legacy'):
                with self.subTest(field=field,invalid=invalid):
                    bad=deepcopy(self.job)
                    if field in ('result','params','proof'):
                        image=next(s for s in bad['steps'] if s['kind']=='image')
                        if field=='proof':image['result']['upload_provenance']=invalid
                        else:image[field]=invalid
                    elif field=='files':bad['manifest']['files']=invalid;bad['digest']=digest(bad['manifest'])
                    else:bad[field]=invalid
                    self.assertEqual(self.warnings(bad),[])
                    self.assertEqual(len(historical_source_reuse([bad,self.job],self.files,ACCOUNT,PARENT)),1)
        self.assertEqual(historical_source_reuse([None,42,[],self.job],self.files,ACCOUNT,PARENT),self.warnings())

    def test_recovered_receipt_uses_later_readback_date_or_omits_unknown_date(self):
        later='2026-10-21T10:00:00+00:00'
        self.job['reconciliation']={'checked_at':later,'read_only':True,'current_state':'match'}
        self.assertEqual(self.warnings()[0]['verified_at'],later)
        for value in (None,{}, {'checked_at':later,'read_only':True,'current_state':'unavailable'},
                      {'checked_at':'invalid','read_only':True,'current_state':'match'}):
            self.job['reconciliation']=value
            self.assertIsNone(self.warnings()[0]['verified_at'])


if __name__=='__main__':unittest.main()
