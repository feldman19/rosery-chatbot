"""Local one-entity pause assessment from existing saved sources, no network."""
from datetime import datetime, timezone
from pathlib import Path
import json
import hashlib
from copy import deepcopy
from management_report import build_report
from business_context import load_rosery_dossier
from order_economics import order_economics
from pause_evaluation import evaluate_pause
from test_pause_evaluation import fixture

def stamp():return datetime.now(timezone.utc).isoformat()

def main():
 root=Path(__file__).parent;timing={'preparation_started_at':stamp()}
 raw=(root/'.local/live-reports.json').read_bytes();snapshot=json.loads(raw)
 report=build_report(snapshot);economics=order_economics(load_rosery_dossier(include_owner_financials=True))
 eligible=[r for r in report['windows']['7']['ads'] if r.get('configured_active') is True
           and r.get('effective_status')=='ACTIVE' and r.get('result_type')=='purchase'
           and 'R03' not in r.get('name','') and isinstance(r.get('spend'),(float,int))]
 if not eligible:raise ValueError('No eligible saved active purchase ad')
 row=max(eligible,key=lambda r:r['spend'])
 timing['preparation_finished_at']=stamp()
 actual=evaluate_pause(report,row,economics);timing['execution_finished_at']=stamp()
 assert actual['status']=='ABSTAIN' and actual['live_recommendation_allowed'] is False
 now,r,ad,s=fixture();synthetic=[]
 for label,modify in (
  ('bounded_future_saving',lambda x:None),
  ('spend_redistributed_no_saving',lambda x:x['avoidable_spend'].update(lower=0,upper=0)),
  ('unknown_contribution_range',lambda x:x['foregone_contribution'].update(lower=30,upper=130)),
  ('attributed_revenue_wrong_meaning',lambda x:x['foregone_contribution'].update(meaning='reported_meta_revenue'))):
  case=deepcopy(s);modify(case)
  synthetic.append({'case':label,'synthetic':True,'evaluation':evaluate_pause(r,ad,{'status':'synthetic','missing':[]},case,now=now)})
 assert [x['evaluation']['status'] for x in synthetic]==[
  'SCENARIO_PAUSE_DOMINATES','SCENARIO_CONTINUE_DOMINATES','ABSTAIN','ABSTAIN']
 timing['verification_finished_at']=stamp()
 out={'goal':'pause_only_forward_economic_assessment','stage':'local_assessment_complete',
      'selection':'Highest observed spend among saved active purchase ads excluding R03; audit sample, not a finding of poor performance',
      'source_sha256':hashlib.sha256(raw).hexdigest(),'actual':actual,'synthetic_cases':synthetic,
      'timing':timing,'waiting':{'required_inputs':actual['missing'],'completed_at':None},
      'server_or_campaign_changed':False,'prospective_quality_proven':False}
 path=root.parent/'outputs/pause-evaluation-acceptance-2026-10-20.json'
 path.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
 print(json.dumps({'actual':actual,'synthetic_statuses':[(x['case'],x['evaluation']['status']) for x in synthetic],'timing':timing},ensure_ascii=False))

if __name__=='__main__':main()
