import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from server import make_server
from workflows import TaskStore, TaskError


class EmployeeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='campaigner-test-')
        self.servers = []
        self.base, self.token = self.start('employee')
        self.task = {'name':'בדיקת חבילה', 'materials_url':'https://drive.google.com/drive/folders/demo',
                     'destination':'https://roseryjewelry.com/', 'copy':'טקסט לבדיקה', 'formats':'feed_story'}

    def start(self, role):
        server = make_server(0, self.tmp.name, workspace=role)
        self.servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        page = urllib.request.urlopen(base).read().decode()
        return base, re.search("const token='([^']+)'", page).group(1)

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def request(self, path, body=None, base=None, token=None):
        base, token = base or self.base, token or self.token
        headers = {'X-Local-Session':token}
        if body is not None:
            headers.update({'Origin':base,'Content-Type':'application/json'})
        request = urllib.request.Request(base+'/api/'+path, headers=headers,
                                          data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, json.load(response)

    def test_employee_cannot_read_finances_or_approve_even_with_role_claim(self):
        callback=urllib.request.urlopen(self.base+'/?hmac=not-trusted&shop=21cc82-3.myshopify.com').read().decode()
        self.assertIn('const token=',callback)
        self.assertEqual(self.request('state',token='not-trusted')[0],403)
        for path in ('state','evidence','registration','reviews'):
            self.assertEqual(self.request(path)[0],403)
        for path in ('propose','approve','execute','reconcile','help/resolve','tasks/review','registration/answer','connections/check','connections/shopify/setup','connections/shopify/report'):
            self.assertEqual(self.request(path,{'role':'manager','id':'forged'})[0],403)

    def test_pilot_employee_projection_and_manager_only_details(self):
        from datetime import datetime, timezone
        fixture={'schema_version':1,'tenant':'rosery','business':'ROSERY',
                 'identity':{'meta_account':'393059510164294','shop_domain':'21cc82-3.myshopify.com'},
                 'captured_at':datetime.now(timezone.utc).isoformat(),
                 'observations':[],'facts':[{'value':'internal-cost'}]}
        (Path(self.tmp.name)/'rosery-pilot.json').write_text(json.dumps(fixture),encoding='utf-8')
        code,worker=self.request('pilot')
        self.assertEqual(code,200)
        self.assertNotIn('observations',worker)
        self.assertNotIn('facts',worker)
        self.assertFalse(worker['live_action_allowed'])
        manager, token=self.start('manager')
        code,owner=self.request('pilot',base=manager,token=token)
        self.assertEqual(code,200)
        self.assertIn('observations',owner)
        self.assertEqual(self.request('pilot',{'live_action_allowed':True})[0],403)

    def test_prepare_task_persists_without_publish(self):
        code, task = self.request('tasks', self.task)
        self.assertEqual(code,200)
        self.assertEqual(task['status'],'awaiting_manager')
        self.assertFalse(task['published'])
        self.assertFalse(task['destination_verified'])
        second, token = self.start('employee')
        state = self.request('workspace',base=second,token=token)[1]
        self.assertEqual(state['tasks'][0]['id'],task['id'])

    def test_invalid_or_extra_payload_denied(self):
        for change in ({'destination':'https://evil.example/'},{'materials_url':'javascript:alert(1)'},
                       {'materials_url':'https://secret:password@example.com/'},{'formats':'anything'},
                       {'budget':9999},{'name':''}):
            self.assertEqual(self.request('tasks',{**self.task,**change})[0],400)

    def test_help_is_knowledge_not_live_ai(self):
        code, result = self.request('help',{'question':'איך מתחילים'})
        self.assertEqual(code,200)
        self.assertFalse(result['ai_connected'])
        self.assertTrue(result['sources'])
        unknown = self.request('help',{'question':'מה יקרה בחודש הבא'})[1]
        self.assertTrue(unknown['needs_manager'])
        self.assertEqual(unknown['sources'],[])

    def test_explicit_escalation_and_manager_resolution(self):
        self.request('help',{'question':'שאלה לא מוכרת'})
        self.assertEqual(self.request('workspace')[1]['questions'],[])
        code, issue = self.request('help/escalate',{'question':'איך משלימים את המשימה הזאת'})
        self.assertEqual(code,200)
        self.assertFalse(issue['notification_sent'])
        manager, token = self.start('manager')
        code, result = self.request('help/resolve',{'id':issue['id'],'resolution':'הכינו קישור לחומרים לבדיקה'},base=manager,token=token)
        self.assertEqual(code,200)
        self.assertEqual(result['status'],'resolved')
        self.assertEqual(self.request('workspace')[1]['questions'][0]['resolution'],'הכינו קישור לחומרים לבדיקה')

    def test_manager_review_visible_to_worker_not_campaign_approval(self):
        task = self.request('tasks',self.task)[1]
        manager, token = self.start('manager')
        code, result = self.request('tasks/review',{'id':task['id'],'status':'needs_changes','note':'צריך גם סטורי','expected_revision':task['revision']},base=manager,token=token)
        self.assertEqual(code,200)
        self.assertFalse(result['published'])
        self.assertEqual(self.request('workspace')[1]['tasks'][0]['manager_note'],'צריך גם סטורי')

    def test_sensitive_question_rejected_and_not_stored(self):
        self.assertEqual(self.request('help/escalate',{'question':'password=secret123'})[0],400)
        self.assertEqual(self.request('workspace')[1]['questions'],[])

    def test_resubmit_only_returned_task_and_version_increment(self):
        task = self.request('tasks',self.task)[1]
        body = {'id':task['id'],'payload':{**self.task,'name':'גרסה מתוקנת'},'expected_revision':1}
        self.assertEqual(self.request('tasks/resubmit',body)[0],400)
        manager, token = self.start('manager')
        review = self.request('tasks/review',{'id':task['id'],'status':'needs_changes','note':'צריך גרסה נוספת','expected_revision':1},base=manager,token=token)[1]
        body['expected_revision'] = review['revision']
        code, revised = self.request('tasks/resubmit',body)
        self.assertEqual(code,200)
        self.assertEqual(revised['revision'],3)
        self.assertEqual(revised['id'],task['id'])
        self.assertEqual(revised['status'],'awaiting_manager')
        self.assertFalse(revised['published'])
        self.assertEqual(len(self.request('workspace')[1]['tasks']),1)
        self.assertNotIn('manager_note',revised)
        self.assertIn('previous_review',revised)
        stale = {'id':task['id'],'status':'ready_for_manager','note':'הכל מוכן','expected_revision':1}
        self.assertEqual(self.request('tasks/review',stale,base=manager,token=token)[0],400)
        self.assertEqual(self.request('tasks/resubmit',body)[0],400)


if __name__ == '__main__':
    unittest.main()
