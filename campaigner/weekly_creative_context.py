"""Join observed weekly outcomes to current content, never infer creative causality."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from urllib.parse import urlsplit

ACCOUNT = '393059510164294'


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def weekly_creative_context(report, workspace, *, snapshot=None, now=None):
    result = {'available':False, 'account_id':ACCOUNT, 'items':[],
              'historical_creative_match':False, 'causal_explanation_available':False,
              'scope':'current_content_not_proven_content_during_performance_window'}
    now = now or datetime.now(timezone.utc)
    if not isinstance(report,dict) or not isinstance(workspace,dict):
        return {**result, 'reason':'invalid_context'}
    if report.get('account_id') != ACCOUNT or workspace.get('account_id') != ACCOUNT:
        return {**result, 'reason':'account_mismatch'}
    raw_source = (snapshot or {}).get('sources', {}).get('meta', {})
    raw_data = raw_source.get('data') or {}
    if raw_data.get('account_id') != ACCOUNT or raw_data.get('verified') is not True or raw_source.get('last_success_at') != workspace.get('checked_at'):
        return {**result, 'reason':'raw_content_provenance_unavailable'}
    bindings = {}
    for collection in ('inventory','creative_inventory','active_ads'):
        for ad in (raw_data.get(collection) or {}).get('ads', []):
            if not isinstance(ad,dict) or not isinstance(ad.get('id'),str):continue
            values = bindings.setdefault(ad.get('id'), {})
            for key, candidates in {'account':[ad.get('account_id')],
                                    'campaign':[ad.get('campaign_id'),(ad.get('campaign') or {}).get('id')],
                                    'adset':[ad.get('adset_id'),(ad.get('adset') or {}).get('id')],
                                    'creative':[(ad.get('creative') or {}).get('id')]}.items():
                values.setdefault(key,set()).update(str(value) for value in candidates if value is not None)
    conflicts = {identity for identity, values in bindings.items() if any(len(v)>1 for v in values.values()) or values.get('account',set())- {ACCOUNT}}
    stamp = _stamp(workspace.get('checked_at'))
    report_stamp = _stamp(report.get('freshness', {}).get('meta_last_success_at'))
    if (not now.tzinfo or not stamp or not report_stamp or stamp != report_stamp
            or not 0 <= (now-stamp).total_seconds() <= 900 or workspace.get('refresh_error')
            or report.get('freshness', {}).get('state') != 'fresh'):
        return {**result, 'reason':'content_snapshot_stale_or_mismatched'}
    window = report.get('windows', {}).get('7', {})
    if window.get('partial') is not False or not window.get('since') or not window.get('until'):
        return {**result, 'reason':'complete_week_unavailable'}
    rows = [row for row in window.get('ads', []) if isinstance(row,dict) and isinstance(row.get('id'),str)]
    creatives = [row for row in workspace.get('ads', []) if isinstance(row,dict) and isinstance(row.get('id'),str)]
    row_counts = Counter(r.get('id') for r in rows)
    creative_counts = Counter(r.get('id') for r in creatives)
    by_id = {r.get('id'):r for r in creatives}
    for row in rows:
        identity = row.get('id')
        count = row.get('purchases')
        if (not isinstance(identity, str) or not identity.isdigit() or row_counts[identity] != 1
                or creative_counts[identity] != 1 or identity in conflicts or identity not in bindings or row.get('result_type') == 'registration'
                or isinstance(count, bool) or not isinstance(count, (int,float))
                or not math.isfinite(count) or count <= 0):
            continue
        creative = by_id[identity]
        if any(not row.get(key) or row[key] != creative.get(key) for key in ('campaign_id','adset_id')):
            continue
        if not isinstance(creative.get('creative_id'),str) or not creative['creative_id'].isdigit():
            continue
        raw_binding=bindings[identity]
        if any(raw_binding.get(key) and raw_binding[key]!={value} for key,value in (
                ('campaign',row['campaign_id']),('adset',row['adset_id']),('creative',creative['creative_id']))):
            continue
        content = {key:creative.get(key) for key in ('primary_text','headline','description')}
        content = {key:value if isinstance(value,str) else None for key,value in content.items()}
        image = creative.get('image_url')
        try:
            parsed = urlsplit(image) if isinstance(image,str) else None
            if not parsed or parsed.scheme != 'https' or parsed.username or parsed.password or not (parsed.hostname or '').endswith(('.fbcdn.net','.cdninstagram.com')):
                image = None
        except ValueError:
            image = None
        item = {'ad_id':identity, 'ad_name':row.get('name') or identity,
                'campaign_id':row['campaign_id'], 'adset_id':row['adset_id'],
                'creative_id':creative['creative_id'], **content, 'image_url':image,
                'text_options':[text for text in (creative.get('text_options') or []) if isinstance(text,str)],
                'has_placement_rules':creative.get('has_placement_rules') is True,
                'image_scope':'current_meta_image_or_thumbnail_not_placement_preview',
                'content_observed_at':workspace['checked_at'],
                'observed_result_type':row.get('result_type'),
                'performance':{'since':window['since'],'until':window['until'],'reported_purchases':count},
                'historical_creative_match':False, 'causal_explanation_available':False,
                'meta_url':'https://adsmanager.facebook.com/adsmanager/manage/ads?act='+ACCOUNT+'&selected_ad_ids='+identity,
                'limitation':'זה התוכן בתמונת המצב הנוכחית. לא אומת שזה התוכן שפעל לאורך כל השבוע, ואין הוכחה שהתמונה או הטקסט גרמו לתוצאות.'}
        item['source_digest'] = hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        result['items'].append(item)
    result['available'] = bool(result['items'])
    result['reason'] = 'exact_current_content_matches' if result['available'] else 'no_unique_matching_content'
    return result
