"""Owner policy for read-only automation; no execution authority."""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading

AUTO_UNAVAILABLE = 'דורש הרחבת מסלול הביצוע לרמת מודעה'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


class AutomationPolicy:
    def __init__(self, directory, generation, rulebook, clock=None):
        self.path = Path(directory) / 'automation-shadow.sqlite3'
        self.generation = generation
        self.rulebook = rulebook
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def status(self):
        with self.lock, self.db() as db:
            row = db.execute('SELECT payload FROM policy WHERE id=1').fetchone()
            book = self.rulebook()
            policy = json.loads(row[0]) if row else {
                'action': 'pause_ad', 'mode': 'manual',
                'roas_threshold': (book.get('rules', {}).get('roas_ladder', {}).get('params', {}).get('fail_below')),
                'interval_hours': 2, 'shadow_days': 14, 'saved_at': None}
            blockers = []
            if book.get('status') != 'available':
                blockers.append('rulebook_unavailable')
            if row:
                unsigned = {k: v for k, v in policy.items() if k != 'digest'}
                if digest(unsigned) != policy.get('digest'):
                    blockers.append('policy_integrity_failed')
                if policy.get('stop_generation') != self.generation():
                    blockers.append('stopped')
                if policy.get('rulebook_digest') != digest(book):
                    blockers.append('rulebook_changed')
            return {'policy': policy, 'effective_mode': policy['mode'] if not blockers else 'manual',
                    'blockers': blockers, 'auto_available': False, 'auto_unavailable_reason': AUTO_UNAVAILABLE,
                    'provider_writes': 0, 'live_action_allowed': False}

    def save(self, body):
        if set(body) != {'action', 'mode', 'roas_threshold', 'interval_hours', 'shadow_days'}:
            raise ValueError('נדרשים סוג פעולה, מצב, סף ROAS, מרווח הרצה וימי צל')
        if body['action'] != 'pause_ad' or body['mode'] not in ('manual', 'shadow'):
            raise ValueError(AUTO_UNAVAILABLE)
        for key, lower, upper in [('roas_threshold', 0.01, 100), ('interval_hours', 0.01, 720), ('shadow_days', 1, 365)]:
            value = body[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError('ערך מדיניות אינו תקין: ' + key)
        if type(body['shadow_days']) is not int:
            raise ValueError('ימי צל חייבים להיות מספר שלם')
        with self.lock:
            book = self.rulebook()
            if book.get('status') != 'available':
                raise ValueError('ספר החוקים אינו מאומת')
            policy = {**body, 'saved_at': self.clock().isoformat(), 'stop_generation': self.generation(),
                      'rulebook_digest': digest(book)}
            policy['digest'] = digest(policy)
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO policy VALUES(1,?)', (json.dumps(policy),))
            return self.status()
