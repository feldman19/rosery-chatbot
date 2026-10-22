import copy
import unittest
from operations_config import operation_proposal, CAMPAIGN


class OperationConfigTests(unittest.TestCase):
    def test_missing_approval_does_not_invent_execution_settings(self):
        p={'available':True,'proposal':{'ads':[]}}
        self.assertNotIn('operation_config',operation_proposal(p)['proposal'])

    def test_host_recipe_preserves_original_and_declares_feed_only_limit(self):
        p={'available':True,'proposal':{
            'current_authorization':{'existing_campaign_id':CAMPAIGN,'currency':'ILS',
                                     'per_new_adset_daily_minor':12000,'restart_at':'2026-10-18T19:00:00+03:00'},
            'adsets':[{'key':'style','label':'Style'},{'key':'reviews','label':'Reviews'}],
            'ads':[{'adset_key':'style','asset_id':'feed-source','story':{'asset_id':'story-source'}}]}}
        before=copy.deepcopy(p)
        result=operation_proposal(p)
        self.assertEqual(p,before)
        cfg=result['proposal']['operation_config']
        self.assertEqual(cfg['media_mode'],'feed_original_only')
        self.assertEqual(result['proposal']['required_format'],'dedicated_feed_story_five_copy')
        self.assertEqual(len(cfg['adsets']),1)
        self.assertEqual(cfg['adsets'][0]['budget']['minor'],12000)
        self.assertEqual(cfg['adsets'][0]['asset_ids'],['feed-source'])
        self.assertEqual(cfg['adsets'][0]['targeting']['facebook_positions'],['feed'])
        self.assertTrue(any('הסטורי' in text for text in cfg['limitations']))

    def test_wrong_campaign_or_currency_cannot_get_recipe(self):
        for field,value in [('existing_campaign_id','another'),('currency','USD'),('per_new_adset_daily_minor',True)]:
            auth={'existing_campaign_id':CAMPAIGN,'currency':'ILS','per_new_adset_daily_minor':12000,'restart_at':'2026-10-18T19:00:00+03:00'}
            auth[field]=value
            self.assertNotIn('operation_config',operation_proposal({'available':True,'proposal':{'current_authorization':auth}})['proposal'])


if __name__=='__main__':unittest.main()
