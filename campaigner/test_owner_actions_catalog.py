"""Synthetic shapes: live GET evidence is still required before deployment."""
from copy import deepcopy
import unittest

from creative_drift import _copyable
from owner_actions import ActionBlocked
from campaign_operations_transport import ProviderRejected
import test_owner_actions as base
from test_owner_actions import AD, CREATIVE


class CatalogTests(unittest.TestCase):
    setUp = base.OwnerActionsTests.setUp
    tearDown = base.OwnerActionsTests.tearDown
    make = base.OwnerActionsTests.make
    def catalog(self):
        creative = self.meta.creatives[CREATIVE]
        creative['object_story_spec'] = {'page_id': '1', 'template_data': {
            'link': 'https://example.com', 'name': '{{product.name}}',
            'message': 'products', 'call_to_action': {'type': 'SHOP_NOW'}}}
        creative['product_set_id'] = '77777'
        creative['template_url_spec'] = {'web': {'url': 'https://example.com/{{product.id}}'}}
        creative['use_page_actor_override'] = False
        creative['degrees_of_freedom_spec']['creative_features_spec'].update({
            f: {'enroll_status': 'OPT_IN'} for f in ('standard_enhancements_catalog',
                'dynamic_media', 'product_extensions', 'adapt_to_placement')})
        return creative

    def test_catalog_and_media_preserved(self):
        creative = self.catalog()
        creative['asset_feed_spec'] = {
            'images': [{'hash': 'x', 'adlabels': [{'name': 'portrait'}]}],
            'videos': [{'video_id': '88888'}], 'ad_formats': ['SINGLE_IMAGE', 'SINGLE_VIDEO'],
            'asset_customization_rules': [{'image_label': {'name': 'portrait'},
                'customization_spec': {'publisher_platforms': ['instagram']}}]}
        original = deepcopy(creative)
        self.assertTrue(_copyable(creative))
        view = self.actions.preview('drift:' + AD)
        self.assertIn('התוכן, המוצרים והקישורים נשארים זהים', view['warnings'])
        self.assertEqual(self.meta.writes, [])
        receipt = self.actions.confirm(view['confirm_token'])
        self.assertEqual(receipt['state'], 'verified')
        for key in ('object_story_spec', 'asset_feed_spec', 'product_set_id', 'url_tags',
                    'template_url_spec', 'use_page_actor_override'):
            self.assertEqual(self.meta.writes[0][2][key], original[key])

    def test_unknown_field_blocks(self):
        creative = self.catalog()
        creative['unknown_visible_field'] = 'content'
        self.assertFalse(_copyable(creative))
        with self.assertRaisesRegex(ActionBlocked, 'unknown_visible_field'):
            self.actions.preview('drift:' + AD)
        self.assertEqual(self.meta.writes, [])

    def test_mismatch_undo(self):
        self.mismatch('product_set_id', '99999')

    def test_template_mismatch_undo(self):
        self.mismatch('object_story_spec', {'page_id': '1'})

    def test_feed_mismatch_undo(self):
        self.mismatch('asset_feed_spec', {'images': [{'hash': 'different'}]})

    def test_missing_features_undo(self):
        self.mismatch('degrees_of_freedom_spec', {})

    def mismatch(self, field, value):
        self.catalog()
        create = self.meta.create
        def altered(token, edge, params):
            result = create(token, edge, params)
            self.meta.creatives[result['id']][field] = value
            return result
        self.meta.create = altered
        receipt = self.actions.confirm(self.actions.preview('drift:' + AD)['confirm_token'])
        self.assertEqual(receipt['state'], 'not_verified')
        self.assertEqual(receipt['rollback'], 'verified')
        self.assertEqual(self.meta.ads[AD]['creative'], CREATIVE)

    def test_refusal_no_retry(self):
        self.catalog()
        view = self.actions.preview('drift:' + AD)
        self.meta.fail_next_write = ProviderRejected(details={
            'message': 'standard_enhancements_catalog does not support OPT_OUT'})
        receipt = self.actions.confirm(view['confirm_token'])
        self.assertEqual(receipt['state'], 'rejected')
        self.assertEqual(receipt['detail'], 'מטא לא מאפשרת לכבות את שיפורים סטנדרטיים לקטלוג במודעת קטלוג')
        self.assertEqual(len(self.meta.writes), 1)


if __name__ == '__main__':
    unittest.main()
