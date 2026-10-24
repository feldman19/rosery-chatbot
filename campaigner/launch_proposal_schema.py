"""Closed creative-launch v2 contract. Validation is not execution authority.

The host must still verify the signed envelope, source bytes, rulebook and live
account state. A structurally valid existing-adset proposal is blocked by default.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _closed(value, fields, label, optional=()):
    if not isinstance(value, dict) or not set(fields.split()) <= set(value) or set(value) - set(fields.split()) - set(optional):
        raise ValueError(label + ': exact fields required (unknown or missing field)')


def _text(value, label, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(label + ': bounded nonempty text required')


def _id(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{5,30}', value):
        raise ValueError(label + ': numeric identity required')


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError(label + ': sha256 required')


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('aware timestamp required')
    return parsed.astimezone(timezone.utc)


def validate_proposal_v2(obj):
    """Return the original validated object; raise ValueError with refusal label."""
    _closed(obj, 'schema_version id request_id request_revision prepared_at expires_at summary strategy rules adsets ads required_format creation_status owner_questions blockers', 'proposal', ('activation_available', 'state', 'prepared_by', 'creation_recipe', 'business_profile', 'profile_reasons'))
    if 'creation_recipe' in obj:
        recipe=obj['creation_recipe']
        _closed(recipe,'adset_fields creative_fields optimization_type','creation_recipe')
        if not isinstance(recipe['adset_fields'],dict) or set(recipe['adset_fields'])-{'is_dynamic_creative','destination_type','bid_strategy'}:raise ValueError('creation_recipe: unsupported adset fields')
        if not isinstance(recipe['creative_fields'],dict) or set(recipe['creative_fields'])!={'degrees_of_freedom_spec'}:raise ValueError('creation_recipe: unsupported creative fields')
        if 'is_dynamic_creative' in recipe['adset_fields'] and type(recipe['adset_fields']['is_dynamic_creative']) is not bool:raise ValueError('creation_recipe: dynamic flag must be boolean')
        for field,allowed in (('destination_type',('WEBSITE',)),('bid_strategy',('LOWEST_COST_WITHOUT_CAP',))):
            if field in recipe['adset_fields'] and recipe['adset_fields'][field] not in allowed:raise ValueError('creation_recipe: unsupported '+field)
        if recipe['optimization_type'] not in ('DEGREES_OF_FREEDOM','PLACEMENT'):raise ValueError('creation_recipe: unsupported optimization_type')
        if 'degrees_of_freedom_spec' in recipe['creative_fields']:
            spec=recipe['creative_fields']['degrees_of_freedom_spec']
            _closed(spec,'creative_features_spec','degrees_of_freedom_spec')
            features=spec['creative_features_spec']
            if not isinstance(features,dict):raise ValueError('creation_recipe: features must be object')
            from business_profile import validate_features
            validate_features(features)
            for feature,setting in features.items():
                if not isinstance(feature,str) or not feature:raise ValueError('creation_recipe: invalid feature')
                _closed(setting,'enroll_status','creative feature')
                allowed=('OPT_OUT','OPT_IN') if feature=='inline_comment' else ('OPT_OUT',)
                if setting['enroll_status'] not in allowed:raise ValueError('creation_recipe: feature must opt out')
    for key, expected in (('activation_available', False), ('state', 'draft_for_review'), ('prepared_by', 'agent_review')):
        if key in obj and (type(obj[key]) is not type(expected) or obj[key] != expected):
            raise ValueError('host envelope: invalid ' + key)
    if type(obj['schema_version']) is not int or obj['schema_version'] != 2:
        raise ValueError('schema_version: only 2 supported')
    for field in ('id', 'request_id', 'summary'):
        _text(obj[field], field)
    if type(obj['request_revision']) is not int or obj['request_revision'] < 1:
        raise ValueError('request_revision: positive integer required')
    if not timedelta(0) < stamp(obj['expires_at']) - stamp(obj['prepared_at']) <= timedelta(minutes=15):
        raise ValueError('freshness: maximum fifteen minutes')
    if obj['required_format'] != 'single_ad_flexible' or obj['creation_status'] != 'PAUSED':
        raise ValueError('format/status: single_ad_flexible PAUSED required')
    for field in ('owner_questions', 'blockers'):
        if not isinstance(obj[field], list) or len(obj[field]) > 30:
            raise ValueError(field + ': bounded list required')
        for value in obj[field]:
            _text(value, field)
    _closed(obj['strategy'], 'campaign_id source', 'strategy')
    _id(obj['strategy']['campaign_id'], 'campaign_id')
    if obj['strategy']['source'] != 'model_advisory':
        raise ValueError('strategy.source: advisory required')
    _closed(obj['rules'], 'rulebook_sha256 citations', 'rules')
    _sha(obj['rules']['rulebook_sha256'], 'rulebook_sha256')
    citations = obj['rules']['citations']
    if not isinstance(citations, list) or not 1 <= len(citations) <= 100 or any(not isinstance(x, str) for x in citations) or len(set(citations)) != len(citations):
        raise ValueError('citations: unique rule ids required')
    for field, maximum in (('adsets', 5), ('ads', 15)):
        if not isinstance(obj[field], list) or not 1 <= len(obj[field]) <= maximum:
            raise ValueError(field + ': bounded nonempty list required')
    groups = set()
    existing_targets = set()
    for group in obj['adsets']:
        _closed(group, 'key label rationale product angle targeting_mode target budget_minor', 'adset')
        for field in ('key', 'label', 'rationale', 'product', 'angle'):
            _text(group[field], 'adset.' + field, 160)
        if group['key'] in groups:
            raise ValueError('duplicate adset key')
        groups.add(group['key'])
        # copy_men / copy_women: exact targeting of a live ad set of that gender in the chosen campaign (owner rule:
        # copy men/women targeting exactly; hard gender separation). The owner confirms the gender before this.
        if group['targeting_mode'] not in ('broad_il_adult', 'copy_men', 'copy_women'):
            raise ValueError('targeting_mode: broad_il_adult, copy_men or copy_women required')
        target = group['target']
        if not isinstance(target, dict) or target.get('mode') not in ('new', 'existing'):
            raise ValueError('target.mode: new or existing required')
        if target['mode'] == 'new':
            _closed(target, 'mode', 'new target')
            if type(group['budget_minor']) is not int or not 1 <= group['budget_minor'] <= 200000:
                raise ValueError('budget_minor: bounded explicit daily budget required')
        else:
            _closed(target, 'mode adset_id rule_cited', 'existing target')
            _id(target['adset_id'], 'adset_id')
            if target['adset_id'] in existing_targets:
                raise ValueError('duplicate existing adset target')
            existing_targets.add(target['adset_id'])
            if target['rule_cited'] != 'existing_adset_reuse' or target['rule_cited'] not in citations:
                raise ValueError('existing_adset_reuse: rule citation required')
            if group['budget_minor'] is not None:
                raise ValueError('existing budget: mutation forbidden')
    assets = set()
    for ad in obj['ads']:
        _closed(ad, 'asset_id sha256 media_kind source_name source_kind review video adset_key primary_text_options headline_options description cta destination_url url_tags', 'ad', ('media_url',))
        if not isinstance(ad['asset_id'], str) or not re.fullmatch('[A-Za-z0-9_-]{10,150}', ad['asset_id']) or ad['asset_id'] in assets:
            raise ValueError('asset_id: unique bounded identity required')
        assets.add(ad['asset_id'])
        if 'media_url' in ad and ad['media_url'] != '/api/launch/proposal/media/' + ad['asset_id']:
            raise ValueError('host media_url: exact source endpoint required')
        _sha(ad['sha256'], 'asset sha256')
        _text(ad['source_name'], 'source_name', 160)
        if ad['source_kind'] not in ('drive', 'device') or ad['asset_id'].startswith('local_') != (ad['source_kind'] == 'device'):
            raise ValueError('source_kind: identity mismatch')
        if ad['adset_key'] not in groups:
            raise ValueError('adset_key: unknown assignment')
        _closed(ad['review'], 'visual_checked method notes audio_status', 'review')
        if ad['review']['visual_checked'] is not True:
            raise ValueError('visual review required')
        for field in ('method', 'notes'):
            _text(ad['review'][field], 'review.' + field)
        if ad['review']['audio_status'] not in ('checked', 'no_audio', 'not_checked', 'not_applicable'):
            raise ValueError('audio coverage required')
        if ad['media_kind'] == 'video':
            _closed(ad['video'], 'duration_ms width height technical_valid thumbnail_asset_id thumbnail_sha256', 'video')
            for field in ('duration_ms', 'width', 'height'):
                if type(ad['video'][field]) is not int or not 1 <= ad['video'][field] <= (600000 if field == 'duration_ms' else 8192):
                    raise ValueError('video metadata: bounded positive integers required')
            if ad['video']['technical_valid'] is not True or ad['review']['audio_status'] == 'not_applicable':
                raise ValueError('video technical validation/audio coverage required')
            if not isinstance(ad['video']['thumbnail_asset_id'], str) or not re.fullmatch('[A-Za-z0-9_-]{10,150}', ad['video']['thumbnail_asset_id']):
                raise ValueError('video thumbnail identity required')
            _sha(ad['video']['thumbnail_sha256'], 'thumbnail sha256')
        elif ad['media_kind'] != 'image' or ad['video'] is not None:
            raise ValueError('media_kind/video: image or validated video required')
        for field in ('primary_text_options', 'headline_options'):
            if not isinstance(ad[field], list) or len(ad[field]) != 5:
                raise ValueError(field + ': exactly five variations required')
            for value in ad[field]:
                _text(value, field)
        if not isinstance(ad['description'], str) or len(ad['description']) > 4000 or not isinstance(ad['url_tags'], str) or len(ad['url_tags']) > 4000:
            raise ValueError('description/url_tags: bounded strings required')
        url = urlsplit(ad['destination_url']) if isinstance(ad['destination_url'], str) else None
        if not url or url.scheme != 'https' or url.hostname != 'roseryjewelry.com' or url.username or url.password or url.port not in (None, 443):
            raise ValueError('destination: exact ROSERY HTTPS required')
        if ad['cta'] != 'SHOP_NOW':
            raise ValueError('cta: SHOP_NOW required')
    if groups != {ad['adset_key'] for ad in obj['ads']}:
        raise ValueError('empty adset forbidden')
    canonical(obj)
    return obj


def make_valid_proposal_v2():
    """Synthetic contract fixture, structurally valid; reuse remains policy blocked."""
    obj = {'schema_version': 2, 'id': 'creative-fixture', 'request_id': 'request-fixture', 'request_revision': 1,
           'prepared_at': '2026-10-23T10:00:00+00:00', 'expires_at': '2026-10-23T10:15:00+00:00',
           'summary': 'Synthetic launch', 'strategy': {'campaign_id': '120246379886090730', 'source': 'model_advisory'},
           'rules': {'rulebook_sha256': 'a'*64, 'citations': ['full_copy_set', 'new_creatives_new_dated_adset', 'existing_adset_reuse']},
           'required_format': 'single_ad_flexible', 'creation_status': 'PAUSED', 'owner_questions': [], 'blockers': [], 'adsets': [], 'ads': []}
    for index, kind in enumerate(('image', 'video')):
        key = 'group' + str(index)
        obj['adsets'].append({'key': key, 'label': 'Synthetic ' + key, 'rationale': 'Test only', 'product': 'ring', 'angle': 'style',
                              'targeting_mode': 'broad_il_adult', 'target': {'mode': 'new'} if index == 0 else {'mode': 'existing', 'adset_id': '123456789', 'rule_cited': 'existing_adset_reuse'}, 'budget_minor': 10000 if index == 0 else None})
        obj['ads'].append({'asset_id': 'fixture_asset_' + str(index), 'sha256': str(index)*64, 'media_kind': kind,
                          'source_name': 'fixture.' + ('png' if kind == 'image' else 'mp4'), 'source_kind': 'drive',
                          'review': {'visual_checked': True, 'method': 'synthetic', 'notes': 'Test only', 'audio_status': 'not_applicable' if kind == 'image' else 'not_checked'},
                          'video': None if kind == 'image' else {'duration_ms': 8000, 'width': 1080, 'height': 1080, 'technical_valid': True, 'thumbnail_asset_id': 'fixture_thumbnail', 'thumbnail_sha256': 'b'*64},
                          'adset_key': key, 'primary_text_options': ['Text ' + str(n) for n in range(5)], 'headline_options': ['Headline ' + str(n) for n in range(5)],
                          'description': 'Synthetic', 'cta': 'SHOP_NOW', 'destination_url': 'https://roseryjewelry.com/collections/all', 'url_tags': ''})
    return validate_proposal_v2(obj)
