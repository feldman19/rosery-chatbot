"""Synthetic draft contract checks; no providers or generated creative claims."""
import unittest
from creative_preparation import build_creative_draft
import test_batch_capacity as fixtures


class CreativeCopyContractTests(unittest.TestCase):
    def build(self,field,value,*,remove=False):
        assets,analysis=fixtures.BatchCapacityTests().materials(1)
        for row in analysis['assets']:
            if remove:row.pop(field)
            else:row[field]=value
        return build_creative_draft({'id':'synthetic','revision':1},assets,analysis)

    def test_empty_optional_description_preserved(self):
        draft=self.build('description','')
        self.assertEqual(draft['ads'][0]['description'],'')
        self.assertFalse(draft['activation_available'])

    def test_required_copy_still_rejects_blank(self):
        for field in ('primary_text','headline'):
            for value in ('','   ',None):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.build(field,value)

    def test_optional_description_must_still_be_explicit_string(self):
        for value in (None,[],{},123):
            with self.subTest(value=value),self.assertRaises(ValueError):self.build('description',value)
        with self.assertRaises(ValueError):self.build('description',None,remove=True)


if __name__=='__main__':unittest.main()
