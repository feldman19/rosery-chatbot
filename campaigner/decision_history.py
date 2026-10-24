"""Hourly first-observed aggregate receipts for later attribution-revision audits.

No customer records, creative content, provider calls or inference. Missing hours
are missing, not reconstructed. No historical snapshots are backdated.
"""
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
from business import ACTIVE as BUSINESS

ACCOUNT=BUSINESS.meta_account
ROW_FIELDS=('ad_id','adset_id','campaign_id','date_start','date_stop','spend','impressions','inline_link_clicks')
QUERY_FIELDS=('action_report_time','action_attribution_windows','use_account_attribution_setting',
              'use_unified_attribution_setting','level','time_increment')


def project_observation(source):
    if not isinstance(source,dict):return 'invalid_source'
    data=source.get('data') or {}
    if not isinstance(data,dict):return 'invalid_source'
    if (source.get('error') or data.get('verified') is not True or data.get('complete') is not True
        or data.get('account_id')!=ACCOUNT or data.get('currency')!=BUSINESS.currency):return 'ineligible_source'
    try:
        observed=datetime.fromisoformat(source['last_success_at'].replace('Z','+00:00'))
        today=date.fromisoformat(data['today'])
        if not observed.tzinfo:return 'invalid_source_time'
        rows=[]
        for row in data['rows']:
            start=date.fromisoformat(row['date_start']);end=date.fromisoformat(row['date_stop'])
            if start!=end:return 'non_daily_rows'
            if start>=today:continue
            item={k:row[k] for k in ROW_FIELDS if k in row}
            for name in ('actions','action_values'):
                if name in row:
                    item[name]=[{k:x[k] for k in ('action_type','value') if k in x} for x in row[name]]
            rows.append(item)
        settings=data.get('query_settings') or {}
        payload={'schema_version':1,'account_id':ACCOUNT,'currency':BUSINESS.currency,
                 'observed_at':observed.isoformat(),'source_today':today.isoformat(),
                 'timezone':data.get('timezone'),
                 'source_since':data.get('since'),'source_until':data.get('until'),
                 'source_complete':data['complete'],
                 'archive_excludes_dates_on_or_after':today.isoformat(),
                 'last_possible_full_day':(today-timedelta(days=1)).isoformat(),
                 'coverage_validated':False,
                 'adset_attribution_settings_history':'not_captured',
                 'revisions_identify_reporting_lag_alone':False,
                 'query_settings':{k:settings[k] for k in QUERY_FIELDS if k in settings},
                 'rows':rows,'sampling':'first_successful_observation_per_utc_hour',
                 'causal_or_maturity_proof':False}
        canonical=json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError,KeyError,AttributeError):return 'invalid_source'
    payload['content_sha256']=hashlib.sha256(canonical.encode()).hexdigest()
    return payload


def save_observation(directory, source):
    payload=project_observation(source)
    if not isinstance(payload,dict):return payload
    observed=datetime.fromisoformat(payload['observed_at'])
    folder=Path(directory)/'decision-observations';folder.mkdir(parents=True,exist_ok=True)
    target=folder/(observed.astimezone(timezone.utc).strftime('%Y%m%dT%H')+'.json')
    try:
        with target.open('x',encoding='utf-8') as output:
            json.dump(payload,output,ensure_ascii=False,allow_nan=False)
    except FileExistsError:
        try:
            saved=json.loads(target.read_text(encoding='utf-8'))
            digest=saved.pop('content_sha256')
            actual=hashlib.sha256(json.dumps(saved,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            if digest!=actual or saved.get('schema_version')!=1:return 'corrupt_existing_hour'
        except (ValueError,TypeError,KeyError,AttributeError,OSError):return 'corrupt_existing_hour'
        return 'hour_already_recorded'
    return 'recorded'
