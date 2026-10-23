import unittest

from creative_drift import _copyable


class DriftInAppTests(unittest.TestCase):
    def test_only_copyable_creatives_offer_the_in_app_button(self):
        story = {'page_id': '1', 'link_data': {'link': 'https://roseryjewelry.com'}}
        self.assertTrue(_copyable({'object_story_spec': story}))
        self.assertTrue(_copyable({'object_story_spec': story, 'asset_feed_spec': {'bodies': [{'text': 'a'}]}}))
        self.assertFalse(_copyable({'object_story_spec': {'page_id': '1', 'template_data': {}}}))  # catalog
        self.assertFalse(_copyable({'object_story_spec': story, 'asset_feed_spec': {'images': [{'hash': 'x'}]}}))
        self.assertFalse(_copyable({'product_set_id': '5', 'object_story_spec': story}))
        self.assertFalse(_copyable({}))


if __name__ == '__main__':
    unittest.main()
