"""Ad Score in shadow beside the rulebook: record both verdicts each day, judge disagreements later.

Never changes a decision or writes to Meta. Each refresh stores, per ad, the rule engine's action and the
score's verdict on the same complete 3-day window, plus yesterday's results. A disagreement about stopping
or scaling is judged on the next 11 full days (days 4–14 of the spec): later ROAS below 2.6 means
"stop" was right, 3 or more means "scale" was right. An ad with under ₪100 in those days (often because
it was paused) cannot be judged and is counted apart: that is the survivor bias the backtest had too.
"""
from contextlib import contextmanager
from datetime import date, timedelta
import json
import sqlite3

import ad_score

FOLLOW_DAYS = 11
MIN_FOLLOW_SPEND = 100
STOP_RIGHT_BELOW, SCALE_RIGHT_FROM = 2.6, 3.0
STOP_SIDE = {'rules': {'PAUSE'}, 'score': {'kill'}}
SCALE_SIDE = {'rules': {'SCALE'}, 'score': {'winner'}}


class ScoreShadow:
    def __init__(self, path):
        self.path = str(path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS calls(ad_id TEXT, day TEXT, payload TEXT NOT NULL, PRIMARY KEY(ad_id, day))')
            db.execute('CREATE TABLE IF NOT EXISTS daily(ad_id TEXT, day TEXT, spend REAL, revenue REAL, purchases REAL, PRIMARY KEY(ad_id, day))')
            # A day is covered only when the whole account's day was read; then an ad with no row spent nothing.
            db.execute('CREATE TABLE IF NOT EXISTS covered(day TEXT PRIMARY KEY)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def record(self, report):
        """Store today's two verdicts and yesterday's results. Returns how many ads were recorded."""
        windows = (report or {}).get('windows') or {}
        w3, w7, w1 = (windows.get(k) or {} for k in ('3', '7', '1'))
        decisions = {d['entity_id']: d for d in ((report.get('recommendations') or {}).get('rule_engine') or {}).get('decisions') or []}
        base = ad_score.baseline(w7.get('ads') or [])
        if not w3.get('until') or not base['cpa']:
            return 0
        count = 0
        with self._db() as db:
            for row in w3.get('ads') or []:
                decision, m = decisions.get(row.get('id')), ad_score.metrics(row)
                if not decision or not m:
                    continue
                s = ad_score.score(m, base)
                payload = {'name': row.get('name'), 'adset': row.get('adset_name'), 'rules': decision['action'],
                           'score': s, 'spend': m['spend'], 'roas': m['revenue'] / m['spend'] if m['spend'] and m['revenue'] is not None else None,
                           'agree': ad_score.agrees(s['verdict'], decision['action'])}
                db.execute('INSERT OR REPLACE INTO calls VALUES (?,?,?)', (row['id'], w3['until'], json.dumps(payload, ensure_ascii=False)))
                count += 1
            if w1.get('until') and w1.get('since') == w1.get('until') and isinstance(w1.get('ads'), list):
                rows = [(row, ad_score.metrics(row)) for row in w1['ads']]
                if all(m for _, m in rows):  # one unknown row → the day is not complete
                    db.execute('INSERT OR REPLACE INTO covered VALUES (?)', (w1['until'],))
                for row in w1.get('ads') or []:
                    m = ad_score.metrics(row)
                    if m:
                        db.execute('INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?)',
                                   (row['id'], w1['until'], m['spend'], m['revenue'] or 0.0, m['purchases']))
        return count

    def _follow(self, db, ad, day):
        start, end = (date.fromisoformat(day) + timedelta(days=n) for n in (1, FOLLOW_DAYS))
        rows = db.execute('SELECT spend, revenue, day FROM daily WHERE ad_id=? AND day BETWEEN ? AND ?',
                          (ad, start.isoformat(), end.isoformat())).fetchall()
        covered = db.execute('SELECT COUNT(*) FROM covered WHERE day BETWEEN ? AND ?', (start.isoformat(), end.isoformat())).fetchone()[0]
        return sum(r[0] for r in rows), sum(r[1] for r in rows), covered

    def judge(self, today):
        """Every disagreement about stopping or scaling whose 11 follow-up days are over."""
        cutoff = (today - timedelta(days=FOLLOW_DAYS + 1)).isoformat()
        judged = []
        with self._db() as db:
            for ad, day, raw in db.execute('SELECT ad_id, day, payload FROM calls WHERE day <= ? ORDER BY day', (cutoff,)).fetchall():
                call = json.loads(raw)
                rules, verdict = call['rules'], call['score']['verdict']
                for topic, side, right in (('stop', STOP_SIDE, lambda r: r < STOP_RIGHT_BELOW), ('scale', SCALE_SIDE, lambda r: r >= SCALE_RIGHT_FROM)):
                    says_rules, says_score = rules in side['rules'], verdict in side['score']
                    if says_rules == says_score:
                        continue
                    spend, revenue, days = self._follow(db, ad, day)
                    item = {'ad_id': ad, 'name': call['name'], 'day': day, 'topic': topic, 'rules': rules, 'score_verdict': verdict,
                            'follow_spend': round(spend, 2), 'follow_days_covered': days}
                    if days < FOLLOW_DAYS:
                        item['outcome'] = 'incomplete_follow_up'  # days not read stay unknown, never zero
                    elif spend < MIN_FOLLOW_SPEND:
                        item['outcome'] = 'not_judgeable'
                    else:
                        item['follow_roas'] = round(revenue / spend, 3)
                        happened = right(revenue / spend)
                        item['outcome'] = 'score_right' if happened == says_score else 'rules_right'
                    judged.append(item)
        # One piece of evidence per ad: its first disagreement (later days and the other topic reuse the same
        # follow-up, so counting them again would treat dependent results as independent).
        first = {}
        for item in judged:
            if item['outcome'] == 'incomplete_follow_up':
                continue
            first.setdefault(item['ad_id'], item)
        return list(first.values())

    def summary(self, today):
        with self._db() as db:
            calls = db.execute('SELECT COUNT(*), COUNT(DISTINCT day) FROM calls').fetchone()
            open_ = [json.loads(r[0]) for r in db.execute('SELECT payload FROM calls WHERE day = (SELECT MAX(day) FROM calls)').fetchall()]
        judged = self.judge(today)
        resolved = [j for j in judged if j['outcome'] != 'not_judgeable']
        return {'calls': calls[0], 'days': calls[1], 'today_disagreements': sum(not c['agree'] for c in open_),
                'judged': judged, 'score_right': sum(j['outcome'] == 'score_right' for j in resolved),
                'rules_right': sum(j['outcome'] == 'rules_right' for j in resolved),
                'not_judgeable': sum(j['outcome'] == 'not_judgeable' for j in judged), 'provider_writes': 0}
