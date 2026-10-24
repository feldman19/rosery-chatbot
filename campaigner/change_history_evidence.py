"""Read only fixed, historical receipts. Never assert complete change history."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from business import ACTIVE as BUSINESS

ACCOUNT = BUSINESS.meta_account
ACTION = 'seven_active_ads_approved_copy_and_enhancement_repairs'
BEFORE = 'outputs/seven-ads-repair-before-2026-10-19.json'
AFTER = 'outputs/seven-ads-repair-after-2026-10-19.json'
LEDGER = 'outputs/rosery-action-ledger-events.jsonl'


def receipt_changes(event, before, after, sources):
    """Prove bounded observed creative replacement, not an exact edit timestamp."""
    if event.get('account') != ACCOUNT or event.get('action') != ACTION:
        return []
    if event.get('before') != BEFORE or event.get('after') != AFTER:
        return []
    try:
        start, end = (datetime.fromisoformat(x['checked_at'].replace('Z', '+00:00')) for x in (before, after))
        if not start.tzinfo or not end.tzinfo or start >= end:
            return []
        old, new = ({x['id']: x for x in data['ads']} for data in (before, after))
        if len(old) != len(before['ads']) or len(new) != len(after['ads']):
            return []
    except (KeyError, TypeError, ValueError, AttributeError):
        return []
    result = []
    for identity in old.keys() & new.keys():
        a, b = old[identity], new[identity]
        if not a.get('adset_id') or not a.get('campaign_id'):
            continue
        if any(a.get(k) != b.get(k) for k in ('adset_id', 'campaign_id')):
            continue
        first, second = (x.get('creative', {}).get('id') for x in (a, b))
        if not first or not second or first == second:
            continue
        result.append({
            'entity_id': identity, 'entity_type': 'ads', 'adset_id': b['adset_id'],
            'campaign_id': b['campaign_id'], 'event_type': 'observed_creative_replacement',
            'before_creative_id': first, 'after_creative_id': second,
            'observed_interval': {'after': before['checked_at'], 'by': after['checked_at']},
            'exact_change_time': None, 'sources': sources,
            'coverage': 'selected_local_receipts_only', 'latest_change_proven': False,
            'attribution_maturity_proven': False,
        })
    return sorted(result, key=lambda x: x['entity_id'])


def load_saved_change_evidence(workspace):
    root = Path(workspace)
    try:
        raw = {p: (root / p).read_bytes() for p in (LEDGER, BEFORE, AFTER)}
        events = [json.loads(line) for line in raw[LEDGER].decode('utf-8-sig').splitlines() if line.strip()]
        event = next((e for e in events if e.get('action') == ACTION and e.get('account') == ACCOUNT), {})
        sources = [{'path': p, 'sha256': hashlib.sha256(raw[p]).hexdigest()} for p in raw]
        return receipt_changes(event, json.loads(raw[BEFORE]), json.loads(raw[AFTER]), sources)
    except (OSError, ValueError, TypeError, StopIteration):
        return []
