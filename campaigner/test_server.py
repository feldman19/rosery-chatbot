import json
import re
from pathlib import Path
import tempfile
import shutil
import time
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from server import make_server
from evidence import SnapshotStore


class ServerTests(unittest.TestCase):
    def test_management_report_is_authenticated_read_only_and_aggregate(self):
        self.assertEqual(self.request('/api/management-report',token=False)[0],403)
        with patch('server.management_report',return_value={'account_id':'393059510164294','windows':{'7':{'metrics':{'spend':None},'campaigns':[]}}}) as report:
            code,value=self.request('/api/management-report')
        self.assertEqual(code,200)
        self.assertIsNone(value['windows']['7']['metrics']['spend'])
        report.assert_called_once()
        self.assertEqual(self.request('/api/management-report',{})[0],404)

    def test_campaign_operations_require_local_session_and_exact_approval_shape(self):
        self.assertEqual(self.request('/api/campaign-operations',token=False)[0],403)
        self.assertEqual(self.request('/api/campaign-operations/preview',{'action':'prepare_launch'},origin=False)[0],403)
        self.assertEqual(self.request('/api/campaign-operations/approve',{'operation_id':'x'})[0],400)
        self.assertEqual(self.request('/api/campaign-operations/execute',{'operation_id':'x','approved':True})[0],400)
        self.assertEqual(self.request('/api/campaign-operations/schedule',{'operation_id':'x','at':'2026-10-18T19:00:00+03:00','approved':True})[0],400)

    def test_campaign_operation_dispatch_does_not_block_http_on_provider(self):
        with patch('server.CampaignOperations.enqueue',return_value={'operation':{'state':'queued'}}) as queued, patch('server.CampaignOperations.start_worker'):
            code,result=self.request('/api/campaign-operations/execute',{'operation_id':'op-test'})
        self.assertEqual(code,200)
        self.assertEqual(result['operation']['state'],'queued')
        queued.assert_called_once_with('op-test')

    def test_campaign_operation_preview_uses_service_not_browser_authority(self):
        with patch('server.CampaignOperations.preview',return_value={'operation':{'state':'preview','digest':'digest'}}) as preview:
            code,result=self.request('/api/campaign-operations/preview',{'action':'prepare_launch'})
        self.assertEqual(code,200)
        self.assertEqual(result['operation']['state'],'preview')
        preview.assert_called_once_with({'action':'prepare_launch'})

    def test_owner_home_is_the_simple_screen_with_a_local_session(self):
        # Owner 2026-10-24: the old advanced workspace is retired; "/" is the simple screen and /advanced redirects to it.
        Path(self.temp.name,'rosery-pilot.json').write_text('{}')
        with urllib.request.urlopen(self.base) as response:
            page=response.read().decode()
            self.assertIn('campaigner_media=',response.headers.get('Set-Cookie',''))
        self.assertNotIn('data-panel="requests"',page)
        self.assertNotIn('__SESSION__',page)
        self.assertIn("const token='"+self.token+"'",page)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        try:
            urllib.request.build_opener(NoRedirect).open(self.base+'/advanced')
            self.fail('/advanced must redirect')
        except urllib.error.HTTPError as error:
            self.assertEqual((error.code,error.headers.get('Location')),(302,'/'))

    def test_owner_request_retry_returns_one_receipt(self):
        body={'question':'Prepare five copy options','request_id':'220ce122-8159-4b49-a03b-217d5fc8312c'}
        code,first=self.request('/api/help/escalate',body)
        self.assertEqual(code,200)
        code,retry=self.request('/api/help/escalate',body)
        self.assertEqual(code,200)
        self.assertEqual(first['id'],retry['id'])
        self.assertTrue(retry['replayed'])
        self.assertEqual(len(self.request('/api/workspace')[1]['questions']),1)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaigner-test-', dir=Path(__file__).parent)
        self.server = make_server(0, self.temp.name, workspace='manager')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        page = urllib.request.urlopen(self.base).read().decode()
        self.token = re.search("const token='([^']+)'", page).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        # Windows: a background writer can still hold a file for a moment; retry, then leave the temp folder (not a test result).
        for _ in range(5):
            try:
                self.temp.cleanup()
                break
            except OSError:
                time.sleep(0.2)
        else:
            shutil.rmtree(self.temp.name, ignore_errors=True)

    def request(self, path, body=None, token=True, origin=True, host=None):
        headers = {'X-Local-Session': self.token if token else 'bad'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            if origin:
                headers['Origin'] = self.base
        if host:
            headers['Host'] = host
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_end_to_end_and_replay_denial(self):
        if not (Path(__file__).parent / 'demo-data.json').is_file():
            self.skipTest('the old demo flow needs demo-data.json, a local file that is not in git')
        code, proposal = self.request('/api/propose', {'budget_minor': 8000})
        self.assertEqual(code, 200)
        pid = proposal['id']
        self.assertEqual(self.request('/api/execute', {'id': pid})[0], 400)
        self.assertEqual(self.request('/api/approve', {'id': pid})[0], 200)
        code, result = self.request('/api/execute', {'id': pid})
        self.assertEqual(code, 200)
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(self.request('/api/execute', {'id': pid})[0], 400)
        state = self.request('/api/state')[1]
        self.assertEqual(state['entity']['state']['budget_minor'], 8000)

    def test_reject_cross_origin_and_missing_token(self):
        self.assertEqual(self.request('/api/state', token=False)[0], 403)
        self.assertEqual(self.request('/api/propose', {'budget_minor': 8000}, origin=False)[0], 403)
        self.assertEqual(self.request('/api/state', host='evil.example')[0], 403)

    def test_preparation_loop_is_protected_and_never_accepts_campaign_changes(self):
        self.assertEqual(self.request('/api/preparation-loop',token=False)[0],403)
        code,state=self.request('/api/preparation-loop');self.assertEqual(code,200);self.assertTrue(state['no_live_actions'])
        self.assertEqual(self.request('/api/preparation-loop/start',{},origin=False)[0],403)
        self.assertEqual(self.request('/api/preparation-loop/start',{'budget_minor':30000})[0],400)
        self.assertEqual(self.request('/api/preparation-loop/start',{})[0],400)

    def test_live_cache_requires_session_and_returns_no_live_permission(self):
        self.assertEqual(self.request('/api/live',token=False)[0],403)
        code,state=self.request('/api/live')
        self.assertEqual(code,200);self.assertFalse(state['live_action_allowed'])
        self.assertEqual(state['refresh_interval_seconds'],300)
        self.assertEqual(self.request('/api/live/refresh',{'url':'https://evil.example'})[0],400)

    def test_action_desk_auth_and_review_never_approve_spend(self):
        self.assertEqual(self.request('/api/action-desk',token=False)[0],403)
        code,result=self.request('/api/action-desk');self.assertEqual(code,200)
        finding=result['findings'][0]
        body={'id':finding['id'],'expected_revision':finding['revision'],'action':'start','note':''}
        self.assertEqual(self.request('/api/action-desk/review',body,origin=False)[0],403)
        code,saved=self.request('/api/action-desk/review',body)
        self.assertEqual(code,200);self.assertFalse(saved['live_action_allowed'])
        self.assertEqual(self.request('/api/action-desk/review',body)[0],400)

    def test_forbid_identity_and_fault_injection(self):
        self.assertEqual(self.request('/api/propose', {'budget_minor': 8000, 'tenant_id': 'other'})[0], 400)
        self.assertEqual(self.request('/api/execute', {'id': 'x', 'fault': 'before_call'})[0], 400)
        self.assertEqual(self.request('/api/propose', {'budget_minor': 20001})[0], 400)

    def test_restart_does_not_reset_entity(self):
        if not (Path(__file__).parent / 'demo-data.json').is_file():
            self.skipTest('the old demo flow needs demo-data.json, a local file that is not in git')
        pid = self.request('/api/propose', {'budget_minor': 9000})[1]['id']
        self.request('/api/approve', {'id': pid})
        self.request('/api/execute', {'id': pid})
        restarted = make_server(0, self.temp.name)
        restarted.server_close()
        self.assertEqual(self.request('/api/state')[1]['entity']['state']['budget_minor'], 9000)

    def test_imported_evidence_separate_and_read_only(self):
        store = SnapshotStore(str(Path(self.temp.name) / 'evidence.sqlite3'))
        store.bind_account('demo-rosery', 'meta', 'demo-meta')
        if not (Path(__file__).parent / 'evidence-example.json').is_file():
            self.skipTest('evidence-example.json is a local fixture that is not in git')
        fixture = json.loads((Path(__file__).parent / 'evidence-example.json').read_text(encoding='utf-8'))
        store.ingest('demo-rosery', fixture)
        response = self.request('/api/evidence')[1]
        self.assertFalse(response['live'])
        self.assertEqual(len(response['snapshots']), 1)
        self.assertFalse(response['snapshots'][0]['assessment']['live_action_allowed'])
        diagnostics = self.request('/api/diagnostics')[1]
        self.assertFalse(diagnostics['reports'][0]['live_action_allowed'])
        self.assertIsNone(diagnostics['reports'][0]['spend_minor'])
        self.assertIn('account_grain_unverified', diagnostics['reports'][0]['blocked_by'])
        self.assertEqual(self.request('/api/evidence', {'file': 'x'})[0], 404)

    def test_decision_loop_is_private_read_only_and_has_no_fake_approval(self):
        self.assertEqual(self.request('/api/decision-loop',token=False)[0],403)
        code,report=self.request('/api/decision-loop')
        self.assertEqual(code,200);self.assertEqual(report['assessment_count'],0)
        self.assertFalse(report['live_action_allowed']);self.assertFalse(report['write_connected'])
        self.assertEqual(self.request('/api/decision-loop',{'approve':True})[0],404)



    def test_live_business_engine_route_cannot_authorize_or_execute(self):
        self.assertEqual(self.request('/api/automation',token=False)[0],403)
        code,report=self.request('/api/automation')
        self.assertEqual(code,200);self.assertFalse(report['live_action_allowed'])
        self.assertFalse(report['write_connected']);self.assertEqual(report['candidate']['status'],'blocked')
        self.assertEqual(self.request('/api/automation',{'execute':True})[0],404)

    def test_work_products_are_private_read_only_and_do_not_execute(self):
        for path in ('/api/operator','/api/experiment'):
            self.assertEqual(self.request(path,token=False)[0],403)
            code,value=self.request(path);self.assertEqual(code,200);self.assertFalse(value['live_action_allowed'])
            self.assertEqual(self.request(path,{'execute':True})[0],404)

    def test_execution_token_route_cannot_choose_app_or_bypass_binding(self):
        path='/api/connections/meta-executor/setup'
        self.assertEqual(self.request(path,{'access_token':'x'*40},token=False)[0],403)
        self.assertEqual(self.request(path,{'access_token':'x'*40},origin=False)[0],403)
        self.assertEqual(self.request(path,{'access_token':'x'*40,'app_id':'fake'})[0],400)
        self.assertEqual(self.request(path,{'access_token':'x'*40})[0],502)
        self.assertFalse((Path(self.temp.name)/'meta-execution.dpapi').exists())

    def test_real_execution_routes_fail_closed_without_credentials_and_exact_approval(self):
        base='/api/meta-execution/'
        self.assertEqual(self.request(base+'status',token=False)[0],403)
        code,status=self.request(base+'status')
        self.assertEqual(code,200)
        self.assertFalse(status['default_action_authorized'])
        self.assertFalse(status['automatic_execution'])
        self.assertEqual(status['proposals'],[])
        self.assertEqual(self.request(base+'preview',{'action':'pause'},origin=False)[0],403)
        self.assertEqual(self.request(base+'preview',{'action':'pause','approved':True})[0],400)
        self.assertEqual(self.request(base+'preview',{'action':'pause'})[0],400)
        self.assertEqual(self.request(base+'approve',{'operation_id':'a'*32,'digest':'b'*64})[0],400)
        self.assertEqual(self.request(base+'execute',{'operation_id':'a'*32,'verified':True})[0],400)
        code,result=self.request(base+'execute',{'operation_id':'a'*32})
        self.assertEqual(code,200);self.assertEqual(result['state'],'blocked')
        self.assertEqual(self.request(base+'stop',{})[0],200)

    def test_launch_request_is_preparation_not_authority(self):
        self.assertEqual(self.request('/api/launch',token=False)[0],403)
        code,report=self.request('/api/launch')
        self.assertEqual(code,200);self.assertIsNone(report['request'])
        body={'brief':'Prepare new collection sales launch','drive_urls':['https://drive.google.com/drive/folders/abcdefghijklmno']}
        self.assertEqual(self.request('/api/launch/request',body,origin=False)[0],403)
        self.assertEqual(self.request('/api/launch/request',{**body,'approved':True})[0],400)
        code,report=self.request('/api/launch/request',body)
        self.assertEqual(code,200);self.assertEqual(report['goal'],'sales')
        self.assertFalse(report['publication_available']);self.assertFalse(report['request']['asset_content_fetched'])
        self.assertFalse(report['request']['execution_approval'])
        self.assertEqual(self.request('/api/launch')[1]['request']['id'],report['request']['id'])

    def test_drive_authorization_and_reads_reject_browser_scope_or_destination(self):
        self.assertEqual(self.request('/api/drive/oauth/status',token=False)[0],403)
        code,value=self.request('/api/drive/oauth/status')
        self.assertEqual(code,200);self.assertFalse(value['connection']['configured'])
        self.assertEqual(self.request('/api/drive/oauth/begin',{},origin=False)[0],403)
        self.assertEqual(self.request('/api/drive/oauth/begin',{'scope':'full-drive'})[0],400)
        self.assertEqual(self.request('/api/drive/oauth/begin',{})[0],400)
        self.assertEqual(self.request('/api/launch/materials/read',{'url':'https://evil.invalid'})[0],400)
        self.assertEqual(self.request('/api/launch/materials/read',{})[0],400)
        # Callback intentionally omits session but fails before exchange without valid state.
        self.assertEqual(self.request('/api/drive/oauth/callback?code=synthetic&state=wrong',token=False)[0],400)

    def test_saved_oauth_is_success_even_if_material_kickoff_fails(self):
        with patch('server.DriveOAuth.complete',return_value={'saved':True,'verified':True}),patch('server.LaunchWorkflow.report',side_effect=ValueError('downstream unavailable')):
            with urllib.request.urlopen(self.base+'/api/drive/oauth/callback?code=synthetic&state=synthetic_state_value') as response:
                self.assertEqual(response.status,200)
                body=response.read().decode()
        self.assertIn('החיבור לדרייב אומת ונשמר מוצפן',body)
        self.assertIn('בלי להתחבר מחדש',body)

    def test_strategy_goal_and_analysis_never_create_execution_authority(self):
        self.assertEqual(self.request('/api/strategy',token=False)[0],403)
        self.assertEqual(self.request('/api/strategy/request',{'goal':'Awareness for a new audience'},origin=False)[0],403)
        self.assertEqual(self.request('/api/strategy/request',{'goal':'Awareness','approved':True})[0],400)
        code,report=self.request('/api/strategy/request',{'goal':'Awareness for a new audience'})
        self.assertEqual(code,200);self.assertEqual(report['request']['category'],'awareness')
        self.assertFalse(report['model_connected']);self.assertFalse(report['live_action_allowed'])
        self.assertEqual(self.request('/api/strategy/analyze',{'ads':[]})[0],400)
        code,result=self.request('/api/strategy/analyze',{})
        self.assertEqual(code,200);self.assertFalse(result['verified'])
        self.assertEqual(result['numerical_recommendations'],[])

    def test_unified_work_request_and_persisted_status_are_not_campaign_authority(self):
        self.assertEqual(self.request('/api/work/status',token=False)[0],403)
        body={'goal':'הכנת קמפיין מכירות לקולקציה','drive_urls':[]}
        self.assertEqual(self.request('/api/work/request',body,origin=False)[0],403)
        self.assertEqual(self.request('/api/work/request',{**body,'approved':True})[0],400)
        code,result=self.request('/api/work/request',body)
        self.assertEqual(code,200);self.assertTrue(result['saved']);self.assertFalse(result['live_action_allowed'])
        status=self.request('/api/work/status')[1]
        self.assertEqual(status['goal'],body['goal']);self.assertNotEqual(status['state'],'needs_consolidation')
        self.assertFalse(status['ai_connected']);self.assertFalse(status['background_execution'])
        self.assertTrue(status['activity']);self.assertFalse(status['execution_approval'])
        self.assertEqual(self.request('/api/launch')[1]['request']['brief'],body['goal'])
        self.request('/api/work/request',{'goal':'awareness for my new brand','drive_urls':[]})
        status=self.request('/api/work/status')[1]
        self.assertEqual(status['state'],'unsupported_goal')

    def test_unified_request_is_saved_even_when_downstream_projection_fails(self):
        body={'goal':'Increase sales now','drive_urls':['https://drive.google.com/drive/folders/abcdefghijkl']}
        with patch('server.LaunchWorkflow.create',side_effect=ValueError('downstream failure')):
            code,result=self.request('/api/work/request',body)
        self.assertEqual(code,200);self.assertTrue(result['saved']);self.assertFalse(result['projection_saved'])
        self.assertEqual(self.request('/api/work/status')[1]['request']['drive_urls'],body['drive_urls'])
        awareness={'goal':'brand awareness','drive_urls':body['drive_urls']}
        self.request('/api/work/request',awareness)
        status=self.request('/api/work/status')[1]
        self.assertEqual(status['request']['drive_urls'],body['drive_urls']);self.assertEqual(status['materials'],{})

    def test_unified_request_starts_existing_coordinator_after_projection(self):
        # Fake dispatcher only: no Drive/model/Meta traffic in this HTTP test.
        body={'goal':'Increase sales now','drive_urls':['https://drive.google.com/drive/folders/abcdefghijkl']}
        with patch('server.CreativePreparation.start',return_value=True) as start:
            code,result=self.request('/api/work/request',body)
        self.assertEqual(code,200);self.assertTrue(result['preparation_started_or_queued'])
        self.assertFalse(result['live_action_allowed']);start.assert_called_once()
        self.assertEqual(start.call_args.args[0],result['request'])
        self.assertEqual(result['request']['preparation_state'],'projected')

    def test_reconcile_route_requires_owner_origin_and_exact_saved_identity(self):
        path='/api/campaign-operations/reconcile';body={'operation_id':'saved-op','digest':'a'*64}
        self.assertEqual(self.request(path,body,token=False)[0],403)
        self.assertEqual(self.request(path,body,origin=False)[0],403)
        self.assertEqual(self.request(path,{'operation_id':'saved-op'})[0],400)
        self.assertEqual(self.request(path,{**body,'approved':True})[0],400)
        with patch('server.CampaignOperations.reconcile',return_value={'state':'reconciled'}) as reconcile:
            code,result=self.request(path,body)
        self.assertEqual(code,200);self.assertEqual(result['state'],'reconciled')
        reconcile.assert_called_once_with('saved-op','a'*64)

    def test_new_urls_never_inherit_old_running_collection_after_projection_failure(self):
        initial={'goal':'Increase sales now','drive_urls':['https://drive.google.com/drive/folders/abcdefghijkl']}
        self.request('/api/work/request',initial)
        old=self.request('/api/launch')[1]['request']
        newer={**initial,'drive_urls':['https://drive.google.com/drive/folders/newerabcdefgh']}
        with patch('server.LaunchWorkflow.create',side_effect=ValueError('failure')):
            result=self.request('/api/work/request',newer)[1]
        old_job={'configured':True,'job':{'state':'running','request_id':old['id'],'request_revision':old['revision']}}
        with patch('server.LaunchMaterials.status',return_value=old_job):status=self.request('/api/work/status')[1]
        self.assertTrue(result['saved']);self.assertEqual(status['state'],'preparation_failed')
        self.assertEqual(status['collection_job'],{});self.assertEqual(status['materials'],{})
        self.assertFalse(any(row['id']=='source_links' for row in status['completed']))

    def test_launch_proposal_and_media_are_private_and_read_only(self):
        self.assertEqual(self.request('/api/launch/proposal',token=False)[0],403)
        self.assertFalse(self.request('/api/launch/proposal')[1]['available'])
        self.assertEqual(self.request('/api/launch/proposal/media/abcdefghijkl')[0],403)
        self.assertEqual(self.request('/api/launch/proposal',{'approved':True})[0],404)

if __name__ == '__main__':
    unittest.main()
