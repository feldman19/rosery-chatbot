"""Offline rendering checks for owner cards, including dynamic provider text."""
from pathlib import Path
import re
import subprocess
import unittest


class SurfaceTests(unittest.TestCase):
    def test_cards_hide_evidence_and_translate_provider_jargon(self):
        page = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        names = ('ownerText', 'compactCard', 'actionCard', 'driftCard',
                 'experimentCard', 'experimentProposal', 'renderLearning', 'renderProfit', 'renderDrift')
        functions = []
        for name in names:
            start = page.index('function '+name+'(')
            end = page.index('\n', start)
            if not page[start:end].endswith('}'):
                end = page.index('\n}', end)+2
            functions.append(page[start:end])
        harness = r"""
const assert=require('node:assert/strict');
class N {
 get childElementCount(){return this.children.length}
 get classList(){return {remove:cls=>{this.className=this.className.split(' ').filter(c=>c!==cls).join(' ')}}}
 constructor(tag,cls='',text=''){this.tag=tag;this.className=cls;this.textContent=ownerText(text);this.children=[]}
 append(...nodes){for(let n of nodes){if(typeof n==='string')n=new N('#text','',n);if(n.parent)n.parent.children=n.parent.children.filter(x=>x!==n);n.parent=this;this.children.push(n)}}
 prepend(n){this.append(n);this.children.pop();this.children.unshift(n)}
 insertBefore(n,b){if(b&&!this.children.includes(b))throw Error('not a direct child');this.append(n);this.children.pop();const i=this.children.indexOf(b);this.children.splice(i<0?this.children.length:i,0,n)}
 replaceChildren(){this.children=[]}
 remove(){this.parent.children=this.parent.children.filter(c=>c!==this)}
 matches(q){return q.split(',').some(s=>s.startsWith('.')?this.className.split(' ').includes(s.slice(1)):this.tag===s)}
 querySelectorAll(q){return this.children.flatMap(n=>[...(n.matches(q)?[n]:[]),...n.querySelectorAll(q)])}
 querySelector(q){return this.querySelectorAll(q)[0]||null}
 setAttribute(){}
}
const el=(t,c='',v='')=>new N(t,c,v),nodes={},$=id=>nodes[id]||(nodes[id]=el('div'));
const x=v=>String(v),ils=v=>'₪'+v,experimentMoney=v=>v==null?'לא ידוע':'₪'+v/100;
let contradiction=false;const decisionFor=()=>null,waiting=()=>{},appendTodayContext=()=>{},todayContradicts=()=>contradiction;
const todayContext=()=>[{contradicts:true,ad:{roas:5.91}}],toast=()=>{};
const actionTitle=()=> 'ROAS CTR Advantage+ אד-סט קריאייטיב ריטרגטינג פרוספקטינג';
const actionWord=()=> 'בדיקה',dismiss=()=>{},openAction=()=>{},markDone=()=>{},api=()=>{},load=()=>{};let helpSeq=0;
const report={},H_STATE={};let learning={hypotheses:[{statement:actionTitle(),test:actionTitle(),status_label:'בבדיקה',evidence:{},sources:[]}],shadow:{}};
let profitWindow='7',profit={status:'ready',windows:{'7':{net_profit:25}}};const profitSettings=()=>el('details'),pct=v=>String(v);
const surface=n=>n.tag==='details'?'':n.textContent+' '+n.children.map(surface).join(' ');
const banned=/ROAS|CTR|Advantage\+|אד-סט|קריאייטיב|ריטרגטינג|פרוספקטינג/i;
for(const term of ['ROAS','CTR','Advantage+','אד-סט','קריאייטיב','ריטרגטינג','פרוספקטינג'])assert.ok(!banned.test(ownerText(term)),term);
const check=(card,kind)=>{assert.ok(!banned.test(surface(card)),kind+': '+surface(card));assert.ok(card.querySelector('details'),kind+' retains details')};
for(const action of ['PAUSE','SCALE','ALERT','HOLD'])check(actionCard({action,headline:actionTitle(),body:actionTitle(),warnings:[actionTitle()]}),action);
check(driftCard({ad_id:'a',name:actionTitle(),features:[{label:actionTitle(),kind_label:'שינוי'}]}),'drift');
check(experimentCard({question:actionTitle(),why:actionTitle()},true),'experiment');
renderLearning();check($('learn').children[0],'learning');
renderProfit();check($('profit').children[0],'profit');
report.creative_drift={status:'ready',items:['a','b'].map(ad_id=>({ad_id,name:'מודעה',features:[{field:'animation',label:'הוספת תנועה',kind_label:'תמונה'}]}))};
renderDrift();assert.equal($('drift').children.length,1);
const grouped=$('drift').children[0];assert.ok(surface(grouped).includes('2 מודעות עם אותו שינוי'));
assert.equal(grouped.querySelectorAll('article').length,2);
const review=grouped.querySelector('button');review.onclick();assert.equal(grouped.querySelector('details').open,true);
contradiction=true;
const conflicting=actionCard({action:'PAUSE',proposal_id:'pause:a',kind:'approve',ready_for_approval:true,headline:'מודעה',body:'מידע'});
assert.ok(surface(conflicting).includes('היום היא דווקא מוכרת טוב'));
assert.equal(conflicting.querySelector('.primary').textContent,'לחכות');
assert.equal(conflicting.querySelectorAll('.primary').length,1);
assert.ok(conflicting.querySelectorAll('button').some(n=>n.textContent==='בדיקה'&&n.onclick));
const card=el('article'),summary=el('p','owner-summary','משפט קצר'),evidence=el('p','note','מידע נוסף'),action=el('div','actions');card.append(summary,evidence,action);compactCard(card);
assert.ok(surface(card).includes('משפט קצר'));assert.ok(!surface(card).includes('מידע נוסף'));assert.equal(card.querySelector('details').querySelector('p'),evidence);
"""
        result = subprocess.run(['node', '-e', '\n'.join(functions)+'\n'+harness],
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_internal_hypotheses_are_not_experiment_candidates(self):
        source = Path(__file__).with_name('server.py').read_text(encoding='utf-8')
        self.assertIn("{'candidates':experiment_store.candidates([])}", source)

    def test_no_corrupted_hebrew_and_valid_script(self):
        page = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        self.assertNotIn('????', page)
        script = re.search(r'<script>([\s\S]*?)</script>', page)[1]
        result = subprocess.run(['node', '--check'], input=script,
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
