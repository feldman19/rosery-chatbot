import json,tempfile,unittest
from pathlib import Path
from claude_chat import ClaudeChat,conversation_scope


class ChatScopeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
  self.prompts=[];self.chat=ClaudeChat(self.tmp.name,runner=lambda prompt:self.prompts.append(prompt) or 'local answer')
  self.context={'account_id':'123','checked_at':'2026-10-21T09:00:00Z','selected_ad':{'id':'ad_a'},'report_window':{'since':'2026-10-14','until':'2026-10-20'}}
 def seed(self,identity,context,question='previous',state='answered'):
  with self.chat.db() as db:db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?)',(identity,question,json.dumps(context),state,'answer-'+identity,None,'2026-10-21T09:00:'+identity.zfill(2)+'Z'))
 def complete(self,context):
  self.seed('99',context,state='running');self.chat._complete('99','current question',context)
  return json.loads(self.prompts[-1].split('\n',1)[1])
 def test_history_filtered_by_account_ad_and_absolute_window(self):
  self.seed('01',self.context,'matching')
  self.seed('02',{**self.context,'selected_ad':{'id':'ad_b'}},'wrong-ad')
  self.seed('03',{**self.context,'account_id':'other'},'wrong-account')
  self.seed('04',{**self.context,'report_window':{'since':'2026-10-07','until':'2026-10-13'}},'wrong-window')
  self.seed('05',{**self.context,'selected_ad':None},'general-account')
  history=self.complete({**self.context,'checked_at':'2026-10-21T10:00:00Z'})['previous_turns']
  self.assertEqual([p['question'] for p in history],['matching'])
  self.assertEqual(history[0]['scope']['entity'],{'level':'ad','id':'ad_a'})
  self.assertEqual(history[0]['checked_at'],self.context['checked_at'])
 def test_snapshot_only_history_never_bleeds_to_new_snapshot(self):
  context={k:v for k,v in self.context.items() if k!='report_window'}
  self.seed('01',context)
  self.assertEqual(self.complete({**context,'checked_at':'later'})['previous_turns'],[])
 def test_unknown_scope_has_no_history(self):
  self.seed('01',{'selected_ad':{'id':'ad_a'}})
  self.assertEqual(self.complete({'selected_ad':{'id':'ad_a'}})['previous_turns'],[])
 def test_owner_language_instruction_and_account_metrics_are_sent(self):
  context={**self.context,'account_metrics':{'1':{'since':'2026-10-22','until':'2026-10-22','metrics':{'spend':90}}}}
  sent=self.complete(context)
  self.assertEqual(sent['selected_context']['account_metrics']['1']['metrics']['spend'],90)
  self.assertIn('אל תציין שמות שדות פנימיים',self.prompts[-1].split('\n',1)[0])
 def test_only_latest_four_matching_turns_are_sent(self):
  for i in range(1,7):self.seed(str(i).zfill(2),self.context,question=str(i))
  self.assertEqual([p['question'] for p in self.complete(self.context)['previous_turns']],['3','4','5','6'])
 def test_relative_window_is_bound_to_snapshot(self):
  for value in ('7 days',{'days':7},{'since':'last_week','until':'today'}):
   a={**self.context,'report_window':value}
   self.assertNotEqual(conversation_scope(a),conversation_scope({**a,'checked_at':'later'}))

if __name__=='__main__':unittest.main()
