import unittest

from creative_drift import _copyable


class DriftInAppTests(unittest.TestCase):
    def test_only_copyable_creatives_offer_the_in_app_button(self):
        # Since catalog-off (2026-10-24) catalog and multi-media ads are copied exactly; unknown fields still block.
        story = {'page_id': '1', 'link_data': {'link': 'https://roseryjewelry.com'}}
        dof = {'creative_features_spec': {'image_animation': {'enroll_status': 'OPT_IN'}}}
        self.assertTrue(_copyable({'object_story_spec': story, 'degrees_of_freedom_spec': dof}))
        self.assertTrue(_copyable({'object_story_spec': story, 'asset_feed_spec': {'bodies': [{'text': 'a'}]}, 'degrees_of_freedom_spec': dof}))
        self.assertTrue(_copyable({'object_story_spec': {'page_id': '1', 'template_data': {}}, 'product_set_id': '5', 'degrees_of_freedom_spec': dof}))
        self.assertTrue(_copyable({'object_story_spec': story, 'asset_feed_spec': {'images': [{'hash': 'x'}]}, 'degrees_of_freedom_spec': dof}))
        self.assertFalse(_copyable({'object_story_spec': story, 'degrees_of_freedom_spec': dof, 'made_up_field': 1}))
        self.assertFalse(_copyable({'object_story_spec': story}))  # no feature settings to copy
        self.assertFalse(_copyable({}))
        # The report's read adds display-only fields; they must not hide the in-app button (regression 2026-10-24).
        self.assertTrue(_copyable({'id': '9', 'thumbnail_url': 'https://x.fbcdn.net/t.jpg', 'image_url': 'https://x.fbcdn.net/i.jpg',
                                   'body': 'טקסט', 'title': 'כותרת', 'link_url': 'https://roseryjewelry.com',
                                   'object_story_spec': story, 'degrees_of_freedom_spec': dof}))

if __name__ == '__main__':
    unittest.main()
