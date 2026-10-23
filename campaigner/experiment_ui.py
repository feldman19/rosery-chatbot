"""Local experiment storage and owner presentation. No provider integrations."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from account_history import AccountHistory
from experiments import Arm, Plan, LIMITS, evaluate, rank_candidates

META_NOTE = 'החיבור למטא ייבנה בשלב הבא, אחרי אישור שלך'


def card(row, today=None):
    plan = row['plan']
    state = plan['state']
    result = row.get('result') or {}
    if state == 'running':
        parsed = Plan(**{**plan, 'a': Arm(**plan['a']), 'b': Arm(**plan['b'])})
        result = evaluate(parsed, row.get('daily') or {}, ((today or date.today()) - timedelta(days=1)).isoformat())
        row = {**row, 'result': result}
    daily = row.get('daily') or {}
    probability = result.get('probability_b_gt_a')
    words = 'הסיכוי עדיין אינו ידוע' if probability is None else f'הסיכוי שזרוע ב עדיפה על א הוא כ־{probability:.0%}'
    totals = {}
    for arm in ('A', 'B'):
        rows = daily.get(arm)
        totals[arm] = {key: sum(r[key] for r in rows) if rows and all(isinstance(r.get(key), (int, float)) and not isinstance(r[key], bool) and r[key] >= 0 for r in rows) else None
                       for key in ('spend_minor', 'purchases')}
    days_left = max(0, plan['max_days'] - ((today or date.today()) - date.fromisoformat(plan['start_date'])).days) if state == 'running' else None
    verdict = {'decided': 'זרוע ' + ('ב' if result.get('winner') == 'B' else 'א') + ' עדיפה לפי נתוני הייחוס',
               'inconclusive': 'לא הוכרע', 'interrupted': 'הניסוי הופסק', 'stopped_for_harm': 'נעצר בשל חשש לנזק'}.get(state)
    return {**row, 'totals': totals, 'probability_words': words, 'days_left': days_left,
            'verdict': verdict, 'next_step': 'לבחון את טווח השינוי ומגבלות הניסוי לפני החלטה נוספת',
            'meta_note': META_NOTE, 'live_action_allowed': False}


class ExperimentStore:
    def __init__(self, data_dir):
        self.data = Path(data_dir)
        self.path = self.data / 'experiments.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_running_experiment ON experiments(state) WHERE state='running'")
            db.execute('CREATE TABLE IF NOT EXISTS ideas(id TEXT PRIMARY KEY, question TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS dismissed(id TEXT PRIMARY KEY)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, plan, daily=None, result=None, identity=None):
        identity = identity or uuid.uuid4().hex
        payload = {'id': identity, 'plan': asdict(plan), 'daily': daily or {}, 'result': result}
        with self.db() as db:
            db.execute('INSERT INTO experiments VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,payload=excluded.payload', (identity, plan.state, json.dumps(payload, ensure_ascii=False)))
        return identity

    def list(self):
        with self.db() as db:
            return [card(json.loads(r[0])) for r in db.execute('SELECT payload FROM experiments ORDER BY rowid DESC')]

    def idea(self, question):
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ValueError('יש לכתוב רעיון באורך של עד 2,000 תווים')
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO ideas VALUES(?,?)', (identity, question.strip()))
        return {'id': identity, 'question': question.strip()}

    def candidates(self, hypotheses):
        with self.db() as db:
            ideas = [{'id': r[0], 'question': r[1]} for r in db.execute('SELECT id,question FROM ideas ORDER BY rowid')]
            dismissed = {r[0] for r in db.execute('SELECT id FROM dismissed')}
        try:
            history = json.loads((self.data / 'account-history.json').read_text(encoding='utf-8'))
            if history.get('verified') is not True:
                history = {}
            elif any('rows' in w for w in history.get('windows', {}).values()):
                history = AccountHistory(self.data).summary()  # saved data only; never refresh
        except (OSError, ValueError):
            history = {}
        journal = [{**h, 'question': h.get('question') or h.get('statement', '')} for h in hypotheses]
        # Filter before ranking so dismissing a top-three item reveals the next one.
        def identify(row):
            return hashlib.sha256(json.dumps({k: row.get(k) for k in ('question', 'variable', 'a_kind', 'b_kind')}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        # Rank the full local pool, including history candidates, then remove dismissals.
        history_rows = []
        for dimension in history.get('windows', {}).get('all', {}).get('patterns', {}).get('dimensions', []):
            history_rows.extend(rank_candidates([], {'windows': {'all': {'patterns': {'dimensions': [dimension]}}}}, [], {}))
        pool = journal + ideas + history_rows
        pool = [r for r in pool if identify(r) not in dismissed]
        proposals = rank_candidates(pool, {}, [], {})
        return [{**p, 'id': identify(p), 'daily_budget_minor': None, 'max_days': 21,
                 'what_it_will_not_prove': LIMITS, 'meta_note': META_NOTE, 'live_action_allowed': False} for p in proposals]

    def dismiss(self, identity, hypotheses):
        if identity not in {r['id'] for r in self.candidates(hypotheses)}:
            raise ValueError('ההצעה אינה קיימת')
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO dismissed VALUES(?)', (identity,))
        return {'dismissed': True}

    def stop(self, identity):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM experiments WHERE id=?', (identity,)).fetchone()
            if not row: raise ValueError('הניסוי אינו קיים')
            payload = json.loads(row[0])
            if payload['plan']['state'] != 'running': raise ValueError('אפשר לעצור רק ניסוי פועל')
            payload['plan']['state'] = 'interrupted'
            db.execute('UPDATE experiments SET state=?,payload=? WHERE id=?', ('interrupted', json.dumps(payload, ensure_ascii=False), identity))
        return card(payload)
