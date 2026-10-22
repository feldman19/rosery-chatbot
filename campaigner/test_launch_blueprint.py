import copy
import unittest
from launch_blueprint import build_blueprint, execution_workflow


class BlueprintTests(unittest.TestCase):
    def test_shared_fifteen_ad_boundary_not_old_ten_limit(self):
        p=self.proposal();base=p['ads'][0]
        p['ads']=[{**copy.deepcopy(base),'asset_id':f'feed{i}'} for i in range(15)]
        self.assertEqual(build_blueprint(p)['ad_count'],15)
        p['ads'].append({**copy.deepcopy(base),'asset_id':'feed15'})
        with self.assertRaises(ValueError):build_blueprint(p)

    def test_workflow_never_downgrades_five_copy_or_claims_speed(self):
        b = build_blueprint(self.proposal())
        full = execution_workflow(b, 'dedicated_feed_story_five_copy')
        self.assertIsNone(full['request_format'])
        self.assertFalse(full['full_requirement_supported'])
        self.assertIsNone(full['measured_end_to_end_seconds'])
        one = execution_workflow(b, 'dedicated_feed_story_single_copy')
        self.assertEqual(one['request_format'], 'dedicated_feed_story_single_copy')
        self.assertTrue(one['same_copy_across_placements'])
        self.assertTrue(one['approval_required'])

    def proposal(self):
        return {'ads': [{'asset_id': 'feed1', 'sha256': 'a'*64, 'adset_key': 'style',
                         'story': {'asset_id': 'story1', 'sha256': 'b'*64},
                         'primary_text': 'copy', 'primary_text_options': ['copy', 'second'],
                         'headline': 'title', 'landing_url': 'https://roseryjewelry.com/',
                         'url_parameters': 'utm_content=c11'}]}

    def test_full_input_is_not_execution_or_timing_proof(self):
        p = self.proposal(); before = copy.deepcopy(p); result = build_blueprint(p)
        self.assertTrue(result['input_complete']); self.assertEqual(result['source_count'], 2)
        self.assertEqual(result['concepts'][0]['primary_texts'], ['copy', 'second'])
        self.assertFalse(result['execution_available']); self.assertFalse(result['timing_target_verified'])
        self.assertEqual(p, before)

    def test_missing_story_and_tracking_are_explicit(self):
        p = self.proposal(); del p['ads'][0]['story']; del p['ads'][0]['url_parameters']
        result = build_blueprint(p)
        self.assertFalse(result['input_complete'])
        self.assertEqual({x['reason'] for x in result['issues']}, {'story_source_binding_missing', 'tracking_missing'})

    def test_changed_copy_invalidates_fingerprint(self):
        p = self.proposal(); original = build_blueprint(p)['fingerprint']
        p['ads'][0]['primary_text'] = 'changed'
        self.assertNotEqual(original, build_blueprint(p)['fingerprint'])

    def test_duplicate_and_external_destination(self):
        p = self.proposal(); p['ads'].append(copy.deepcopy(p['ads'][0]))
        with self.assertRaises(ValueError): build_blueprint(p)
        p = self.proposal(); p['ads'][0]['landing_url'] = 'https://other.example/'
        self.assertFalse(build_blueprint(p)['input_complete'])


if __name__ == '__main__': unittest.main()
