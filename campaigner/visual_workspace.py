"""Read-only public projection of already collected Meta creative data."""
from urllib.parse import urlsplit
from datetime import datetime,timezone
ACCOUNT='393059510164294'

def placement_summary(ad):
    raw=ad.get('placement_readback');creative=(ad.get('creative') or {}).get('id')
    if not isinstance(raw,dict) or raw.get('status') not in ('unknown_field_not_returned','field_returned_unverified','unavailable'):return None
    if raw.get('account_id')!=ACCOUNT or raw.get('ad_id')!=ad.get('id') or not creative or raw.get('creative_id')!=creative:return None
    try:
        stamp=datetime.fromisoformat(raw['checked_at'])
        if stamp.tzinfo is None or stamp>datetime.now(timezone.utc):return None
    except (KeyError,ValueError,TypeError):return None
    return {'status':raw['status'],'checked_at':raw['checked_at'],'account_id':ACCOUNT,'ad_id':ad['id'],'creative_id':creative,'placement_pair_verified':False,'rendered_preview_verified':False}
def safe_image(value):
    if not isinstance(value,str): return None
    try:
        u=urlsplit(value); h=(u.hostname or '').lower()
        if u.scheme=='https' and not u.username and not u.password and (h=='fbcdn.net' or h.endswith('.fbcdn.net') or h=='cdninstagram.com' or h.endswith('.cdninstagram.com')): return value
    except ValueError: pass
    return None

def build_workspace(snapshot):
    source=(snapshot.get('sources') or {}).get('meta') or {}
    data=source.get('data') or {}
    if data.get('account_id')!=ACCOUNT or data.get('verified') is not True:
        return {'available':False,'ads':[],'checked_at':source.get('last_success_at'),'reason':'No verified connected Meta snapshot'}
    inventory=data.get('inventory') or {}; campaign=inventory.get('campaign') or {}
    rows={}
    for group in [inventory.get('ads') or [],(data.get('creative_inventory') or {}).get('ads') or [],(data.get('active_ads') or {}).get('ads') or []]:
        for ad in group:
            if isinstance(ad,dict) and str(ad.get('id','')).isdigit(): rows[ad['id']]={**rows.get(ad['id'],{}),**ad}
    ads=[]
    for ad in rows.values():
        c=ad.get('creative') or {}; spec=c.get('object_story_spec') or {}; link=spec.get('link_data') or {}; feed=c.get('asset_feed_spec') or {}
        texts=[x.get('text') for x in feed.get('bodies',[]) if isinstance(x,dict) and isinstance(x.get('text'),str)]
        title=next((x.get('text') for x in feed.get('titles',[]) if isinstance(x,dict) and x.get('text')),None)
        parent=ad.get('campaign') or campaign if ad.get('adset_id') in {x.get('id') for x in inventory.get('adsets',[])} else ad.get('campaign') or {}
        aset=ad.get('adset') or {}; sid=ad.get('adset_id') or aset.get('id')
        aset=next((x for x in inventory.get('adsets',[]) if x.get('id')==sid),aset)
        picture=safe_image(c.get('image_url')) or safe_image(c.get('thumbnail_url')) or safe_image(link.get('picture'))
        ads.append({'id':ad['id'],'name':ad.get('name') or ad['id'],'campaign_id':parent.get('id'),'campaign_name':parent.get('name'),'adset_id':sid,'adset_name':aset.get('name') or sid,'status':ad.get('status'),'effective_status':ad.get('effective_status'),'creative_id':c.get('id'),'placement_readback':placement_summary(ad),'primary_text':link.get('message') or c.get('body') or (texts[0] if texts else None),'text_options':texts,'headline':link.get('name') or c.get('title') or title,'description':link.get('description') or next((x.get('text') for x in feed.get('descriptions',[]) if isinstance(x,dict)),None),'image_url':picture,'image_kind':'Meta creative image or thumbnail; not placement preview' if picture else None,'has_placement_rules':bool(feed.get('asset_customization_rules')),'meta_url':'https://adsmanager.facebook.com/adsmanager/manage/ads?act='+ACCOUNT+'&selected_ad_ids='+ad['id']})
    return {'available':True,'account_id':ACCOUNT,'checked_at':source.get('last_success_at'),'refresh_error':bool(source.get('error')),'ads':ads,'coverage':'Connected launch inventory plus account active ads; paused ads outside launch may be missing','ai_connected':False,'read_only':True}
