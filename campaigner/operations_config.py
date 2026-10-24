"""Host-owned ROSERY execution recipe. A recipe is not consent to execute it.

This initial recipe deliberately creates normal, editable single-image ads in
feed placements. It does not silently recreate the asset_feed_spec structure
that caused the previous editor lock. Story placement customization and multiple
text variants are not claimed by this recipe.
"""
import copy
import re
from business import ACTIVE as BUSINESS

ACCOUNT = BUSINESS.meta_account
CAMPAIGN = BUSINESS.meta_sales_campaign


def operation_proposal(report):
    if not isinstance(report, dict) or report.get('available') is not True:
        return report
    result = copy.deepcopy(report)
    p = result['proposal']
    if 'schema_version' in p and p['schema_version'] != 1:
        from launch_proposal_schema import validate_proposal_v2
        validate_proposal_v2(p)
        return result
    # Owner's full launch requirement is not narrowed by this legacy recipe.
    p['required_format'] = p.get('required_format') or 'dedicated_feed_story_five_copy'
    paired = p['required_format'] == 'dedicated_feed_story_single_copy'
    owner_budget_entry = paired and p.get('creation_budget_entry_required') is True
    for ad in p.get('ads', []):
        if not ad.get('destination_url') and isinstance(ad.get('landing_url'),str):
            ad['destination_url'] = ad['landing_url']
        if not ad.get('url_tags') and isinstance(ad.get('url_parameters'),str):
            ad['url_tags'] = ad['url_parameters']
        if not ad.get('url_tags'):
            concept = re.search(r'ROSERY_([CR]\d+)_',str(ad.get('source_name','')))
            if concept:
                ad['url_tags'] = ('utm_source={{site_source_name}}&utm_medium=paid_social'
                                  '&utm_campaign=after_hour_launch&utm_content='+concept[1].lower()+
                                  '&utm_id={{campaign.id}}&adset_id={{adset.id}}&ad_id={{ad.id}}')
    authority = p.get('current_authorization') or {}
    budget = authority.get('per_new_adset_daily_minor')
    start = authority.get('restart_at')
    if not owner_budget_entry and (authority.get('existing_campaign_id') != CAMPAIGN or
            authority.get('currency') != BUSINESS.currency or type(budget) is not int or
            not 1 <= budget <= 200000 or not isinstance(start, str)):
        # The service will show a missing-config blocker, not invent a budget.
        return result
    groups = []
    for group in p.get('adsets', []):
        key = group.get('key')
        if key not in ('style', 'reviews') and not (paired and owner_budget_entry and isinstance(key,str) and re.fullmatch(r'creative_[a-f0-9]{10}',key)):
            continue
        assets = [a['asset_id'] for a in p.get('ads', []) if a.get('adset_key') == key]
        if not assets:
            continue
        groups.append({
            'key': key, 'name': 'ROSERY | ' + str(group.get('label') or key) + (' | POST+STORY | מערכת' if paired else ' | FEED | מערכת'),
            'targeting': {'geo_locations': {'countries': ['IL']}, 'age_min': 18,
                          **({} if str(key).startswith('creative_') else {'genders':[1]}), 'publisher_platforms': ['facebook', 'instagram'],
                          'facebook_positions': ['feed','story','facebook_reels'] if paired else ['feed'], 'instagram_positions': ['stream','story','reels'] if paired else ['stream']},
            'optimization_goal': 'OFFSITE_CONVERSIONS', 'billing_event': 'IMPRESSIONS',
            'promoted_object': {'pixel_id': '723893636602228', 'custom_event_type': 'PURCHASE'},
            'start_time': None if owner_budget_entry else start, 'end_time': None,
            'budget': {'type': 'daily', 'minor': None if owner_budget_entry else budget, 'approved': not owner_budget_entry},
            'asset_ids': assets,
        })
    p['operation_config'] = {
        'account_id': ACCOUNT, 'campaign_id': CAMPAIGN,
        'page_id': '170018166205299', 'instagram_user_id': '17841461774110293',
        'pixel_id': '723893636602228',
        'source_ref': 'host:operations_config.py; owner current_authorization; explicit UI approval still required',
        'media_mode': 'dedicated_feed_story_single_copy' if paired else 'feed_original_only', 'adsets': groups,
        'owner_budget_entry': owner_budget_entry, 'paused_unscheduled': owner_budget_entry,
        'limitations': [
            'ייווצרו קבוצות מודעות חדשות וכבויות. הטיוטות הקיימות במטא לא יוחלפו ולא יתוקנו בפעולה הזאת.',
            'תמונת הפיד המקורית וטקסט ראשי אחד בלבד. תמונות הסטורי ואפשרויות הטקסט הנוספות לא ישויכו.',
            'מיקומים: פיד פייסבוק ופיד אינסטגרם בלבד. זו אינה בחירת המיקומים האוטומטית של הטיוטות הישנות.',
            'השלמת הביצוע תאמת את שדות ה־API בלבד. תצוגה בפועל, מדידה ורווחיות דורשות בדיקה נוספת.',
        ],
    }
    if paired:
        p['operation_config']['limitations'] = [
            'ייווצרו קבוצות ומודעות חדשות מושהות בלבד; שום קמפיין או מודעה קיימים לא ישתנו.',
            'תמונת פוסט ותמונת סטורי נפרדות ואותו קופי אחד לשניהם; חלופות קופי נוספות אינן כלולות.',
            'פיד, סטורי ורילס של פייסבוק ואינסטגרם. התקציב שהוזן יוצג לכל קבוצה חדשה לאישור מדויק.',
            'אין תזמון הפעלה; יצירה וקריאה חוזרת אינן בדיקת תצוגה, עריכה או מדידה.',
        ]
    return result
