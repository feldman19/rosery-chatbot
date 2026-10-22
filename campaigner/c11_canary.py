"""Exact owner-approved PAUSED C11 experiment; explicit one-shot CLI only.

No uploads, budget edits, existing-ad edits or activation. Receipts survive each
POST; an existing receipt forbids rerunning after uncertain outcomes.
"""
import json, hashlib, hmac
from pathlib import Path
from campaign_operations_transport import CampaignTransport, ProviderRejected, ACCOUNT, PARENT
from meta_write_connection import load

HOST = '120253138070700730'
FEED = '4f1f41d9e836b8b8942adcb043f1f180'
STORY = '9ef275501705cbd46ab7cbe84207202b'


def run(directory, receipt):
    directory, receipt = Path(directory), Path(receipt)
    if receipt.exists(): raise ValueError('Receipt already exists; reconcile, never retry')
    envelope = json.loads((directory/'launch-proposal.json').read_text(encoding='utf-8'))
    if not hmac.compare_digest(envelope['signature'],hmac.new((directory/'signing.key').read_bytes(),envelope['payload'].encode(),hashlib.sha256).hexdigest()):
        raise ValueError('Signed proposal mismatch')
    proposal = json.loads(envelope['payload'])
    ad = next(a for a in proposal['ads'] if a.get('source_name') == 'Copy of ROSERY_C11_self_style_v10_FEED.png')
    token = load(directory); transport = CampaignTransport()
    before = transport.read(token, HOST, 'id,account_id,campaign_id,status,daily_budget,lifetime_budget,is_dynamic_creative')
    parent = transport.read(token, PARENT, 'id,account_id,status')
    if (before.get('account_id'),before.get('campaign_id'),before.get('status'),before.get('is_dynamic_creative')) != (ACCOUNT,PARENT,'PAUSED',False) or parent.get('status') != 'PAUSED':
        raise ValueError('Paused account/parent preflight mismatch')
    # The original recipe failed provider validation. Build only the narrowed
    # single-copy placement candidate; this is NOT the full five-copy launch.
    from canary_plan import build_candidate
    creative = build_candidate(ad, FEED, STORY)
    state={'approval':'call_246dd4e76a3e413ab6edb8568df7826b: approved paused C11 API canary',
           'account_id':ACCOUNT,'host_adset_id':HOST,'before':before,'parent_before':parent,
           'creative_request':creative,'state':'creative_inflight','ui_verified':False}
    def save(): receipt.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    with receipt.open('x',encoding='utf-8') as f: json.dump(state,f,ensure_ascii=False)
    try:
        result=transport.create(token,'adcreatives',creative); state['creative_id']=result['id']; save()
        state['creative_readback']=transport.read(token,result['id'],'id,account_id,object_story_spec,asset_feed_spec,url_tags,degrees_of_freedom_spec'); save()
        if state['creative_readback'].get('account_id') != ACCOUNT: raise ValueError('Creative account mismatch')
        request={'name':'ROSERY | C11 API CANARY | DO NOT ACTIVATE','adset_id':HOST,'status':'PAUSED','creative':{'creative_id':result['id']}}
        state.update(state='ad_inflight',ad_request=request);save()
        result=transport.create(token,'ads',request);state['ad_id']=result['id'];save()
        state['ad_readback']=transport.read(token,result['id'],'id,account_id,campaign_id,adset_id,status,creative{id}')
        if any(state['ad_readback'].get(k)!=v for k,v in {'account_id':ACCOUNT,'campaign_id':PARENT,'adset_id':HOST,'status':'PAUSED'}.items()):raise ValueError('Paused ad readback mismatch')
        state['after']=transport.read(token,HOST,'id,account_id,campaign_id,status,daily_budget,lifetime_budget,is_dynamic_creative')
        if state['after']!=before:raise ValueError('Host changed during canary')
        state['state']='api_readback_complete_ui_pending';save()
    except ProviderRejected as error:
        state.update(state='provider_rejected',error_type=type(error).__name__,provider_error=error.details);save();raise
    except Exception as error:
        state.update(state='reconcile_required',error_type=type(error).__name__);save();raise
    return {'state':state['state'],'ad_id':state['ad_id'],'creative_id':state['creative_id'],'host_status':state['after']['status'],'budget_unchanged':True}


if __name__=='__main__':
    print(json.dumps(run('.local','../outputs/c11-api-canary-2026-10-18.json')))
