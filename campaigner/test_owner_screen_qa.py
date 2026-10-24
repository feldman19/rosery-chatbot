"""Offline owner-screen regressions; no provider or model calls."""
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from server import owner_chat_metrics


class OwnerChatMetricsTests(unittest.TestCase):
    def test_exact_windows_and_missing_values(self):
        result = owner_chat_metrics({'windows': {
            '1': {'since': '2026-10-22', 'until': '2026-10-22',
                  'metrics': {'spend': 90, 'purchases': 0, 'attributed_revenue': 143.1,
                              'store_revenue': 500, 'store_orders': 2}},
            '3': {'since': '2026-10-20', 'until': '2026-10-22', 'metrics': {'spend': 270}}}})
        self.assertEqual(result['1']['metrics']['store_orders'], 2)
        self.assertEqual(result['1']['metrics']['purchases'], 0)
        self.assertEqual(result['3']['since'], '2026-10-20')
        self.assertIsNone(result['3']['metrics']['purchases'])
        self.assertIsNone(owner_chat_metrics({})['1']['metrics']['spend'])


@unittest.skipUnless(shutil.which('node'), 'Node required for UI behavior tests')
class OwnerScreenTests(unittest.TestCase):
    def test_script_syntax(self):
        html = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        script = re.search(r'<script>([\s\S]*?)</script>', html)[1]
        result = subprocess.run(['node', '--check'], input=script, encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_safe_markdown_pause_counts_and_budget(self):
        html = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        names = ('counted', 'todayContext', 'todayContradicts', 'actionDecisions', 'safeAnswer', 'pauseExplanation', 'isolate', 'lastLaunchBudget', 'launchBudgetMinor',
                 'launchBlockReason', 'launchCounts', 'renderToday')
        functions = []
        for name in names:
            start = html.index('function '+name+'(')
            end = html.index('\n', start)
            if not html[start:end].endswith('}'):
                end = html.index('\n}', end) + 2
            functions.append(html[start:end])
        harness = r"""
const assert=require('node:assert/strict');
class N {constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.hidden=false}
 prepend(...args){this.children.unshift(...args)} append(...args){this.children.push(...args)} replaceChildren(){this.children=[]}}
const el=(tag,cls='',text='')=>new N(tag,text);
const document={createElement:tag=>new N(tag),createTextNode:text=>new N('#text',text),querySelector:()=>({value:'men'})};
const nodes={};const $=id=>nodes[id]||(nodes[id]=new N('div'));
const x=n=>String(n),ils=n=>'₪'+n;
const greeting=()=>'',dismissed=()=>[],emptyState=()=>new N('div'),actionCard=()=>new N('article');
let outcomes=null,report={creative_drift:{status:'ready',items:[{features:[{feature:'music'}]},{features:[{feature:'music'}]}]},recommendations:{today:{items:[],status:'ready'}}};
let launchState={draft:{adsets:[{key:'a',target:{mode:'new'}},{key:'b',target:{mode:'new'}}],ads:[{adset_key:'a'},{adset_key:'b'}]}};
const compactCard=card=>card;const H_STATE={};let learning={hypotheses:[{id:'x',outside:{summary:'משפט ראשון. More internal jargon.'},evidence:{},sources:[]}],shadow:{days:1}};
const text=n=>n.textContent+n.children.map(c=>typeof c==='string'?c:text(c)).join('');
let localStorage={getItem:()=>null};
const answer=new N('div');safeAnswer(answer,'**שלום**\n<img src=x onerror=alert(1)>');
assert.equal(answer.children[1].tag,'strong');
assert.equal(answer.children.some(c=>c.tag==='img'),false);
assert.ok(text(answer).includes('<img src=x onerror=alert(1)>'));
assert.ok(!text(answer).includes('**'));
assert.ok(pauseExplanation(1.59,90,{fail_below:5.2}).includes('מתחת לסף העצירה'));
assert.ok(!pauseExplanation(1.59,90,{fail_below:5.2}).includes('מוציאה יותר'));
assert.ok(pauseExplanation(0.5,90,{fail_below:5.2}).includes('מתחת לסף העצירה שלך (₪5.20)'));
assert.ok(pauseExplanation(1.59,90,{}).includes('סף העצירה אינו ידוע'));
assert.equal(isolate('20.10–22.10'),'\u206620.10–22.10\u2069');
assert.equal(lastLaunchBudget(),'');
localStorage.getItem=()=> '150';assert.equal(lastLaunchBudget(),'');
localStorage.getItem=()=> 'NaN';assert.equal(lastLaunchBudget(),'');
assert.equal(launchBudgetMinor(''),null);assert.equal(launchBudgetMinor(' '),null);
assert.equal(launchBudgetMinor('0'),null);assert.equal(launchBudgetMinor('2001'),null);
assert.equal(launchBudgetMinor('NaN'),null);assert.equal(launchBudgetMinor('100'),10000);
assert.equal(launchCounts(),'2');
assert.ok(launchBlockReason('ads_per_adset: live plus proposed count outside rule').includes('כאן יש 2'));
assert.ok(launchBlockReason('Proposal expired').includes('ההצעה פגה'));
assert.ok(launchBlockReason('Campaign budget conflicts with adset budget').includes('מתנגש'));
assert.ok(launchBlockReason('No active ad set of that gender in the campaign to copy targeting from').includes('אין בקמפיין'));
renderToday();assert.equal($('badge').textContent,'2');assert.ok(text($('hello-sub')).includes('2 מודעות עם שינויים של מטא'));

"""
        result = subprocess.run(['node', '-e', '\n'.join(functions)+'\n'+harness],
                                encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
