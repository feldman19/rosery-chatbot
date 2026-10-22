"""Real preparation/selector boundary, synthetic local provider only."""
import unittest
from copy import deepcopy
import test_campaign_operations as fixtures
from campaign_operations import ACCOUNT
from owner_action_proposals import build_owner_action_proposals

class LaunchCardTests(unittest.TestCase):
    setUp=fixtures.CampaignOperationsTests.setUp
    tearDown=fixtures.CampaignOperationsTests.tearDown
    binding=fixtures.CampaignOperationsTests.binding
    new_service=fixtures.CampaignOperationsTests.new_service
    preview=fixtures.CampaignOperationsTests.preview
    def cards(self,ops=None):
        report={'account_id':ACCOUNT,'checked_at':self.now.isoformat(),'freshness':{'state':'fresh','meta_last_success_at':self.now.isoformat()},'windows':{}}
        source={'available':True,'proposal':deepcopy(self.proposal),'source_binding':self.binding()}
        return build_owner_action_proposals(report,ops or self.service.status(),source,now=self.now)['proposals']
    def test_real_prepared_operation_becomes_exact_paused_card(self):
        p=self.preview();self.assertEqual(p['manifest']['source_binding'],self.binding())
        cards=self.cards();self.assertEqual(len(cards),1)
        self.assertEqual(cards[0]['next_action'],{'type':'review_exact_operation','operation_id':p['operation_id'],'digest':p['digest']})
        self.assertFalse(cards[0]['is_performance_recommendation']);self.assertEqual(self.provider.writes,[])
    def test_changed_current_proposal_is_not_a_card(self):
        self.preview();self.proposal['request_revision']+=1
        self.assertEqual(self.cards(),[])
    def test_legacy_manifest_not_rewritten_or_suggested(self):
        p=self.preview()
        legacy=deepcopy(p);legacy['manifest'].pop('source_binding')
        original=deepcopy(legacy)
        self.assertEqual(self.cards({'account_id':ACCOUNT,'proposals':[legacy]}),[])
        self.assertEqual(legacy,original)
    def test_prior_disjoint_attempt_does_not_hide_new_selection(self):
        p=self.preview();old=deepcopy(p);old.update(operation_id='old',state='unknown',provider_write_attempted=True)
        old['manifest']['files']=[dict(x,sha256='b'*64) for x in old['manifest']['files']]
        cards=self.cards({'account_id':ACCOUNT,'proposals':[old,p]})
        self.assertEqual(len(cards),1)
    def test_duplicate_bytes_or_unproven_old_selection_stays_blocked(self):
        p=self.preview()
        for missing in (False,True):
            old=deepcopy(p);old.update(operation_id='old',state='unknown',provider_write_attempted=True)
            if missing:old['manifest'].pop('files')
            self.assertEqual(self.cards({'account_id':ACCOUNT,'proposals':[old,p]}),[])

if __name__=='__main__':unittest.main()
