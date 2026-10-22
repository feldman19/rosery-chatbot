"""One deterministic ROSERY registration creative brief; no APIs or writes.

plan_experiment(snapshot, now=None) consumes the authenticated /api/live shape.
The trusted host is responsible for source authenticity. This module requires
fresh verified fixed-account inventory and separately verified creative inventory.
Names never supply visual meaning. All copy, hashes, IDs and destinations come
from collected fields. Historical reporting is observational, not an experiment.
No budget, sample size, loser threshold, approval or causal winner is invented.
"""
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from meta_direct import ACCOUNT, LAUNCH
from creative_audit import audit as creative_audit


TRACKING_KEYS = frozenset({'ad_id', 'fbclid', 'gclid', 'ttclid', '_gl'})


def _is_tracking(key):
    return key.casefold().startswith('utm_') or key.casefold() in TRACKING_KEYS


def _landing(asset):
    if len(asset['destinations']) != 1:
        return None
    try:
        parsed = urlsplit(asset['destinations'][0])
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return None
        pairs = parse_qsl(parsed.query, keep_blank_values=True) + parse_qsl(asset.get('observed_url_tags') or '', keep_blank_values=True)
        business = sorted((key, value) for key, value in pairs if not _is_tracking(key))
        host = parsed.hostname.casefold()
        netloc = host + (':'+str(parsed.port) if parsed.port not in (None, 443) else '')
        return {'scheme': parsed.scheme, 'host': host, 'port': parsed.port or 443,
                'path': parsed.path or '/', 'business_parameters': [list(pair) for pair in business],
                'fragment': parsed.fragment,
                'url_without_tracking': urlunsplit((parsed.scheme, netloc, parsed.path or '/', urlencode(business), parsed.fragment))}
    except (ValueError, TypeError):
        return None


def _tracking(snapshot, now, selected):
    try:
        audited = creative_audit(snapshot, now)
    except (ValueError, TypeError, KeyError, AttributeError):
        audited = {'verified': False, 'findings': [], 'unknown_ads': []}
    identities = {asset['ad_id'] for asset in selected}
    dependencies = []
    for finding in audited.get('findings') or []:
        members = {row['ad_id'] for row in (finding.get('evidence') or {}).get('ads') or []}
        if members & identities:
            dependencies.append({'case_id': finding['id'], 'fingerprint': finding['fingerprint'],
                                 'kind': finding['kind'], 'ad_ids': sorted(members & identities),
                                 'evidence_refs': finding.get('evidence_refs') or []})
    unknown = not audited.get('verified') or bool(identities & set(audited.get('unknown_ads') or []))
    arm_keys = []
    for asset in selected:
        if not asset['url_tags_field_present'] or len(asset['destinations']) != 1:
            unknown = True
            continue
        try:
            url = urlsplit(asset['destinations'][0])
            values = {}
            for key, value in parse_qsl(url.query, keep_blank_values=True)+parse_qsl(asset['observed_url_tags'] or '', keep_blank_values=True):
                if key.casefold() in {'ad_id', 'utm_content'}:
                    values.setdefault(key.casefold(), []).append(value)
            if any(len(set(items)) > 1 for items in values.values()):
                unknown = True
                continue
            ad_id = next(iter(values.get('ad_id', [])), None)
            content = next(iter(values.get('utm_content', [])), None)
            if ad_id and ad_id == asset['ad_id'] and '{{' not in ad_id:
                arm_keys.append(('ad_id', ad_id))
            elif not ad_id and content and '{{' not in content:
                arm_keys.append(('utm_content', content))
            else:
                unknown = True
        except (TypeError, ValueError):
            unknown = True
    if len(arm_keys) != len(selected) or len(set(arm_keys)) != len(arm_keys):
        unknown = True
    if unknown:
        fingerprint = _hash([{'ad_id': asset['ad_id'], 'urls': asset['destinations'],
                              'url_tags': asset['observed_url_tags'], 'tags_collected': asset['url_tags_field_present']}
                             for asset in selected])
        dependencies.append({'case_id': 'experiment-tracking-unknown-'+_hash(sorted(identities))[:20],
                             'fingerprint': fingerprint, 'kind': 'tracking_configuration_unknown',
                             'ad_ids': sorted(identities), 'evidence_refs': ['sha256:'+fingerprint]})
    return {'blocked': bool(dependencies), 'dependencies': dependencies,
            'status': 'collision' if any(item['kind'] == 'tracking_collision' for item in dependencies) else 'unknown' if unknown else 'unique_in_read_configuration',
            'configuration_audit_verified': audited.get('verified') is True}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _strings(values):
    return sorted({value for value in values if isinstance(value, str) and value})


def _copy_inventory(ad):
    creative = ad.get('creative')
    if not isinstance(creative, dict) or not isinstance(creative.get('id'), str):
        return None
    primary, headlines, descriptions, images, videos, destinations, ctas = [], [], [], [], [], [], []
    sources = []
    story = creative.get('object_story_spec')
    if isinstance(story, dict):
        for kind in ('link_data', 'video_data'):
            part = story.get(kind)
            if not isinstance(part, dict):
                continue
            for key, target in [('message', primary), ('name', headlines), ('title', headlines),
                                ('description', descriptions), ('image_hash', images),
                                ('video_id', videos), ('link', destinations)]:
                if isinstance(part.get(key), str) and part[key]:
                    target.append(part[key])
                    sources.append('object_story_spec.'+kind+'.'+key)
            call = part.get('call_to_action')
            if isinstance(call, dict):
                ctas.append(call.get('type'))
                if isinstance(call.get('value'), dict):
                    destinations.append(call['value'].get('link'))
    feed = creative.get('asset_feed_spec')
    if isinstance(feed, dict):
        for key, field, target in [('bodies', 'text', primary), ('titles', 'text', headlines),
                                   ('descriptions', 'text', descriptions), ('images', 'hash', images),
                                   ('videos', 'video_id', videos), ('link_urls', 'website_url', destinations)]:
            for item in feed.get(key) or []:
                if isinstance(item, dict) and isinstance(item.get(field), str):
                    target.append(item[field])
                    sources.append('asset_feed_spec.'+key+'.'+field)
        if isinstance(feed.get('call_to_action_types'), list):
            ctas.extend(feed['call_to_action_types'])
    destinations.append(creative.get('link_url'))
    copy = {'primary_texts': _strings(primary), 'headlines': _strings(headlines),
            'descriptions': _strings(descriptions)}
    result = {'ad_id': ad['id'], 'ad_name': ad.get('name'), 'adset_id': ad.get('adset_id'),
              'creative_id': creative['id'], 'effective_status': ad.get('effective_status'),
              'copy': copy, 'provider_image_hashes': _strings(images),
              'video_ids': _strings(videos), 'destinations': _strings(destinations),
              'call_to_action_types': _strings(ctas), 'source_fields': sorted(set(sources))}
    result['observed_url_tags'] = creative.get('url_tags') if isinstance(creative.get('url_tags'), str) else None
    result['url_tags_field_present'] = 'url_tags' in creative
    result['creative_version_sha256'] = _hash(creative)
    result['copy_sha256'] = _hash(copy)
    return result


def _number(value, integer=False):
    if isinstance(value, bool):
        raise ValueError('boolean metric')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('invalid metric') from None
    if not result.is_finite() or result < 0 or integer and result != result.to_integral_value():
        raise ValueError('invalid metric')
    return result


def _action(actions, *aliases):
    if not isinstance(actions, list):
        raise ValueError('actions unavailable')
    values = {}
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get('action_type'), str):
            raise ValueError('invalid action')
        key = action['action_type']
        value = _number(action.get('value'), integer=True)
        if key in values and values[key] != value:
            raise ValueError('conflicting action values')
        values[key] = value
    for alias in aliases:
        if alias in values:
            return values[alias]
    return Decimal(0)


def _metrics(meta, ad_ids):
    totals = {identity: {'spend': Decimal(0), 'impressions': Decimal(0),
                          'registrations': Decimal(0), 'landing_page_views': Decimal(0)} for identity in ad_ids}
    seen, duplicates, excluded_partial = {}, 0, 0
    today = date.fromisoformat(meta['today'])
    start, end = date.fromisoformat(meta['since']), date.fromisoformat(meta['until'])
    if start > end or today < start or today > end:
        raise ValueError('invalid report coverage')
    if start >= today:
        raise ValueError('no complete day is covered')
    for row in meta['rows']:
        if not isinstance(row, dict):
            raise ValueError('invalid daily row')
        if row.get('campaign_id') != LAUNCH or row.get('ad_id') not in ad_ids:
            continue
        day = date.fromisoformat(row['date_start'])
        if row.get('date_stop') != row['date_start'] or not start <= day <= end:
            raise ValueError('daily grain or coverage mismatch')
        if day >= today:
            excluded_partial += 1
            continue
        values = {'spend': _number(row['spend']), 'impressions': _number(row['impressions'], True),
                  'registrations': _action(row.get('actions'), 'omni_complete_registration', 'complete_registration'),
                  'landing_page_views': _action(row.get('actions'), 'landing_page_view')}
        key = (row['ad_id'], day.isoformat())
        if key in seen:
            if seen[key] != values:
                raise ValueError('conflicting duplicate grain')
            duplicates += 1
            continue
        seen[key] = values
        for metric, value in values.items():
            totals[row['ad_id']][metric] += value
    result = {}
    for identity, values in totals.items():
        result[identity] = {key: str(value) for key, value in values.items()}
        count = values['registrations']
        result[identity]['attributed_cost_per_registration'] = str(values['spend']/count) if count else None
        result[identity]['status'] = 'observational_report_only'
    return result, {'since': start.isoformat(), 'until': min(end, today-timedelta(days=1)).isoformat(),
                    'current_partial_rows_excluded': excluded_partial, 'duplicate_rows_removed': duplicates}


def plan_experiment(snapshot, now=None):
    """Return exactly one versioned brief, including an inventory-only fallback.

    Ready means ready for HUMAN REVIEW only. A new asset remains an input, not a
    generated ad. Existing paused ads remain paused; referencing an existing
    image does not authorize publishing/reusing it or moving any Drive files.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware host clock required')
    source = ((snapshot or {}).get('sources') or {}).get('meta') or {}
    meta = source.get('data') or {}
    inventory = meta.get('inventory') or {}
    creative_inventory = meta.get('creative_inventory') or {}
    campaign = inventory.get('campaign') or {}
    captured = _time(source.get('last_success_at'))
    fresh = captured is not None and 0 <= (now-captured).total_seconds() <= 720 and not source.get('error')
    bound = (fresh and meta.get('verified') is True and meta.get('account_id') == ACCOUNT and
             inventory.get('verified') is True and campaign.get('id') == LAUNCH and campaign.get('account_id') == ACCOUNT and
             creative_inventory.get('verified') is True and creative_inventory.get('account_id') == ACCOUNT and
             creative_inventory.get('campaign_id') == LAUNCH)
    blockers, assets, observations, question, proposal = [], [], {}, None, None
    tracking = {'blocked': True, 'dependencies': [], 'status': 'not_evaluated', 'configuration_audit_verified': False}
    coverage = None
    if not bound:
        blockers.append('fresh_verified_launch_creative_inventory_required')
    else:
        inventory_ads = {row.get('id'): row for row in inventory.get('ads') or [] if isinstance(row, dict)}
        observed_ads = creative_inventory.get('ads') or []
        seen = {}
        conflict_ids = set()
        for ad in observed_ads:
            if not isinstance(ad, dict) or not isinstance(ad.get('id'), str) or ad['id'] not in inventory_ads:
                blockers.append('creative_ad_scope_mismatch')
                continue
            if ad['id'] in seen and seen[ad['id']] != ad:
                conflict_ids.add(ad['id'])
            seen[ad['id']] = ad
        for identity in sorted(seen):
            if identity in conflict_ids:
                blockers.append('conflicting_creative_versions')
                continue
            ad = seen[identity]
            original = inventory_ads[identity]
            if ad.get('adset_id') != original.get('adset_id') or ad.get('effective_status') != original.get('effective_status'):
                blockers.append('creative_inventory_changed_during_read')
                continue
            asset = _copy_inventory(ad)
            if asset is None:
                blockers.append('creative_fields_unavailable')
            else:
                assets.append(asset)
        if set(seen) != set(inventory_ads):
            blockers.append('creative_inventory_incomplete')
        try:
            if meta.get('complete') is not True or not isinstance(meta.get('rows'), list):
                raise ValueError('complete reporting required')
            observations, coverage = _metrics(meta, {asset['ad_id'] for asset in assets})
        except (ValueError, KeyError, TypeError, ArithmeticError):
            blockers.append('report_metrics_unavailable_or_inconsistent')
            observations = {}
        active_parent = campaign.get('effective_status') == 'ACTIVE'
        adsets = {row.get('id'): row for row in inventory.get('adsets') or [] if isinstance(row, dict)}
        candidates = [asset for asset in assets if active_parent and asset['effective_status'] == 'ACTIVE' and
                      (adsets.get(asset['adset_id']) or {}).get('effective_status') == 'ACTIVE' and
                      asset['copy']['primary_texts'] and len(asset['provider_image_hashes']) == 1 and not asset['video_ids'] and
                      len(asset['destinations']) == 1]
        pairs = []
        for index, first in enumerate(candidates):
            for second in candidates[index+1:]:
                if (first['adset_id'] == second['adset_id'] and first['copy'] == second['copy'] and
                    _landing(first) is not None and _landing(first) == _landing(second) and
                    first['call_to_action_types'] == second['call_to_action_types'] and
                    first['provider_image_hashes'] != second['provider_image_hashes']):
                    pairs.append((first, second))
        basis = None
        if pairs:
            first, second = pairs[0]
            basis = 'observed_same_copy_different_image'
        elif observations and len(candidates) >= 2:
            ordered = sorted(candidates, key=lambda asset: (-Decimal(observations[asset['ad_id']]['spend']), asset['ad_id']))
            first = ordered[0]
            alternatives = [asset for asset in ordered[1:] if asset['adset_id'] == first['adset_id'] and
                            asset['provider_image_hashes'] != first['provider_image_hashes']]
            second = alternatives[-1] if alternatives else None
            total = sum((Decimal(observations[a['ad_id']]['spend']) for a in candidates), Decimal(0))
            if second and total > 0:
                basis = 'observed_spend_distribution_not_creative_quality'
            else:
                first = second = None
        else:
            first = second = None
        if first and second:
            question = 'האם החלפת התמונה בלבד, תוך שמירה על הקופי והיעד של מודעה '+first['ad_id']+', משנה את העלות להרשמה מדווחת?'
            proposal = {'variable_to_change': 'image_only', 'basis': basis,
                        'baseline_ad_id': first['ad_id'], 'image_reference_ad_id': second['ad_id'],
                        'baseline_creative_id': first['creative_id'], 'reference_creative_id': second['creative_id'],
                        'before_image_hash': first['provider_image_hashes'][0],
                        'candidate_image_hash': second['provider_image_hashes'][0],
                        'copy_to_hold': first['copy'], 'destination_to_hold': (_landing(first) or {}).get('url_without_tracking'),
                        'landing_controls': _landing(first),
                        'cta_types_to_hold': first['call_to_action_types'], 'adset_id_to_hold': first['adset_id'],
                        'allocation': 'not_defined_until_approved_envelope',
                        'note': 'Existing results do not isolate the image effect. This is a proposed controlled comparison, not a winner declaration.'}
            tracking = _tracking(snapshot, now, [first, second])
            proposal['tracking_contract'] = {
                'observed_arms': [{'ad_id': asset['ad_id'], 'destination_urls': asset['destinations'],
                                   'url_tags': asset['observed_url_tags']} for asset in (first, second)],
                'hold_constant': ['scheme', 'host', 'port', 'path', 'business_parameters', 'fragment'],
                'must_differ_per_arm': 'Unique verified static ad_id or distinct utm_content without conflicting URL/tag duplicates.',
                'new_variant_tracking': {'ad_id': None, 'utm_content': None,
                                         'requires_actual_created_variant_id': True,
                                         'reuse_source_ad_tracking': False},
                'configuration_status': tracking['status'],
                'site_parameter_persistence_verified': False,
                'unique_registration_measurement_verified': False,
                'note': 'A clean configuration readback clears only the configuration collision gate, not site persistence or identity measurement.'}
            if tracking['blocked']:
                blockers.append('chosen_arms_tracking_configuration_blocked')
            if observations:
                total = sum((Decimal(observations[a['ad_id']]['spend']) for a in candidates), Decimal(0))
                proposal['observed_spend_distribution'] = {
                    'baseline_spend': observations[first['ad_id']]['spend'],
                    'image_reference_spend': observations[second['ad_id']]['spend'],
                    'baseline_share_of_candidate_spend': str(Decimal(observations[first['ad_id']]['spend'])/total) if total else None,
                    'interpretation': 'Delivery allocation only; zero spend does not mean creative failure.'}
        else:
            blockers.append('two_active_comparable_image_references_not_available')
    blockers = sorted(set(blockers))
    content = {'account_id': ACCOUNT, 'campaign_id': LAUNCH, 'goal': 'completed_website_registration',
               'inventory': assets, 'observations': observations, 'report_coverage': coverage,
               'question': question, 'proposal': proposal, 'blockers': blockers}
    version = _hash(content)
    return {**content, 'brief_id': _hash({'account': ACCOUNT, 'campaign': LAUNCH, 'brief': 'registration_image_experiment'})[:24],
            'version': version, 'source_snapshot_sha256': _hash({'meta': meta, 'last_success_at': source.get('last_success_at')}),
            'prepared_at': now.isoformat(), 'source_observed_at': source.get('last_success_at'),
            'expires_at': (captured+timedelta(seconds=720)).isoformat() if captured else None,
            'kind': 'creative_experiment_brief' if question else 'creative_inventory_brief',
            'state': 'ready_for_review' if question and not blockers else 'inventory_only_requires_evidence',
            'measured_metric': 'Meta attributed complete registrations and attributed cost per registration',
            'blocked_by_tracking': tracking['blocked'], 'tracking_configuration_status': tracking['status'],
            'tracking_configuration_audit_verified': tracking['configuration_audit_verified'],
            'tracking_dependencies': tracking['dependencies'],
            'dependency_case_id': tracking['dependencies'][0]['case_id'] if tracking['dependencies'] else None,
            'dependency_fingerprint': tracking['dependencies'][0]['fingerprint'] if tracking['dependencies'] else None,
            'site_parameter_persistence_verified': False,
            'unique_registrations_verified': False, 'measurement_verified': False,
            'acceptance_before_launch': ['Independently verify successful registration persistence and single-event/identity matching.',
                'Approve exact testing envelope, total spend exposure, stop/recheck criteria and time window.',
                'Read and hold audience, placements, attribution, destination, copy and CTA; review uncollected creative enhancements.',
                'Assign unique tracking to actual created arm IDs; never reuse source/reference ad tracking for a new variant.',
                'Validate source asset rights, feed/story fit and rendered media before publishing any paused draft.',
                'Do not interpret observational delivery allocation or small samples as causal winners.'],
            'budget_change': None, 'spend_cap': None, 'published': False,
            'execute_permission': False, 'live_action_allowed': False,
            'limitations': ['Observed copy/media IDs are factual; visual contents and customer motives were not inferred.',
                           'Paused and zero-spend ads remain inventory, not failed tests.',
                           'Current partial day is excluded from comparison; attribution can still mature.',
                           'This brief does not create, activate, pause or scale an ad.']}
