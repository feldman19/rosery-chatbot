"""Hypothesis journal: what the system thinks may work for this business, and how sure it is.

Owner principle (2026-10-23): learn from this business's own data all the time, but never conclude from
noise, and check findings against what is known outside. So every hypothesis carries its evidence, and
its status only moves through the evidence gate:
- testing: too little data, or a result that chance explains (two-sided sign test p ≥ 0.05);
- supported / not_supported: at least MIN_RESOLVED judged cases and p < 0.05;
- outside sources (web research with links) are context next to the evidence, never part of the gate.
No hypothesis changes a decision; adopting one into the rulebook is the owner's call.
"""
from datetime import datetime, timezone
from math import comb
import json
import threading
from pathlib import Path

MIN_RESOLVED = 10
STATUS_LABEL = {'testing': 'בבדיקה', 'supported': 'נתמכת בנתונים', 'not_supported': 'לא נתמכת', 'proposed': 'הצעה'}


def sign_test(wins, losses):
    """Two-sided exact sign test p-value for wins vs losses (ties left out)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = max(wins, losses)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def gate(wins, losses):
    n, p = wins + losses, sign_test(wins, losses)
    if n < MIN_RESOLVED or p >= 0.05:
        return 'testing', p
    return ('supported' if wins > losses else 'not_supported'), p


SEED = [{
    'id': 'ad_score_beats_3day_roas',
    'statement': 'הציון המשוקלל (משפך + הוצאה) מזהה מוקדם יותר ממה ש-ROAS של 3 ימים מזהה, אילו מודעות יעצרו ואילו יגדלו.',
    'test': 'מצב צל: בכל מחלוקת על עצירה או הגדלה, מי צדק לפי ROAS אחרי ₪600 הוצאה נוספת (לפחות 7 ימים, עד 21 יום).',
    'backtest': {'date': '2026-10-23', 'ads': 64, 'spearman_score': 0.25, 'spearman_raw_roas': 0.0,
                 'out_of_sample_tuned': 0.15, 'note': 'מדרג טוב יותר, אבל חלש; 37 מודעות נעצרו לפני היום הרביעי ולא נבדקו.'},
}]


class Hypotheses:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        if not self.path.exists():
            self._write([{**h, 'status': 'testing', 'evidence': None, 'sources': [], 'created_at': self.clock().isoformat()} for h in SEED])

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []

    def _write(self, items):
        pending = self.path.with_suffix('.pending')
        pending.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
        pending.replace(self.path)

    def list(self):
        with self.lock:
            items = self._read()
        return [{**h, 'status_label': STATUS_LABEL.get(h['status'], h['status'])} for h in items]

    def update_evidence(self, hypothesis_id, wins, losses, unjudgeable, detail):
        """Run the gate on new shadow results; the status only follows the gate."""
        status, p = gate(wins, losses)
        with self.lock:
            items = self._read()
            for h in items:
                if h['id'] == hypothesis_id:
                    h.update(status=status, evidence={'checked_at': self.clock().isoformat(), 'for': wins, 'against': losses,
                                                      'not_judgeable': unjudgeable, 'p_value': round(p, 4),
                                                      'needed': MIN_RESOLVED, 'detail': detail})
            self._write(items)
        return status

    def get(self, hypothesis_id):
        return next((h for h in self.list() if h['id'] == hypothesis_id), None)

    def set_outside(self, hypothesis_id, state, review=None, detail=None):
        """Outside research (claims with links) kept as context beside the evidence; never moves the status."""
        with self.lock:
            items = self._read()
            for h in items:
                if h['id'] == hypothesis_id:
                    h['outside'] = {'state': state, 'checked_at': self.clock().isoformat(), 'detail': detail,
                                    **({'summary': review['summary']} if review else {})}
                    if review:
                        h['sources'] = review['findings']
            self._write(items)
