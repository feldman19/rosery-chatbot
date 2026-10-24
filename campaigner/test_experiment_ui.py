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
        self.assertEqual(len(candidates),3)
        self.assertTrue(any('השערה'==c['question'] for c in candidates))
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

    def test_owner_input_is_rendered_as_text_and_prepare_disabled(self):
        page=Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        block=page.split('// BEGIN experiments-ui')[1].split('// END experiments-ui')[0]
        self.assertNotIn('innerHTML',block)
        self.assertIn('prepare.disabled=true',block)
        self.assertIn('experimentMoney=v=>v==null',block)


    def test_javascript_cards_with_unknown_zero_and_owner_markup(self):
        page=Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        block=page.split('// BEGIN experiments-ui')[1].split('// END experiments-ui')[0]
        compact=page.split('function compactCard(card){',1)[1].split('function actionCard(',1)[0]
        script="""
const assert=require('assert');
let report=null;
const ils=v=>'₪'+Math.round(v);
function el(tag,cls='',text){return {tag,cls,text,children:[],parent:null,
 get childElementCount(){return this.children.length},
 classList:{add(){}},
 append(...nodes){for(const n of nodes){if(n.parent)n.parent.children.splice(n.parent.children.indexOf(n),1);n.parent=this;this.children.push(n)}},
 matches(selector){return selector.split(',').some(s=>{
   if(s==='p:not(.btn-help)')return this.tag==='p'&&!this.cls.split(' ').includes('btn-help');
   const [t,c]=s.split('.');return (!t||t===this.tag)&&(!c||this.cls.split(' ').includes(c))})},
 querySelectorAll(selector){return selector===':scope > button'?this.children.filter(n=>n.tag==='button'):all(this).filter(n=>n!==this&&n.matches(selector))},
 querySelector(selector){return this.querySelectorAll(selector)[0]||null},
 insertBefore(n,ref){if(n.parent)n.parent.children.splice(n.parent.children.indexOf(n),1);n.parent=this;const i=ref?this.children.indexOf(ref):this.children.length;assert(i>=0);this.children.splice(i,0,n)}
}}
function all(n){return [n,...n.children.flatMap(all)]}
const $=()=>({});
"""+"function compactCard(card){"+compact+"//"+block+"""
assert.equal(experimentMoney(null),'לא ידוע');assert.equal(experimentMoney(0),'₪0');
const idea='<img src=x onerror=alert(1)>';
const proposed=experimentCard({question:idea,max_days:21,meta_note:'חיבור בהמשך'},true);
assert(all(proposed).some(c=>c.text===idea));
assert(all(proposed).some(c=>c.text==='רעיון לבדיקה'));
assert(!all(proposed).some(c=>(c.text||'').includes('ליום לכל זרוע')));
assert(!all(proposed).some(c=>(c.text||'').includes('אשר והכן')));
const assets={A:['a','b','c','d','e'],B:['f','g','h','i','j']};
const candidate={a_kind:'תמונה',b_kind:'וידאו',asset_ids:assets,max_days:21};
for(const metrics of [{spend:null,purchases:10},{spend:100,purchases:null},{spend:100,purchases:0},{spend:-1,purchases:10},{spend:'100',purchases:10}]){
  report={windows:{'7':{metrics}}};assert.equal(experimentProposal(candidate).cpa,null);
}
report={windows:{'7':{metrics:{spend:900,purchases:10}}}};
const ready=experimentProposal(candidate);assert.equal(ready.cpa,90);assert.equal(ready.daily,10000);assert.equal(ready.total,420000);
assert.equal(experimentProposal({...candidate,daily_budget_minor:15000,max_days:7}).total,210000);
assert(experimentProposal({...candidate,asset_ids:{A:assets.A,B:assets.A}}).missing.length>0);
assert(experimentProposal({...candidate,asset_ids:{A:['a','a','a','a','a'],B:assets.B}}).missing.length>0);
const prepared=experimentCard(candidate,true);
assert(prepared.children.some(n=>n.tag==='details'&&!n.open));
assert(prepared.children.some(n=>n.tag==='button'&&n.disabled));
assert(prepared.children.filter(n=>n.tag==='p').length<=2);
assert(all(prepared).some(c=>c.text==='אשר והכן במצב מושהה'&&c.disabled));
assert(all(prepared).some(c=>(c.text||'').includes('מחיר רכישה בחשבון (7 ימים): ₪90')));
assert(all(prepared).some(c=>(c.text||'').includes('הצעת תקציב: ₪100')));
report.windows['7'].metrics.spend=0;assert.equal(experimentProposal(candidate).cpa,0);
const running=experimentCard({plan:{state:'running',question:'שאלה',a:{label:'א'},b:{label:'ב'},max_days:21,daily_budget_minor:100},totals:{A:{spend_minor:null,purchases:null},B:{spend_minor:0,purchases:0}},probability_words:'לא ידוע',days_left:3});
assert(all(running).some(c=>c.text==='עצור ניסוי'));
const result=experimentCard({plan:{state:'inconclusive',a:{label:'א'},b:{label:'ב'}},totals:{A:{},B:{}},verdict:'לא הוכרע',result:{lift_interval_90:[-.2,.4]},next_step:'לבדוק'});
assert(all(result).some(c=>(c.text||'').includes('90%')));
"""
        done=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(done.returncode,0,done.stderr)


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
