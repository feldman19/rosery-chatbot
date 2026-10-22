"""Pure full-launch intake contract. Never creates provider objects.

The two-aspect-ratio recipe needs an actual canary edit/save/readback before
being exposed as executable. An API field's existence is not that evidence.
"""
import hashlib
import hmac
from batch_limits import MAX_ADS
import json
import re
from urllib.parse import urlsplit
from pathlib import Path


def execution_workflow(blueprint, required_format=None):
    """Describe the existing executor without granting a format downgrade."""
    supported = required_format == 'dedicated_feed_story_single_copy'
    full = required_format == 'dedicated_feed_story_five_copy'
    return {
        'method': 'prepare_once_batch_api_then_readback',
        'required_format': required_format,
        'request_format': 'dedicated_feed_story_single_copy' if supported else None,
        'same_copy_across_placements': True,
        'ad_count': blueprint.get('ad_count', 0),
        'source_count': blueprint.get('source_count', 0),
        'input_complete': blueprint.get('input_complete') is True,
        'approval_required': True,
        'full_requirement_supported': supported,
        'validation': {'provider_rendered_qa': 'unknown',
                       'ui_edit_save_reopen': 'unknown',
                       'operator_end_to_end': 'unknown',
                       'full_launch_ready': False},
        'blocker': ('חמש חלופות קופי עם סטורי ייעודי עדיין לא הוכחו יחד. לא נצמצם לקופי אחד.'
                    if full else None if supported else 'מסלול התמונות הנפרדות טרם אושר להצעה הזאת.'),
        'stages': [
            {'title': 'מכינים לכל מודעה קופי ופוסט + סטורי', 'state': 'ready' if blueprint.get('input_complete') else 'missing'},
            {'title': 'מאשרים הצעה מדויקת לפני העלאה', 'state': 'pending'},
            {'title': 'מעלים במרוכז דרך API כמודעות מושהות', 'state': 'available_after_approval' if supported else 'blocked'},
            {'title': 'מאמתים מדיה, קופי והגדרות מול מטא', 'state': 'pending'},
        ],
        'exception_policy': 'מציגים את המודעה והשדה שנכשלו. לא מבצעים ניסיון חוזר לא ודאי או תיקון דפדפן אוטומטי.',
        'measured_end_to_end_seconds': None,
    }


def attach_probe(blueprint, directory, key):
    """Host-signed diagnostic only; never makes execution available."""
    try:
        envelope = json.loads((Path(directory)/'full-launch-probe.json').read_text(encoding='utf-8'))
        raw = envelope['payload']
        if not hmac.compare_digest(envelope['signature'], hmac.new(key,raw.encode(),hashlib.sha256).hexdigest()):
            return blueprint
        evidence = json.loads(raw)
        if evidence.get('blueprint_fingerprint') == blueprint['fingerprint']:
            blueprint['provider_probe'] = evidence
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return blueprint


def build_blueprint(proposal):
    ads = proposal.get('ads') or []
    if not isinstance(ads, list) or not 1 <= len(ads) <= MAX_ADS:
        raise ValueError(f'One to {MAX_ADS} source concepts required')
    rows, issues, seen = [], [], set()
    for ad in ads:
        identity = ad.get('asset_id')
        if not isinstance(identity, str) or identity in seen:
            raise ValueError('Unique source concepts required')
        seen.add(identity)
        row = {'asset_id': identity, 'adset_key': ad.get('adset_key'),
               'media': [], 'primary_texts': [], 'issues': []}
        for placement, asset in [('feed', ad), ('story', ad.get('story'))]:
            if not isinstance(asset, dict) or not asset.get('asset_id') or not re.fullmatch(r'[a-f0-9]{64}', str(asset.get('sha256', ''))):
                row['issues'].append(placement + '_source_binding_missing')
                continue
            row['media'].append({'placement': placement, 'asset_id': asset['asset_id'],
                                 'sha256': asset['sha256'], 'name': asset.get('source_name')})
        options = [ad.get('primary_text')] + (ad.get('primary_text_options') or [])
        row['primary_texts'] = list(dict.fromkeys(x for x in options if isinstance(x, str) and x.strip()))[:5]
        url = ad.get('destination_url') or ad.get('landing_url')
        parsed = urlsplit(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme != 'https' or parsed.hostname != 'roseryjewelry.com' or parsed.username or parsed.password:
            row['issues'].append('destination_missing_or_invalid')
        if not row['primary_texts'] or not ad.get('headline'):
            row['issues'].append('copy_missing')
        row.update(destination_url=url, headline=ad.get('headline'),
                   description=ad.get('description', ''), cta=ad.get('cta', 'SHOP_NOW'),
                   url_tags=ad.get('url_tags') or ad.get('url_parameters'))
        if row['cta'] not in ('SHOP_NOW', 'SIGN_UP', 'LEARN_MORE'):
            row['issues'].append('cta_invalid')
        if not row['url_tags']:
            row['issues'].append('tracking_missing')
        rows.append(row)
        issues.extend({'asset_id': identity, 'reason': x} for x in row['issues'])
    payload = {'concepts': rows, 'ad_count': len(rows),
               'adset_count': len({x['adset_key'] for x in rows}),
               'source_count': sum(len(x['media']) for x in rows),
               'input_complete': not issues, 'issues': issues,
               'provider_mode': 'unverified_feed_story_same_ad',
               'execution_available': False,
               'required_gates': ['canary_provider_readback', 'canary_ui_edit_save_readback',
                                  'exact_paused_api_creation_approval'],
               'target_submission_seconds': 60, 'measured_submission_seconds': None,
               'timing_target_verified': False}
    payload['fingerprint'] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return payload
