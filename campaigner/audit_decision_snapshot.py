"""Read the saved source snapshot and record an offline recommendation audit.

No provider calls, worker startup, model request or campaign mutation.
Not a historical backtest: uses the saved snapshot as observed today.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from management_report import build_report
from action_recommendations import build_recommendations
from business_context import load_rosery_dossier
from owner_action_proposals import build_owner_action_proposals


def main():
    root=Path(__file__).resolve().parent
    source=root/'.local/live-reports.json'
    raw=source.read_bytes()
    snapshot=json.loads(raw)
    now=datetime.now(timezone.utc)
    report=build_report(snapshot,now=now)
    decisions=build_recommendations(report,load_rosery_dossier(include_owner_financials=True),now=now)
    actions=build_owner_action_proposals(report,now=now)
    rows=decisions['recommendations']
    unsafe=[x['id'] for x in rows if x['live_change'] or x['action']!='INSUFFICIENT_EVIDENCE']
    assert not unsafe
    result={'evaluated_at':now.isoformat(),'mode':'saved_snapshot_shadow_audit_not_backtest',
        'source_file':str(source),'source_sha256':hashlib.sha256(raw).hexdigest(),
        'source_observed_at':report['freshness'],'account_id':report['account_id'],
        'report_generated_at':report['checked_at'],'decision_engine_status':decisions['decision_engine_status'],
        'automatic_financial_recommendations':0,'unsupported_experiment_recommendations':0,
        'entities_checked':len(rows),'scope':'Existing source only; no causal or predictive validation',
        'campaign_mutations':0,'prospective_validation_completed':False,
        'owner_action_status':actions,
        'ad_observations':[{'id':x['entity_id'],'name':x['entity_name'],'action':x['action'],
            'evidence':x['evidence'],'assessment':x['evidence_assessment'],
            'sensitivity':x['one_event_sensitivity'],'sensitivity_scope':x['sensitivity_scope']}
            for x in rows if x['entity_type']=='ad']}
    path=root.parent/'outputs/recommendation-reliability-shadow-2026-10-20.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'artifact':str(path),'entities_checked':len(rows),
        'fresh':decisions['fresh'],'unsupported_actions':unsafe,
        'source_observed_at':report['freshness']['meta_last_success_at']},ensure_ascii=False))

if __name__=='__main__':main()
