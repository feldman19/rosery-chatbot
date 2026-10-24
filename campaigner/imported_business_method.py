"""Locally curated business preferences; never imported execution authority.

Only exact, source-bound nonfinancial preferences become usable. Source text is
not a prompt and is never included wholesale in the dossier.
"""
import hashlib
import json
from pathlib import Path
from business import ACTIVE as BUSINESS

ACCOUNT = BUSINESS.meta_account
FOLDER = 'claude-project-import-20261021'
ALLOWED = {'masculine_copy', 'headline_no_emoji', 'no_unverified_superlatives',
           'avoid_prominent_material', 'shop_now_cta', 'offer_in_headline',
           'review_text_improvements', 'new_creatives_new_dated_adset',
           'preserve_social_proof', 'no_automatic_graduation'}


def load_imported_business_method(root, *, business_id, account_id):
    result = {'status': 'unavailable', 'preferences': [], 'conflicts': [],
              'live_action_allowed': False, 'approval_imported': False,
              'financial_thresholds_executable': False}
    if business_id != BUSINESS.id or account_id != ACCOUNT:
        return {**result, 'reason': 'tenant_mismatch'}
    directory = Path(root).parent / 'outputs' / FOLDER
    try:
        policy = json.loads((directory / 'curated-business-method.json').read_text(encoding='utf-8'))
        if (policy.get('business_id'), policy.get('account_id'), policy.get('version')) != (BUSINESS.id, ACCOUNT, 1):
            raise ValueError('policy_scope_mismatch')
        sources = policy['sources']
        if not isinstance(sources, dict) or not sources:
            raise ValueError('sources_missing')
        for name, metadata in sources.items():
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('invalid_source_path')
            path = directory / name
            if path.is_symlink() or path.stat().st_size > 2_000_000:
                raise ValueError('invalid_source')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
                raise ValueError('source_changed')
        preferences = policy['preferences']
        seen = set()
        for item in preferences:
            if item['id'] not in ALLOWED or item['id'] in seen or item['source'] not in sources:
                raise ValueError('invalid_preference')
            seen.add(item['id'])
            text = (directory / item['source']).read_text(encoding='utf-8-sig')
            if not item['source_excerpt'] or item['source_excerpt'] not in text:
                raise ValueError('unsupported_preference')
        return {**result, 'status': 'available', 'business_id': business_id,
                'account_id': account_id, 'preferences': preferences,
                'sources': sources, 'conflicts': policy['conflicts'],
                'precedence': policy['precedence'],
                'source_ref': 'outputs/' + FOLDER + '/curated-business-method.json',
                'use': 'draft_and_review_preferences_not_current_settings_or_permission'}
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, 'reason': 'missing_changed_or_invalid_import_sources'}


def load_imported_method(root=None):
    """ROSERY-only convenience entrypoint; never use for another tenant."""
    return load_imported_business_method(root or Path(__file__).resolve().parent,
                                        business_id=BUSINESS.id, account_id=ACCOUNT)
