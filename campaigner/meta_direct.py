"""Fixed ROSERY reporting reader. GET only; no campaign mutation surface."""
import json
import re
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from connections import NoRedirect
from shopify_direct import crypt
from business import ACTIVE as BUSINESS

APP_ID=BUSINESS.meta_app_id
ACCOUNT=BUSINESS.meta_account
LAUNCH=BUSINESS.meta_launch_campaign
SALES_RESTART=BUSINESS.meta_sales_campaign
VERSION='v26.0'
QUERY_SETTINGS={'time_increment':'1','use_unified_attribution_setting':True,'action_report_time':'default_unchanged'}


def graph(token, path, params=None):
    allowed={'app','me/permissions','act_'+ACCOUNT,'act_'+ACCOUNT+'/insights','act_'+ACCOUNT+'/campaigns','act_'+ACCOUNT+'/ads','act_'+ACCOUNT+'/adimages',LAUNCH,LAUNCH+'/ads',LAUNCH+'/adsets',SALES_RESTART}
    if path not in allowed:raise ValueError('Destination not allowed')
    url='https://graph.facebook.com/'+VERSION+'/'+path+'?'+urlencode(params or {})
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=25) as response:
        raw=response.read(2097153)
    if len(raw)>2097152:raise ValueError('Response too large')
    result=json.loads(raw)
    if result.get('error'):raise ValueError('Meta request rejected')
    return result


def identity(token):
    app=graph(token,'app',{'fields':'id'})
    if app.get('id')!=APP_ID:raise ValueError('App mismatch')
    permissions=graph(token,'me/permissions')
    granted={p['permission'] for p in permissions.get('data',[]) if p.get('status')=='granted'}
    if 'ads_read' not in granted or granted-{'ads_read','public_profile'}:
        raise ValueError('Unexpected granted permissions')
    account=graph(token,'act_'+ACCOUNT,{'fields':'account_id,name,currency,timezone_name,timezone_offset_hours_utc'})
    if account.get('account_id')!=ACCOUNT:raise ValueError('Account mismatch')
    return account


def configure(data_dir,token):
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{30,4096}',token):
        raise ValueError('Invalid token')
    account=identity(token)
    path=Path(data_dir)/'meta-report.dpapi'
    temp=path.with_suffix('.pending')
    temp.write_bytes(crypt(token.encode()));temp.replace(path)
    return {'saved':True,'verified':True,'account_id':ACCOUNT,'app_id':APP_ID,
            'currency':account.get('currency'),'scopes':['ads_read'],
            'encryption':'Windows current-user DPAPI','live_action_allowed':False,
            'automatic_renewal':False}


def get_token(data_dir):
    return crypt((Path(data_dir)/'meta-report.dpapi').read_bytes(),True).decode()


def status(data_dir):
    if not (Path(data_dir)/'meta-report.dpapi').exists():
        return {'state':'missing_credential','verified':False,'account_id':ACCOUNT}
    try:
        account=identity(get_token(data_dir))
        return {'state':'identity_verified','verified':True,'account_id':ACCOUNT,
                'currency':account.get('currency'),'automatic_renewal':False}
    except (ValueError,KeyError,TypeError,OSError):
        return {'state':'credential_rejected','verified':False,'account_id':ACCOUNT}


def read_reports(data_dir):
    deadline=time.monotonic()+120
    token=get_token(data_dir);account=identity(token)
    # Provider's current UTC offset avoids depending on Windows tzdata. Date-only
    # ranges are evaluated in the ad account timezone by Meta, including DST days.
    offset=float(account['timezone_offset_hours_utc'])
    if not -14<=offset<=14:raise ValueError('Invalid account timezone offset')
    today=datetime.now(timezone(timedelta(hours=offset))).date()
    since=(today-timedelta(days=7)).isoformat()
    from insights_split import read_split_insights
    split=read_split_insights(graph,token,ACCOUNT,since,today.isoformat(),'ad',deadline,QUERY_SETTINGS)
    rows=split['rows'];complete=split['complete']
    inventory={'verified':False}
    try:
        campaign=graph(token,LAUNCH,{'fields':'id,name,account_id,status,effective_status,daily_budget,lifetime_budget,objective,updated_time'})
        if campaign.get('account_id')!=ACCOUNT:raise ValueError('Campaign account mismatch')
        entities={}
        for edge,fields in [('adsets','id,name,status,effective_status,daily_budget,lifetime_budget,optimization_goal,promoted_object,attribution_spec,updated_time'),('ads','id,name,status,effective_status,adset_id,updated_time')]:
            page=graph(token,LAUNCH+'/'+edge,{'fields':fields,'limit':500})
            if (page.get('paging') or {}).get('next'):raise ValueError('Incomplete inventory')
            entities[edge]=page['data']
        inventory={'verified':True,'campaign':campaign,**entities}
    except (ValueError,KeyError,TypeError,OSError):pass
    creative_inventory={'verified':False}
    if inventory.get('verified'):
        try:
            page=graph(token,LAUNCH+'/ads',{'fields':'id,name,status,effective_status,adset_id,creative{id,thumbnail_url,image_url,object_story_spec,asset_feed_spec,url_tags,link_url,degrees_of_freedom_spec}','limit':500})
            if not isinstance(page.get('data'),list) or (page.get('paging') or {}).get('next'):raise ValueError('Incomplete creative inventory')
            if {x['id'] for x in page['data']}!={x['id'] for x in inventory['ads']}:raise ValueError('Creative inventory changed during read')
            creative_inventory={'verified':True,'account_id':ACCOUNT,'campaign_id':LAUNCH,'ads':page['data']}
        except (ValueError,KeyError,TypeError,OSError):pass
    account_campaigns=read_campaign_inventory(token,deadline)
    active_ads=read_active_ads(token,deadline)
    enrich_audience_frequency(token,active_ads,today,deadline)
    from placement_readback import enrich_placement_readback
    enrich_placement_readback(graph,token,ACCOUNT,active_ads.get('ads') or [],deadline)
    enrich_creative_images(token,[*(creative_inventory.get("ads") or []),*(active_ads.get("ads") or [])],deadline)
    campaign_insights=read_campaign_insights(token,since,today.isoformat(),deadline)
    period_delivery=None
    try:
        from period_delivery import read_period_delivery
        period_delivery=read_period_delivery(graph,token,ACCOUNT,today,deadline,QUERY_SETTINGS)
    except Exception:pass
    return {'verified':True,'complete':complete,'metric_streams':split['metric_streams'],'metric_collection':split['metric_collection'],'metric_rows_sha256':split['metric_rows_sha256'],'account_id':ACCOUNT,'app_id':APP_ID,'inventory':inventory,'creative_inventory':creative_inventory,'account_campaigns':account_campaigns,'active_ads':active_ads,'campaign_insights':campaign_insights,'period_delivery':period_delivery,'query_settings':dict(QUERY_SETTINGS),
            'currency':account.get('currency'),'timezone':account.get('timezone_name'),'timezone_offset_hours':offset,
            'today':today.isoformat(),'since':since,'until':today.isoformat(),
            'checked_at':datetime.now(timezone.utc).isoformat(),'rows':rows,
            'window':'7 complete account-local days plus current partial day; daily rows',
            'attribution':'Ad-set attribution settings; attributed events are not unique registrations or incremental sales',
            'source':'direct_meta_api','live_action_allowed':False}


def read_campaign_insights(token,since,until,deadline=None):
    """Optional independent daily campaign report, never normalizes omissions.

    Official SDK AdAccount.get_insights supports level=campaign, fields,
    time_range/time_increment and use_unified_attribution_setting:
    github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/adaccount.py
    No next URL is followed. Failure cannot erase the main ad-level report.
    """
    deadline=deadline or time.monotonic()+45;empty={'verified':False,'complete':False,'account_id':ACCOUNT,'since':since,'until':until,'rows':[]}
    try:
        from insights_split import read_split_insights
        split=read_split_insights(graph,token,ACCOUNT,since,until,'campaign',deadline,QUERY_SETTINGS)
        return {'verified':True,'complete':True,'account_id':ACCOUNT,'since':since,'until':until,'checked_at':datetime.now(timezone.utc).isoformat(),**split,'query_settings':dict(QUERY_SETTINGS),'attribution':'Ad-set attribution settings; separate delivery/action reads','source':'direct_meta_campaign_insights'}
    except Exception:pass
    return {**empty,'error':'Independent campaign comparison unavailable or incomplete'}


def read_campaign_inventory(token,deadline=None):
    """Read current account-wide campaign metadata, including paused campaigns.

    Metadata is not proof of historical success or suitability for a new offer.
    Reconstruct every page on the fixed account path; never follow next URLs.
    """
    deadline=deadline or time.monotonic()+45
    params={'fields':'id,name,account_id,status,effective_status,objective,daily_budget,lifetime_budget,updated_time','limit':100}
    rows=[];seen=set();cursors=set()
    try:
        for _ in range(20):
            if time.monotonic()>deadline:raise ValueError('Inventory deadline')
            result=graph(token,'act_'+ACCOUNT+'/campaigns',params)
            batch=result.get('data')
            if not isinstance(batch,list):raise ValueError('Inventory rows missing')
            for row in batch:
                if not isinstance(row,dict) or row.get('account_id')!=ACCOUNT or not str(row.get('id','')).isdigit() or row['id'] in seen:raise ValueError('Inventory binding or grain mismatch')
                seen.add(row['id']);rows.append(row)
            paging=result.get('paging') or {}
            if not paging.get('next'):
                return {'verified':True,'complete':True,'account_id':ACCOUNT,'campaigns':rows,'checked_at':datetime.now(timezone.utc).isoformat()}
            cursor=(paging.get('cursors') or {}).get('after')
            if not isinstance(cursor,str) or not cursor or len(cursor)>4096 or cursor in cursors:raise ValueError('Invalid inventory cursor')
            cursors.add(cursor);params['after']=cursor
    except Exception:pass
    return {'verified':False,'complete':False,'account_id':ACCOUNT,'campaigns':[],'error':'Account campaign inventory unavailable or incomplete'}


def read_active_ads(token,deadline=None):
    """Current active inventory, not historical-active status or causal evidence.

    get_ads/effective_status follows Meta's official facebook-python-business-sdk.
    """
    deadline=deadline or time.monotonic()+45
    params={'fields':'id,name,account_id,status,effective_status,updated_time,campaign{id,name,objective,status,effective_status,updated_time},adset{id,name,status,daily_budget,lifetime_budget,updated_time,optimization_goal,promoted_object,attribution_spec,targeting,effective_status},creative{id,thumbnail_url,image_url,body,title,object_story_spec,asset_feed_spec,url_tags,degrees_of_freedom_spec}','effective_status':json.dumps(['ACTIVE']),'limit':100}
    rows=[];seen=set();cursors=set()
    try:
        for _ in range(20):
            if time.monotonic()>deadline:raise ValueError('Active inventory deadline')
            result=graph(token,'act_'+ACCOUNT+'/ads',params)
            batch=result.get('data')
            if not isinstance(batch,list):raise ValueError('Active inventory unavailable')
            for row in batch:
                if (not isinstance(row,dict) or row.get('account_id')!=ACCOUNT or not isinstance(row.get('id'),str) or not row['id'].isdigit() or row['id'] in seen or row.get('effective_status')!='ACTIVE'):raise ValueError('Active ad identity or state invalid')
                if any(not isinstance(row.get(key),dict) or not str(row[key].get('id','')).isdigit() or row[key].get('effective_status')!='ACTIVE' for key in ('campaign','adset')):raise ValueError('Active parent binding missing')
                seen.add(row['id']);rows.append(row)
            paging=result.get('paging') or {}
            if not paging.get('next'):
                return {'verified':True,'complete':True,'account_id':ACCOUNT,'ads':rows,'checked_at':datetime.now(timezone.utc).isoformat()}
            cursor=(paging.get('cursors') or {}).get('after')
            if not isinstance(cursor,str) or not cursor or len(cursor)>4096 or cursor in cursors:raise ValueError('Invalid active inventory cursor')
            cursors.add(cursor);params['after']=cursor
    except Exception:pass
    return {'verified':False,'complete':False,'account_id':ACCOUNT,'ads':[],'error':'Current active-ad inventory unavailable or incomplete'}


def enrich_creative_images(token,ads,deadline):
    """Best-effort original image URLs, matched by exact provider image hash.

    Thumbnails are retained if image-library reads fail. No placement preview claim.
    Official AdImage fields: facebook-python-business-sdk/adobjects/adimage.py.
    """
    bindings=[]
    for ad in ads:
        c=ad.get('creative') or {}; spec=c.get('object_story_spec') or {}; link=spec.get('link_data') or {}; feed=c.get('asset_feed_spec') or {}
        h=link.get('image_hash')
        if not h:
            images=feed.get('images') or []; rules=feed.get('asset_customization_rules') or []
            default=next((r for r in rules if r.get('is_default') or not (r.get('customization_spec') or {}).get('publisher_platforms')),None)
            label=(default.get('image_label') or {}).get('name') if default else None
            image=next((i for i in images if label and any(l.get('name')==label for l in i.get('adlabels',[]))),None)
            if image is None and len(images)==1:image=images[0]
            h=image.get('hash') if image else None
        if isinstance(h,str) and re.fullmatch('[a-f0-9]{16,64}',h):bindings.append((c,h))
    hashes=list(dict.fromkeys(h for _,h in bindings))[:100]
    if not hashes or time.monotonic()>deadline:return
    try:
        result=graph(token,'act_'+ACCOUNT+'/adimages',{'hashes':json.dumps(hashes),'fields':'hash,url,width,height','limit':100})
        images={i.get('hash'):i for i in result.get('data',[]) if isinstance(i,dict)}
        for c,h in bindings:
            image=images.get(h) or {}
            if isinstance(image.get('url'),str):c['image_url']=image['url'];c['workspace_image_hash']=h
    except Exception:pass


# Meta rejects DELETED in this filter (error 100/1815001); deleted ads keep their insights rows only.
AD_STATUSES=['ACTIVE','PAUSED','ARCHIVED','CAMPAIGN_PAUSED','ADSET_PAUSED','PENDING_REVIEW','DISAPPROVED','PREAPPROVED','PENDING_BILLING_INFO','IN_PROCESS','WITH_ISSUES']
HISTORY_CREATIVE_FIELDS='id,name,effective_status,creative{id,object_type,body,title,call_to_action_type,object_story_spec,asset_feed_spec,video_id,image_hash,product_set_id}'


def _all_pages(token,path,request,deadline,max_pages=40):
    """Every page on the fixed path; cursors only, never a provider-supplied next URL."""
    rows=[];cursors=set();query=dict(request)
    for _ in range(max_pages):
        if time.monotonic()>deadline:raise ValueError('History deadline')
        result=graph(token,path,query);batch=result.get('data')
        if not isinstance(batch,list):raise ValueError('History rows missing')
        rows.extend(batch);paging=result.get('paging') or {}
        if not paging.get('next'):return rows
        cursor=(paging.get('cursors') or {}).get('after')
        if not isinstance(cursor,str) or not cursor or len(cursor)>4096 or cursor in cursors:raise ValueError('Invalid history cursor')
        cursors.add(cursor);query={**request,'after':cursor}
    raise ValueError('History pagination too long')


def read_account_history(data_dir,min_creative_spend=200,max_creatives=1000):
    """Ad-level totals for the whole account history and the last 90 full days, plus
    creative text/format for ads that spent enough to compare. GET only.

    Delivery and actions are read separately and merged by ad: an ad missing from
    the actions read has unknown purchases, never zero.
    """
    deadline=time.monotonic()+300
    token=get_token(data_dir);account=identity(token)
    offset=float(account['timezone_offset_hours_utc'])
    if not -14<=offset<=14:raise ValueError('Invalid account timezone offset')
    today=datetime.now(timezone(timedelta(hours=offset))).date()
    ranges={'all':{'date_preset':'maximum'},
            '90':{'time_range':json.dumps({'since':(today-timedelta(days=90)).isoformat(),'until':(today-timedelta(days=1)).isoformat()})}}
    common={'level':'ad','time_increment':'all_days','use_unified_attribution_setting':'true','limit':'500'}
    windows={}
    for key,span in ranges.items():
        delivery={**common,**span,'fields':'ad_id,ad_name,adset_name,campaign_id,campaign_name,spend,impressions,date_start,date_stop'}
        actions={**common,**span,'fields':'ad_id,actions,action_values,date_start,date_stop'}
        merged={};orphans=0
        for row in _all_pages(token,'act_'+ACCOUNT+'/insights',delivery,deadline):
            ad=row.get('ad_id') if isinstance(row,dict) else None
            if not isinstance(ad,str) or not ad.isdigit() or ad in merged:raise ValueError('History delivery identity invalid')
            merged[ad]={**row,'actions_present':False}
        for row in _all_pages(token,'act_'+ACCOUNT+'/insights',actions,deadline):
            ad=row.get('ad_id') if isinstance(row,dict) else None
            if not isinstance(ad,str) or not ad.isdigit():raise ValueError('History actions identity invalid')
            if ad not in merged:
                # Attributed actions in the window for an ad with no delivery row there; kept out, counted.
                orphans+=1;continue
            if merged[ad]['actions_present']:raise ValueError('History actions identity invalid')
            merged[ad].update({k:row[k] for k in ('actions','action_values') if k in row},actions_present=True)
        windows[key]={'request':{k:v for k,v in delivery.items() if k!='fields'},'rows':list(merged.values()),'complete':True,
                      'actions_without_delivery':orphans}
    def spend(row):
        try:return float(row.get('spend') or 0)
        except (TypeError,ValueError):return 0
    wanted=[r['ad_id'] for r in sorted(windows['all']['rows'],key=spend,reverse=True) if spend(r)>=min_creative_spend][:max_creatives]
    creatives={};failed=0
    for start in range(0,len(wanted),50):
        chunk=wanted[start:start+50]
        request={'fields':HISTORY_CREATIVE_FIELDS,'filtering':json.dumps([{'field':'id','operator':'IN','value':chunk}]),
                 'effective_status':json.dumps(AD_STATUSES),'limit':'50'}
        try:
            for ad in _all_pages(token,'act_'+ACCOUNT+'/ads',request,deadline,max_pages=3):
                if isinstance(ad,dict) and ad.get('id') in chunk and isinstance(ad.get('creative'),dict):
                    creatives[ad['id']]={'name':ad.get('name'),'effective_status':ad.get('effective_status'),**ad['creative']}
        except (ValueError,KeyError,TypeError,OSError):failed+=len(chunk)
    return {'verified':True,'account_id':ACCOUNT,'checked_at':datetime.now(timezone.utc).isoformat(),'today':today.isoformat(),
            'windows':windows,'creatives':creatives,'creatives_requested':len(wanted),'creatives_failed':failed,
            'query_settings':{'time_increment':'all_days','use_unified_attribution_setting':True,'action_report_time':'default_unchanged'},
            'source':'direct_meta_api','live_action_allowed':False}


def enrich_audience_frequency(token, active, today, deadline):
    """Seven complete account-local days, aggregate ad-set frequency, GET only."""
    if not active.get('ads'):return
    since=(today-timedelta(days=7)).isoformat();until=(today-timedelta(days=1)).isoformat()
    try:
        rows=_all_pages(token,'act_'+ACCOUNT+'/insights',{
            'level':'adset','fields':'adset_id,frequency,reach,date_start,date_stop',
            'time_range':json.dumps({'since':since,'until':until}),
            'time_increment':'all_days','limit':500},deadline)
        by_id={}
        for row in rows:
            sid=row.get('adset_id')
            if not sid or sid in by_id or row.get('date_start')!=since or row.get('date_stop')!=until:
                raise ValueError('Frequency binding mismatch')
            by_id[sid]=row
        for ad in active.get('ads') or []:
            adset=ad['adset'];row=by_id.get(adset['id']) or {}
            adset['audience_frequency_7d']={
                'frequency':row.get('frequency'),'reach':row.get('reach'),
                'since':since,'until':until}
    except (ValueError,KeyError,TypeError,OSError):
        pass
