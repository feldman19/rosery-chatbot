"""Automatic read-only assessment and follow-up journal; never a live executor."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

from decisions import build_report
from meta_direct import ACCOUNT, LAUNCH
from decision_history import project_observation
from business import ACTIVE as BUSINESS


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def totals(rows):
    spend = Decimal(0); registrations = Decimal(0)
    for row in rows:
        value = Decimal(str(row['spend']))
        if not value.is_finite() or value < 0: raise ValueError('Invalid spend')
        spend += value
        actions = {a['action_type']: a['value'] for a in row.get('actions', [])}
        value = Decimal(str(actions.get('omni_complete_registration', actions.get('complete_registration', 0))))
        if not value.is_finite() or value < 0: raise ValueError('Invalid attributed count')
        registrations += value
    return {'spend_ils': str(spend), 'registrations_attributed': str(registrations),
            'cpl_attributed_ils': str((spend / registrations).quantize(Decimal('.01'))) if registrations else None}


class DecisionLoop:
    def __init__(self, directory):
        self.path = Path(directory) / 'decision-loop.sqlite3'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS assessments(
                id TEXT PRIMARY KEY, at TEXT NOT NULL, local_day TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(
                id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL, at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shadow_assessments(
                id TEXT PRIMARY KEY, at TEXT NOT NULL, local_day TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS watermark(id INTEGER PRIMARY KEY, source_time TEXT NOT NULL);''')
            # Preserve byte-for-byte the first local development receipt, moving
            # only this schema to its table in the same journal. Legacy readers
            # continue reading their original assessments without schema mixing.
            for row in db.execute("SELECT * FROM assessments WHERE payload LIKE '%recommendation_shadow_v1%'").fetchall():
                if json.loads(row['payload']).get('schema')!='recommendation_shadow_v1':continue
                self._validate_shadow(row['id'],json.loads(row['payload']))
                db.execute('INSERT OR IGNORE INTO shadow_assessments VALUES(?,?,?,?)',tuple(row))
                saved=db.execute('SELECT payload FROM shadow_assessments WHERE id=?',(row['id'],)).fetchone()
                if saved['payload']!=row['payload']:raise ValueError('Migration payload conflict')
                db.execute('DELETE FROM assessments WHERE id=?',(row['id'],))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10); db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback(); raise
        finally: db.close()

    def record(self, snapshot, settings, now=None):
        now = now or datetime.now(timezone.utc)
        source = snapshot.get('sources', {}).get('meta', {})
        data = source.get('data') or {}
        # Cached, partial, foreign or malformed data cannot create a learning record.
        try:
            offset = float(data['timezone_offset_hours'])
            if not -14 <= offset <= 14: raise ValueError('Invalid timezone offset')
            local_day = now.astimezone(timezone(timedelta(hours=offset))).date().isoformat()
            age = (now - datetime.fromisoformat(source['last_success_at'])).total_seconds()
            valid = (not source.get('error') and 0 <= age <= 720 and data.get('verified') is True
                     and data.get('complete') is True and data.get('account_id') == ACCOUNT
                     and data.get('currency') == BUSINESS.currency and data.get('today') == local_day
                     and data['since'] <= local_day <= data['until'])
        except (KeyError, TypeError, ValueError): valid = False
        if not valid: return self.read()
        rows = data.get('rows', [])
        grains = [(r.get('ad_id'), r.get('date_start')) for r in rows]
        if any(not all(g) for g in grains) or len(set(grains)) != len(grains) or any(r.get('date_stop')!=r.get('date_start') for r in rows):
            raise ValueError('Daily ad grain required')
        report = build_report(snapshot, settings, now)
        windows = []
        for days in (1, 3, 7):
            start = (datetime.fromisoformat(local_day).date() - timedelta(days=days)).isoformat()
            end = (datetime.fromisoformat(local_day).date() - timedelta(days=1)).isoformat()
            covered = data['since'] <= start and end <= data['until']
            selected = [r for r in rows if r.get('campaign_id') == LAUNCH and start <= r['date_start'] <= end]
            windows.append({'days': days, 'since': start, 'until': end, 'covered': covered,
                            'metrics': totals(selected) if covered else None})
        assessment = {'account_id': ACCOUNT, 'campaign_id': LAUNCH, 'local_day': local_day,
            'decision': 'defer_automated_changes', 'title': 'ממתינים לאימות המדידה לפני החלטת תקציב',
            'reason': 'הנתונים מאפשרים תצפית בביצועים, אך לא אומתו נרשמים ייחודיים או ערכם לעסק',
            'next_step': 'השלמת התאמה בין הרשמה שנשמרה, אירוע ומודעת המקור',
            'windows': windows, 'inventory': report['current'],
            'blockers': [b['id'] for b in report['blockers']],
            'measurement_verified': False, 'execution_performed': False,
            'evaluation_plan': 'לאחר יום מלא נוסף נשמור תוצאות מיוחסות להשוואה תיאורית בלבד',
            'economics_state': report['economics']['state'], 'live_action_allowed': False}
        identity = digest(assessment)
        source_time = source['last_success_at']
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT source_time FROM watermark WHERE id=1').fetchone()
            if previous and source_time < previous['source_time']: return self.read()
            db.execute('INSERT OR REPLACE INTO watermark VALUES(1,?)', (source_time,))
            db.execute('INSERT OR IGNORE INTO assessments VALUES(?,?,?,?)',
                       (identity, now.isoformat(), local_day, encode(assessment)))
            # Follow up past assessments only after at least one complete later day.
            for old in db.execute('SELECT * FROM assessments WHERE local_day<?', (local_day,)).fetchall():
                if json.loads(old['payload']).get('schema')=='recommendation_shadow_v1':continue
                start = (datetime.fromisoformat(old['local_day']).date() + timedelta(days=1)).isoformat()
                end = (datetime.fromisoformat(local_day).date() - timedelta(days=1)).isoformat()
                if not data['since'] <= start <= end <= data['until']: continue
                selected = [r for r in rows if r.get('campaign_id') == LAUNCH and start <= r['date_start'] <= end]
                observation = {'since': start, 'until': end, 'metrics': totals(selected),
                    'source': 'Meta attributed registrations', 'causal_effect_verified': False,
                    'experiment_executed': False,
                    'note': 'תצפית לאחר הערכה, ללא שינוי שבוצע במערכת וללא הוכחת השפעה'}
                oid = digest({'assessment': old['id'], 'observation': observation})
                db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?)',
                           (oid, old['id'], now.isoformat(), encode(observation)))
        return self.read()

    def freeze_recommendations(self, snapshot, recommendations, dossier, now=None):
        """Append once to the existing journal; never upgrade authority or rewrite."""
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None:raise ValueError('Aware freeze time required')
        if recommendations.get('account_id')!=ACCOUNT:raise ValueError('Wrong account')
        baseline=project_observation(snapshot.get('sources',{}).get('meta') or {})
        if not isinstance(baseline,dict):return {'status':'not_recorded','reason':baseline}
        if datetime.fromisoformat(baseline['observed_at'])>now:raise ValueError('Future source')
        root=Path(__file__).parent
        files=('action_recommendations.py','decision_evidence.py','order_economics.py',
               'management_report.py','decision_loop.py','decision_history.py','shadow_reporting_check.py',
               'decision_uncertainty.py','server.py')
        policy={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
        binding={'account_id':ACCOUNT,'source_sha256':baseline['content_sha256'],
                 'dossier_sha256':digest(dossier),'policy_sha256':digest(policy)}
        identity=digest(binding)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT * FROM shadow_assessments WHERE id=?',(identity,)).fetchone()
            if existing:
                payload=json.loads(existing['payload']);self._validate_shadow(identity,payload)
                return {'status':'already_recorded','id':identity,'frozen_at':payload['frozen_at']}
            payload={'schema':'recommendation_shadow_v1','input_binding':binding,
                'frozen_at':now.isoformat(),'policy_files':policy,
                'decision_output':recommendations,'decision_output_sha256':digest(recommendations),
                'baseline':baseline,'execution_performed':False,'live_action_allowed':False,
                'decision':'frozen_recommendation_shadow','title':'החלטות נשמרו לבדיקה עתידית',
                'reason':'תיעוד בזמן אמת; אין בכך אישור לפעולה או הוכחת שיפור',
                'evaluation_criterion':{
                    'version':'reporting_revision_v1','kind':'reporting_revision_only',
                    'eligible_after':now.isoformat(),'comparison':'same recorded ad/day cells',
                    'since':(datetime.fromisoformat(baseline['source_today']).date()-timedelta(days=7)).isoformat(),
                    'until':(datetime.fromisoformat(baseline['source_today']).date()-timedelta(days=1)).isoformat(),
                    'requires':'later source; identical nonempty query settings; full baseline date coverage; matching account/currency/timezone; valid archive digest',
                    'maturity_assumed':False,'profit_target':None,'cac_threshold':None,
                    'profit_evaluation':'not_identified','causal_evaluation':'not_identified',
                    'decision_quality_evaluation':'not_validated_by_reporting_revision'},
                'missing':['prospective_decision_loss_policy','causal_identification',
                           'attribution_settings_history','maturity_evidence']}
            payload['integrity_sha256']=digest(payload)
            db.execute('INSERT INTO shadow_assessments VALUES(?,?,?,?)',
                       (identity,now.isoformat(),baseline['source_today'],encode(payload)))
        return {'status':'recorded','id':identity,'frozen_at':now.isoformat()}

    @staticmethod
    def _validate_shadow(identity,payload):
        body=dict(payload);claimed=body.pop('integrity_sha256',None)
        if (claimed!=digest(body) or identity!=digest(payload['input_binding'])
            or payload['decision_output_sha256']!=digest(payload['decision_output'])):
            raise ValueError('Shadow record integrity mismatch')

    def evaluate_frozen(self, identity):
        """Pure local readback; report revision is not action/profit validation."""
        from shadow_reporting_check import evaluate_record
        with self.db() as db:
            row=db.execute('SELECT payload FROM shadow_assessments WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('Unknown frozen decision')
        payload=json.loads(row['payload'])
        self._validate_shadow(identity,payload)
        return evaluate_record(payload,self.path.parent/'decision-observations')

    def read_frozen(self):
        with self.db() as db:
            rows=[dict(x) for x in db.execute('SELECT * FROM shadow_assessments ORDER BY rowid DESC')]
        for row in rows:
            row['payload']=json.loads(row['payload']);self._validate_shadow(row['id'],row['payload'])
        return rows

    def read(self):
        with self.db() as db:
            assessments = [dict(r) for r in db.execute('SELECT * FROM assessments ORDER BY rowid DESC LIMIT 30')]
            observations = [dict(r) for r in db.execute('SELECT * FROM observations ORDER BY rowid DESC LIMIT 30')]
            count = db.execute('SELECT count(*) FROM assessments').fetchone()[0]
        for rows in (assessments, observations):
            for row in rows:
                row['payload'] = json.loads(row['payload'])
                if rows is assessments and row['payload'].get('schema')=='recommendation_shadow_v1':
                    self._validate_shadow(row['id'],row['payload'])
                    continue
                expected = digest(row['payload']) if rows is assessments else digest({'assessment':row['assessment_id'],'observation':row['payload']})
                if row['id'] != expected: raise ValueError('Journal integrity mismatch')
        return {'assessments': assessments, 'observations': observations, 'assessment_count': count,
                'live_action_allowed': False, 'causal_effect_verified': False,
                'mode': 'automatic_readonly_assessment', 'write_connected': False}
