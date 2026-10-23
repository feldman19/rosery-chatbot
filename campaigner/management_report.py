"""Pure full-day management report; no API, credentials or recommendations.

Meta is ad/day grain. Identical duplicate rows collapse; conflicting duplicates
or ranged rows invalidate that window. For overlapping action types choose the
first PRESENT canonical type, never add pixel/omni synonyms. Explicit empty
actions on complete reporting mean zero events; absent/malformed actions mean
unknown. Revenue is reported platform attribution, not incremental revenue or
Shopify revenue. Current inventory status/budget is never historical status.
Shopify all-channel totals require every exact calendar day once, valid fields,
verified fixed shop/currency and declared date coverage. Sparse reports are not
silently filled with zeros. Monetary metrics are major currency units.
"""
from datetime import datetime,date,timedelta,timezone
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
import json

ACCOUNT='393059510164294';SHOP='21cc82-3.myshopify.com'
PURCHASE=('offsite_conversion.fb_pixel_purchase','omni_purchase')
REGISTRATION=('offsite_conversion.fb_pixel_complete_registration','omni_complete_registration','complete_registration')
METRICS=('spend','landing_page_views','purchases','attributed_revenue','roas','registrations','cost_per_registration','store_orders','store_revenue')

def _stamp(value):
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        return stamp if stamp.tzinfo else None
    except (AttributeError,TypeError,ValueError):return None
def _date(value):
    try:return date.fromisoformat(value) if isinstance(value,str) and len(value)==10 else None
    except ValueError:return None
def _number(value,negative=False):
    if isinstance(value,bool) or value is None:return None
    try:
        result=Decimal(str(value));return result if result.is_finite() and result.copy_abs()<=Decimal('1e15') and (negative or result>=0) else None
    except (InvalidOperation,ValueError,TypeError):return None
def _public(value):return float(value) if value is not None else None
def _sum(values):return sum(values,Decimal(0)) if all(x is not None for x in values) else None
def _event(row,field,types):
    # Meta omits empty action lists. Only a row confirmed present in the actions
    # stream turns that omission into zero; otherwise absence stays unknown.
    if field not in row and field in ('actions','action_values') and (row.get('metric_sources') or {}).get('actions_present') is True:return Decimal(0)
    if field not in row or not isinstance(row[field],list):return None
    selected={}
    for action in row[field]:
        if not isinstance(action,dict) or not isinstance(action.get('action_type'),str):return None
        kind=action['action_type']
        if kind not in types:continue
        value=_number(action.get('value'))
        if value is None or kind in selected and selected[kind]!=value:return None
        selected[kind]=value
    return next((selected[kind] for kind in types if kind in selected),Decimal(0))
def _ratio(value,count):return value/count if value is not None and count is not None and count>0 else None
def _fresh(source,now):
    at=_stamp(source.get('last_success_at'))
    return bool(at and 0<=(now-at).total_seconds()<=900 and not source.get('error'))
def _coverage(data,start,end):
    since=_date(data.get('since'));until=_date(data.get('until'))
    return bool(since and until and since<=start<=end<=until)

def _inventory(data):
    inventory=data.get('account_campaigns') or {}
    if inventory.get('verified') is not True or inventory.get('complete') is not True or inventory.get('account_id')!=ACCOUNT:return {},None
    rows=inventory.get('campaigns');result={}
    if not isinstance(rows,list):return {},None
    for row in rows:
        if not isinstance(row,dict) or row.get('account_id')!=ACCOUNT or not isinstance(row.get('id'),str) or row['id'] in result:return {},None
        result[row['id']]=row
    return result,inventory.get('checked_at')

def _registration_campaigns(data):
    inventory=data.get('inventory') or {};campaign=inventory.get('campaign') or {};sets=inventory.get('adsets')
    if inventory.get('verified') is True and campaign.get('account_id')==ACCOUNT and isinstance(sets,list) and sets and all((s.get('promoted_object') or {}).get('custom_event_type')=='COMPLETE_REGISTRATION' for s in sets if isinstance(s,dict)) and all(isinstance(s,dict) for s in sets) and campaign.get('objective') in ('OUTCOME_LEADS','LEAD_GENERATION'):
        return {campaign.get('id')}
    return set()

def _meta_rows(data,start,end):
    from insights_split import split_evidence_valid
    if not split_evidence_valid(data,'ad'):return None,'מקורות מדדי החשיפה והפעולות אינם תואמים; נדרש רענון'
    rows=data.get('rows')
    if not isinstance(rows,list):return None,'אין טבלת דיווח תקינה'
    selected={}
    for row in rows:
        if not isinstance(row,dict):return None,'שורת דיווח אינה תקינה'
        day=_date(row.get('date_start'));stop=_date(row.get('date_stop'))
        if day is None or stop is None:return None,'חסר תאריך דיווח תקין'
        if stop<day:return None,'טווח תאריכי דיווח הפוך'
        if day>end or stop<start:continue
        if day!=stop:return None,'נמצאה שורה שמערבבת כמה ימים; החלון אינו מלא'
        if not start<=day<=end:continue
        if not isinstance(row.get('ad_id'),str) or not row['ad_id'] or not isinstance(row.get('campaign_id'),str) or not row['campaign_id']:return None,'חסר מזהה מודעה או קמפיין בדיווח'
        key=(row['ad_id'],day.isoformat());raw=json.dumps(row,sort_keys=True,separators=(',',':'))
        if key in selected and selected[key][0]!=raw:return None,'נמצאו נתונים סותרים לאותה מודעה ויום'
        selected[key]=(raw,row)
    return [item[1] for item in selected.values()],None

def _aggregate(rows):
    purchases=[_event(row,'actions',PURCHASE) for row in rows]
    revenue=[]
    for row,count in zip(rows,purchases):
        value=_event(row,'action_values',PURCHASE)
        if value==0 and count is not None and count>0 and not any(isinstance(x,dict) and x.get('action_type') in PURCHASE for x in row.get('action_values',[])):value=None
        revenue.append(value)
    values={'spend':_sum([_number(row.get('spend')) for row in rows]),'landing_page_views':_sum([_event(row,'actions',('landing_page_view',)) for row in rows]),'purchases':_sum(purchases),'registrations':_sum([_event(row,'actions',REGISTRATION) for row in rows]),'attributed_revenue':_sum(revenue)}
    values['roas']=_ratio(values['attributed_revenue'],values['spend']);return values

ADD_TO_CART=('offsite_conversion.fb_pixel_add_to_cart','omni_add_to_cart')
CHECKOUT=('offsite_conversion.fb_pixel_initiate_checkout','omni_initiated_checkout')

def _funnel(rows):
    """Diagnostic funnel signals; missing in any row means unknown, never zero."""
    clicks=_sum([_number(row.get('inline_link_clicks')) for row in rows]);impressions=_sum([_number(row.get('impressions')) for row in rows])
    values={'add_to_cart':_sum([_event(row,'actions',ADD_TO_CART) for row in rows]),'initiate_checkout':_sum([_event(row,'actions',CHECKOUT) for row in rows]),'link_clicks':clicks,'impressions':impressions,'link_ctr':_ratio(clicks,impressions)}
    return {key:_public(value) for key,value in values.items()}

def _observed_results(rows):
    """Partial attributed-event observations, NEVER replacements for totals."""
    result={}
    for key,types in (('purchases',PURCHASE),('registrations',REGISTRATION)):
        values=[_event(row,'actions',types) for row in rows];known=[value for value in values if value is not None];missing=len(values)-len(known)
        result[key]={'value':_public(sum(known,Decimal(0))) if known or not rows else None,'known_rows':len(known),'missing_rows':missing,'complete':missing==0,'semantics':'attributed_events_in_known_rows_not_unique_people_or_orders'}
    return result

def _row_name(rows,key,fallback=None):
    ordered=sorted(rows,key=lambda row:(row.get('date_start',''),row.get('ad_id','')),reverse=True)
    return next((row[key] for row in ordered if isinstance(row.get(key),str) and row[key]),fallback)

def entity_update_evidence(current, identity, kind, source_path, observed_at):
    """An entity update is not a typed budget/creative change or maturity clock."""
    raw = current.get('updated_time') if current.get('id') == identity else None
    stamp, observed = _stamp(raw), _stamp(observed_at)
    valid = stamp is not None and observed is not None and stamp <= observed
    return {
        'status': 'entity_update_observed' if valid else 'invalid_entity_timestamp' if raw else 'not_collected',
        'entity_id': identity, 'entity_type': kind, 'source_path': source_path,
        'source_field': 'updated_time', 'updated_time': raw if isinstance(raw, str) else None,
        'observed_at': observed_at, 'scope': 'entity_update_only',
        'relevant_change_time': None, 'relevant_change_status': 'unknown',
        'attribution_maturity_proven': False,
        'limitation': 'חותמת עדכון הישות אינה מזהה מה השתנה; זמן שינוי תקציב או קריאייטיב והבשלת ייחוס עדיין לא אומתו.',
    }


def _detail_inventory(data):
    """Merge verified edges; provenance stays per entity, active scope is not all ads."""
    source=data.get('inventory') or {};campaign=source.get('campaign') or {};sets={};ads={}
    if source.get('verified') is True and campaign.get('account_id')==ACCOUNT and isinstance(campaign.get('id'),str):
        for edge,target in (('adsets',sets),('ads',ads)):
            rows=source.get(edge)
            if not isinstance(rows,list):continue
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('id'),str) or row['id'] in target or row.get('account_id',ACCOUNT)!=ACCOUNT or row.get('campaign_id',campaign['id'])!=campaign['id']:target.clear();break
                target[row['id']]={**row,'campaign_id':campaign['id'],'_observed_at':data.get('checked_at'),'_source_path':'sources.meta.data.inventory.'+edge}
    active=data.get('active_ads') or {};stamp=_stamp(active.get('checked_at'))
    if active.get('verified') is True and active.get('complete') is True and active.get('account_id')==ACCOUNT and stamp and isinstance(active.get('ads'),list):
        candidates={'ads':{},'adsets':{}};invalid=False
        for ad in active['ads']:
            if not isinstance(ad,dict):invalid=True;break
            parent=ad.get('campaign') or {};adset=ad.get('adset') or {};cid=parent.get('id');sid=adset.get('id')
            if ad.get('account_id')!=ACCOUNT or not all(isinstance(x,str) and x for x in (ad.get('id'),cid,sid)) or parent.get('account_id',ACCOUNT)!=ACCOUNT or adset.get('account_id',ACCOUNT)!=ACCOUNT or ad.get('campaign_id',cid)!=cid or ad.get('adset_id',sid)!=sid or adset.get('campaign_id',cid)!=cid:invalid=True;break
            for kind,row in (('ads',{**ad,'campaign_id':cid,'adset_id':sid}),('adsets',{**adset,'campaign_id':cid})):
                previous=candidates[kind].get(row['id'])
                if previous is not None and previous!=row:invalid=True;break
                candidates[kind][row['id']]=row
            if invalid:break
        if not invalid:
            for kind,target in (('ads',ads),('adsets',sets)):
                for identity,row in candidates[kind].items():
                    previous=target.get(identity)
                    if previous and any(previous.get(k)!=row.get(k) for k in (('campaign_id','adset_id') if kind=='ads' else ('campaign_id',))):
                        target.pop(identity,None);continue
                    prior_stamp=_stamp(previous.get('_observed_at')) if previous else None
                    if prior_stamp and prior_stamp>=stamp:continue
                    target[identity]={**row,'_observed_at':active['checked_at'],'_source_path':'sources.meta.data.active_ads.ads'+('.adset' if kind=='adsets' else '')}
    return sets,ads,data.get('checked_at')

def _active_ad_ids(data):
    """Ids in a verified, complete active-ad read, or None. An ad with delivery that is absent from it is not active now."""
    active=data.get('active_ads') or {};ads=active.get('ads')
    if active.get('verified') is not True or active.get('complete') is not True or active.get('account_id')!=ACCOUNT or not isinstance(ads,list):return None
    if not all(isinstance(ad,dict) and isinstance(ad.get('id'),str) and ad['id'] for ad in ads):return None
    return {ad['id'] for ad in ads}

def _configuration_blockers(campaign=None,adset=None,ad=None,campaign_at=None,adset_at=None,ad_at=None):
    """Known switch settings only: no claim that ACTIVE can/will deliver."""
    blockers=[];observed={}
    for key,row,at in (('campaign_paused',campaign,campaign_at),('adset_paused',adset,adset_at),('ad_paused',ad,ad_at)):
        if isinstance(row,dict) and row.get('status')=='PAUSED':blockers.append(key);observed[key]=at
    return {'configured_delivery_blockers':blockers,'configured_delivery_blockers_observed_at':observed,'configured_delivery_blockers_scope':'verified_current_configuration_not_actual_delivery'}

def _campaign_comparison(data,rows,start,end,now):
    """Independent provider totals, never inferred zero from missing ad fields."""
    source=data.get('campaign_insights');empty={'values':None,'conflicts':{},'notes':[],'checked_at':None}
    if source is None:return empty
    failure={**empty,'notes':['דוח ההשוואה ברמת הקמפיין אינו מלא או אינו תואם; מדדי המודעות לא הושלמו באפס']}
    settings={'time_increment':'1','use_unified_attribution_setting':True,'action_report_time':'default_unchanged'}
    if not isinstance(source,dict) or source.get('verified') is not True or source.get('complete') is not True or source.get('account_id')!=ACCOUNT or source.get('query_settings')!=settings or data.get('query_settings')!=settings or source.get('since')!=data.get('since') or source.get('until')!=data.get('until') or not _coverage(source,start,end):return failure
    from insights_split import split_evidence_valid
    if not split_evidence_valid(source,'campaign') or not split_evidence_valid(data,'ad'):return failure
    if any(key in source and source[key] != data.get(key) for key in ('currency','timezone')):return failure
    if source.get('level','campaign')!='campaign' or data.get('level','ad')!='ad':return failure
    checked=_stamp(source.get('checked_at'));ad_checked=_stamp(data.get('checked_at'))
    if checked is None or ad_checked is None or checked>now or ad_checked>now or abs((checked-ad_checked).total_seconds())>180:return failure
    raw=source.get('rows')
    if not isinstance(raw,list) or len(raw)>10000:return failure
    selected={};seen=set()
    for row in raw:
        if not isinstance(row,dict) or not isinstance(row.get('campaign_id'),str) or not row['campaign_id'] or row.get('account_id',ACCOUNT)!=ACCOUNT:return failure
        day=_date(row.get('date_start'))
        if day is None or row.get('date_stop')!=row['date_start'] or not _date(source['since'])<=day<=_date(source['until']):return failure
        key=(row['campaign_id'],row['date_start'])
        if key in seen:return failure
        seen.add(key)
        if start<=day<=end:selected[key]=row
    ad_days={}
    for row in rows:ad_days.setdefault((row['campaign_id'],row['date_start']),[]).append(row)
    if set(selected)!=set(ad_days):return failure
    grouped={};conflicts={};notes=[];unavailable=set()
    for key,ad_rows in ad_days.items():
        cid=key[0];row=selected[key];ad_values=_aggregate(ad_rows);campaign_values=_aggregate([row]);grouped.setdefault(cid,[]).append(row)
        if ad_values['spend'] is None or campaign_values['spend'] is None:
            if data.get('metric_collection')==source.get('metric_collection')=='split_delivery_actions_v1':
                unavailable.add(cid);continue
            return failure
        if ad_values['spend']!=campaign_values['spend']:conflicts.setdefault(cid,set()).add('spend')
        for field in ('purchases','registrations','landing_page_views','attributed_revenue'):
            reported=campaign_values[field]
            if reported is None:continue
            known=[_aggregate([item])[field] for item in ad_rows];known=[value for value in known if value is not None]
            if ad_values[field] is not None and ad_values[field]!=reported or known and sum(known,Decimal(0))>reported:conflicts.setdefault(cid,set()).add(field)
    if any('spend' in fields for fields in conflicts.values()):notes.append('הוצאות דוח הקמפיין שונות מסכום המודעות באותו יום; ההוצאה שבמחלוקת נשארה לא זמינה')
    if any(fields-{'spend'} for fields in conflicts.values()):notes.append('נמצאה סתירה בין תוצאות הקמפיין לשורות המודעות; המדדים שבמחלוקת נשארו לא זמינים')
    if unavailable:notes.append('בחלק מהקמפיינים חסרות שורות מדדי חשיפה תואמות לפעולות; ההוצאה והיעילות שלהם נשארו לא זמינות')
    values={cid:_aggregate(own) for cid,own in grouped.items() if cid not in unavailable}
    return {'values':values,'conflicts':conflicts,'unavailable_campaigns':unavailable,'notes':notes,'checked_at':source['checked_at']}

def _apply_campaign_comparison(data,rows,metrics,campaigns,start,end,now):
    comparison=_campaign_comparison(data,rows,start,end,now);values=comparison['values'];sources={key:'ad_insights' for key in ('spend','landing_page_views','purchases','registrations','attributed_revenue')}
    for campaign in campaigns:campaign.update(metrics_source='ad_insights',metrics_sources=dict(sources))
    if values is None:return 'ad_insights',sources,comparison['notes']
    conflicts=comparison['conflicts'];spend_conflict=any('spend' in fields for fields in conflicts.values())
    for campaign in campaigns:
        cid=campaign['id'];candidate=values.get(cid);own_sources=dict(sources)
        if candidate is None:campaign['metrics_source']='ad_insights';campaign['metrics_sources']=own_sources;continue
        for field in sources:
            if field in conflicts.get(cid,set()):campaign[field]=None;own_sources[field]='conflict'
            elif not spend_conflict and candidate[field] is not None:campaign[field]=_public(candidate[field]);own_sources[field]='campaign_insights'
        campaign['roas']=_public(_ratio(_number(campaign['attributed_revenue']),_number(campaign['spend'])));campaign['cost_per_registration']=_public(_ratio(_number(campaign['spend']),_number(campaign['registrations']))) if campaign['result_type']=='registration' else None
        campaign['metrics_source']='conflict' if 'conflict' in own_sources.values() else 'campaign_insights' if 'campaign_insights' in own_sources.values() else 'ad_insights';campaign['metrics_sources']=own_sources;campaign['comparison_checked_at']=comparison['checked_at']
    for field in sources:
        if any(field in group for group in conflicts.values()):metrics[field]=None;sources[field]='conflict'
        elif not spend_conflict and not comparison.get('unavailable_campaigns'):
            complete=_sum([item[field] for item in values.values()])
            if complete is not None:metrics[field]=_public(complete);sources[field]='campaign_insights'
    metrics['roas']=_public(_ratio(_number(metrics['attributed_revenue']),_number(metrics['spend'])))
    source='conflict' if 'conflict' in sources.values() else 'campaign_insights' if 'campaign_insights' in sources.values() else 'ad_insights'
    notes=list(comparison['notes'])
    if source=='campaign_insights':notes.append('סיכום הקמפיינים נקרא בדוח Meta נפרד ותואם בהוצאה היומית; פירוט המודעות נשאר לפי המקור המקורי')
    return source,sources,notes

def _guard_comparison_efficiency(data, rows, metrics, campaigns, drilldown, start, end, now):
    """A disputed parent cannot leave apparently usable CPA in its children."""
    if data.get('campaign_insights') is None:
        return
    comparison = _campaign_comparison(data, rows, start, end, now)
    incompatible = comparison['values'] is None
    affected = set(comparison['conflicts']) | set(comparison.get('unavailable_campaigns',()))
    if not incompatible and not affected:
        return
    reason = 'comparison_not_comparable' if incompatible else 'cross_report_conflict'
    metrics['roas'] = None
    metrics['cost_per_registration'] = None
    for item in campaigns + drilldown['adsets'] + drilldown['ads']:
        cid = item.get('campaign_id', item['id'])
        if incompatible or cid in affected:
            if not incompatible and cid not in comparison['conflicts'] and cid in comparison.get('unavailable_campaigns',()):
                # Missing siblings prevent parent reconciliation, not observation
                # of this entity's own complete delivery rows.
                item['efficiency_integrity']={'status':'comparison_unavailable','reason':'split_source_coverage_missing','cross_level_reconciled':False}
                item['roas']=None
                item['cost_per_registration']=None
                continue
            item['efficiency_integrity'] = {'status': 'blocked', 'reason': 'split_source_coverage_missing' if cid in comparison.get('unavailable_campaigns',()) else reason}
            # Keep the observed value separately; downstream CPA consumers use spend.
            item['reported_spend_unreconciled'] = item.get('spend')
            item['spend'] = None
            item['roas'] = None
            item['cost_per_registration'] = None


def _drilldown(data,rows,campaign_inventory,reg_campaigns):
    sets_inventory,ads_inventory,observed_at=_detail_inventory(data);adset_groups={};ad_groups={};missing_sets=0
    source=data.get('inventory') or {};bound_campaign=source.get('campaign') or {}
    if source.get('verified') is not True or bound_campaign.get('account_id')!=ACCOUNT:bound_campaign={}
    account_inventory_at=(data.get('account_campaigns') or {}).get('checked_at');active_ids=_active_ad_ids(data)
    for row in rows:
        ad_groups.setdefault(row['ad_id'],[]).append(row)
        if isinstance(row.get('adset_id'),str) and row['adset_id']:adset_groups.setdefault(row['adset_id'],[]).append(row)
        else:missing_sets+=1
    output={'adsets':[],'ads':[]};notes=[]
    if missing_sets:notes.append('חסר מזהה קבוצת מודעות ב־'+str(missing_sets)+' שורות; פירוט קבוצות המודעות אינו מכסה אותן')
    for kind,groups,current_inventory in (('adsets',adset_groups,sets_inventory),('ads',ad_groups,ads_inventory)):
        for identity,own in sorted(groups.items()):
            parents=sorted({row['campaign_id'] for row in own});campaign_id=parents[0] if len(parents)==1 else None
            set_ids=sorted({row['adset_id'] for row in own if isinstance(row.get('adset_id'),str) and row['adset_id']});set_id=set_ids[0] if len(set_ids)==1 and all(isinstance(row.get('adset_id'),str) and row['adset_id'] for row in own) else None
            current=current_inventory.get(identity) or {}
            if current.get('campaign_id')!=campaign_id or kind=='ads' and current.get('adset_id')!=set_id:current={}
            parent_current=campaign_inventory.get(campaign_id) or (bound_campaign if bound_campaign.get('id')==campaign_id else {});parent_at=account_inventory_at if campaign_id in campaign_inventory else data.get('checked_at') if parent_current else None;objective=parent_current.get('objective');rtype='registration' if campaign_id in reg_campaigns else 'purchase' if objective in ('OUTCOME_SALES','SALES','PRODUCT_CATALOG_SALES') else None
            values=_aggregate(own);namefield='adset_name' if kind=='adsets' else 'ad_name';entry={'id':identity,'name':current.get('name') or _row_name(own,namefield,identity),'campaign_id':campaign_id,'campaign_name':(parent_current.get('name') or _row_name(own,'campaign_name')) if campaign_id else None,'adset_id':identity if kind=='adsets' else set_id,'adset_name':_row_name(own,'adset_name') if kind=='adsets' or set_id else None,'objective':objective,'result_type':rtype,**{key:_public(value) for key,value in values.items()},'observed_results':_observed_results(own),'cost_per_registration':_public(_ratio(values['spend'],values['registrations'])) if rtype=='registration' else None,'status':current.get('status'),'effective_status':current.get('effective_status'),'configured_active':current.get('status')=='ACTIVE' if current.get('status') in ('ACTIVE','PAUSED','ARCHIVED','DELETED') else None,'inventory_observed_at':observed_at if current else None,'status_scope':'current_configuration_not_period_history','hierarchy_complete':len(parents)==1 and (kind=='adsets' or set_id is not None)}
            entry['funnel'] = _funnel(own)
            entry['inventory_observed_at'] = current.get('_observed_at') if current else None
            entry['inventory_source_path'] = current.get('_source_path')
            if kind=='ads' and not current and active_ids is not None and identity not in active_ids:
                # Spent in the window but missing from the complete active read: paused or stopped since.
                entry['configured_active']=False;entry['status_note']='not_in_complete_active_inventory'
            entry['change_evidence'] = entity_update_evidence(current, identity, kind, current.get('_source_path') or 'sources.meta.data.inventory.' + kind, current.get('_observed_at'))
            if not entry['hierarchy_complete']:entry['parent_ids_observed']={'campaign_ids':parents,'adset_ids':set_ids}
            parent_set=current if kind=='adsets' else sets_inventory.get(set_id) or {}
            if parent_set.get('campaign_id')!=campaign_id:parent_set={}
            entry.update(_configuration_blockers(parent_current,parent_set,current if kind=='ads' else None,parent_at,parent_set.get('_observed_at') if parent_set else None,current.get('_observed_at') if current and kind=='ads' else None))
            if kind=='adsets':
                budget=_number(current.get('daily_budget'));lifetime=_number(current.get('lifetime_budget'));known=budget is not None and budget==budget.to_integral_value() and lifetime is not None
                entry.update(daily_budget_minor=int(budget) if known else None,budget_known=known)
            output[kind].append(entry)
    if any(not row['hierarchy_complete'] for row in output['ads']):notes.append('בחלק מהמודעות שיוך ההורה חסר או סותר; לא הומצא שיוך לקמפיין או לקבוצה')
    return output,notes

def _store(data,start,end,currency):
    if data.get('verified') is not True or data.get('shop')!=SHOP or data.get('currency')!=currency:return None,None,'נתוני החנות או המטבע לא אומתו'
    if not _coverage(data,start,end):return None,None,'דוח החנות אינו מכסה את כל תאריכי החלון'
    table=data.get('table') or {};rows=table.get('rows');columns=table.get('columns')
    if not isinstance(rows,list) or not isinstance(columns,list) or not {'day','orders','total_sales'}<={x.get('name') for x in columns if isinstance(x,dict)}:return None,None,'עמודות דוח החנות אינן מלאות'
    selected={}
    for row in rows:
        if not isinstance(row,dict):return None,None,'מבנה דוח החנות אינו נתמך'
        day=_date(row.get('day'))
        if day is None:continue  # Summary/footer rows are not calendar days.
        if not start<=day<=end:continue
        if day in selected:return None,None,'נמצאו כמה שורות חנות לאותו יום'
        orders=_number(row.get('orders'));sales=_number(row.get('total_sales'),negative=True)
        if orders is None or orders!=orders.to_integral_value() or sales is None:return None,None,'חסר נתון הזמנות או מכירות תקין בחנות'
        selected[day]=(orders,sales)
    expected={start+timedelta(days=i) for i in range((end-start).days+1)}
    if set(selected)!=expected:return None,None,'חסרים ימים בדוח החנות; לא הושלמו באפס'
    return _sum([x[0] for x in selected.values()]),_sum([x[1] for x in selected.values()]),None

def _daily(data,store,today,valid,currency):
    """One entry per day of the stored read (7 full days, then today so far), for the owner's day picker.
    Ad-row totals per day; a day not fully read stays unknown (None), never zero."""
    days=[]
    for back in range(7,-1,-1):
        day=today-timedelta(days=back);notes=[];metrics={key:None for key in ('spend','purchases','attributed_revenue','roas','landing_page_views')}
        funnel={key:None for key in ('link_clicks','impressions','link_ctr','add_to_cart','initiate_checkout')};ads=[]
        rows,reason=_meta_rows(data,day,day) if valid and _coverage(data,day,day) else (None,'הנתונים ממטא ליום הזה לא נקראו במלואם')
        if rows is None:
            notes.append(reason)
            # A partial, identity-bound read can still carry observed spend.
            # It is a lower bound, never an account total or a ROAS denominator.
            if data.get('verified') is True and data.get('account_id')==ACCOUNT and data.get('currency')==currency and data.get('today')==today.isoformat():
                observed,_=_meta_rows(data,day,day)
                known=[_number(row.get('spend')) for row in observed or [] if _number(row.get('spend')) is not None]
                if known:metrics['spend_lower_bound']=_public(_sum(known))
        else:
            metrics.update({key:_public(value) for key,value in _aggregate(rows).items() if key in metrics});funnel=_funnel(rows)
            # Action-only rows are not evidence of missing account delivery when
            # the independent delivery stream is verified complete.
            from insights_split import split_evidence_valid
            if data.get('metric_collection')=='split_delivery_actions_v1' and split_evidence_valid(data,'ad'):
                delivery=[row for row in rows if (row.get('metric_sources') or {}).get('delivery_present') is True]
                metrics['spend']=_public(_sum([_number(row.get('spend')) for row in delivery]))
                metrics['roas']=_public(_ratio(_number(metrics['attributed_revenue']),_number(metrics['spend'])))
            known=[_number(row.get('spend')) for row in rows if _number(row.get('spend')) is not None]
            if metrics['spend'] is None and known:metrics['spend_lower_bound']=_public(_sum(known))
            grouped={}
            for row in rows:grouped.setdefault(row['ad_id'],[]).append(row)
            for identity,own in grouped.items():
                values=_aggregate(own)
                if (values['spend'] or 0)>0:ads.append({'id':identity,'name':_row_name(own,'ad_name',identity),'spend':_public(values['spend']),'purchases':_public(values['purchases']),'attributed_revenue':_public(values['attributed_revenue']),'roas':_public(values['roas'])})
            ads.sort(key=lambda a:-(a['spend'] or 0))
        orders,sales,sreason=_store(store,day,day,currency)
        if sreason:notes.append(sreason)
        days.append({'date':day.isoformat(),'partial':back==0,'metrics':{**metrics,'store_orders':_public(orders),'store_revenue':_public(sales)},
                     'funnel':funnel,'ads':ads[:10],'ads_with_spend':len(ads),'notes':notes})
    return days

def build_report(live_snapshot,proposal_report=None,operations_status=None,now=None,change_history=None):
    now=now or datetime.now(timezone.utc)
    if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('Aware report clock required')
    snapshot=live_snapshot if isinstance(live_snapshot,dict) else {};sources=snapshot.get('sources') or {};meta=sources.get('meta') or {};shop=sources.get('shopify') or {};data=meta.get('data') or {};store=shop.get('data') or {}
    notes=[];meta_identity=data.get('verified') is True and data.get('account_id')==ACCOUNT and data.get('currency')=='ILS';valid=meta_identity and data.get('complete') is True
    currency='ILS';today=_date(data.get('today'))
    try:actual_today=now.astimezone(ZoneInfo(data['timezone'])).date()
    except (KeyError,TypeError,ValueError,ZoneInfoNotFoundError):actual_today=None
    if actual_today and today!=actual_today:today=actual_today;valid=False;notes.append('נדרש רענון Meta לאחר החלפת יום בחשבון')
    if today is None:today=actual_today or now.astimezone(timezone.utc).date();valid=False;notes.append('תאריך החשבון לא אומת')
    if not valid:notes.append('כיסוי Meta חסר או לא מאומת; נתון חסר אינו אפס')
    if not _fresh(meta,now):notes.append('נתוני Meta אינם מעודכנים לרבע השעה האחרונה')
    if not _fresh(shop,now):notes.append('נתוני Shopify אינם מעודכנים לרבע השעה האחרונה')
    if snapshot.get('collection_active') is False:notes.append('האיסוף האוטומטי אינו פעיל; מוצגים נתונים שמורים')
    store_valid=store.get('verified') is True and store.get('shop')==SHOP and store.get('currency')==currency
    if not store_valid:notes.append('מקור החנות או המטבע לא אומתו')
    meta_state='unavailable' if not meta_identity else 'stale' if not _fresh(meta,now) else 'fresh' if valid and _coverage(data,today-timedelta(days=7),today-timedelta(days=1)) else 'partial'
    store_coverage=_store(store,today-timedelta(days=7),today-timedelta(days=1),currency)[2] is None if store_valid else False
    shop_state='unavailable' if not store_valid else 'stale' if not _fresh(shop,now) else 'fresh' if store_coverage else 'partial'
    source_states={'meta':meta_state,'shopify':shop_state}
    freshness='stale' if meta_identity and store_valid and ('stale' in source_states.values() or snapshot.get('collection_active') is False) else 'fresh' if all(state=='fresh' for state in source_states.values()) and snapshot.get('collection_active') is not False else 'partial' if meta_identity or store_valid else 'unavailable'
    inventory,inventory_at=_inventory(data);reg_campaigns=_registration_campaigns(data);windows={}
    for days in (1,3,7):
        start=today-timedelta(days=days);end=today-timedelta(days=1);wnotes=[];metrics={key:None for key in METRICS};campaigns=[];drilldown={'adsets':[],'ads':[]};observed_results=None;metrics_source='ad_insights';metrics_sources={}
        rows,reason=_meta_rows(data,start,end) if valid and _coverage(data,start,end) else (None,'דיווח Meta אינו מכסה את כל הימים המלאים בחלון')
        if rows is None:wnotes.append(reason)
        else:
            aggregate=_aggregate(rows);metrics.update({key:_public(value) for key,value in aggregate.items()});grouped={}
            observed_results=_observed_results(rows);drilldown,detail_notes=_drilldown(data,rows,inventory,reg_campaigns);wnotes.extend(detail_notes)
            missing_actions=sum('actions' not in row for row in rows)
            if missing_actions:wnotes.append('שדה הפעולות חסר ב־'+str(missing_actions)+' שורות Meta; פירוט ההמרות במודעות אינו מלא ולא הושלם באפס')
            for row in rows:grouped.setdefault(row['campaign_id'],[]).append(row)
            for identity in sorted(set(grouped)|set(inventory)):
                own=grouped.get(identity,[]);current=inventory.get(identity) or {};values=_aggregate(own);objective=current.get('objective');rtype='registration' if identity in reg_campaigns else 'purchase' if objective in ('OUTCOME_SALES','SALES','PRODUCT_CATALOG_SALES') else None
                budget=_number(current.get('daily_budget'));lifetime=_number(current.get('lifetime_budget'));known=bool(current) and budget is not None and lifetime is not None
                daily=int(budget) if known and budget==budget.to_integral_value() else None
                campaigns.append({'id':identity,'name':current.get('name') or next((r.get('campaign_name') for r in own if r.get('campaign_name')),identity),'status':current.get('status'),'effective_status':current.get('effective_status'),'objective':objective,'result_type':rtype,**{key:_public(value) for key,value in values.items()},'observed_results':_observed_results(own),'cost_per_registration':_public(_ratio(values['spend'],values['registrations'])) if rtype=='registration' else None,'daily_budget_minor':daily,'budget_known':known,'configured_active':current.get('status')=='ACTIVE' if current.get('status') in ('ACTIVE','PAUSED','ARCHIVED','DELETED') else None,'inventory_observed_at':inventory_at,'status_scope':'current_configuration_not_period_history',**_configuration_blockers(campaign=current,campaign_at=inventory_at)})
            for campaign in campaigns:
                campaign['change_evidence'] = entity_update_evidence(inventory.get(campaign['id']) or {}, campaign['id'], 'campaigns', 'sources.meta.data.account_campaigns.campaigns', inventory_at)
            spenders={r['campaign_id'] for r in rows if (_number(r.get('spend')) or 0)>0}
            if spenders and spenders<=reg_campaigns:metrics['cost_per_registration']=_public(_ratio(aggregate['spend'],aggregate['registrations']))
            elif aggregate['registrations'] is not None and aggregate['registrations']>0:wnotes.append('עלות להרשמה כללית לא חושבה: ההוצאה מערבת יעדי קמפיין שונים או יעד שלא אומת')
            if any(value is None for key,value in aggregate.items() if key!='roas'):wnotes.append('חלק משדות דוח המודעות חסרים; פירוט המודעות אינו מלא')
            metrics_source,metrics_sources,comparison_notes=_apply_campaign_comparison(data,rows,metrics,campaigns,start,end,now);wnotes.extend(comparison_notes)
            if spenders and spenders<=reg_campaigns:metrics['cost_per_registration']=_public(_ratio(_number(metrics['spend']),_number(metrics['registrations'])))
            _guard_comparison_efficiency(data,rows,metrics,campaigns,drilldown,start,end,now)
            from period_delivery import apply_period_delivery
            apply_period_delivery(data,campaigns,drilldown,start,end,now,ACCOUNT,metrics)
            if metrics.get('spend_evidence'):metrics_sources['spend']='exact_period_delivery'
        for kind, entities in (('campaigns', campaigns), ('adsets', drilldown['adsets']), ('ads', drilldown['ads'])):
            for entity in entities:
                receipts = [event for event in (change_history or []) if isinstance(event,dict) and
                    ((kind=='ads' and event.get('entity_id')==entity['id']) or
                     (kind=='adsets' and event.get('adset_id')==entity['id']) or
                     (kind=='campaigns' and event.get('campaign_id')==entity['id']))]
                if receipts:
                    entity['change_evidence']['observed_creative_changes'] = receipts
                    entity['change_evidence']['history_coverage'] = 'selected_local_receipts_only'
        orders,sales,sreason=_store(store,start,end,currency);metrics['store_orders']=_public(orders);metrics['store_revenue']=_public(sales)
        if sreason:wnotes.append(sreason)
        wnotes.append('הכנסות Meta הן מיוחסות; מכירות החנות כוללות את כל הערוצים, לרבות דמי החלפה אם נרשמו, ואינן מצטרפות להכנסות Meta')
        windows[str(days)]={'since':start.isoformat(),'until':end.isoformat(),'days':days,'partial':False,'metrics':metrics,'metrics_source':metrics_source,'metrics_sources':metrics_sources,'observed_results':observed_results,'observed_results_source':'ad_insights','campaigns':campaigns,**drilldown,'notes':wnotes}
    proposal=proposal_report if isinstance(proposal_report,dict) else {};operations=operations_status if isinstance(operations_status,dict) else {};jobs=operations.get('jobs') if isinstance(operations.get('jobs'),list) else []
    generation=snapshot.get('generation');generation=generation if type(generation) is int and generation>=0 else None
    cycle=snapshot.get('last_cycle_at');cycle=cycle if _stamp(cycle) is not None else None
    report = {'account_id':ACCOUNT,'currency':currency,'checked_at':now.isoformat(),'collection':{'refreshing':snapshot.get('refreshing') is True,'active':snapshot.get('collection_active') is True,'last_cycle_at':cycle,'generation':generation},'freshness':{'state':freshness,'source_states':source_states,'meta_last_success_at':meta.get('last_success_at'),'shopify_last_success_at':shop.get('last_success_at'),'notes':notes},'windows':windows,'action_summary':{'proposal_available':proposal.get('available') is True,'pending_approval_count':sum(1 for x in operations.get('proposals',[]) if isinstance(x,dict) and x.get('approval_status')=='pending' and x.get('state')=='prepared' and _stamp(x.get('expires_at')) is not None and _stamp(x.get('expires_at'))>now and not x.get('blocked_reasons')),'jobs_requiring_attention':sum(1 for x in jobs if isinstance(x,dict) and x.get('state') in ('blocked','stale','unknown','partial')),'live_action_performed_by_report':False}}

    report['daily']=_daily(data,store,today,valid,currency)
    from management_guidance import build_guidance
    from economics_observation import recorded_economics
    report['recorded_economics'] = recorded_economics(snapshot, now=now)
    report["guidance"] = build_guidance(report, operations)
    return report
