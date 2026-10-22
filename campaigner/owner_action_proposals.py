"""Select existing exact owner actions, never invent budget recommendations.

Inputs must be host-produced management/operation/proposal reports. This pure
selector grants no authority; existing preview/approval/execute gates remain.
"""
from datetime import datetime, timezone
import re
import hashlib
import json
from copy import deepcopy
from experiment_suggestions import propose_experiments, assess_experiment_evidence

ACCOUNT = '393059510164294'
_HASH = re.compile(r'^[a-f0-9]{64}$')


def _stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except (AttributeError, TypeError, ValueError): return None


def _launch_source_hashes(op):
    """Exact selected bytes; filenames/new request IDs cannot hide duplication."""
    files=(op.get('manifest') or {}).get('files')
    if not isinstance(files,list) or not files:return None
    hashes=[x.get('sha256') if isinstance(x,dict) else None for x in files]
    if any(not isinstance(x,str) or not _HASH.fullmatch(x) for x in hashes):return None
    return set(hashes)


def build_owner_action_proposals(report, operations_status=None, proposal_report=None, *, now=None):
    """Return at most three exact proposals. No provider calls or writes.

    Unbound creative drafts are intentionally never upload suggestions: available
    alone cannot establish they were not already used. New creative work must
    first produce a fresh, exact, unattempted prepare_launch operation.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None: raise ValueError('Timezone-aware now required')
    if not isinstance(report, dict) or report.get('account_id') != ACCOUNT:
        raise ValueError('ROSERY account mismatch')
    result = {'account_id': ACCOUNT, 'proposals': [], 'blocked_reasons': [], 'live_action_allowed': False, 'evidence_assessment': assess_experiment_evidence(report)}
    checked = _stamp(report.get('checked_at'))
    source_checked = _stamp(report.get('freshness', {}).get('meta_last_success_at'))
    if (report.get('freshness', {}).get('state') != 'fresh' or checked is None or not 0 <= (now-checked).total_seconds() <= 900
        or source_checked is None or not 0 <= (now-source_checked).total_seconds() <= 900):
        result['blocked_reasons'].append('fresh_report_required'); return result
    status = operations_status or {}
    if status and status.get('account_id') != ACCOUNT:
        raise ValueError('ROSERY operations mismatch')
    proposals = status.get('proposals', [])
    if not isinstance(proposals, list): proposals = []
    # Any attempted/uncertain write on the same intent suppresses stale siblings.
    risky_entities, attempted_launches = set(), []
    for op in proposals:
        if not isinstance(op, dict): continue
        manifest = op.get('manifest') or {}
        if op.get('provider_write_attempted') is not False or op.get('state') in ('queued','running','scheduled','verified','partial','unknown'):
            if op.get('action') == 'prepare_launch': attempted_launches.append(op)
            if manifest.get('entity_id'): risky_entities.add(manifest['entity_id'])
    seen = set()
    for op in reversed(proposals):
        if not isinstance(op, dict): continue
        identity, digest = op.get('operation_id'), op.get('digest')
        expiry = _stamp(op.get('expires_at'))
        action, manifest = op.get('action'), op.get('manifest') or {}
        if (not isinstance(identity,str) or identity in seen or not isinstance(digest,str) or not _HASH.fullmatch(digest)
            or op.get('state') != 'prepared' or op.get('blocked_reasons') != []
            or op.get('provider_write_attempted') is not False or op.get('provider_write_performed') is not False
            or op.get('scheduled_at') is not None or expiry is None or expiry <= now
            or action not in ('pause','activate','set_budget','prepare_launch')
            or manifest.get('account_id', ACCOUNT) != ACCOUNT): continue
        if op.get('approval_status') == 'approved':
            if op.get('execution_ready') is not True: continue
            next_type = 'execute_approved_operation'
        elif op.get('approval_status') == 'pending': next_type = 'review_exact_operation'
        else: continue
        before, after = op.get('before'), op.get('after')
        if not isinstance(before,dict) or not isinstance(after,dict): continue
        target = manifest.get('entity_id')
        if action == 'prepare_launch':
            # Require exact unused paused-operation output, not an old signed draft.
            selected=_launch_source_hashes(op)
            # A proven disjoint new selection is not an old launch replay.
            # Missing historical hashes or any reused bytes remain conservative.
            if selected is None or any(_launch_source_hashes(old) is None or selected & _launch_source_hashes(old) for old in attempted_launches):continue
            if after.get('new_adsets') != 'PAUSED' or after.get('new_ads') != 'PAUSED' or after.get('parent_unchanged') is not True: continue
            binding = (proposal_report or {}).get('source_binding')
            if not (proposal_report or {}).get('available') or not isinstance(binding,dict): continue
            proposed = (proposal_report or {}).get('proposal') or {}
            if proposed.get('meta_draft_id') or any(ad.get('meta_draft_id') for ad in proposed.get('ads',[]) if isinstance(ad,dict)): continue
            # Public manifest omits source_digest. No exact binding means no upload card.
            if manifest.get('source_binding') != binding: continue
            title, why = 'לאשר הכנת מודעות מושהות', 'כבר הוכנה פעולה מדויקת עם חומרי מקור קשורים; ההעלאה תשאיר את המודעות והאדסטים מושהים.'
        else:
            if target in risky_entities or not target or before.get('id') != target: continue
            changes = manifest.get('changes') or {}
            if action == 'set_budget':
                if set(changes) not in ({'daily_budget'}, {'lifetime_budget'}): continue
                value = next(iter(changes.values()))
                if type(value) is not int or value <= 0: continue
                title = 'לבדוק ולאשר את שינוי התקציב שהכנת'
            else:
                if changes != {'status': 'PAUSED' if action == 'pause' else 'ACTIVE'}: continue
                title = 'לבדוק ולאשר השהיה' if action == 'pause' else 'לבדוק ולאשר הפעלה'
            binding = {'account_id': ACCOUNT, 'entity_id': target, 'entity_type': manifest.get('entity_type')}
            why = 'זו בקשה מדויקת שכבר הכנת, ולא המלצת סקייל חדשה. מוצג השינוי לפני אישורך.'
        seen.add(identity)
        result['proposals'].append({'id': 'operation:'+identity, 'title': title, 'why': why,
            'before': deepcopy(before), 'after': deepcopy(after),
            'risk': 'הפעולה עשויה לשנות פרסום; האישור חל רק על הגרסה והתקציב המדויקים. לפני ביצוע נשמרות בדיקות המצב הקיימות.',
            'source_binding': deepcopy(binding), 'approval_required': op.get('approval_status') == 'pending',
            'next_action': {'type': next_type, 'operation_id': identity, 'digest': digest},
            'expires_at': op['expires_at'], 'is_performance_recommendation': False})
        if len(result['proposals']) == 3: break
    slots = 3 - len(result['proposals'])
    result['proposals'].extend(propose_experiments(report)[:slots])
    slots = 3 - len(result['proposals'])
    if slots:
        windows = report.get('windows') or {}
        latest = windows.get('1') or {}
        rows = latest.get('adsets') if latest.get('partial') is False else []
        rows = rows if isinstance(rows, list) else []
        binding = {'account_id': ACCOUNT,
                   'meta_last_success_at': report.get('freshness', {}).get('meta_last_success_at'),
                   'shopify_last_success_at': report.get('freshness', {}).get('shopify_last_success_at'),
                   'snapshot_sha256': hashlib.sha256(json.dumps(windows, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()}
        active = [row for row in rows if row.get('configured_active') is True and row.get('result_type') == 'purchase' and row.get('campaign_id') and row.get('id')]
        missing = [row for row in active if row.get('purchases') is None]
        if missing:
            ids = sorted(row['id'] for row in missing)
            result['proposals'].append({'id': 'analysis:missing-results:' + binding['snapshot_sha256'][:16],
                'title': 'לרכז את פערי התוצאות באדסטים הפעילים',
                'why': 'בחלק מהאדסטים הפעילים חסרים נתוני רכישות. אכין פירוט של ההוצאה ושל אילו מודעות חסרות, בלי להפוך חוסר לאפס.',
                'before': {'entity_ids': ids, 'results': 'unknown'},
                'after': {'artifact': 'missing_results_by_ad', 'campaign_changes': False},
                'risk': 'הדוח המקומי מזהה את הפער; הוא אינו מבטיח תיקון המדידה או שחזור רכישות חסרות.',
                'source_binding': binding, 'approval_required': False,
                'next_action': {'type': 'local_reconciliation', 'entity_ids': ids, 'source_binding': binding},
                'result_use': 'למנוע עצירה או סקייל על סמך נתוני המרות חסרים ולזהות מה עוד צריך למשוך.',
                'is_performance_recommendation': False})
            slots -= 1
        peers = {}
        for row in active:
            spend, purchases = row.get('spend'), row.get('purchases')
            if type(spend) in (int, float) and type(purchases) in (int, float) and spend >= 0 and purchases > 0:
                peers.setdefault(row['campaign_id'], []).append(row)
        groups = [group for group in peers.values() if len(group) >= 2]
        if slots and groups:
            group = max(groups, key=lambda group: max(row['spend']/row['purchases'] for row in group))
            target = max(group, key=lambda row: row['spend']/row['purchases'])
            ids = sorted(row['id'] for row in group)
            result['proposals'].append({'id': 'analysis:compare-ads:' + target['id'] + ':' + binding['snapshot_sha256'][:16],
                'title': 'לנתח אילו מודעות מסבירות את העלות הגבוהה באדסט',
                'why': 'באדסט ' + str(target.get('name') or target['id']) + ' העלות הנצפית לרכישה ביום המלא האחרון גבוהה יותר מהאדסטים האחרים באותו קמפיין שיש להם נתוני רכישות מלאים. אכין פירוט ברמת המודעות.',
                'before': {'entity_id': target['id'], 'observed_cpa': target['spend']/target['purchases'], 'peer_entity_ids': ids},
                'after': {'artifact': 'per_ad_comparison', 'campaign_changes': False},
                'risk': 'זו השוואה תיאורית במדגם הקיים; אינה מוכיחה כשל קריאייטיב, הבדל קהל או חוסר רווחיות.',
                'source_binding': binding, 'approval_required': False,
                'next_action': {'type': 'local_analysis', 'entity_ids': [target['id']], 'peer_entity_ids': ids, 'source_binding': binding},
                'result_use': 'לבחור אם יש בסיס להצעה מדויקת ברמת מודעה, במקום לעצור אדסט שלם לפי ממוצע.',
                'is_performance_recommendation': False})
    if not result['proposals']:
        result['blocked_reasons'].append('no_fresh_unattempted_exact_operation_or_supported_analysis')
    return result
