"""Pure evidence-bound operator workplan; no provider calls or permissions.

build_workplan(snapshot, expectations=None, now=None) consumes /api/live-shaped
snapshots. Trusted host must authenticate source acquisition before supplying
data. Data.verified is necessary, not a substitute for that host boundary.

Optional expectations is host-controlled, not untrusted API input:
{'campaigns': {campaign_id: {'verified': True, 'source_ref': 'approval:...',
  'goal': 'registration'|'purchase', 'expected_event': 'COMPLETE_REGISTRATION',
  'expected_pixel_id': '...'}},
 'ads': {ad_id: {'verified': True, 'source_ref': 'approval:...',
  'destination_url': 'https://...'}}}.
Expectations describe desired settings, never authority to execute changes.
Observed config requires fresh authenticated inventory, valid parent/child IDs,
and PRESENT leaf values. Absent fields are unknown, not configuration failures.
Destination support expects an explicitly collected ad.destination_url field;
or separately verified creative_inventory.ads[].creative link fields. Positive
creative links are usable; all-null creative roots do not rule out existing-post
links. An empty creative destination therefore also requires the trusted host's
destination_audit_complete=True after resolving supported story references.

Stable IDs identify conditions for ActionDesk integration; fingerprints change
with semantic evidence. The module is not a new queue and does not mark actions
done, resolve historical findings, create approvals, or execute provider writes.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from urllib.parse import urlsplit

from meta_direct import ACCOUNT, LAUNCH
from connections import SHOP


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (AttributeError, ValueError, TypeError):
        return None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _leaf(row, path):
    value = row
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return False, None
        value = value[key]
    return True, value


def _desired(row):
    return isinstance(row, dict) and row.get('verified') is True and isinstance(row.get('source_ref'), str) and bool(row['source_ref'])


def _web_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _entities(rows):
    result, conflicts = {}, set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            continue
        identity = row['id']
        if identity in conflicts:
            continue
        if identity in result and result[identity] != row:
            conflicts.add(identity)
            del result[identity]
        else:
            result[identity] = row
    return result, conflicts


def build_workplan(snapshot, expectations=None, now=None):
    """Return tasks, scoped campaign summaries and one practical next step.

    Times are evidence age, not performance maturity. Freshness is 12 minutes,
    matching the host's cache contract; it is NOT an ad optimization threshold.
    Zero impressions/spend or paused entities never imply budget waste.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware host time required')
    if not isinstance(snapshot, dict) or not isinstance(expectations or {}, dict):
        raise ValueError('normalized snapshot and host expectations required')
    expectations = expectations or {}
    sources = snapshot.get('sources') or {}
    tasks, completed, unknowns = {}, [], []

    def add(kind, entity_type, entity_id, title, explanation, evidence, proposal, priority=2):
        stable = _digest({'account': ACCOUNT, 'kind': kind, 'type': entity_type, 'entity': entity_id})[:24]
        task = {'id': stable, 'kind': kind, 'entity': {'platform': 'meta' if entity_type != 'source' else entity_id,
                'account_id': ACCOUNT if entity_type != 'source' else None,
                'type': entity_type, 'id': entity_id},
                'title': title, 'explanation': explanation, 'priority': priority,
                'evidence': evidence, 'proposal': proposal,
                'execute_permission': False, 'execution_supported': False,
                'state': 'proposed_for_review',
                'evidence_refs': ['sha256:'+_digest(evidence)],
                'expires_at': ((_stamp(evidence.get('observed_at')) or now)+timedelta(seconds=720)).isoformat(),
                'recheck_condition': 'Next successful source collection or any entity/configuration change.',
                'guardrails': ['Re-read exact entity and evidence before any change.',
                               'Obtain exact current authorization; expectations are not permission.',
                               'Verify saved fields after a change; never publish unrelated drafts.']}
        semantic = {key: value for key, value in evidence.items() if key not in {'observed_at', 'checked_at'}}
        task['fingerprint'] = _digest({'kind': kind, 'evidence': semantic, 'proposal': proposal})
        tasks[stable] = task

    good_sources = {}
    for key, expected_identity in [('meta', ACCOUNT), ('shopify', SHOP)]:
        source = sources.get(key) or {}
        data = source.get('data') or {}
        timestamp = _stamp(source.get('last_success_at'))
        age = (now - timestamp).total_seconds() if timestamp else None
        identity = data.get('account_id') if key == 'meta' else data.get('shop')
        bound = data.get('verified') is True and identity == expected_identity
        fresh = age is not None and 0 <= age <= 720 and not source.get('error')
        complete = data.get('complete') is not False
        # Meta reports explicitly expose paging completeness. Shopify aggregate
        # reports do not; missing table data still means unknown, not zero.
        if key == 'meta':
            complete = data.get('complete') is True
        else:
            complete = complete and isinstance(data.get('table'), dict)
        good_sources[key] = bound and fresh and complete
        observed = {'source': key, 'observed_at': source.get('last_success_at'),
                    'identity_verified': bound, 'complete': complete,
                    'last_attempt_failed': bool(source.get('error'))}
        if source.get('error'):
            add('report_read_failed', 'source', key, 'קריאת '+key+' נכשלה',
                'מוצגת הקריאה האחרונה שנשמרה, אם קיימת. כשל דיווח אינו הוכחה לאפס תוצאות.', observed,
                {'kind': 'read_only_refresh', 'source': key, 'provider_write': False}, 1)
        elif timestamp and not fresh:
            add('report_stale', 'source', key, 'צריך לרענן את נתוני '+key,
                'הנתונים מחוץ לחלון העדכון של המערכת; אין להסיק מהם מצב נוכחי.', observed,
                {'kind': 'read_only_refresh', 'source': key, 'provider_write': False}, 1)
        elif not bound:
            unknowns.append({'kind': 'source_not_verified', 'source': key})
        elif not complete:
            add('report_incomplete', 'source', key, 'דוח '+key+' לא הושלם',
                'נתונים חלקיים אינם אפס פעילות. נדרשת קריאה מלאה לפני החלטה.', observed,
                {'kind': 'read_only_refresh', 'source': key, 'provider_write': False}, 1)
        elif fresh:
            completed.append({'kind': 'source_read', 'source': key, 'observed_at': source.get('last_success_at')})

    meta_source = sources.get('meta') or {}
    meta = meta_source.get('data') or {}
    inventory = meta.get('inventory') or {}
    campaign = inventory.get('campaign') or {}
    inventory_timestamp = _stamp(meta_source.get('last_success_at'))
    inventory_fresh = (inventory_timestamp is not None and not meta_source.get('error') and
                       0 <= (now-inventory_timestamp).total_seconds() <= 720)
    config_verified = (inventory_fresh and meta.get('verified') is True and meta.get('account_id') == ACCOUNT and inventory.get('verified') is True and
                       campaign.get('account_id') == ACCOUNT and campaign.get('id') == LAUNCH)
    campaign_summaries, cases = [], []
    if config_verified:
        completed.append({'kind': 'configuration_read', 'campaign_id': campaign['id'],
                          'observed_at': meta_source.get('last_success_at')})
        objective = campaign.get('objective')
        family = ('leads' if objective in {'OUTCOME_LEADS', 'LEAD_GENERATION'} else
                  'sales' if objective in {'OUTCOME_SALES', 'CONVERSIONS', 'PRODUCT_CATALOG_SALES'} else 'unknown')
        desired_campaign = (expectations.get('campaigns') or {}).get(campaign['id']) or {}
        goal = desired_campaign.get('goal') if _desired(desired_campaign) else None
        campaign_summaries.append({'id': campaign['id'], 'name': campaign.get('name'),
                                   'objective': objective, 'objective_family': family,
                                   'host_confirmed_goal': goal,
                                   'result_metric': 'registration' if goal == 'registration' else 'purchase' if goal == 'purchase' else 'unknown',
                                   'status': campaign.get('effective_status'),
                                   'measurement_verified': False})
        active_parent = campaign.get('effective_status') == 'ACTIVE'
        adsets, adset_conflicts = _entities(inventory.get('adsets'))
        unknowns.extend({'kind': 'conflicting_inventory_versions', 'entity_id': identity} for identity in sorted(adset_conflicts))
        for adset in adsets.values():
            if not active_parent or adset.get('effective_status') != 'ACTIVE':
                continue
            # Existing collector is campaign-scoped. If a parent ID is returned,
            # it must agree; never turn foreign inventory into a proposal.
            if adset.get('campaign_id') not in (None, campaign['id']):
                unknowns.append({'kind': 'inventory_parent_mismatch', 'entity_id': adset['id']})
                continue
            if not _desired(desired_campaign):
                continue
            for field, expected_key, kind, title in [
                    ('custom_event_type', 'expected_event', 'optimization_event_mismatch', 'אירוע האופטימיזציה שונה מהיעד שאושר'),
                    ('pixel_id', 'expected_pixel_id', 'pixel_mismatch', 'הפיקסל שונה מההגדרה שאושרה')]:
                desired = desired_campaign.get(expected_key)
                present, observed_value = _leaf(adset, 'promoted_object.'+field)
                if not isinstance(desired, str) or not desired or not present:
                    if desired is not None and not present:
                        unknowns.append({'kind': 'configuration_field_not_collected', 'entity_id': adset['id'], 'field': 'promoted_object.'+field})
                    continue
                if observed_value == desired:
                    continue
                before = adset['promoted_object']
                after = {**before, field: desired}
                add(kind, 'adset', adset['id'], title,
                    'פער בהגדרה שנקראה באדסט פעיל; זו אינה הוכחה לתקלה באתר או להפסד כספי.',
                    {'observed_at': meta_source.get('last_success_at'), 'campaign_id': campaign['id'],
                     'entity_name': adset.get('name'), 'field': 'promoted_object.'+field,
                     'observed': observed_value, 'expected': desired,
                     'expectation_source_ref': desired_campaign['source_ref']},
                    {'kind': 'review_adset_field_change', 'entity_id': adset['id'],
                     'before': {'promoted_object': before}, 'after': {'promoted_object': after},
                     'rollback': {'promoted_object': before}, 'budget_change': None,
                     'precondition_hash': _digest(before)}, 1)
        creative_inventory = meta.get('creative_inventory') or {}
        creative_rows, creative_conflicts = _entities(creative_inventory.get('ads')) if creative_inventory.get('verified') is True else ({}, set())
        ads, ad_conflicts = _entities(inventory.get('ads'))
        unknowns.extend({'kind': 'conflicting_inventory_versions', 'entity_id': identity}
                        for identity in sorted(creative_conflicts | ad_conflicts))
        for ad in ads.values():
            if not isinstance(ad, dict) or not ad.get('id') or not active_parent or ad.get('effective_status') != 'ACTIVE':
                continue
            parent = adsets.get(ad.get('adset_id'))
            if not parent or parent.get('effective_status') != 'ACTIVE' or parent.get('campaign_id') not in (None, campaign['id']):
                continue
            desired_ad = (expectations.get('ads') or {}).get(ad['id']) or {}
            present, destination = _leaf(ad, 'destination_url')
            if not present and ad['id'] in creative_rows:
                creative = creative_rows[ad['id']].get('creative') or {}
                if not isinstance(creative, dict):
                    unknowns.append({'kind': 'creative_fields_unavailable', 'entity_id': ad['id']})
                    continue
                observed_urls = []
                for path in ['link_url', 'object_story_spec.link_data.link',
                             'object_story_spec.video_data.call_to_action.value.link']:
                    collected, link = _leaf(creative, path)
                    if collected and isinstance(link, str) and link:
                        observed_urls.append(link)
                asset_links = (creative.get('asset_feed_spec') or {}).get('link_urls') if isinstance(creative.get('asset_feed_spec'), dict) else None
                for item in asset_links if isinstance(asset_links, list) else []:
                    if isinstance(item, dict) and item.get('website_url'):
                        observed_urls.append(item['website_url'])
                if observed_urls:
                    present, destination = True, observed_urls[0]
                elif (creative_rows[ad['id']].get('destination_audit_complete') is True and
                      all(field in creative for field in ('link_url', 'object_story_spec', 'asset_feed_spec'))):
                    present, destination = True, None
            if not present:
                unknowns.append({'kind': 'destination_not_collected', 'entity_id': ad['id']})
                continue
            if destination not in (None, '') or not _desired(desired_ad) or not _web_url(desired_ad.get('destination_url')):
                continue
            # Meta creative objects may be immutable: prepare a paused ordinary
            # replacement for review, never pretend an inline URL patch is safe.
            add('destination_missing', 'ad', ad['id'], 'לא הוגדר קישור במודעה הפעילה',
                'השדה שנקרא ריק. היעד החלופי נשען על תיעוד מאושר; הפעלה דורשת אישור נפרד.',
                {'observed_at': meta_source.get('last_success_at'), 'entity_name': ad.get('name'),
                 'adset_id': ad['adset_id'], 'destination_url': destination,
                 'expectation_source_ref': desired_ad['source_ref']},
                {'kind': 'prepare_paused_replacement_ad', 'copy_from_ad_id': ad['id'],
                 'adset_id': ad['adset_id'], 'destination_url': desired_ad['destination_url'],
                 'status': 'PAUSED', 'original_ad_change': None, 'budget_change': None,
                 'rollback': 'Keep original unchanged; replacement remains paused.'}, 1)
        # Morning disposition is a scoped diagnostic case, not a budget rule.
        observed_at = meta_source.get('last_success_at')
        report_case = {'campaign_id': campaign['id'], 'objective_family': family,
                       'host_confirmed_goal': goal, 'evidence_refs': ['sha256:'+_digest({'campaign': campaign, 'rows': meta.get('rows', [])})],
                       'observed_at': observed_at,
                       'expires_at': (_stamp(observed_at)+timedelta(seconds=720)).isoformat(),
                       'recheck_condition': 'Next successful collection, status/schedule change, or completion of a measurement test.',
                       'execute_permission': False}
        campaign_tasks = [item for item in tasks.values() if item['entity']['type'] != 'source']
        if not active_parent:
            disposition, reason = 'retain', 'הקמפיין אינו פעיל כרגע; אין מכאן המלצה להפעילו או טענה לבזבוז תקציב.'
        elif campaign_tasks:
            disposition, reason = 'investigate_measurement', 'נמצא פער הגדרה מאומת ביחס לתיעוד היעד; יש לבחון את הראיה המדויקת.'
        else:
            yesterday = None
            try:
                yesterday = (datetime.fromisoformat(meta['today']).date()-timedelta(days=1)).isoformat()
            except (KeyError, TypeError, ValueError):
                pass
            values, seen_grain, valid = [], set(), True
            for row in meta.get('rows') or []:
                if not isinstance(row, dict):
                    valid = False
                    continue
                if row.get('campaign_id') != campaign['id'] or row.get('date_start') != yesterday:
                    continue
                grain = (row.get('ad_id'), row.get('date_start'), row.get('date_stop'))
                if grain in seen_grain:
                    continue
                seen_grain.add(grain)
                try:
                    spend, impressions = Decimal(str(row['spend'])), Decimal(str(row['impressions']))
                    if not spend.is_finite() or not impressions.is_finite() or spend < 0 or impressions < 0:
                        raise ValueError()
                    values.append((spend, impressions))
                except (KeyError, TypeError, ValueError, InvalidOperation):
                    valid = False
            full_day = (meta.get('complete') is True and yesterday and
                        isinstance(meta.get('since'), str) and isinstance(meta.get('until'), str) and
                        meta['since'] <= yesterday <= meta['until'])
            if full_day and valid and not any(spend or impressions for spend, impressions in values):
                disposition, reason = 'investigate_delivery', 'הקמפיין פעיל כעת, וביום המלא האחרון לא דווחה פעילות. לא ידוע כמה זמן היה פעיל; אין הוכחה לתקלה.'
                add('inspect_delivery', 'campaign', campaign['id'], 'בדוק מדוע אין פעילות מדווחת', reason,
                    {'observed_at': observed_at, 'campaign_id': campaign['id'], 'reported_day': yesterday,
                     'current_status': campaign['effective_status'], 'active_during_reported_day': 'unknown'},
                    {'kind': 'read_only_delivery_review', 'entity_id': campaign['id'],
                     'inspect': ['schedule_and_start_time', 'current_delivery_diagnostics', 'account_delivery_limits'],
                     'provider_write': False}, 2)
            elif full_day and valid:
                disposition, reason = 'retain', 'יש פעילות מדווחת ולא נמצא פער הגדרה מאומת בנתונים שנקראו. אין מכאן המלצה לסקייל.'
            else:
                disposition, reason = 'insufficient_evidence', 'אין כיסוי מלא וברור ליום האחרון; אין להסיק אפס פעילות.'
        report_case.update(disposition=disposition, explanation=reason)
        report_case['id'] = _digest({'account': ACCOUNT, 'campaign_id': campaign['id'], 'case': 'morning'})[:24]
        cases.append(report_case)
    elif good_sources.get('meta'):
        unknowns.append({'kind': 'configuration_inventory_not_verified', 'campaign_id': LAUNCH})

    if not cases:
        observed_at = meta_source.get('last_success_at')
        cases.append({'id': _digest({'account': ACCOUNT, 'campaign_id': LAUNCH, 'case': 'morning'})[:24],
                      'campaign_id': LAUNCH, 'disposition': 'insufficient_evidence',
                      'explanation': 'הנתונים או הגדרות הקמפיין אינם מאומתים ועדכניים מספיק לאבחון.',
                      'evidence_refs': ['sha256:'+_digest({'observed_at': observed_at,
                                                          'source_verified': meta.get('verified') is True,
                                                          'inventory_verified': inventory.get('verified') is True})],
                      'observed_at': observed_at,
                      'expires_at': ((_stamp(observed_at) or now)+timedelta(seconds=720)).isoformat(),
                      'recheck_condition': 'Next successful verified source and configuration read.',
                      'execute_permission': False})

    ordered = sorted(tasks.values(), key=lambda task: (task['priority'], task['kind'], task['entity']['id']))
    if ordered:
        next_step = {'task_id': ordered[0]['id'], 'title': ordered[0]['title'],
                     'entity': ordered[0]['entity'], 'requires_provider_write': False,
                     'action': 'Review the exact evidence and proposed next step; no change is authorized.'}
        blocker = 'confirmed_issue_or_reporting_gap'
    elif not good_sources.get('meta'):
        next_step = {'task_id': None, 'title': 'חבר קריאת Meta מאומתת לפני הכנת שינויים',
                     'action': 'Open connection settings; no campaign change.'}
        blocker = 'meta_source_not_verified'
    elif not config_verified:
        next_step = {'task_id': None, 'title': 'השלם קריאת הגדרות של קמפיין ההשקה',
                     'action': 'Read exact campaign/adset/ad fields; do not infer missing settings.'}
        blocker = 'configuration_not_verified'
    else:
        next_step = {'task_id': None, 'title': 'לא נמצא תיקון הגדרות שמבוסס על הראיות שנקראו',
                     'action': 'Continue collection. Purchase/registration reconciliation is not proven by these reports.'}
        blocker = None
    return {'account_id': ACCOUNT, 'as_of': now.isoformat(), 'tasks': ordered,
            'campaigns': campaign_summaries, 'analysis_cases': cases, 'completed': completed, 'unknowns': unknowns,
            'operator_summary': {'done': completed, 'next': next_step, 'blocker': blocker,
                                 'measurement_status': 'not_established_by_aggregate_reports'},
            'live_action_allowed': False, 'execute_permission': False,
            'limitations': ['Only the collected launch campaign inventory is reviewed.',
                           'No budget waste, profitability, winner or scale conclusion is inferred.',
                           'Expectations and source verification must come from trusted host evidence.']}
