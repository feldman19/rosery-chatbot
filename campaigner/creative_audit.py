"""Read-only, evidence-bound creative tracking audit. No provider writes.

Static unique ad IDs avoid reliance on runtime macro expansion. Proposed URLs
are review artifacts, not instructions to mutate immutable Meta creatives.
"""
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from datetime import datetime,timezone,timedelta
import hashlib
import json
from meta_direct import ACCOUNT,LAUNCH

def destinations(creative):
    result=[]
    def add(path,url):
        if isinstance(url,str) and url:result.append({'path':path,'url':url})
    story=creative.get('object_story_spec') or {}
    for field in ('link_data','video_data','template_data'):
        part=story.get(field) or {}
        add('object_story_spec.'+field+'.link',part.get('link'))
        add('object_story_spec.'+field+'.call_to_action.value.link',((part.get('call_to_action') or {}).get('value') or {}).get('link'))
    for index,row in enumerate((creative.get('asset_feed_spec') or {}).get('link_urls') or []):
        add(f'asset_feed_spec.link_urls.{index}.website_url',row.get('website_url'))
    add('link_url',creative.get('link_url'))
    return result

def audit(snapshot,now=None):
    now=now or datetime.now(timezone.utc)
    source=((snapshot.get('sources') or {}).get('meta') or {});data=source.get('data') or {}
    inv=data.get('inventory') or {};creative=data.get('creative_inventory') or {}
    result={'verified':False,'findings':[],'active_ads_checked':0,'unknown_ads':[],
            'checked_at':now.isoformat(),'live_action_allowed':False}
    try:fresh=0<=(now-datetime.fromisoformat(source['last_success_at'])).total_seconds()<=720
    except (ValueError,TypeError,KeyError):fresh=False
    if not fresh or source.get('error') or data.get('verified') is not True or data.get('complete') is not True or data.get('account_id')!=ACCOUNT:return result
    if inv.get('verified') is not True or (inv.get('campaign') or {}).get('id')!=LAUNCH or (inv.get('campaign') or {}).get('account_id')!=ACCOUNT:return result
    if creative.get('verified') is not True or creative.get('account_id')!=ACCOUNT or creative.get('campaign_id')!=LAUNCH:return result
    ads=creative.get('ads') or [];base={a['id']:a for a in inv.get('ads') or []}
    if len(base)!=len(inv.get('ads') or []) or len({a['id'] for a in ads})!=len(ads) or set(base)!={a['id'] for a in ads}:return result
    for ad in ads:
        prior=base[ad['id']]
        if any(prior.get(k)!=ad.get(k) for k in ('adset_id','status','effective_status')):
            result['unknown_ads'].append(ad['id']);return result
        previous_creative=(prior.get('creative') or {}).get('id')
        if previous_creative and previous_creative!=(ad.get('creative') or {}).get('id'):
            result['unknown_ads'].append(ad['id']);return result
    result['verified']=True
    if inv['campaign'].get('effective_status')!='ACTIVE':return result
    active_sets={a['id'] for a in inv.get('adsets') or [] if a.get('effective_status')=='ACTIVE'}
    groups={}
    for ad in ads:
        if ad.get('effective_status')!='ACTIVE' or base[ad['id']].get('effective_status')!='ACTIVE' or ad.get('adset_id') not in active_sets:continue
        result['active_ads_checked']+=1
        c=ad.get('creative') or {};links=destinations(c);tags=parse_qsl(c.get('url_tags') or '',keep_blank_values=True)
        if not links:result['unknown_ads'].append(ad['id']);continue
        for url in sorted({x['url'] for x in links}):
            try:
                p=urlsplit(url)
                if p.scheme!='https' or p.hostname not in ('roseryjewelry.com','www.roseryjewelry.com') or p.username or p.password or p.port not in (None,443):raise ValueError()
                pairs=parse_qsl(p.query,keep_blank_values=True)+tags
                values={key:[v for k,v in pairs if k==key] for key in ('utm_content','ad_id','utm_source','utm_medium','utm_campaign')}
                # Conflicting duplicate keys have unknown merge semantics, never guessed.
                if any(len(set(v))>1 for v in values.values()):result['unknown_ads'].append(ad['id']);continue
                content=next(iter(values['utm_content']),None);identity=next(iter(values['ad_id']),None)
                if not content or '{{' in content or (identity and ('{{' in identity or identity==ad['id'])):continue
                key=(p.hostname,p.path,tuple((k,tuple(values[k])) for k in values))
                groups.setdefault(key,{})[ad['id']]={'ad_id':ad['id'],'name':ad.get('name'),'creative_id':c.get('id'),'url':url,'paths':[x['path'] for x in links if x['url']==url],'url_tags':c.get('url_tags') or ''}
            except (ValueError,TypeError):result['unknown_ads'].append(ad['id'])
    for key,members in groups.items():
        if len(members)<2:continue
        entries=[]
        for row in sorted(members.values(),key=lambda x:x['ad_id']):
            p=urlsplit(row['url']);pairs=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k!='utm_content']
            pairs.append(('utm_content',row['ad_id']))
            entries.append({**row,'proposed_url':urlunsplit((p.scheme,p.netloc,p.path,urlencode(pairs),p.fragment)),
                            'tracking_identity_basis':'source_ad_id','replacement_ad_id':None,
                            'replacement_requires_mapping':True,
                            'proposed_url_tags':urlencode([(k,v) for k,v in parse_qsl(row['url_tags'],keep_blank_values=True) if k!='utm_content'])})
        evidence={'account_id':ACCOUNT,'campaign_id':LAUNCH,'observed_at':source['last_success_at'],'ads':entries}
        fingerprint=hashlib.sha256(json.dumps(evidence['ads'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        result['findings'].append({'id':'tracking-collision-'+hashlib.sha256('|'.join(sorted(members)).encode()).hexdigest()[:20],
            'fingerprint':fingerprint,'kind':'tracking_collision','title':'שתי מודעות חולקות את אותו סימון כניסה לאתר',
            'explanation':'לפי פרמטרי הקישור שנקראו, אי אפשר להפריד בין המודעות באמצעות סימון זה בלבד. אין בכך הוכחה שמדידת Meta שגויה.',
            'evidence':evidence,'evidence_refs':['sha256:'+fingerprint],'proposed_change':'prepare_tracking_replacement',
            'before':{'urls':[x['url'] for x in entries]},'after':{'urls':[x['proposed_url'] for x in entries]},
            'next_step':'להכין גרסאות מושהות עם סימון ייחודי ולבדוק שהקישור והפרמטרים נשמרים באתר, לפני אישור החלפה.',
            'risk':'החלפת קריאייטיב/מודעה עשויה לפתוח בדיקה מחדש. אין לשנות או להפעיל ללא אישור מדויק.',
            'valid_until':(now+timedelta(minutes=10)).isoformat(),'check_after':'קריאה מחדש של הגדרות המודעות ובדיקת הקישור לאחר השמירה',
            'rollback':'להשאיר את המודעות המקוריות ללא שינוי עד אימות ואישור החלפה','live_action_allowed':False})
    result['unknown_ads']=sorted(set(result['unknown_ads']))
    return result
