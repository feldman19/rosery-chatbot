"""Pure narrowed canary recipe. No credentials, I/O, execution or approval.

Single-copy placement creation later passed provider readback and feed/story
preview in the scoped C11 canary. UI edit/save/reopen and multiple copy variants
remain explicit unresolved requirements, never silently marked implemented.
"""
import re
from urllib.parse import urlsplit

def build_candidate(ad, feed_hash, story_hash):
    if any(not isinstance(x,str) or not re.fullmatch(r'[a-f0-9]{32}',x) for x in (feed_hash,story_hash)):
        raise ValueError('Exact existing library hashes required')
    url=ad.get('destination_url') or ad.get('landing_url')
    parsed=urlsplit(url) if isinstance(url,str) else None
    if not parsed or parsed.scheme!='https' or parsed.hostname!='roseryjewelry.com' or parsed.username or parsed.password:
        raise ValueError('Exact ROSERY destination required')
    if not all(isinstance(ad.get(k),str) and ad[k].strip() for k in ('primary_text','headline')):
        raise ValueError('Reviewed primary text and headline required')
    tags=ad.get('url_tags') or ad.get('url_parameters')
    if not isinstance(tags,str) or not tags.strip():raise ValueError('Reviewed tracking required')
    return {'name':'ROSERY | C11 API CANARY | DO NOT ACTIVATE',
            'object_story_spec':{'page_id':'170018166205299','instagram_user_id':'17841461774110293'},
            'url_tags':tags,
            'asset_feed_spec':{'ad_formats':['SINGLE_IMAGE'],'optimization_type':'PLACEMENT',
                'images':[{'hash':feed_hash,'adlabels':[{'name':'feed_original'}]},
                          {'hash':story_hash,'adlabels':[{'name':'story_original'}]}],
                'bodies':[{'text':ad['primary_text']}], 'titles':[{'text':ad['headline']}],
                'descriptions':[{'text':ad.get('description','')}],
                'link_urls':[{'website_url':url}], 'call_to_action_types':['SHOP_NOW'],
                'asset_customization_rules':[
                    {'customization_spec':{'publisher_platforms':['facebook','instagram'],
                        'facebook_positions':['story','facebook_reels'],'instagram_positions':['story','reels']},
                     'image_label':{'name':'story_original'},'priority':1},
                    {'customization_spec':{},'image_label':{'name':'feed_original'},'is_default':True,'priority':2}]}}

def build_deferred_candidate(ad, feed_step, story_step):
    """Reuse the observed recipe with host-resolved upload dependencies."""
    if ad.get('cta','SHOP_NOW')!='SHOP_NOW':raise ValueError('Only observed SHOP_NOW recipe supported')
    if any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_]+',x) for x in (feed_step,story_step)):raise ValueError('Host step keys required')
    candidate=build_candidate(ad,'a'*32,'b'*32)
    for image,key in zip(candidate['asset_feed_spec']['images'],(feed_step,story_step)):
        image['hash']={'ref':key,'field':'hash'}
    return candidate

def validate_resolved_candidate(candidate):
    """Reject extensions beyond the single-copy placement recipe observed live."""
    spec=candidate['asset_feed_spec']
    ad={'primary_text':spec['bodies'][0]['text'],'headline':spec['titles'][0]['text'],
        'description':spec['descriptions'][0]['text'],'destination_url':spec['link_urls'][0]['website_url'],'url_tags':candidate['url_tags']}
    expected=build_candidate(ad,spec['images'][0]['hash'],spec['images'][1]['hash']);expected['name']=candidate['name']
    if candidate!=expected:raise ValueError('Unsupported placement recipe extension')

def placement_readback_matches(actual,expected):
    """Allow only provider label IDs and default ages seen in live readback."""
    try:
        validate_resolved_candidate(expected)
        if any(actual.get(k)!=expected[k] for k in ('name','url_tags','object_story_spec')):return False
        a=actual['asset_feed_spec'];e=expected['asset_feed_spec']
        for key in ('optimization_type','ad_formats','bodies','titles','descriptions','link_urls','call_to_action_types'):
            if a.get(key)!=e[key]:return False
        if len(a['images'])!=2 or len(a['asset_customization_rules'])!=2:return False
        for image,want in zip(a['images'],e['images']):
            if image.get('hash')!=want['hash'] or [x.get('name') for x in image.get('adlabels',[])]!=[x['name'] for x in want['adlabels']]:return False
        for rule,want in zip(a['asset_customization_rules'],e['asset_customization_rules']):
            if rule.get('priority')!=want['priority'] or rule.get('image_label',{}).get('name')!=want['image_label']['name']:return False
            observed=dict(rule['customization_spec'])
            for key,value in (('age_min',13),('age_max',65)):
                if observed.get(key)==value:observed.pop(key)
            if observed!=want['customization_spec']:return False
            if 'is_default' in rule and rule['is_default']!=want.get('is_default',False):return False
        return True
    except (KeyError,TypeError,ValueError,IndexError):return False
