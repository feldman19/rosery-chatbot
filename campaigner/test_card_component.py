"""Offline behavioral fixtures for the shared card component; no integrations."""
from pathlib import Path
import json
import re
import subprocess
import unittest

PAGE = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')

def functions(*names):
    result=[]
    for name in names:
        start=PAGE.index('function '+name+'(')
        end=PAGE.index('\n', start)
        if not PAGE[start:end].endswith('}'):
            end=PAGE.index('\n}',end)+2
        result.append(PAGE[start:end])
    return '\n'.join(result)

HELPERS=functions('salesLine','salesFor','salesExplanation','appendProfileReason','counted','ownerText','ratio','stateRows','card','adPicture','featureInfo','featureSummary','driftAfter','actionCard','driftCard','renderDrift','experimentProposal','experimentCard','hypothesisProgress','renderLearning')
DOM=r"""
const assert=require('node:assert/strict');
class N {
 constructor(tag,cls='',text=''){this.tag=tag;this.className=cls;this.textContent=ownerText(text);this.children=[]}
 get childElementCount(){return this.children.length}
 get classList(){return {remove:c=>this.className=this.className.split(' ').filter(x=>x!==c).join(' ')}}
 append(...nodes){for(let n of nodes){if(typeof n==='string')n=new N('#text','',n);n.remove();n.parent=this;this.children.push(n)}}
 prepend(n){n.remove();n.parent=this;this.children.unshift(n)}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null}
 insertBefore(n,b){n.remove();n.parent=this;const i=b?this.children.indexOf(b):this.children.length;assert(i>=0);this.children.splice(i,0,n)}
 replaceWith(n){this.parent.insertBefore(n,this);this.remove()}
 replaceChildren(...nodes){this.children=[];this.append(...nodes)}
 matches(q){return q.split(',').some(s=>s.startsWith('.')?this.className.split(' ').includes(s.slice(1)):this.tag===s)}
 querySelectorAll(q){return this.children.flatMap(n=>[...(n.matches(q)?[n]:[]),...n.querySelectorAll(q)])}
 querySelector(q){return this.querySelectorAll(q)[0]||null}
 setAttribute(k,v){this[k]=v}
}
const el=(t,c='',v='')=>new N(t,c,v),nodes={},$=id=>nodes[id]||(nodes[id]=el('div'));
const windowCard=card,ils=v=>Number.isFinite(v)?'₪'+v:'לא ידוע',x=v=>String(v),experimentMoney=v=>v==null?'לא ידוע':'₪'+v/100;
let report={},learning={},fixtureDecision=null,contradiction=false,helpSeq=0,clicked=null;
const decisionFor=()=>fixtureDecision,todayContradicts=()=>contradiction,todayContext=()=>[{contradicts:true,ad:{roas:5.27}}];
const openAction=id=>clicked=id,go=t=>clicked=t,dismiss=()=>{},toast=()=>{},api=async()=>{},load=async()=>{},experimentAction=(path,body)=>clicked={path,body};
const localStorage={setItem:(k,v)=>clicked={k,v}},loadLearning=()=>{};
const surface=n=>(n.tag==='details'?'':n.textContent+' '+n.children.map(surface).join(' ')).replace(/[\u2066-\u2069]/g,'');
const allText=n=>n.textContent+' '+n.children.map(allText).join(' ');
"""

class CardTests(unittest.TestCase):
    def run_js(self,code):
        done=subprocess.run(['node'],input=HELPERS+'\n'+DOM+'\n'+code,encoding='utf-8',capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)

    def test_decision_surface_and_real_approval_target(self):
        self.run_js(r"""
fixtureDecision={entity_name:'קטלוג נמכרים ביותר',numbers:{spend:386,roas:2.17,purchases:3},thresholds:{fail_below:2.6},audience_fallback:true};
const item={action:'PAUSE',entity_id:'a',proposal_id:'pause:a',kind:'approve',ready_for_approval:true,body:'reason',waiting_days:1};
const c=actionCard(item);assert.equal(c.querySelectorAll('.primary').length,1);assert.equal(surface(c).split('₪2.17').length-1,1);
assert.equal(c.querySelectorAll('th').filter(n=>n.textContent==='קהל').length,1);assert.ok(surface(c).includes('מחכה מאתמול'));
c.querySelector('.primary').onclick();assert.equal(clicked,'pause:a');
contradiction=true;const other=actionCard(item);assert.equal(other.querySelector('.primary').textContent,'לחכות למחר');
assert.ok(surface(other).includes('₪5.27'));assert.equal(other.querySelectorAll('.primary').length,1);
other.querySelector('.primary').onclick();assert.equal(clicked.k,'rosery-wait:pause:a');assert.ok(clicked.v);
""")

    def test_learning_progress_unknown_eta_and_hidden_technical_evidence(self):
        self.run_js(r"""
const h={id:'ctr_weight_not_needed',created_at:'2026-10-22T00:00:00Z',status:'testing',statement:'CTR weights',test:'p-values',evidence:{for:3,against:0,needed:10,checked_at:'2026-10-24T00:00:00Z'}};
assert.equal(hypothesisProgress(h).eta,5);assert.equal(hypothesisProgress({}).judged,null);assert.equal(hypothesisProgress({}).eta,null);
learning={hypotheses:[h]};renderLearning();const c=$('learn').children[0],s=surface(c);
assert.ok(c.querySelector('progress'));assert.ok(s.includes('נבדקו 3 מתוך 10'));assert.ok(s.includes('מה זה אומר לך:'));assert.ok(s.includes('עוד מוקדם — אין תשובה'));assert.ok(s.includes('כ-5 ימים'));
assert.ok(!s.includes('weights'));assert.ok(!s.includes('p-values'));assert.ok(allText(c).includes('weights'));assert.equal(c.querySelectorAll('.primary').length,1);
""")

    def test_idea_steps_cost_and_honest_unavailable_start(self):
        self.run_js(r"""
report={windows:{'7':{metrics:{spend:900,purchases:10}}}};
const p={id:'idea',question:'מה מוכר יותר?',a_kind:'חולצות',b_kind:'שרשראות',asset_ids:{A:['1','2','3','4','5'],B:['6','7']},max_days:14};
const c=experimentCard(p,true),s=surface(c);assert.ok(s.includes('מה צריך ממך:'));assert.ok(s.includes('חסרים 3'));assert.ok(s.includes('✓'));assert.ok(s.includes('לאשר תקציב'));
assert.ok(s.includes('מה תקבל בסוף:'));assert.ok(s.includes('הפעלה מכאן תגיע בגרסה הבאה'));assert.ok(!c.querySelectorAll('button').some(b=>b.disabled));
c.querySelector('button').onclick();assert.equal(clicked.path,'/api/experiments/dismiss');assert.equal(clicked.body.id,'idea');
const q=experimentProposal(p);assert.equal(q.total,120000);assert.equal(q.daily,8572);
for(const metrics of [{spend:null,purchases:10},{spend:100,purchases:0},{spend:-1,purchases:1},{spend:'100',purchases:1}]){report.windows['7'].metrics=metrics;assert.equal(experimentProposal(p).total,null)}
""")

    def test_meta_thumbnails_groups_and_feature_limit(self):
        self.run_js(r"""
const f=['music','image_animation','hide_price','image_touchups','image_auto_crop'].map(feature=>({feature}));
const c=driftCard({ad_id:'a',name:'מודעה',features:f});assert.equal(c.querySelectorAll('.primary').length,1);assert.ok(surface(c.querySelector('.state-rows')).includes('ועוד 2'));
const no=adPicture({name:'קטלוג'});assert.equal(no.querySelectorAll('img').length,0);
const image=adPicture({name:'קטלוג',thumbnail_url:'https://example.test/no'});const parent=el('div');parent.append(image);image.onerror();assert.equal(parent.children.length,0);
report={creative_drift:{status:'ready',items:['א','ב','ג','ד'].map(ad_id=>({ad_id,name:'מודעה '+ad_id,features:f}))}};renderDrift();const group=$('drift').children[0];assert.equal(group.querySelector('.group-ads').children.length,4);assert.equal(group.querySelector('.group-ads').querySelectorAll('.image-placeholder').length,4);
assert.equal(group.children.filter(n=>n.className==='actions')[0].querySelectorAll('.primary').length,1);
""")

    def test_script_and_all_card_routes(self):
        script='\n'.join(re.findall(r'<script>(.*?)</script>',PAGE,re.S))
        done=subprocess.run(['node','--check'],input=script,encoding='utf-8',capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)
        self.assertIn('var(--tabs-height,96px) + 24px',PAGE)
        self.assertIn('white-space:nowrap;unicode-bidi:isolate',PAGE)
