import unittest
from creative_destination import resolve_destination
from creative_preparation import build_creative_draft
from test_batch_capacity import BatchCapacityTests


class CreativeDestinationTests(unittest.TestCase):
    def test_explicit_collection_is_preserved_in_draft(self):
        assets, analysis = BatchCapacityTests().materials(1)
        url='https://roseryjewelry.com/collections/clothing'
        result=build_creative_draft({'id':'test','revision':1,'goal':'להעלות בגדים לדף '+url},assets,analysis)
        self.assertEqual(result['ads'][0]['destination_url'],url)
        self.assertEqual(result['ads'][0]['destination_source'],'owner_brief')
        self.assertFalse(result['ads'][0]['destination_verified_live'])

    def test_default_is_labelled_and_drive_is_not_destination(self):
        result=resolve_destination('חומרים https://drive.google.com/file/d/abc/view')
        self.assertEqual(result['source'],'business_default')
        self.assertFalse(result['verified_live'])

    def test_other_or_multiple_destinations_never_fall_back(self):
        for goal in ('https://other.example/item','http://roseryjewelry.com/','https://www.roseryjewelry.com/',
                     'https://roseryjewelry.com:1234/', 'https://u:p@roseryjewelry.com/',
                     'https://roseryjewelry.com/?access_token=secret',
                     'https://roseryjewelry.com/a https://roseryjewelry.com/b'):
            with self.subTest(goal=goal),self.assertRaises(ValueError):resolve_destination(goal)

if __name__=='__main__':unittest.main()
