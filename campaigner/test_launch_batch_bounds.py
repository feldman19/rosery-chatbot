"""Bounded synthetic execution; no network, credentials, or workers."""
import unittest
from copy import deepcopy
from test_placement_launch_path import PlacementLaunchTests
from creative_preparation import build_creative_draft


class LaunchBatchBoundsTests(unittest.TestCase):
    def test_three_sets_fifteen_paired_ads_approval_and_verified_readback(self):
        fixture=PlacementLaunchTests();fixture.setUp()
        try:
            f=fixture.f;base=deepcopy(f.proposal['ads'][0]);group=deepcopy(f.proposal['operation_config']['adsets'][0])
            f.proposal['ads']=[];f.proposal['operation_config']['adsets']=[]
            for g in range(3):
                current=deepcopy(group);current['key']='batch'+str(g);current['asset_ids']=[]
                for n in range(5):
                    ad=deepcopy(base);identity='batchsource%02d'%(g*5+n)
                    ad['asset_id']=identity
                    (f.root/'drive-assets'/(identity+'-'+ad['sha256']+'.bin')).write_bytes(f.file.read_bytes())
                    current['asset_ids'].append(identity);f.proposal['ads'].append(ad)
                f.proposal['operation_config']['adsets'].append(current)
            operation=fixture.preview()
            self.assertEqual(operation['blocked_reasons'],[])
            self.assertFalse(f.provider.writes)
            result=f.execute(operation)
            self.assertEqual(result['job']['state'],'verified')
            self.assertEqual(sum(k=='adsets' for k,v in f.provider.writes),3)
            self.assertEqual(sum(k=='ads' for k,v in f.provider.writes),15)
            self.assertTrue(all(v['status']=='PAUSED' for k,v in f.provider.writes if k in ('ads','adsets')))
            f.proposal['ads'].append({**base,'asset_id':'sixteenthad00'})
            self.assertTrue(fixture.preview()['blocked_reasons'])
        finally:
            fixture.tearDown()

    def test_thirty_images_group_to_three_sets_and_sixteenth_pair_rejected(self):
        assets=[];rows=[]
        for n in range(15):
            for fmt,other in [('feed','story'),('story','feed')]:
                identity=f'asset{n:02d}{fmt}'
                assets.append({'id':identity,'name':identity,'sha256':'a'*64})
                rows.append({'asset_id':identity,'sha256':'a'*64,'format':fmt,'concept_key':str(n),
                             'paired_asset_id':f'asset{n:02d}{other}','angle':'angle'+str(n//5),
                             'primary_text':'Copy','headline':'Headline','description':'Description'})
        request={'id':'test','revision':1}
        proposal=build_creative_draft(request,assets,{'assets':rows})
        self.assertEqual((len(proposal['adsets']),len(proposal['ads'])),(3,15))
        self.assertFalse(proposal['activation_available'])
        with self.assertRaises(ValueError):
            build_creative_draft(request,assets+assets[:2],{'assets':rows+rows[:2]})


if __name__=='__main__':
    unittest.main()
