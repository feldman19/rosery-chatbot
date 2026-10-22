import copy
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from creative_claims import CreativeClaimReviewRequired, validate_creative_claims
from creative_vision import CreativeVision, FIELDS
from creative_preparation import build_creative_draft


class CreativeClaimsTests(unittest.TestCase):
    def setUp(self):
        self.rows=[]
        for i,fmt in enumerate(('feed','story')):
            row={key:'תיאור חזותי' for key in FIELDS}
            row.update(asset_id='source_asset_'+str(i),sha256=str(i)*64,format=fmt,
                       paired_asset_id='source_asset_'+str(1-i),confidence=.8,warnings=[],
                       concept_key='shirt',primary_text='חולצה בגוון כחול, למראה יומיומי',
                       headline='לוק חדש',description='',observed_product='חולצה כחולה')
            self.rows.append(row)
        self.sources=[{key:row[key] for key in ('asset_id','sha256','format')} for row in self.rows]
        self.assets=[{'id':row['asset_id'],'sha256':row['sha256'],'name':row['format']+'.png'} for row in self.rows]
        self.request={'id':'request','revision':1,'goal':'clothing sales'}

    def test_claims_rejected_at_both_authoritative_boundaries(self):
        for claim in ('זהב מלא','בעבודת יד','משלוח תוך 24 שעות','50% הנחה','אחריות לכל החיים','500 לקוחות מרוצים','100% כותנה','צמידי כסף 925'):
            for field in ('primary_text','headline','description'):
                rows=copy.deepcopy(self.rows);rows[0][field]=claim
                for operation in (lambda:CreativeVision._validate({'assets':rows},self.sources),
                                  lambda:build_creative_draft(self.request,self.assets,{'assets':rows})):
                    with self.subTest(claim=claim,field=field),self.assertRaises(CreativeClaimReviewRequired) as caught:
                        operation()
                    self.assertEqual(caught.exception.status,'needs_fact_review')
                    self.assertEqual(caught.exception.issues[0]['field'],field)
                    self.assertEqual(rows[0][field],claim)

    def test_safe_visual_copy_and_color_only_material_pass(self):
        for row in self.rows:
            row.update(primary_text='צמיד בגוון זהב וטבעת בגוון כסף/אפור. בחרו את הלוק שלכם.',observed_product='צמיד בגוון זהב')
        self.assertEqual(len(CreativeVision._validate({'assets':self.rows},self.sources)),2)
        draft=build_creative_draft(self.request,self.assets,{'assets':self.rows})
        self.assertEqual(draft['ads'][0]['primary_text'],self.rows[0]['primary_text'])

    def test_ocr_and_warnings_are_not_claim_permission(self):
        self.rows[0]['visible_text']='50% הנחה ומשלוח תוך 24 שעות'
        self.rows[0]['warnings']=['אין ראיה לזהב מלא או אחריות לכל החיים']
        validate_creative_claims(self.rows)
        self.rows[0]['primary_text']=self.rows[0]['visible_text']
        with self.assertRaises(CreativeClaimReviewRequired):validate_creative_claims(self.rows)

    def test_material_classification_requires_evidence_and_unicode_does_not_bypass(self):
        self.rows[0]['observed_product']='טבעת זהב מלא'
        with self.assertRaises(CreativeClaimReviewRequired):validate_creative_claims(self.rows)

    def test_blocked_generated_review_is_preserved_without_authority_or_images(self):
        rows=copy.deepcopy(self.rows);rows[0]['headline']='אחריות לכל החיים'
        sources=[{**s,'name':'selected.png','raw':b'synthetic-image','mime_type':'image/png','review_sha256':'f'*64} for s in self.sources]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            runner=lambda payload:{'assets':rows}
            with patch.object(CreativeVision,'_sources',return_value=sources),self.assertRaises(CreativeClaimReviewRequired) as caught:
                CreativeVision(directory,runner).analyze([{'source_kind':'device'}],local_image_review_consent=True)
            identity=caught.exception.blocked_review_receipt_id
            self.assertIsNotNone(identity)
            receipt=json.loads((Path(directory)/'creative-blocked-reviews'/(identity+'.json')).read_text(encoding='utf-8'))
            self.assertEqual(receipt['review']['assets'][0]['headline'],'אחריות לכל החיים')
            self.assertEqual(receipt['issues'][0]['source_name'],'selected.png')
            self.assertFalse(receipt['automatic_reuse']);self.assertFalse(receipt['execution_authority'])
            self.assertNotIn('raw',receipt['sources'][0])
        self.rows[0]['observed_product']='חולצה כחולה'
        self.rows[0]['primary_text']='זהב\u200b מלא'
        with self.assertRaises(CreativeClaimReviewRequired):validate_creative_claims(self.rows)


if __name__=='__main__':unittest.main()
