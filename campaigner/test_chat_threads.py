import json,sqlite3,tempfile,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from claude_chat import ClaudeChat,KINDS,thread_title
import test_server


class ThreadTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
  self.prompts=[];self.chat=ClaudeChat(self.tmp.name,runner=lambda p:self.prompts.append(p) or 'answer')
  self.context={'account_id':'account','checked_at':'now','selected_ad':None}
 def seed(self,thread,question):
  identity=str(uuid.uuid4())
  with self.chat.db() as db:db.execute('INSERT INTO turns(id,question,context,state,answer,created_at,thread_id) VALUES(?,?,?,?,?,?,?)',(identity,question,json.dumps(self.context),'answered','answer','now',thread['id']))
  return identity
 def test_migration_keeps_all_turns_and_is_idempotent(self):
  path=Path(self.tmp.name)/'old';path.mkdir()
  with sqlite3.connect(path/'claude-chat.sqlite3') as db:
   db.execute('CREATE TABLE turns(id TEXT PRIMARY KEY,question TEXT NOT NULL,context TEXT NOT NULL,state TEXT NOT NULL,answer TEXT,error TEXT,created_at TEXT NOT NULL)')
   for i in range(50):db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?)',(str(i),'question','{}','answered','answer',None,'then'))
  db.close()
  chat=ClaudeChat(path,runner=lambda p:'answer');thread=chat.list_threads()[0]
  self.assertEqual(thread['title'],'שיחות קודמות')
  self.assertEqual(len(chat.thread_status(thread['id'])['turns']),50)
  self.assertEqual(len(ClaudeChat(path).list_threads()),1)
 def test_context_isolated(self):
  a=self.chat.create_thread();b=self.chat.create_thread()
  self.seed(a,'private to a');identity=self.seed(b,'question b')
  self.chat._complete(identity,'question b',self.context)
  self.assertEqual(json.loads(self.prompts[-1].split('\n',1)[1])['previous_turns'],[])
 def test_search_archive_restore_and_latest(self):
  a=self.chat.create_thread(question='planning');self.seed(a,'needle')
  b=self.chat.create_thread(question='different')
  self.assertEqual(self.chat.resolve_thread()['id'],b['id'])
  self.assertEqual(self.chat.list_threads('needle')[0]['id'],a['id'])
  self.chat.update_thread(a['id'],title='renamed',archived=True)
  self.assertEqual([r['id'] for r in self.chat.list_threads()],[b['id']])
  self.assertEqual(self.chat.list_threads('renamed',True)[0]['id'],a['id'])
  self.chat.update_thread(a['id'],archived=False)
  self.assertEqual(len(self.chat.list_threads()),2)
 def test_title_and_kind(self):
  self.assertEqual(self.chat.create_thread(question='תכנון קמפיין חדש')['kind'],KINDS[1])
  self.assertEqual(self.chat.create_thread(question='מה עבד לאורך זמן')['kind'],KINDS[2])
  self.assertLessEqual(len(thread_title('one two three '*10)),40)
 def test_submit_persists_thread_answer_time_and_rejects_archived(self):
  thread=self.chat.create_thread();identity=str(uuid.uuid4())
  self.chat.auth_checked=time.monotonic();self.chat.auth_ok=True
  with patch('claude_chat.threading.Thread'):
   self.chat.submit('first question',identity,self.context,thread['id'])
  self.chat._complete(identity,'first question',self.context)
  status=self.chat.thread_status(thread['id'])
  self.assertEqual(status['turns'][0]['state'],'answered')
  self.assertEqual(self.chat.list_threads()[0]['title'],'first question')
  self.assertIsNotNone(self.chat.list_threads()[0]['last_answer_at'])
  self.chat.update_thread(thread['id'],archived=True)
  with self.assertRaises(ValueError):self.chat.submit('another question',str(uuid.uuid4()),self.context,thread['id'])


class ThreadHttpTests(unittest.TestCase):
 setUp=test_server.ServerTests.setUp
 tearDown=test_server.ServerTests.tearDown
 request=test_server.ServerTests.request
 def test_routes_require_session_origin_and_phone_allowlist(self):
  from phone_gateway import POST_ROUTES
  for route in ('threads','thread','create','update'):
   path='/api/claude-chat/'+route
   self.assertIn(path,POST_ROUTES)
   self.assertEqual(self.request(path,{},token=False)[0],403)
   self.assertEqual(self.request(path,{},origin=False)[0],403)
 def test_ad_bound_context_uses_saved_id_and_assessment(self):
  with patch('server.build_workspace',return_value={'account_id':'account','checked_at':'now','ads':[{'id':'ad-a','name':'Ad A'}]}):
   code,thread=self.request('/api/claude-chat/create',{'kind':'מודעה מסוימת','ad_id':'ad-a','question':'explain ad'})
   self.assertEqual(code,200)
   with patch('server.chat_rule_context',return_value={'status':'ready','ad_id':'ad-a'}) as assessment,patch('server.ClaudeChat.submit',return_value={'state':'running'}) as submit:
    code,_=self.request('/api/claude-chat',{'question':'explain this','request_id':str(uuid.uuid4()),'ad_id':'wrong','thread_id':thread['id']})
   self.assertEqual(code,200)
   self.assertEqual(assessment.call_args.args[1],'ad-a')
   self.assertEqual(submit.call_args.args[2]['selected_ad']['id'],'ad-a')
   self.assertEqual(submit.call_args.args[2]['rule_assessment']['ad_id'],'ad-a')
   self.assertEqual(submit.call_args.args[3],thread['id'])
   self.assertEqual(self.request('/api/claude-chat/thread',{'thread_id':thread['id']})[0],200)
