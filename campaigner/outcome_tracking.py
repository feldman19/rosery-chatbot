"""What happened after the owner acted on a "today" proposal. Never writes to Meta.

An action enters tracking when the owner taps "ביצעתי", or when a refresh sees
that a recently offered proposal was applied (the owner then confirms it).
Every refresh keeps the newest Meta value of each compared day while Meta still
reports it, so late purchases are picked up. A day is settled once it was read
at least SETTLE_DAYS after it ended. Results are observations next to a change,
never proof that the change caused them; a missing value stays unknown.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import sqlite3
import uuid

from management_report import ACCOUNT, _aggregate, _coverage, _meta_rows, _public, _stamp

WINDOW_DAYS = 3
SETTLE_DAYS = 7
OFFER_DAYS = 7
SCALE_TOLERANCE = 0.03
NOT_SEEN_DAYS = 2
SCOPES = {'pause': ('ad', 'adset', 'account'), 'scale': ('adset', 'account')}
SCOPE_LABELS = {'ad': 'המודעה', 'adset': 'האד-סט', 'account': 'כל החשבון'}


class OutcomeDenied(ValueError):
    pass


def _day(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def _money(value):
    return f'₪{value:,.0f}' if isinstance(value, (int, float)) else 'חסר'


def _ratio(value):
    return f'{value:.2f}' if isinstance(value, (int, float)) else 'חסר'


def local_today(report):
    """The account's current day: one after the last full day in the report."""
    until = _day(((report.get('windows') or {}).get('1') or {}).get('until'))
    return until + timedelta(days=1) if until else None


def _local_change_day(updated_time, zone):
    stamp = _stamp(updated_time)
    if stamp is None:
        return None
    try:
        return stamp.astimezone(ZoneInfo(zone)).date()
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None


def _entities(report):
    """Current configuration of ads and ad sets seen in the 7-day window."""
    window = (report.get('windows') or {}).get('7') or {}
    ads = {r['id']: r for r in window.get('ads', []) if isinstance(r, dict) and isinstance(r.get('id'), str)}
    sets = {r['id']: r for r in window.get('adsets', []) if isinstance(r, dict) and isinstance(r.get('id'), str)}
    return ads, sets


def _updated(entity):
    evidence = entity.get('change_evidence') or {}
    return evidence.get('updated_time') if evidence.get('status') == 'entity_update_observed' else None


class OutcomeTracker:
    def __init__(self, path, verified_changes=None):
        self.path = str(path)
        # {proposal_id: finished_at} of changes "אשר ובצע" wrote and read back from Meta. Used when the
        # refreshed data no longer lists the entity (paused ads drop out of the active-only inventory).
        self.verified_changes = verified_changes or (lambda: {})
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS offers(proposal_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,payload TEXT NOT NULL,at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS days(action_id TEXT NOT NULL,scope TEXT NOT NULL,day TEXT NOT NULL,'
                       'payload TEXT NOT NULL,PRIMARY KEY(action_id,scope,day))')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    def _offers(self, db):
        return {pid: json.loads(raw) for pid, raw in db.execute('SELECT proposal_id,payload FROM offers')}

    def _actions(self, db):
        return [json.loads(raw) for (raw,) in db.execute('SELECT payload FROM actions ORDER BY at')]

    def _save_action(self, db, action):
        db.execute('INSERT OR REPLACE INTO actions VALUES(?,?,?)',
                   (action['id'], json.dumps(action, ensure_ascii=False, sort_keys=True), action['created_at']))

    def record_offers(self, today, report, now=None):
        """Remember what was offered and the state before it, for marking and detection."""
        now = now or datetime.now(timezone.utc)
        day = local_today(report)
        if day is None or not isinstance(today, dict):
            return
        ads, sets = _entities(report)
        offers = []
        for item in today.get('items') or []:
            pid = item.get('proposal_id') if item.get('kind') == 'approve' else None
            if not isinstance(pid, str):
                continue
            kind, _, entity_id = pid.partition(':')
            if kind == 'pause' and entity_id in ads:
                ad = ads[entity_id]
                offers.append({'proposal_id': pid, 'kind': 'pause', 'entity_id': entity_id, 'adset_id': ad.get('adset_id'),
                               'headline': item.get('headline'), 'before': {'status': ad.get('status')},
                               'expected': {'saving_per_day': item.get('saving_per_day')}})
            elif kind == 'scale' and entity_id in sets:
                adset = sets[entity_id]
                budget = adset.get('daily_budget_minor') if adset.get('budget_known') else None
                if budget is None:
                    continue
                offers.append({'proposal_id': pid, 'kind': 'scale', 'entity_id': entity_id, 'adset_id': entity_id,
                               'headline': item.get('headline'), 'before': {'daily_budget_minor': budget},
                               'expected': {'daily_budget_minor': int(round(budget * 1.2))}})
        with self._db() as db:
            known = self._offers(db)
            for offer in offers:
                previous = known.get(offer['proposal_id'])
                # A new offer after the previous one expired starts fresh; otherwise
                # keep the first state seen, which is the state before any change.
                fresh = previous is None or (day - _day(previous['last_offered'])).days > OFFER_DAYS
                base = offer if fresh else {**previous, 'headline': offer['headline']}
                base.update(first_offered=day.isoformat() if fresh else previous['first_offered'],
                            last_offered=day.isoformat())
                db.execute('INSERT OR REPLACE INTO offers VALUES(?,?)',
                           (offer['proposal_id'], json.dumps(base, ensure_ascii=False, sort_keys=True)))

    def mark(self, proposal_id, report, now=None):
        """Owner says the change was made in Meta. Repeated taps reuse the same record."""
        now = now or datetime.now(timezone.utc)
        day = local_today(report)
        if day is None:
            raise OutcomeDenied('הדוח לא מזהה את היום בחשבון. רענן ונסה שוב.')
        with self._db() as db:
            offer = self._offers(db).get(proposal_id)
            if offer is None or (day - _day(offer['last_offered'])).days > 1:
                raise OutcomeDenied('ההצעה הזאת כבר לא מוצגת. רענן את המסך.')
            for action in self._actions(db):
                if action['proposal_id'] == proposal_id and action['offer_first'] == offer['first_offered'] \
                        and action['state'] != 'rejected':
                    if action['source'] == 'auto_detected' and action['state'] == 'needs_answer':
                        action.update(state='tracking', answered_at=now.isoformat())
                        self._save_action(db, action)
                    return action
            action = self._new_action(offer, 'owner_marked', day, now)
            self._save_action(db, action)
            return action

    def answer(self, action_id, answer, now=None):
        """Owner confirms or rejects an automatically detected change."""
        if answer not in ('yes', 'no'):
            raise OutcomeDenied('תשובה לא תקינה')
        now = now or datetime.now(timezone.utc)
        with self._db() as db:
            action = next((a for a in self._actions(db) if a['id'] == action_id), None)
            if action is None or action['state'] != 'needs_answer':
                raise OutcomeDenied('אין שאלה פתוחה על השינוי הזה. רענן את המסך.')
            action.update(state='tracking' if answer == 'yes' else 'rejected', answered_at=now.isoformat())
            self._save_action(db, action)
            return action

    def _new_action(self, offer, source, change_day, now):
        return {'id': str(uuid.uuid4()), 'proposal_id': offer['proposal_id'], 'kind': offer['kind'],
                'entity_id': offer['entity_id'], 'adset_id': offer['adset_id'], 'headline': offer['headline'],
                'before': offer['before'], 'expected': offer['expected'], 'offer_first': offer['first_offered'],
                'source': source, 'state': 'needs_answer' if source == 'auto_detected' else 'tracking',
                'marked_day': change_day.isoformat(), 'change_day': change_day.isoformat(),
                'change_day_source': 'marked' if source == 'owner_marked' else 'detected_refresh',
                'verification': {'status': 'not_checked'}, 'created_at': now.isoformat()}

    def refresh(self, data, report, now=None):
        """Read back current configuration, detect applied offers, store daily values."""
        now = now or datetime.now(timezone.utc)
        today = local_today(report)
        if today is None or not isinstance(data, dict) or data.get('account_id') != ACCOUNT:
            return
        zone = data.get('timezone')
        ads, sets = _entities(report)
        with self._db() as db:
            actions = self._actions(db)
            tracked = {(a['proposal_id'], a['offer_first']) for a in actions}
            for offer in self._offers(db).values():
                if (offer['proposal_id'], offer['first_offered']) in tracked:
                    continue
                if (today - _day(offer['last_offered'])).days > OFFER_DAYS:
                    continue
                seen = self._readback(offer, ads, sets)
                changed = _local_change_day(seen.get('updated_time'), zone)
                if changed and changed < _day(offer['first_offered']):
                    continue  # changed before it was ever offered; not a response to it
                if seen['status'] in ('confirmed', 'different_change'):
                    action = self._new_action(offer, 'auto_detected', today, now)
                    actions.append(action)
            for action in actions:
                if action['state'] == 'rejected':
                    continue
                seen = self._readback(action, ads, sets)
                if seen['status'] == 'not_seen' and (today - _day(action['marked_day'])).days >= NOT_SEEN_DAYS:
                    seen['status'] = 'not_confirmed'
                changed = _local_change_day(seen.get('updated_time'), zone)
                if seen['status'] == 'confirmed' and changed and changed >= _day(action['offer_first']) - timedelta(days=1):
                    action.update(change_day=changed.isoformat(), change_day_source='meta_updated_time')
                action['verification'] = {**seen, 'checked_on': today.isoformat()}
                self._save_action(db, action)
                if seen['status'] == 'confirmed':
                    self._store_days(db, action, data, ads, sets, today)

    def _readback(self, item, ads, sets):
        executed = self.verified_changes().get(item['proposal_id'])
        if item['kind'] == 'pause':
            ad = ads.get(item['entity_id'])
            if ad is None or ad.get('status') is None:
                if executed:
                    return {'status': 'confirmed', 'observed': {'status': 'PAUSED'}, 'updated_time': executed,
                            'source': 'approve_execute_readback'}
                if ad is None:
                    return {'status': 'unknown'}
            adset = sets.get(ad.get('adset_id')) or {}
            if ad.get('status') == 'PAUSED':
                return {'status': 'confirmed', 'observed': {'status': 'PAUSED'}, 'updated_time': _updated(ad)}
            if adset.get('status') == 'PAUSED':
                return {'status': 'different_change', 'observed': {'adset_status': 'PAUSED'}, 'updated_time': _updated(adset)}
            return {'status': 'not_seen', 'observed': {'status': ad.get('status')}}
        adset = sets.get(item['entity_id'])
        if adset is None or not adset.get('budget_known'):
            return {'status': 'unknown'}
        now_budget, before = adset.get('daily_budget_minor'), item['before']['daily_budget_minor']
        observed = {'daily_budget_minor': now_budget}
        if now_budget == before:
            return {'status': 'not_seen', 'observed': observed}
        expected = item['expected']['daily_budget_minor']
        close = abs(now_budget - expected) <= expected * SCALE_TOLERANCE
        return {'status': 'confirmed' if close else 'different_change', 'observed': observed,
                'updated_time': _updated(adset)}

    @staticmethod
    def _days(action):
        center = _day(action['change_day'])
        return ([center - timedelta(days=i) for i in range(WINDOW_DAYS, 0, -1)],
                [center + timedelta(days=i) for i in range(1, WINDOW_DAYS + 1)])

    def _store_days(self, db, action, data, ads, sets, today):
        before, after = self._days(action)
        paused_ad = action['kind'] == 'pause'
        for day in before + after:
            if day >= today or not _coverage(data, day, day):
                continue
            rows, reason = _meta_rows(data, day, day)
            if rows is None:
                continue
            for scope in SCOPES[action['kind']]:
                if scope == 'ad':
                    own = [r for r in rows if r.get('ad_id') == action['entity_id']]
                elif scope == 'adset':
                    own = [r for r in rows if r.get('adset_id') == action['adset_id']]
                else:
                    own = rows
                values = {key: _public(value) for key, value in _aggregate(own).items()
                          if key in ('spend', 'purchases', 'attributed_revenue')}
                note = None
                if not own:
                    # Meta omits an ad/day with no delivery. Only a complete report for
                    # an ad confirmed paused after the change may read that as zero.
                    if paused_ad and scope == 'ad' and day > _day(action['change_day']) and data.get('complete') is True:
                        values = {'spend': 0.0, 'purchases': 0.0, 'attributed_revenue': 0.0}
                        note = 'no_delivery_rows_while_paused'
                    else:
                        values = {'spend': None, 'purchases': None, 'attributed_revenue': None}
                        note = 'no_rows'
                payload = {'values': values, 'observed_on': today.isoformat(),
                           'age_days': (today - day).days, 'note': note}
                db.execute('INSERT OR REPLACE INTO days VALUES(?,?,?,?)',
                           (action['id'], scope, day.isoformat(), json.dumps(payload, sort_keys=True)))

    def list(self, report, now=None):
        today = local_today(report)
        with self._db() as db:
            actions = self._actions(db)
            stored = {}
            for aid, scope, day, raw in db.execute('SELECT action_id,scope,day,payload FROM days'):
                stored.setdefault(aid, {}).setdefault(scope, {})[day] = json.loads(raw)
        live = [a for a in actions if a['state'] != 'rejected']
        cards = [self._card(a, stored.get(a['id'], {}), today, live) for a in live]
        cards.sort(key=lambda c: c['change_day'], reverse=True)
        return {'items': cards, 'live_action_allowed': False, 'provider_writes': 0,
                'note': 'תצפית לפני ואחרי בלבד. היא לא מוכיחה שהשינוי גרם לתוצאה, ונתון חסר נשאר חסר.'}

    def _card(self, action, stored, today, everyone):
        before, after = self._days(action)
        verification = action.get('verification') or {}
        card = {'id': action['id'], 'proposal_id': action['proposal_id'], 'kind': action['kind'], 'headline': action['headline'],
                'change_day': action['change_day'], 'change_day_source': action['change_day_source'],
                'source': action['source'], 'needs_answer': action['state'] == 'needs_answer',
                'verification': verification.get('status'), 'lines': [], 'warnings': [], 'phase': 'waiting'}
        status = verification.get('status')
        if status == 'different_change':
            card['phase'] = 'different_change'
            observed = verification.get('observed') or {}
            if 'daily_budget_minor' in observed:
                card['lines'].append(f'התקציב שונה ל-{_money(observed["daily_budget_minor"] / 100)} ליום ולא '
                                     f'ל-{_money(action["expected"]["daily_budget_minor"] / 100)} כפי שהוצע. לא מחושבת תוצאה.')
            else:
                card['lines'].append('בוצע שינוי שונה מההצעה (האד-סט כולו הושהה). לא מחושבת תוצאה.')
            return card
        if status != 'confirmed':
            card['phase'] = 'unverified'
            card['lines'].append('עוד לא רואים את השינוי בנתוני מטא.' if status in ('not_seen', 'not_checked', 'unknown')
                                 else 'השינוי לא נראה בנתוני מטא יומיים אחרי הסימון. כדאי לבדוק ב-Ads Manager.')
            return card
        if today is None or today <= after[-1]:
            left = (after[-1] + timedelta(days=1) - today).days if today else None
            card['days_left'] = left
            card['lines'].append(f'ממתין לנתונים: עוד {left} ימים עד תוצאה ראשונה.' if left else 'ממתין לנתונים.')
            return card
        summary = {}
        unsettled = set()
        for scope in SCOPES[action['kind']]:
            days = stored.get(scope, {})
            summary[scope] = {}
            for label, span in (('before', before), ('after', after)):
                entries = [days.get(d.isoformat()) for d in span]
                if any(e is None for e in entries):
                    summary[scope][label] = None
                    continue
                unsettled |= {d.isoformat() for d, e in zip(span, entries) if e['age_days'] < SETTLE_DAYS}
                totals = {}
                for key in ('spend', 'purchases', 'attributed_revenue'):
                    values = [e['values'].get(key) for e in entries]
                    totals[key] = sum(values) if all(v is not None for v in values) else None
                spend, revenue = totals['spend'], totals['attributed_revenue']
                summary[scope][label] = {
                    'spend_per_day': spend / WINDOW_DAYS if spend is not None else None,
                    'purchases': totals['purchases'],
                    'roas': revenue / spend if spend and revenue is not None else None}
        if all(s.get('before') is None or s.get('after') is None for s in summary.values()):
            card['phase'] = 'incomplete'
            card['lines'].append('חסרים ימים בנתונים שנשמרו, לכן אי אפשר להשוות. '
                                 'המערכת שומרת נתונים רק כשהמסך נפתח או מתרענן.')
            return card
        card['phase'] = 'preliminary' if unsettled else 'final'
        card['comparison'] = summary
        for scope, values in summary.items():
            b, a = values.get('before'), values.get('after')
            if b is None or a is None:
                card['lines'].append(f'{SCOPE_LABELS[scope]}: חסרים ימים, אין השוואה.')
                continue
            card['lines'].append(
                f'{SCOPE_LABELS[scope]}: הוצאה {_money(b["spend_per_day"])} → {_money(a["spend_per_day"])} ליום, '
                f'רכישות {_count(b["purchases"])} → {_count(a["purchases"])}, '
                f'החזר על ₪1 {_ratio(b["roas"])} → {_ratio(a["roas"])}.')
        card['expected_vs_observed'] = self._expected(action, summary, verification)
        if card['expected_vs_observed']:
            card['lines'].insert(0, card['expected_vs_observed'])
        if unsettled:
            card['warnings'].append('תוצאה ראשונית: מטא עוד עשויה להוסיף רכישות מאוחרות לחלק מהימים.')
        others = [o for o in everyone if o['id'] != action['id'] and o['adset_id'] == action['adset_id']
                  and abs((_day(o['change_day']) - _day(action['change_day'])).days) <= WINDOW_DAYS]
        if others:
            card['warnings'].append('היה שינוי נוסף באותו אד-סט בתוך החלון, לכן התוצאה מעורבבת.')
        if action['change_day_source'] != 'meta_updated_time':
            card['warnings'].append('יום השינוי לפי יום הסימון, כי מטא לא החזירה זמן עדכון.')
        return card

    @staticmethod
    def _expected(action, summary, verification):
        if action['kind'] == 'pause':
            ad = summary.get('ad') or {}
            b, a = ad.get('before'), ad.get('after')
            expected = action['expected'].get('saving_per_day')
            if not b or not a or b['spend_per_day'] is None or a['spend_per_day'] is None:
                return None
            saved = b['spend_per_day'] - a['spend_per_day']
            return f'צפוי היה לחסוך {_money(expected)} ליום; אחרי ההשהיה המודעה הוציאה {_money(saved)} פחות ליום.'
        observed = (verification.get('observed') or {}).get('daily_budget_minor')
        return (f'התקציב היומי עלה מ-{_money(action["before"]["daily_budget_minor"] / 100)} '
                f'ל-{_money(observed / 100)} (הוצע {_money(action["expected"]["daily_budget_minor"] / 100)}).'
                if observed is not None else None)


def _count(value):
    return f'{value:g}' if isinstance(value, (int, float)) else 'חסר'


# BEGIN clear-actions: local presentation state, never provider execution.
class ProposalVisibility:
    def __init__(self, path):
        from pathlib import Path
        import threading
        self.path = Path(path)
        self.lock = threading.RLock()

    def _load(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding='utf-8'))

    @staticmethod
    def fingerprint(item):
        import hashlib
        def bucket(value):
            if isinstance(value, bool) or value is None:
                return value
            if isinstance(value, (float, int)):
                return round(value, 1)
            if isinstance(value, dict):
                return {k: bucket(v) for k, v in value.items()}
            if isinstance(value, list):
                return [bucket(v) for v in value]
            return value
        content = {k: item.get(k) for k in ('action', 'headline', 'body', 'saving_per_day', 'features', 'creative_id', 'numbers')}
        # Detection timestamps are age, not recommendation content.
        if content['features']:
            content['features'] = [{k: v for k, v in f.items() if k not in ('since', 'kind_label')} for f in content['features']]
        return hashlib.sha256(json.dumps(bucket(content), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def update(self, item, restore=False):
        with self.lock:
            records = self._load()
            pid = item['proposal_id']
            if restore:
                records.pop(pid, None)
            else:
                records[pid] = {'fingerprint': self.fingerprint(item), 'dismissed_at': datetime.now(timezone.utc).isoformat(),
                                'item': item}
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.path)

    def annotate(self, report, tracker, now=None):
        now = now or datetime.now(timezone.utc)
        with tracker._db() as db:
            offers = tracker._offers(db)
            acted = {a['proposal_id'] for a in tracker._actions(db)}
        with self.lock:
            records = self._load()
        items = ((report.get('recommendations') or {}).get('today') or {}).get('items') or []
        drift_items = (report.get('creative_drift') or {}).get('items') or []
        decisions = {d['entity_id']: d for d in ((report.get('recommendations') or {}).get('rule_engine') or {}).get('decisions', [])}
        for item in items:
            if not item.get('proposal_id'):
                import hashlib
                import re
                identity = [item.get(k) for k in ('action', 'entity_id', 'link')]
                if not item.get('entity_id') and not item.get('link'):
                    identity.append(re.sub(r'[\d,.]+', '', item.get('headline') or ''))
                item['proposal_id'] = 'notice:' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
            entity_id = (item.get('proposal_id') or '').partition(':')[2]
            if entity_id in decisions:
                item['numbers'] = decisions[entity_id].get('numbers')
        for item in drift_items:
            item['proposal_id'] = 'drift:' + item['ad_id']
        for item in items + drift_items:
            pid = item.get('proposal_id')
            if not pid:
                continue
            first = (offers.get(pid) or {}).get('first_offered')
            if pid.startswith('drift:'):
                first = min((f['since'] for f in item.get('features', []) if f.get('since')), default=None)
            item['first_offered'] = first
            try:
                zone = ZoneInfo('Asia/Jerusalem')
                if len(first) == 10:
                    offered_day = date.fromisoformat(first)
                else:
                    stamp = datetime.fromisoformat(first.replace('Z', '+00:00'))
                    offered_day = stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(zone).date()
                days = (now.astimezone(zone).date() - offered_day).days
                item['waiting_days'] = days if days >= 0 else None
            except (TypeError, ValueError):
                item['waiting_days'] = None
            previous = records.get(pid)
            item['dismissed'] = bool(previous and previous['fingerprint'] == self.fingerprint(item))
            item['dismissed_at'] = previous['dismissed_at'] if item['dismissed'] else None
            item['returned_changed'] = bool(previous and not item['dismissed'])
            item['untouched'] = bool(item['waiting_days'] is not None and item['waiting_days'] >= 3 and not previous and pid not in acted)
        for group in (items, drift_items):
            group.sort(key=lambda i: (not i.get('untouched', False), -(i.get('waiting_days') or 0)))
        current = {i.get('proposal_id') for i in items + drift_items}
        report['dismissed_items'] = [i for i in items + drift_items if i.get('dismissed')]
        report['dismissed_items'] += [{**r['item'], 'dismissed_at': r['dismissed_at']} for pid, r in records.items()
                                      if pid not in current and r.get('item')]
# END clear-actions
