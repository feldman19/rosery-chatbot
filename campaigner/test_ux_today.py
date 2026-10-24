import json
import subprocess
import time
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from insights_split import read_split_insights
from management_report import _daily, ACCOUNT
from outside_check import _run, _clean

class TodayReadTests(unittest.TestCase):
    def data(self):
        day='2026-10-23'
        row={'ad_id':'123','adset_id':'456','campaign_id':'789','date_start':day,'date_stop':day,'spend':'129','impressions':'100','actions':[],'action_values':[]}
        orphan={**row,'ad_id':'124'}
        def graph(token,path,params):
            fields=params['fields'].split(',')
            return {'data':[{k:v for k,v in r.items() if k in fields} for r in ([row] if 'spend' in fields else [row,orphan])]}
        result=read_split_insights(graph,'synthetic',ACCOUNT,day,day,'ad',time.monotonic()+30,{})
        return {**result,'account_id':ACCOUNT,'since':day,'until':day,'query_settings':{},'checked_at':datetime.now(timezone.utc).isoformat()}

    def test_complete_delivery_today_ignores_action_only_rows_for_account_spend(self):
        result=_daily(self.data(),{},date(2026,10,23),True,'ILS')[-1]
        self.assertTrue(result['partial'])
        self.assertEqual(result['metrics']['spend'],129)
        self.assertEqual(result['metrics']['roas'],0)
        self.assertIsNone(result['metrics']['store_orders'])

    def test_incomplete_delivery_stays_unknown_with_explicit_lower_bound(self):
        data=self.data();data.pop('metric_collection')
        result=_daily(data,{},date(2026,10,23),True,'ILS')[-1]['metrics']
        self.assertIsNone(result['spend']);self.assertEqual(result['spend_lower_bound'],129)
        self.assertIsNone(result['roas'])

    def test_incomplete_account_read_exposes_only_identity_bound_lower_bound(self):
        data=self.data();data.pop('metric_collection');data.update(verified=True,currency='ILS',today='2026-10-23',complete=False)
        result=_daily(data,{},date(2026,10,23),False,'ILS')[-1]['metrics']
        self.assertIsNone(result['spend']);self.assertEqual(result['spend_lower_bound'],129)
        self.assertIsNone(result['roas'])
        data['account_id']='other'
        self.assertNotIn('spend_lower_bound',_daily(data,{},date(2026,10,23),False,'ILS')[-1]['metrics'])

    def test_invalid_provenance_does_not_adopt_delivery_total(self):
        data=self.data();data['metric_streams']['delivery']['complete']=False
        result=_daily(data,{},date(2026,10,23),True,'ILS')[-1]['metrics']
        self.assertIsNone(result['spend']);self.assertNotIn('spend_lower_bound',result)

    def test_research_failure_diagnosis_does_not_expose_cli_output(self):
        done=subprocess.CompletedProcess([],1,'','rate limit private-marker')
        with patch('outside_check.shutil.which',return_value='claude'),patch('outside_check.subscription_environment',return_value={}),patch('outside_check.subscription_auth',return_value=True),patch('outside_check.subprocess.run',return_value=done):
            with self.assertRaisesRegex(ValueError,'מגבלת שימוש') as error:_run('synthetic')
        self.assertNotIn('private-marker',str(error.exception))

    def test_source_optional_fields_do_not_crash_cleaning(self):
        result=_clean({'findings':[{'url':'https://example.com','relation':'context','source':'Example'}]})
        self.assertEqual(result['findings'][0]['source'],'Example')

class VersionTests(unittest.TestCase):
    def test_update_requires_valid_distinct_descendant(self):
        from server import screen_update_newer
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run:
            self.assertFalse(screen_update_newer('__VERSION__','גרסה abcdef012'))
            self.assertFalse(screen_update_newer('abcdef0','גרסה abcdef012 · המסך עודכן 23.10'))
            self.assertFalse(run.called)
            self.assertTrue(screen_update_newer('1234567','גרסה abcdef012 · המסך עודכן 23.10'))
        with patch('subprocess.run',return_value=subprocess.CompletedProcess([],1)):
            self.assertFalse(screen_update_newer('1234567','גרסה abcdef012'))

class RecommendationTodayTests(unittest.TestCase):
    def test_partial_day_context_and_thresholds(self):
        from pathlib import Path
        page=Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
        helpers=page.split('// UX pass: partial-day context',1)[1].split('// UX pass: keep',1)[0]
        script="const assert=require('assert'); // context"+helpers+r"""
const x=v=>typeof v==='number'?v.toFixed(2):'—',ils=v=>'₪'+v,el=(tag,cls,text)=>({text});
const ds=[{entity_id:'a',entity_name:'catalog',adset_id:'s',action:'PAUSE',retargeting:true,thresholds:{fail_below:5.2,hold_up_to:6},numbers:{roas:1.59}}];
const decisions=()=>ds,decisionFor=id=>ds.find(d=>d.entity_id===id);
let report={daily:[{partial:false,ads:[{id:'a',spend:270,roas:1.59}]},{partial:true,ads:[{id:'a',spend:130,purchases:2,roas:3.13}]}]};
const pause={action:'PAUSE',proposal_id:'pause:a'};
assert.equal(todayContradicts(pause),false);
let lines=[];const card={append:n=>lines.push(n.text)};appendTodayContext(card,pause);
assert(lines.some(t=>t.includes('130')&&t.includes('3.13')&&t.includes('מתעדכן')));
assert(lines.some(t=>t.includes('3 ימים מלאים בלבד')));
assert(lines.some(t=>t.includes('הסף כפול (₪5.20)')));
assert(pauseExplanation(1.59,90,ds[0].thresholds).includes('(₪5.20)'));
assert(!pauseExplanation(1.59,90,{}).includes('2.6'));
ds[0].thresholds.hold_up_to=3;assert.equal(todayContradicts(pause),true);
lines=[];appendTodayContext(card,pause);assert(lines.some(t=>t.includes('היום היא דווקא עובדת טוב (ROAS 3.13)')));
const other={action:'ALERT'};
assert.deepEqual([pause,other].sort((a,b)=>Number(todayContradicts(a))-Number(todayContradicts(b))),[other,pause]);
report.daily[1].ads[0].spend=49;assert.equal(todayContradicts(pause),false);
report.daily[1].ads[0].spend=50;assert.equal(todayContradicts(pause),true);
ds[0].action='SCALE';const scale={action:'SCALE',proposal_id:'scale:s'};assert.equal(todayContradicts(scale),true);
report.daily[1].ads[0].roas=5.2;assert.equal(todayContradicts(scale),false);
report.daily[1].ads=[];assert.equal(todayContradicts(scale),false);
lines=[];appendTodayContext(card,scale);assert(lines.some(t=>t.includes('לא ידועה')&&t.includes('מתעדכן')));
assert.equal(ds[0].numbers.roas,1.59);
"""
        done=subprocess.run(['node','-e',script],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(done.returncode,0,done.stderr)
        self.assertIn("run.onclick=()=>openAction(item.proposal_id)",page)

    def test_daily_keeps_ads_beyond_top_ten(self):
        from copy import deepcopy
        data=deepcopy(TodayReadTests().data())
        rows=data['rows']
        base=rows[0]
        rows[:]=[{**base,'ad_id':str(i),'spend':str(i+1)} for i in range(11)]
        with patch('management_report._meta_rows',return_value=(rows,None)),patch('management_report._coverage',return_value=True):
            result=_daily(data,{},date(2026,10,23),True,'ILS')[-1]
        self.assertEqual(len(result['ads']),11)
