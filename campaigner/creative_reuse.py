"""Historical image-use evidence, never delivery state or execution authority.

Caller supplies HMAC-verified operation records. Missing legacy provenance is
deliberately omitted; image-library receipts alone cannot establish ad use.
"""
import hashlib
import json
import re
from datetime import datetime


def historical_source_reuse(jobs, selected_files, account_id, campaign_id):
    def digest(value):
        return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

    def refs(value):
        if isinstance(value,dict):
            if set(value)=={'ref','field'}:
                return [value]
            return [ref for item in value.values() for ref in refs(item)]
        if isinstance(value,list):
            return [ref for item in value for ref in refs(item)]
        return []

    selected={}
    if not isinstance(jobs,list) or not isinstance(selected_files,list):return []
    for row in selected_files[:30]:
        if isinstance(row,dict) and re.fullmatch('[a-f0-9]{64}',str(row.get('sha256',''))):
            selected.setdefault(row['sha256'],[]).append(row)
    warnings=[];seen=set()
    for job in jobs:
        try:
            if not isinstance(job,dict):continue
            manifest=job['manifest']
            timings=job.get('timings')
            if not isinstance(manifest,dict) or not isinstance(timings,dict):continue
            if (job['action']!='prepare_launch' or job['state']!='verified'
                or timings.get('transport_kind')!='meta_http'
                or manifest.get('account_id')!=account_id or manifest.get('campaign_id')!=campaign_id
                or digest(manifest)!=job['digest']):continue
            if not isinstance(job.get('steps'),list) or not isinstance(manifest.get('steps'),list) or not isinstance(manifest.get('files'),list):continue
            if any(not isinstance(s,dict) or not isinstance(s.get('params'),dict) or not isinstance(s.get('result'),dict) for s in job['steps']):continue
            if any(not isinstance(s,dict) for s in manifest['steps']+manifest['files']):continue
            steps={s['key']:s for s in job['steps']};frozen={s['key']:s for s in manifest['steps']}
            if len(steps)!=len(job['steps']) or set(steps)!=set(frozen):continue
            if any(s.get('state')!='verified' or any(s.get(k)!=frozen[key].get(k) for k in ('kind','params','expected')) for key,s in steps.items()):continue
            # An uncertain original execution may only have become verified on
            # a later GET reconciliation. Never date that proof to its failure.
            reconciliation=job.get('reconciliation')
            verified_at=job.get('finished_at') if 'reconciliation' not in job else None
            if isinstance(reconciliation,dict) and reconciliation.get('read_only') is True and reconciliation.get('current_state')=='match':
                verified_at=reconciliation.get('checked_at')
            try:
                stamp=datetime.fromisoformat(verified_at.replace('Z','+00:00')) if isinstance(verified_at,str) else None
                if stamp is None or stamp.tzinfo is None:verified_at=None
            except ValueError:verified_at=None

            def resolve(value):
                if isinstance(value,dict):
                    if set(value)=={'ref','field'}:return steps[value['ref']]['result'][value['field']]
                    return {k:resolve(v) for k,v in value.items()}
                if isinstance(value,list):return [resolve(v) for v in value]
                return value

            for ad in steps.values():
                if ad['kind']!='ads':continue
                adid=ad['result']['id']
                if not re.fullmatch('[0-9]{5,30}',adid):continue
                expected=resolve(ad['params']);expected['creative']={'id':expected['creative']['creative_id']};expected['campaign_id']=campaign_id
                if ad['result'].get('verified_fields')!=expected:continue
                creative=steps[ad['params']['creative']['creative_id']['ref']]
                adset=steps[ad['params']['adset_id']['ref']]
                if (creative['kind']!='adcreatives' or adset['kind']!='adsets'
                    or adset['result'].get('verified_fields')!=resolve(adset['params'])
                    or adset['params'].get('campaign_id')!=campaign_id
                    or creative['result'].get('verified_fields')!=resolve(creative['params'])):continue
                for ref in refs(creative['params']):
                    image=steps[ref['ref']];result=image['result'];sha=image['params'].get('sha256')
                    if image['kind']!='image' or ref['field']!='hash' or sha not in selected:continue
                    proof=result.get('upload_provenance',{})
                    if not isinstance(proof,dict):continue
                    source=next((f for f in manifest.get('files',[]) if f.get('id')==image['params'].get('asset_id') and f.get('sha256')==sha),{})
                    if (result.get('reused') is not False or result.get('source_sha256')!=sha
                        or result.get('hash')!=image.get('provider_image_hash')
                        or not re.fullmatch('[A-Za-z0-9]{8,128}',str(result.get('hash','')))
                        or proof.get('method')!='local_bytes_upload_then_library_readback'
                        or proof.get('account_id')!=account_id or proof.get('sha256')!=sha
                        or proof.get('transport_kind')!='meta_http' or type(proof.get('byte_count')) is not int or proof['byte_count']<=0
                        or source.get('size')!=proof['byte_count']):continue
                    for row in selected[sha]:
                        key=(row.get('id'),adid,job['operation_id'])
                        if key in seen:continue
                        seen.add(key)
                        warnings.append({'asset_id':row.get('id'),'source_name':row.get('name',row.get('filename','')),
                            'sha256':sha,'operation_id':job['operation_id'],'ad_id':adid,'adset_id':expected['adset_id'],
                            'verified_at':verified_at,'current_presence_verified':False})
                        if len(warnings)>=150:return warnings
        except (KeyError,TypeError,ValueError,AttributeError,RecursionError):
            continue
    return warnings
