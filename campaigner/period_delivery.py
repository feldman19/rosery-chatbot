"""Independent exact-window delivery totals; never backfill missing daily rows."""
import hashlib,json,time
from datetime import datetime,timezone,timedelta

def digest(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def fields(level):return (['ad_id','adset_id'] if level=='ad' else [])+['campaign_id','date_start','date_stop','spend','impressions']
def params(level,start,end):return {'fields':','.join(fields(level)),'level':level,'time_range':json.dumps({'since':start,'until':end}),'time_increment':'all_days','use_unified_attribution_setting':'true','limit':'500'}
def read_period_delivery(graph,token,account,today,deadline,query_settings):
 result={'version':'exact_period_delivery_v1','account_id':account,'query_settings':dict(query_settings),'windows':{}}
 for days in (1,3,7):
  start=(today-timedelta(days=days)).isoformat();end=(today-timedelta(days=1)).isoformat();window={'since':start,'until':end}
  for level in ('ad','campaign'):
   request=params(level,start,end);query=dict(request);rows=[];cursors=set();began=datetime.now(timezone.utc).isoformat();complete=False
   for page in range(20):
    if time.monotonic()>deadline:raise ValueError('Period delivery deadline')
    response=graph(token,'act_'+account+'/insights',query);batch=response.get('data')
    if not isinstance(batch,list) or len(batch)>500:raise ValueError('Invalid period rows')
    rows.extend(batch);paging=response.get('paging') or {}
    if not paging.get('next'):complete=True;break
    cursor=(paging.get('cursors') or {}).get('after')
    if not isinstance(cursor,str) or not cursor or len(cursor)>4096 or cursor in cursors:raise ValueError('Invalid period pagination')
    cursors.add(cursor);query={**request,'after':cursor}
   if not complete:raise ValueError('Incomplete period pagination')
   window[level]={'request':request,'rows':rows,'rows_sha256':digest(rows),'complete':True,'started_at':began,'checked_at':datetime.now(timezone.utc).isoformat()}
  result['windows'][str(days)]=window
 result['checked_at']=datetime.now(timezone.utc).isoformat()
 return result

def validate_period_delivery(source,start,end,now,account,query_settings):
 try:
  if not isinstance(source,dict) or source.get('version')!='exact_period_delivery_v1' or source.get('account_id')!=account or source.get('query_settings')!=query_settings:return None
  outer=datetime.fromisoformat(source['checked_at'])
  if not outer.tzinfo or not 0<=(now-outer).total_seconds()<=900:return None
  days=(end-start).days+1
  if days not in (1,3,7):return None
  window=source['windows'][str(days)]
  if window['since']!=start.isoformat() or window['until']!=end.isoformat():return None
  output={};times=[]
  from decimal import Decimal
  for level in ('ad','campaign'):
   receipt=window[level];request=receipt['request'];expected=params(level,start.isoformat(),end.isoformat())
   if request!=expected or receipt.get('complete') is not True:return None
   began=datetime.fromisoformat(receipt['started_at']);finished=datetime.fromisoformat(receipt['checked_at'])
   if not began.tzinfo or not finished.tzinfo or not began<=finished<=outer or (outer-began).total_seconds()>180:return None
   times.extend((began,finished));rows=receipt['rows']
   if not isinstance(rows,list) or len(rows)>10000 or digest(rows)!=receipt['rows_sha256']:return None
   index={}
   for row in rows:
    if not isinstance(row,dict) or row.get('account_id',account)!=account:return None
    if row.get('date_start')!=window['since'] or row.get('date_stop')!=window['until']:return None
    for key in ('campaign_id',)+(('ad_id','adset_id') if level=='ad' else ()):
     if not isinstance(row.get(key),str) or not row[key].isdigit():return None
    identity=row[level+'_id']
    if identity in index:return None
    spend=Decimal(str(row['spend']));impressions=Decimal(str(row['impressions']))
    if not spend.is_finite() or spend<0 or not impressions.is_finite() or impressions<0:return None
    index[identity]=row
   output[level]=index
  output['checked_at']=source['checked_at'];output['since']=window['since'];output['until']=window['until']
  return output
 except (ValueError,KeyError,TypeError,ArithmeticError):return None

def apply_period_delivery(data,campaigns,drilldown,start,end,now,account,metrics=None):
 from decimal import Decimal
 period=validate_period_delivery(data.get('period_delivery'),start,end,now,account,data.get('query_settings'))
 if period is None:return
 sums={}
 for row in period['ad'].values():sums[row['campaign_id']]=sums.get(row['campaign_id'],Decimal(0))+Decimal(row['spend'])
 reconciled={cid for cid,row in period['campaign'].items() if cid in sums and sums[cid]==Decimal(row['spend'])}
 # Capture known daily lower bounds BEFORE period adoption mutates display rows.
 # An incomplete aggregate is not permission to discard its known components.
 coverage_conflict=False;daily_bounds={};campaign_bounds={};hierarchy_conflicts=set()
 for row in data.get('rows',[]):
  if not isinstance(row,dict) or not start.isoformat()<=str(row.get('date_start',''))<=end.isoformat():continue
  bound=period['ad'].get(row.get('ad_id'))
  if bound is not None and (bound['campaign_id']!=row.get('campaign_id') or row.get('adset_id') is not None and bound['adset_id']!=row['adset_id']):
   coverage_conflict=True;hierarchy_conflicts.add(row['ad_id'])
  if row.get('spend') is None:continue
  try:
   value=Decimal(str(row['spend']))
   if not value.is_finite() or value<0:raise ValueError('Invalid daily spend')
   aid=row['ad_id'];cid=row['campaign_id']
   daily_bounds[aid]=daily_bounds.get(aid,Decimal(0))+value
   campaign_bounds[cid]=campaign_bounds.get(cid,Decimal(0))+value
   if value>0 and (aid not in period['ad'] or cid not in period['campaign']):coverage_conflict=True
  except (ValueError,KeyError,ArithmeticError):coverage_conflict=True
 for kind,items,bounds in (('ad',drilldown['ads'],daily_bounds),('campaign',campaigns,campaign_bounds)):
  for identity,lower in bounds.items():
   source=period[kind].get(identity)
   if lower>0 and (source is None or lower>Decimal(source['spend'])):coverage_conflict=True
  for item in items:
   prior=item.get('spend')
   if prior is not None and Decimal(str(prior))>0 and item['id'] not in period[kind]:coverage_conflict=True
 for kind,items in (('campaign',campaigns),('ad',drilldown['ads'])):
  for item in items:
   source=period[kind].get(item['id'])
   if source is None:continue
   cid=source['campaign_id']
   if kind=='ad' and (item['id'] in hierarchy_conflicts or item.get('campaign_id')!=cid or item.get('adset_id')!=source['adset_id']):
    coverage_conflict=True
    item['reported_spend_unreconciled']=item.get('spend');item['spend']=None;item['roas']=None;item['cost_per_registration']=None
    item['efficiency_integrity']={'status':'blocked','reason':'daily_period_hierarchy_conflict'};continue
   if (item.get('efficiency_integrity') or {}).get('status')=='blocked':continue
   lower=(daily_bounds if kind=='ad' else campaign_bounds).get(item['id'],Decimal(0))
   if lower>Decimal(source['spend']):
    item['reported_spend_unreconciled']=float(lower);item['spend']=None;item['roas']=None;item['cost_per_registration']=None
    item['efficiency_integrity']={'status':'blocked','reason':'period_below_daily_lower_bound'};continue
   if cid in sums and cid in period['campaign'] and cid not in reconciled:
    item['reported_spend_unreconciled']=float(Decimal(source['spend']));item['spend']=None;item['roas']=None;item['cost_per_registration']=None
    item['efficiency_integrity']={'status':'blocked','reason':'period_cross_level_delivery_conflict'};continue
   value=Decimal(source['spend']);prior=item.get('spend')
   if prior is not None and Decimal(str(prior))!=value:
    item['reported_spend_unreconciled']=prior;item['spend']=None;item['roas']=None;item['cost_per_registration']=None
    item['efficiency_integrity']={'status':'blocked','reason':'daily_period_delivery_conflict'};continue
   item['spend']=float(value);item.setdefault('metrics_sources',{})['spend']='exact_period_delivery'
   item['delivery_evidence']={'source':'exact_period_delivery','since':period['since'],'until':period['until'],'checked_at':period['checked_at'],'cross_level_reconciled':cid in reconciled,'daily_missing_rows_filled':False}
   item['efficiency_integrity']={'status':'reconciled' if cid in reconciled else 'comparison_unavailable','reason':'exact_period_delivery','cross_level_reconciled':cid in reconciled}
   revenue=item.get('attributed_revenue');registrations=item.get('registrations')
   item['roas']=float(Decimal(str(revenue))/value) if cid in reconciled and value>0 and revenue is not None else None
   item['cost_per_registration']=float(value/Decimal(str(registrations))) if cid in reconciled and item.get('result_type')=='registration' and registrations is not None and registrations>0 else None
 for item in drilldown['adsets']:
  members=[a for a in drilldown['ads'] if a.get('adset_id')==item['id']];provided=[a for a in period['ad'].values() if a['adset_id']==item['id']]
  if not members or {a['id'] for a in members}!={a['ad_id'] for a in provided}:continue
  if any(a.get('spend') is None or (a.get('delivery_evidence') or {}).get('cross_level_reconciled') is not True for a in members):continue
  if (item.get('efficiency_integrity') or {}).get('status')=='blocked':continue
  value=sum((Decimal(str(a['spend'])) for a in members),Decimal(0));prior=item.get('spend')
  if prior is not None and Decimal(str(prior))!=value:
   item['reported_spend_unreconciled']=prior;item['spend']=None;item['roas']=None;item['cost_per_registration']=None
   item['efficiency_integrity']={'status':'blocked','reason':'daily_period_delivery_conflict'};continue
  item['spend']=float(value)
  item.setdefault('metrics_sources',{})['spend']='exact_period_delivery_ad_sum'
  item['delivery_evidence']={'source':'exact_period_delivery_ad_sum','since':period['since'],'until':period['until'],'checked_at':period['checked_at'],'cross_level_reconciled':True,'daily_missing_rows_filled':False}
  item['efficiency_integrity']={'status':'reconciled','reason':'exact_period_delivery','cross_level_reconciled':True}
  revenue=item.get('attributed_revenue');registrations=item.get('registrations')
  item['roas']=float(Decimal(str(revenue))/value) if value>0 and revenue is not None else None
  item['cost_per_registration']=float(value/Decimal(str(registrations))) if item.get('result_type')=='registration' and registrations is not None and registrations>0 else None
 # Both requests are complete, unfiltered account-wide period queries. Their
 # explicit populations and sums must agree before exposing a whole-account
 # delivery total. No assertion is made about omitted entity/day zero values.
 if metrics is not None and period['campaign'] and set(sums)==set(period['campaign']) and reconciled==set(period['campaign']):
  if coverage_conflict:
   metrics['spend']=None;metrics['roas']=None;metrics['cost_per_registration']=None;metrics.pop('spend_evidence',None)
   metrics['spend_integrity']={'status':'blocked','reason':'period_population_or_daily_lower_bound_conflict'};return
  if any((item.get('efficiency_integrity') or {}).get('status')=='blocked' for item in campaigns+drilldown['ads']+drilldown['adsets']):return
  total=sum((Decimal(row['spend']) for row in period['campaign'].values()),Decimal(0))
  prior=metrics.get('spend')
  if prior is not None and Decimal(str(prior))!=total:
   metrics['reported_spend_unreconciled']=prior;metrics['spend']=None;metrics['roas']=None;metrics['cost_per_registration']=None
   metrics['spend_integrity']={'status':'blocked','reason':'daily_period_delivery_conflict'};return
  metrics['spend']=float(total)
  metrics['spend_evidence']={'source':'exact_period_delivery','scope':'whole_account_complete_provider_delivery_population','since':period['since'],'until':period['until'],'checked_at':period['checked_at'],'cross_level_reconciled':True,'daily_missing_rows_filled':False}
