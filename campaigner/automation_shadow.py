"""Read-only shadow evaluation. Only local SQLite writes; no credential/transport imports."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import threading

from rule_engine import evaluate_report, _rescue, _number
from automation_policy import digest


def evaluate_shadow(report, book, policy, now):
    # Owner threshold overrides the effective failure threshold, including default attribution.
    if book.get('status') != 'available':
        return {'items': [], 'blockers': ['rulebook_unavailable'], 'status': 'blocked',
                'provider_writes': 0, 'live_action_allowed': False}
    if not isinstance(report, dict):
        report = {}
    effective = deepcopy(book)
    threshold = policy['roas_threshold']
    for name in ('roas_ladder', 'default_attribution_threshold'):
        effective['rules'][name]['params']['fail_below'] = threshold
    result = evaluate_report(report, effective, now=now)
    window = (report.get('windows') or {}).get('3') or {}
    rows = {r['id']: r for r in window.get('ads', [])}
    candidates = [p for p in result['proposals'] if p['kind'] == 'pause_ad']
    selected = {p['entity_id'] for p in candidates}
    inventory = ((report.get('windows') or {}).get('7') or {})
    active = {}
    for row in inventory.get('ads', []):
        if (row.get('configured_active') is True and row.get('hierarchy_complete') is True
                and not row.get('configured_delivery_blockers')):
            active.setdefault(row.get('adset_id'), set()).add(row['id'])
    items = []
    for proposal in candidates:
        row = rows[proposal['entity_id']]
        blockers = list(proposal['blockers'])
        if row.get('hierarchy_complete') is not True:
            blockers.append('hierarchy_unknown')
        if row.get('configured_delivery_blockers'):
            blockers.append('parent_or_ad_paused')
        if inventory.get('partial') is not False:
            blockers.append('inventory_incomplete')
        if not row.get('adset_id') or not (active.get(row.get('adset_id'), set()) - selected):
            blockers.append('last_active_ad_in_adset')
        if _rescue(book, row.get('funnel') or {}) is None:
            blockers.append('rescue_signal_unknown')
        if any(_number(row.get(k)) is None for k in ('spend', 'purchases', 'attributed_revenue')):
            blockers.append('metrics_unknown')
        items.append({**proposal, 'blockers': blockers, 'would_pause': not blockers,
                      'evidence': deepcopy(row), 'label': 'המערכת הייתה משהה' if not blockers else 'השהיה חסומה'})
    return {'items': items, 'assessments': result['decisions'],
            'blockers': result['blocked_by'], 'status': result['status'],
            'provider_writes': 0, 'live_action_allowed': False}


class AutomationShadow:
    def __init__(self, policy, snapshot, outcomes, rulebook, clock=None):
        self.policy = policy
        self.snapshot = snapshot
        self.outcomes = outcomes
        self.rulebook = rulebook
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        with policy.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS shadow_runs(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')

    def status(self):
        with self.lock, self.policy.db() as db:
            rows = db.execute('SELECT payload FROM shadow_runs ORDER BY id DESC LIMIT 30').fetchall()
        state = self.policy.status()
        policy = state['policy']
        ended = policy['mode'] == 'shadow' and bool(policy.get('saved_at')) and self.clock() >= (
            datetime.fromisoformat(policy['saved_at']) + timedelta(days=policy['shadow_days']))
        return {**state, 'phase': 'ended' if ended else state['effective_mode'],
                'runs': [json.loads(r[0]) for r in rows]}

    def tick(self):
        with self.lock, self.policy.lock:
            state = self.policy.status()
            if state['effective_mode'] != 'shadow':
                return None
            policy = state['policy']
            now = self.clock()
            previous = self.status()['runs']
            if previous and previous[0]['policy_digest'] == policy['digest']:
                last = datetime.fromisoformat(previous[0]['at'])
                if now < last + timedelta(hours=policy['interval_hours']):
                    return None
            if now >= datetime.fromisoformat(policy['saved_at']) + timedelta(days=policy['shadow_days']):
                return None  # Ending shadow never authorizes execution.
            data, report = self.snapshot()
            book = self.rulebook()
            if digest(book) != policy['rulebook_digest']:
                return None
            result = evaluate_shadow(report, book, policy, now)
            # Re-check generation after potentially slow snapshot collection.
            if self.policy.status()['effective_mode'] != 'shadow':
                return None
            offers = {'items': [{'kind': 'approve', 'proposal_id': p['id'], 'headline': p['label']}
                                for p in result['items'] if p['would_pause']]}
            self.outcomes.record_offers(offers, report, now=now)
            self.outcomes.refresh(data, report, now=now)
            comparisons = self.outcomes.list(report, now=now)['items']
            result['comparisons'] = [
                {'proposal_id': c['proposal_id'], 'source': c['source'],
                 'owner_reported': c['source'] == 'owner_marked',
                 'change_observed': c.get('verification') == 'confirmed',
                 'execution_verified': False,
                 'execution_verification': 'no_verified_ad_execution_receipt', 'outcome': c}
                for c in comparisons]
            for item in result['items']:
                item['comparison'] = [c for c in result['comparisons'] if c['proposal_id'] == item['id']]
            run = {**result, 'at': now.isoformat(), 'policy': deepcopy(policy),
                   'window': deepcopy((report.get('windows') or {}).get('3')),
                   'freshness': deepcopy(report.get('freshness')), 'policy_digest': policy['digest'],
                   'rulebook_digest': policy['rulebook_digest'], 'causal_claim': False}
            with self.policy.db() as db:
                db.execute('INSERT INTO shadow_runs(payload) VALUES(?)', (json.dumps(run, ensure_ascii=False),))
            return run

    def start(self):
        if self.thread is not None:
            return
        def loop():
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception:
                    # Keep fail-closed diagnostic evidence, without recording private exception text.
                    with self.policy.db() as db:
                        db.execute('INSERT INTO shadow_runs(payload) VALUES(?)', (json.dumps({
                            'at': self.clock().isoformat(), 'policy_digest': None, 'items': [],
                            'blockers': ['shadow_evaluation_failed'], 'provider_writes': 0}),))
                self.stop_event.wait(30)
        self.thread = threading.Thread(target=loop, daemon=True, name='rosery-automation-shadow')
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
