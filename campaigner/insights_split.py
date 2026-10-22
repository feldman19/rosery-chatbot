"""Read delivery and action fields separately, with exact entity/day binding.

Live evidence: outputs/rosery-live-spend-query-isolation-2026-10-21.json.
This is a scoped workaround for observed mixed-field responses, not an assertion
about Meta's internal cause. Missing stream membership remains unknown, not zero.
"""
import hashlib,json,time
from datetime import datetime,timezone

DELIVERY=('spend','impressions')
ACTIONS=('inline_link_clicks','actions','action_values')


def _identity_fields(level):
    if level not in ('ad','campaign'):raise ValueError('Unsupported insights level')
    return (['ad_id','ad_name','adset_id','adset_name'] if level=='ad' else [])+['campaign_id','campaign_name','date_start','date_stop']


def _index_rows(rows,account,since,until,level,kind):
    start=datetime.strptime(since,'%Y-%m-%d').date();end=datetime.strptime(until,'%Y-%m-%d').date()
    if not start<=end or (end-start).days>8:raise ValueError('Invalid split date scope')
    if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Invalid stream rows')
    entity=level+'_id';fields=_identity_fields(level)+list(DELIVERY if kind=='delivery' else ACTIONS);index={}
    for row in rows:
        if not isinstance(row,dict) or row.get('account_id',account)!=account:raise ValueError('Split account mismatch')
        if any(not isinstance(row.get(k),str) or not row[k].isdigit() for k in (entity,'campaign_id')+ (('adset_id',) if level=='ad' else ())):raise ValueError('Split identity missing')
        day=datetime.strptime(row['date_start'],'%Y-%m-%d').date()
        if row.get('date_stop')!=row['date_start'] or not start<=day<=end:raise ValueError('Split daily grain mismatch')
        key=(row[entity],row['date_start'],row['date_stop'])
        if key in index:raise ValueError('Duplicate split grain')
        index[key]={k:row[k] for k in fields if k in row}
    return index


def _merge_streams(streams,account,since,until,level):
    indexes={kind:_index_rows(streams[kind]['rows'],account,since,until,level,kind) for kind in ('delivery','actions')}
    delivery,actions=indexes['delivery'],indexes['actions'];merged=[];identity=_identity_fields(level)
    for key in sorted(set(delivery)|set(actions)):
        d,a=delivery.get(key),actions.get(key)
        if d is not None and a is not None:
            if any(d.get(k)!=a.get(k) for k in ('campaign_id',)+(('adset_id',) if level=='ad' else ())):raise ValueError('Split hierarchy changed')
        row={k:v for k,v in (d if d is not None else a).items() if k in identity}
        if d is not None:row.update({k:d[k] for k in DELIVERY if k in d})
        if a is not None:row.update({k:a[k] for k in ACTIONS if k in a})
        row['metric_sources']={'delivery_present':d is not None,'actions_present':a is not None};merged.append(row)
    return merged


def split_evidence_valid(data,level):
    if data.get('metric_collection') is None:return True
    if data.get('metric_collection')!='split_delivery_actions_v1':return False
    streams=data.get('metric_streams')
    if not isinstance(streams,dict) or set(streams)!={'delivery','actions'}:return False
    merged=data.get('rows')
    if not isinstance(merged,list) or len(merged)>20000:return False
    if hashlib.sha256(json.dumps(merged,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=data.get('metric_rows_sha256'):return False
    try:
        outer=datetime.fromisoformat(data['checked_at'])
        if outer.tzinfo is None:return False
        if _merge_streams(streams,data['account_id'],data['since'],data['until'],level)!=merged:return False
    except (ValueError,KeyError,TypeError):return False
    times=[]
    for kind,stream in streams.items():
        if not isinstance(stream,dict) or stream.get('complete') is not True:return False
        if any(stream.get(k)!=data.get(k) for k in ('account_id','since','until','query_settings')) or stream.get('level')!=level:return False
        request=stream.get('request') or {}
        try:
            if set(request)!={'fields','level','time_range','time_increment','use_unified_attribution_setting','limit'}:return False
            if json.loads(request['time_range'])!={'since':data['since'],'until':data['until']}:return False
            if request['level']!=level or request['time_increment']!='1' or request['use_unified_attribution_setting']!='true':return False
            if request['limit']!='500':return False
            fields=request['fields'].split(',')
            expected=_identity_fields(level)+list(DELIVERY if kind=='delivery' else ACTIONS)
            if len(fields)!=len(expected) or set(fields)!=set(expected):return False
            rows=stream['rows']
            if not isinstance(rows,list) or len(rows)>10000:return False
            if hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=stream['rows_sha256']:return False
            if datetime.fromisoformat(stream['started_at'])>datetime.fromisoformat(stream['checked_at']):return False
            for field in ('started_at','checked_at'):
                stamp=datetime.fromisoformat(stream[field])
                if stamp.tzinfo is None:return False
                if not 0<=(outer-stamp).total_seconds()<=180:return False
                times.append(stamp)
        except (ValueError,KeyError,TypeError):return False
    return bool(times and 0<=(max(times)-min(times)).total_seconds()<=180)


def read_split_insights(graph,token,account,since,until,level,deadline,query_settings):
    if level not in ('ad','campaign'):raise ValueError('Unsupported insights level')
    start=datetime.strptime(since,'%Y-%m-%d').date();end=datetime.strptime(until,'%Y-%m-%d').date()
    if not start<=end or (end-start).days>8:raise ValueError('Bounded daily insights required')
    entity=level+'_id'
    identity_fields=['campaign_id','campaign_name','date_start','date_stop']
    if level=='ad':identity_fields=['ad_id','ad_name','campaign_id','campaign_name','adset_id','adset_name','date_start','date_stop']
    streams={}
    for kind,metrics in (('delivery',DELIVERY),('actions',ACTIONS)):
        fields=identity_fields+list(metrics)
        params={'fields':','.join(fields),'level':level,'time_range':json.dumps({'since':since,'until':until}),'time_increment':'1','use_unified_attribution_setting':'true','limit':'500'}
        requested=dict(params);checked_start=datetime.now(timezone.utc).isoformat();rows={};cursors=set();complete=False
        for _ in range(20):
            if time.monotonic()>deadline:raise ValueError('Split report deadline exceeded')
            response=graph(token,'act_'+account+'/insights',dict(params));batch=response.get('data')
            if not isinstance(batch,list) or len(batch)>500:raise ValueError('Invalid split report rows')
            for original in batch:
                if not isinstance(original,dict) or original.get('account_id',account)!=account:raise ValueError('Split report account mismatch')
                if any(not isinstance(original.get(k),str) or not original[k].isdigit() for k in (entity,'campaign_id')):raise ValueError('Split report entity missing')
                day=datetime.strptime(original['date_start'],'%Y-%m-%d').date()
                if original.get('date_stop')!=original['date_start'] or not start<=day<=end:raise ValueError('Split report daily grain mismatch')
                key=(original[entity],original['date_start'],original['date_stop'])
                if key in rows:raise ValueError('Duplicate split report grain')
                rows[key]={k:original[k] for k in fields if k in original}
            paging=response.get('paging') or {}
            if not paging.get('next'):complete=True;break
            cursor=(paging.get('cursors') or {}).get('after')
            if not isinstance(cursor,str) or not cursor or len(cursor)>4096 or cursor in cursors:raise ValueError('Invalid split report paging')
            cursors.add(cursor);params['after']=cursor
        if not complete:raise ValueError('Incomplete split report pagination')
        streams[kind]={'index':rows,'rows':list(rows.values()),'request':requested,'checked_at':datetime.now(timezone.utc).isoformat(),'started_at':checked_start,'complete':True,'account_id':account,'level':level,'since':since,'until':until,'query_settings':dict(query_settings)}
    merged=_merge_streams(streams,account,since,until,level)
    provenance={}
    for kind,stream in streams.items():
        stream.pop('index');stream['rows_sha256']=hashlib.sha256(json.dumps(stream['rows'],sort_keys=True,separators=(',',':')).encode()).hexdigest();provenance[kind]=stream
    return {'rows':merged,'complete':True,'metric_streams':provenance,'metric_collection':'split_delivery_actions_v1','metric_rows_sha256':hashlib.sha256(json.dumps(merged,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
