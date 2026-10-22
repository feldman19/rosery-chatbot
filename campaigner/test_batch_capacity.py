"""Synthetic capacity proof; no credentials, image provider or Meta calls."""
from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from creative_preparation import build_creative_draft
from work_intake import WorkIntake


class BatchCapacityTests(unittest.TestCase):
    def materials(self, count=15):
        assets, rows = [], []
        for i in range(count):
            for mode, other in [('feed', 'story'), ('story', 'feed')]:
                identity = f'asset_{i:03}_{mode}'
                assets.append({'id': identity, 'sha256': 'a'*64, 'name': identity})
                rows.append(dict(asset_id=identity, sha256='a'*64, format=mode,
                    paired_asset_id=f'asset_{i:03}_{other}', concept_key=str(i),
                    angle=f'angle {i//5}', primary_text='test copy',
                    headline='test headline', description='test description'))
        return assets, {'assets': rows}

    def test_thirty_links_fifteen_pairs_three_groups(self):
        assets, analysis = self.materials()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            request = WorkIntake(directory).create({'goal': 'prepare only',
                'drive_urls': [f'https://drive.google.com/file/d/asset_{i:010}/view' for i in range(30)]})
            draft = build_creative_draft(request, assets, analysis)
            self.assertEqual(len(draft['ads']), 15)
            self.assertEqual(len(draft['adsets']), 3)
            self.assertFalse(draft['activation_available'])
            self.assertFalse(request['execution_approval'])
            with self.assertRaises(ValueError):
                WorkIntake(directory).create({'goal': 'too large',
                    'drive_urls': request['drive_urls'] + ['https://drive.google.com/file/d/asset_9999999999/view']})

    def test_oversize_and_fourth_group_rejected(self):
        with self.assertRaises(ValueError):
            build_creative_draft({'id': 'test', 'revision': 1}, *self.materials(16))
        assets, analysis = self.materials()
        analysis['assets'][0]['angle'] = 'fourth angle'
        with self.assertRaises(ValueError):
            build_creative_draft({'id': 'test', 'revision': 1}, assets, analysis)

    def test_exact_approved_fifteen_ads_three_groups_fake_readback(self):
        from test_campaign_operations import CampaignOperationsTests
        fixture = CampaignOperationsTests()
        fixture.setUp()
        try:
            original = deepcopy(fixture.proposal['ads'][0])
            group = deepcopy(fixture.proposal['operation_config']['adsets'][0])
            ads, groups = [], []
            for g in range(3):
                entry = deepcopy(group)
                entry.update(key=f'group{g}', name=f'Test group {g}', asset_ids=[])
                for i in range(5):
                    identity = f'capacity_{g}_{i}_source'
                    ad = deepcopy(original)
                    ad['asset_id'] = identity
                    (fixture.root/'drive-assets'/f'{identity}-{ad["sha256"]}.bin').write_bytes(fixture.file.read_bytes())
                    ads.append(ad)
                    entry['asset_ids'].append(identity)
                groups.append(entry)
            fixture.proposal['ads'] = ads
            fixture.proposal['operation_config']['adsets'] = groups
            preview = fixture.preview(selected_adset_keys=[x['key'] for x in groups])
            self.assertEqual(preview['blocked_reasons'], [])
            result = fixture.execute(preview)
            self.assertEqual(result['operation']['state'], 'verified')
            self.assertEqual(sum(k=='ads' for k,_ in fixture.provider.writes), 15)
            self.assertEqual(sum(k=='adsets' for k,_ in fixture.provider.writes), 3)
            self.assertTrue(all(p['status']=='PAUSED' for k,p in fixture.provider.writes if k in ('ads','adsets')))
        finally:
            fixture.tearDown()
