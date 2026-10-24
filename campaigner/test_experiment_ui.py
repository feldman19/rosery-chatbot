import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import date
from experiment_ui import ExperimentStore, card
from experiments import Arm, Plan
import test_server


def plan(state='running'):
    return Plan('האם ב עדיפה?', 'סוג', Arm('א', 'a', tuple('a'+str(i) for i in range(5))), Arm('ב', 'b', tuple('b'+str(i) for i in range(5))), 10000, '2026-10-01', state=state)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ExperimentStore(self.tmp.name)

    def test_single_running_across_instances_and_stop_persists(self):
        identity=self.store.save(plan())
        other=ExperimentStore(self.tmp.name)
        with self.assertRaises(sqlite3.IntegrityError):other.save(plan())
        other.stop(identity)
        self.assertEqual(self.store.list()[0]['plan']['state'],'interrupted')
        self.store.save(plan())
        with self.assertRaises(ValueError):self.store.stop(identity)

    def test_ideas_dismissal_and_history_journal_ranking(self):
        self.store.idea('בדיקת תמונות')
        history={'verified':True,'windows':{'all':{'patterns':{'dimensions':[{'dimension':'סוג','values':[{'value':'א','enough_data':False},{'value':'ב','enough_data':False}]}]}}}}
        Path(self.tmp.name,'account-history.json').write_text(json.dumps(history),encoding='utf-8')
        candidates=self.store.candidates([{'statement':'השערה','status':'testing'}])
        self.assertEqual(len(candidates),2)
        self.assertFalse(any('השערה'==c['question'] for c in candidates))
        identity=candidates[0]['id'];self.store.dismiss(identity,[{'statement':'השערה','status':'testing'}])
        self.assertNotIn(identity,[c['id'] for c in self.store.candidates([{'statement':'השערה','status':'testing'}])])
        with self.assertRaises(ValueError):self.store.idea(' ')

    def test_raw_history_uses_only_saved_summary(self):
        Path(self.tmp.name,'account-history.json').write_text(json.dumps({'verified':True,'windows':{'all':{'rows':[]}}}),encoding='utf-8')
        with patch('experiment_ui.AccountHistory.summary',return_value={'windows':{}}) as summary, patch('account_history.AccountHistory.refresh_if_stale',side_effect=AssertionError('no provider refresh')):
            self.store.candidates([])
        summary.assert_called_once_with()

    def test_render_helpers_preserve_unknown_and_result(self):
        identity=self.store.save(plan())
        row=self.store.list()[0]
        self.assertIsNone(row['totals']['A']['spend_minor'])
        self.assertIn('אינו ידוע',row['probability_words'])
        self.assertFalse(row['live_action_allowed'])
        self.store.save(plan('inconclusive'),result={'lift_interval_90':[-.2,.4]},identity=identity)
        self.assertEqual(self.store.list()[0]['verdict'],'לא הוכרע')

    def test_owner_input_is_rendered_as_text_and_start_unavailable(self):
        from test_card_component import HELPERS, DOM
        script = r"""
const text='<img src=x onerror=alert(1)>';
const c=experimentCard({question:text},true);
assert.ok(surface(c).includes(text));assert.equal(c.querySelectorAll('img').length,0);
assert.ok(surface(c).includes('הפעלה מכאן תגיע בגרסה הבאה'));
assert.ok(!c.querySelectorAll('button').some(b=>b.disabled));
const running=experimentCard({id:'r',plan:{state:'running',question:'שאלה',a:{label:'א'},b:{label:'ב'}},totals:{A:{spend_minor:null},B:{spend_minor:0}}});
assert.ok(allText(running).includes('לא ידוע'));assert.ok(allText(running).includes('₪0'));
running.querySelector('.primary').onclick();assert.equal(clicked.path,'/api/experiments/stop');
"""
        done=subprocess.run(['node'],input=HELPERS+'\n'+DOM+script,capture_output=True,encoding='utf-8')
        self.assertEqual(done.returncode,0,done.stderr)

    def test_seed_hypotheses_never_become_ideas(self):
        from hypotheses import SEED
        self.store.idea('מה מוכר יותר בחנות?')
        proposals=self.store.candidates(SEED)
        self.assertEqual([r['question'] for r in proposals], ['מה מוכר יותר בחנות?'])
        self.assertFalse(any(r.get('id') in {h['id'] for h in SEED} for r in proposals))


class RouteTests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request

    def test_gates_and_phone_routes(self):
        from phone_gateway import GET_ROUTES,POST_ROUTES
        for route in ('idea','dismiss','stop'):
            path='/api/experiments/'+route
            self.assertIn(path,POST_ROUTES)
            self.assertEqual(self.request(path,{},token=False)[0],403)
            self.assertEqual(self.request(path,{},origin=False)[0],403)
        for path in ('/api/experiments','/api/experiments/candidates'):
            self.assertIn(path,GET_ROUTES)
            self.assertEqual(self.request(path,token=False)[0],403)
            self.assertEqual(self.request(path)[0],200)

    def test_idea_dismiss_stop_and_no_prepare(self):
        self.assertEqual(self.request('/api/experiments/idea',{'question':'בדיקת רעיון בעברית'})[0],200)
        code,data=self.request('/api/experiments/candidates');self.assertEqual(code,200)
        self.assertEqual(self.request('/api/experiments/dismiss',{'id':data['candidates'][0]['id']})[0],200)
        store=ExperimentStore(self.temp.name);identity=store.save(plan())
        self.assertEqual(self.request('/api/experiments/stop',{'id':identity})[0],200)
        self.assertEqual(store.list()[0]['plan']['state'],'interrupted')
        self.assertEqual(self.request('/api/experiments/prepare',{})[0],400)
        self.assertEqual(self.request('/api/experiments/idea',{'question':''})[0],400)
