"""One-shot exact paused C11 canary; never imported by the service."""
import json,time
from pathlib import Path
from datetime import datetime,timezone
from meta_write_connection import load
from campaign_operations_transport import CampaignTransport,ProviderRejected,ACCOUNT,PARENT
from c11_canary import HOST,FEED,STORY

def run():
    receipt=Path('../outputs/c11-api-canary-2026-10-18.json')
    history=json.loads(receipt.read_text(encoding='utf-8'))
    if 'post_publish_attempt' in history: raise ValueError('Attempt exists: reconcile, never retry')
    package=json.loads(Path('../outputs/canary-parent-readback-2026-10-18.json').read_text(encoding='utf-8'))['post_publish_preflight']
    t=CampaignTransport();start=time.monotonic();token=load('.local')
    fields='id,account_id,campaign_id,name,status,daily_budget,lifetime_budget,is_dynamic_creative'
    host=t.read(token,HOST,fields);parent=t.read(token,PARENT,'id,account_id,name,status')
    if host!=package['host'] or parent!=package['parent'] or host['status']!='PAUSED':raise ValueError('State drift')
    existing=t._request(token,HOST+'/ads',params={'fields':'id,name,status,creative{id}','limit':100})
    if existing.get('paging',{}).get('next') or any('CANARY' in a.get('name','').upper() for a in existing.get('data',[])):raise ValueError('Duplicate scan unresolved')
    for h in [FEED,STORY]:
        rows=t.image_info(token,h).get('data',[])
        if len(rows)!=1 or rows[0].get('hash')!=h or rows[0].get('account_id')!=ACCOUNT:raise ValueError('Image mismatch')
    state={'approval':'User nu after exact paused one-ad canary approval request; no activation or budget change','started_at':datetime.now(timezone.utc).isoformat(),'before':host,'parent_before':parent,'creative_request':package['candidate'],'state':'creative_inflight','ui_verified':False,'timings':{'preflight_seconds':time.monotonic()-start}}
    history['post_publish_attempt']=state
    def save():receipt.write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    try:
        tick=time.monotonic();r=t.create(token,'adcreatives',state['creative_request']);state['timings']['creative_submission_seconds']=time.monotonic()-tick
        state['creative_id']=r['id'];state['state']='creative_created';save()
        state['creative_readback']=t.read(token,r['id'],'id,account_id,object_story_spec,asset_feed_spec,url_tags');save()
        if state['creative_readback'].get('account_id')!=ACCOUNT:raise ValueError('Creative mismatch')
        state['state']='ad_inflight';save();tick=time.monotonic()
        r=t.create(token,'ads',{'name':'ROSERY | C11 API CANARY | DO NOT ACTIVATE','adset_id':HOST,'status':'PAUSED','creative':{'creative_id':state['creative_id']}})
        state['timings']['ad_submission_seconds']=time.monotonic()-tick;state['ad_id']=r['id'];save()
        state['ad_readback']=t.read(token,r['id'],'id,account_id,campaign_id,adset_id,status,creative{id}')
        if any(state['ad_readback'].get(k)!=v for k,v in {'account_id':ACCOUNT,'campaign_id':PARENT,'adset_id':HOST,'status':'PAUSED'}.items()):raise ValueError('Ad mismatch')
        if t.read(token,HOST,fields)!=host or t.read(token,PARENT,'id,account_id,name,status')!=parent:raise ValueError('Host drift')
        state['state']='api_readback_complete_ui_pending'
    except ProviderRejected as e:state.update(state='provider_rejected',provider_error=e.details)
    except Exception as e:state.update(state='reconcile_required',error_type=type(e).__name__)
    finally:state['timings']['total_api_seconds']=time.monotonic()-start;save()
    return {k:state.get(k) for k in ['state','ad_id','creative_id','provider_error','error_type','timings']}

if __name__=='__main__':print(json.dumps(run(),ensure_ascii=True))
