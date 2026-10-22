"""One local real-source proof, no network or execution workers."""
from datetime import datetime, timezone
from pathlib import Path
import json
from decision_loop import DecisionLoop
from management_report import build_report
from action_recommendations import build_recommendations
from business_context import load_rosery_dossier

def stamp():return datetime.now(timezone.utc).isoformat()

def main():
 root=Path(__file__).parent;started=stamp()
 snapshot=json.loads((root/'.local/live-reports.json').read_text(encoding='utf-8'))
 dossier=load_rosery_dossier(include_owner_financials=True)
 recommendations=build_recommendations(build_report(snapshot),dossier)
 prepared=stamp();loop=DecisionLoop(root/'.local')
 first=loop.freeze_recommendations(snapshot,recommendations,dossier);executed=stamp()
 second=loop.freeze_recommendations(snapshot,recommendations,dossier)
 assert first['id']==second['id'] and second['status']=='already_recorded'
 outcome=loop.evaluate_frozen(first['id']);verified=stamp()
 result={'goal':'freeze_actual_shadow_decisions_in_existing_journal',
         'first':first,'repeat':second,'evaluation':outcome,
         'decision_count':len(recommendations['recommendations']),
         'stage':'local_acceptance_complete','source_observed_at':snapshot['sources']['meta']['last_success_at'],
         'timing':{'preparation_started_at':started,'preparation_finished_at':prepared,
                   'execution_finished_at':executed,'verification_finished_at':verified,
                   'future_observation_wait_started_at':first['frozen_at'],'future_observation_wait_finished_at':None},
         'limitations':['Reporting comparison only, no decision-loss or profitability validation',
                        'Server code connected; no server restart performed by this script'],
         'external_actions':False}
 path=root.parent/'outputs/shadow-freeze-acceptance-2026-10-20.json'
 path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
