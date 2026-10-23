"""Ad Score in shadow beside the rulebook: record both verdicts each day, judge disagreements later.

Never changes a decision or writes to Meta. Each refresh stores, per ad, the rule engine's action and the
score's verdict on the same complete 3-day window, plus yesterday's results. A disagreement about stopping
or scaling is judged once the ad has spent ₪600 after it (about 3 purchases at the account's CPA), but
never on fewer than 7 days, so each case holds a whole week with its weak Friday and Saturday (owner,
2026-10-23). Later ROAS below 2.6 means "stop" was right, 3 or more means "scale" was right. An ad that
does not reach ₪600 within 21 days (often because it was paused) cannot be judged and is counted apart:
that is the survivor bias the backtest had too.

Beside the main score, a variant without CTR (ad_score.VARIANT_NO_CTR) is recorded the same way, and its
disagreements with the main score are judged with the same follow-up and gate (hypothesis
ctr_weight_not_needed). The main score's weights and every decision stay as they are.
"""
from contextlib import contextmanager
from datetime import date, timedelta
import json
import sqlite3

import ad_score

FOLLOW_SPEND = 600
MIN_FOLLOW_DAYS, MAX_FOLLOW_DAYS = 7, 21
STOP_RIGHT_BELOW, SCALE_RIGHT_FROM = 2.6, 3.0
STOP_SIDE = {'rules': {'PAUSE'}, 'score': {'kill'}, 'main': {'kill'}, 'variant': {'kill'}}
SCALE_SIDE = {'rules': {'SCALE'}, 'score': {'winner'}, 'main': {'winner'}, 'variant': {'winner'}}


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
                           'agree': ad_score.agrees(s['verdict'], decision['action']),
                           'variant': ad_score.score(m, base, ad_score.VARIANT_NO_CTR)}
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

    def _follow(self, db, ad, day, today):
        """(state, spend, revenue, days): judged after ≥7 covered days once spend reaches ₪600; a day not read
        before that point leaves it incomplete; 21 covered days without ₪600 → not judgeable; else pending."""
        first = date.fromisoformat(day) + timedelta(days=1)
        last = (first + timedelta(days=MAX_FOLLOW_DAYS - 1)).isoformat()
        rows = {d: (s, r) for s, r, d in db.execute('SELECT spend, revenue, day FROM daily WHERE ad_id=? AND day BETWEEN ? AND ?',
                                                     (ad, first.isoformat(), last)).fetchall()}
        covered = {r[0] for r in db.execute('SELECT day FROM covered WHERE day BETWEEN ? AND ?', (first.isoformat(), last)).fetchall()}
        spend = revenue = 0.0
        for n in range(MAX_FOLLOW_DAYS):
            current = (first + timedelta(days=n)).isoformat()
            if current >= today.isoformat():
                return 'pending', spend, revenue, n
            if current not in covered:
                return 'incomplete', spend, revenue, n  # days not read stay unknown, never zero
            s, r = rows.get(current, (0.0, 0.0))
            spend += s
            revenue += r
            if n + 1 >= MIN_FOLLOW_DAYS and spend >= FOLLOW_SPEND:
                return 'judged', spend, revenue, n + 1
        return 'not_judgeable', spend, revenue, MAX_FOLLOW_DAYS

    def _judge(self, today, sides):
        """Every disagreement about stopping or scaling whose follow-up has ended (judged or not judgeable).
        sides(call) → (first, second, first_label, second_label) or None; outcome '<label>_right'."""
        cutoff = (today - timedelta(days=MIN_FOLLOW_DAYS + 1)).isoformat()
        judged = []
        with self._db() as db:
            for ad, day, raw in db.execute('SELECT ad_id, day, payload FROM calls WHERE day <= ? ORDER BY day', (cutoff,)).fetchall():
                call = json.loads(raw)
                pair = sides(call)
                if not pair:
                    continue
                a, b, a_name, b_name = pair
                for topic, side, right in (('stop', STOP_SIDE, lambda r: r < STOP_RIGHT_BELOW), ('scale', SCALE_SIDE, lambda r: r >= SCALE_RIGHT_FROM)):
                    says_a, says_b = a in side[a_name], b in side[b_name]
                    if says_a == says_b:
                        continue
                    state, spend, revenue, days = self._follow(db, ad, day, today)
                    item = {'ad_id': ad, 'name': call['name'], 'day': day, 'topic': topic, a_name: a, b_name + '_verdict': b,
                            'follow_spend': round(spend, 2), 'follow_days': days}
                    if state in ('pending', 'incomplete'):
                        item['outcome'] = 'incomplete_follow_up'
                    elif state == 'not_judgeable':
                        item['outcome'] = 'not_judgeable'
                    else:
                        item['follow_roas'] = round(revenue / spend, 3)
                        happened = right(revenue / spend)
                        item['outcome'] = (b_name if happened == says_b else a_name) + '_right'
                    judged.append(item)
        # One piece of evidence per ad: its first disagreement (later days and the other topic reuse the same
        # follow-up, so counting them again would treat dependent results as independent).
        first = {}
        for item in judged:
            if item['outcome'] == 'incomplete_follow_up':
                continue
            first.setdefault(item['ad_id'], item)
        return list(first.values())

    def judge(self, today):
        """The rulebook against the main score."""
        return self._judge(today, lambda call: (call['rules'], call['score']['verdict'], 'rules', 'score'))

    def judge_variant(self, today):
        """The main score against the variant without CTR; days recorded before the variant existed are skipped."""
        return self._judge(today, lambda call: (call['score']['verdict'], call['variant']['verdict'], 'main', 'variant')
                           if call.get('variant') else None)

    def summary(self, today):
        with self._db() as db:
            calls = db.execute('SELECT COUNT(*), COUNT(DISTINCT day) FROM calls').fetchone()
            open_ = [json.loads(r[0]) for r in db.execute('SELECT payload FROM calls WHERE day = (SELECT MAX(day) FROM calls)').fetchall()]
        judged, variant = self.judge(today), self.judge_variant(today)
        resolved = [j for j in judged if j['outcome'] != 'not_judgeable']
        return {'calls': calls[0], 'days': calls[1], 'today_disagreements': sum(not c['agree'] for c in open_),
                'judged': judged, 'score_right': sum(j['outcome'] == 'score_right' for j in resolved),
                'rules_right': sum(j['outcome'] == 'rules_right' for j in resolved),
                'not_judgeable': sum(j['outcome'] == 'not_judgeable' for j in judged),
                'variant': {'today_disagreements': sum(bool(c.get('variant')) and c['variant']['verdict'] != c['score']['verdict'] for c in open_),
                            'judged': variant, 'variant_right': sum(j['outcome'] == 'variant_right' for j in variant),
                            'main_right': sum(j['outcome'] == 'main_right' for j in variant),
                            'not_judgeable': sum(j['outcome'] == 'not_judgeable' for j in variant)},
                'provider_writes': 0}
