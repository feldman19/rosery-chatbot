"""Isolated HTTP wiring; no external collectors, model calls or ad writers."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_server
from decision_memory import DecisionMemory
from test_decision_memory import envelope


class DecisionMemoryHTTPTests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request

    def test_chat_database_connection_closes_after_use(self):
        import sqlite3
        from claude_chat import ClaudeChat
        chat=ClaudeChat(Path(self.temp.name))
        with chat.db() as connection:
            connection.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')

    def test_memory_requires_local_session_and_manager(self):
        self.assertEqual(self.request('/api/decision-memory',token=False)[0],403)
        self.assertEqual(self.request('/api/decision-memory/query',{'question':'','ad_id':None},origin=False)[0],403)
        code,value=self.request('/api/decision-memory')
        self.assertEqual(code,200)
        self.assertEqual(value['past']['status'],'missing')
        self.assertFalse(value['recommendation']['execution_allowed'])

    def test_real_route_retrieves_imported_history_without_model_submission(self):
        memory=DecisionMemory(Path(self.temp.name)/'decision-memory.sqlite3')
        memory.ingest('rosery','393059510164294',envelope('393059510164294'))
        with patch('server.ClaudeChat.submit',side_effect=AssertionError('No model submission permitted')):
            code,value=self.request('/api/decision-memory/query',{'question':'bracelet','ad_id':'3'})
        self.assertEqual(code,200)
        self.assertEqual(value['past']['rows'],4)
        self.assertEqual(value['past']['examples'][0]['id'],'3')
        self.assertEqual(value['now']['status'],'missing_or_unverified')
        self.assertEqual(self.request('/api/decision-memory/query',{'question':'x','ad_id':None,'account_id':'222'})[0],400)

    def test_decision_outcome_journal_links_without_campaign_execution(self):
        code,decision=self.request('/api/decision-memory/decision',{'hypothesis':'Closeups may help','evidence_refs':['history:test'],
                                        'action':'prepare test only','evaluation_plan':'Compare later under documented conditions'})
        self.assertEqual(code,200)
        code,observed=self.request('/api/decision-memory/observation',{'decision_id':decision['id'],'evidence_refs':['snapshot:later'],
                                             'design':'before_after','note':'Owner-declared observation; not verified effect'})
        self.assertEqual(code,200)
        self.assertEqual(observed['parent'],decision['id'])
        code,value=self.request('/api/decision-memory')
        self.assertEqual(value['after']['status'],'recorded_observations_only')
        self.assertFalse(value['after']['causal_effect_verified'])
        self.assertFalse(value['after']['decisions'][0]['payload']['live_permission'])

    def test_existing_chat_keeps_original_metadata_sharing_boundary(self):
        with patch('server.build_workspace',return_value={'account_id':'393059510164294','ads':[]}), patch('server.ClaudeChat.submit',return_value={'state':'queued'}) as submit:
            code,_=self.request('/api/claude-chat',{'question':'Explain results','request_id':'not-used-by-mocked-submit','ad_id':None})
        self.assertEqual(code,200)
        self.assertNotIn('decision_memory',submit.call_args.args[2])
        self.assertFalse(submit.call_args.args[2]['image_provided'])


if __name__=='__main__':unittest.main()
