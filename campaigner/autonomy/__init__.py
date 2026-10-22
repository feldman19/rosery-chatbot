"""Offline autonomy policy and job ledger. No provider execution or authentication.
All identity and evidence claims must be supplied by a trusted, separately built host.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid

class AutonomyDenied(ValueError): pass

def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def stamp(x=None):
    if x is None: return datetime.now(timezone.utc)
    if isinstance(x,str):
        try: x=datetime.fromisoformat(x.replace('Z','+00:00'))
        except ValueError as e: raise AutonomyDenied('invalid_time') from e
    if not isinstance(x,datetime) or x.tzinfo is None or x.utcoffset() is None: raise AutonomyDenied('aware_time_required')
    return x.astimezone(timezone.utc)
def text(x):
    if not isinstance(x,str) or not x.strip() or len(x)>200: raise AutonomyDenied('invalid_identifier')
    return x

class AutonomyStore:
    def __init__(self,db_path,signing_key):
        if not isinstance(signing_key,bytes) or len(signing_key)<32: raise ValueError('key_minimum_32_bytes')
        self.path=str(db_path); self.key=signing_key
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS auto_accounts(account TEXT PRIMARY KEY,tenant TEXT NOT NULL,mode TEXT NOT NULL,killed INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS auto_grants(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,payload TEXT NOT NULL,signature TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS auto_jobs(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,signature TEXT NOT NULL,claim_token TEXT,worker TEXT,lease_end TEXT,proof TEXT);
            CREATE TABLE IF NOT EXISTS auto_reservations(job TEXT PRIMARY KEY,tenant TEXT NOT NULL,account TEXT NOT NULL,currency TEXT NOT NULL,kind TEXT NOT NULL,bucket TEXT NOT NULL,amount INTEGER NOT NULL,at TEXT NOT NULL,released INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS auto_intent_keys(tenant TEXT,account TEXT,decision_key TEXT,intent TEXT NOT NULL,PRIMARY KEY(tenant,account,decision_key));
            ''')
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None); db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE'); yield db
            if db.in_transaction: db.commit()
        except BaseException:
            if db.in_transaction: db.rollback()
            raise
        finally: db.close()
    def _sign(self,p): return hmac.new(self.key,canonical(p).encode(),hashlib.sha256).hexdigest()
    def _account(self,db,t,a):
        row=db.execute('SELECT * FROM auto_accounts WHERE tenant=? AND account=?',(t,a)).fetchone()
        if not row: raise AutonomyDenied('account_not_bound')
        return row
    def bind_account(self,tenant,account):
        text(tenant); text(account)
        with self._db() as db:
            old=db.execute('SELECT tenant FROM auto_accounts WHERE account=?',(account,)).fetchone()
            if old and old['tenant']!=tenant: raise AutonomyDenied('account_already_bound')
            db.execute("INSERT OR IGNORE INTO auto_accounts VALUES(?,?,'observe',0)",(account,tenant))
    def set_mode(self,tenant,account,mode='observe'):
        if mode not in ('observe','propose','execute_in_envelope'): raise AutonomyDenied('invalid_mode')
        with self._db() as db:
            self._account(db,tenant,account)
            db.execute('UPDATE auto_accounts SET mode=? WHERE tenant=? AND account=?',(mode,tenant,account))
    def set_kill_switch(self,tenant,account,enabled):
        if type(enabled) is not bool: raise AutonomyDenied('boolean_required')
        with self._db() as db:
            self._account(db,tenant,account)
            db.execute('UPDATE auto_accounts SET killed=? WHERE tenant=? AND account=?',(int(enabled),tenant,account))
    def issue_grant(self,tenant,account,scope):
        fields={'currency','budget_type','cap_minor','allowed_actions','allowed_entity_ids','expires_at','ends_at','min_interval_seconds','max_evidence_age_seconds'}
        if not isinstance(scope,dict) or set(scope)!=fields: raise AutonomyDenied('invalid_scope_fields')
        p=json.loads(canonical(scope))
        if p['budget_type'] not in ('daily','total') or not isinstance(p['currency'],str) or len(p['currency'])!=3 or not p['currency'].isascii() or not p['currency'].isupper() or not p['currency'].isalpha(): raise AutonomyDenied('invalid_budget_scope')
        for k in ('cap_minor','min_interval_seconds','max_evidence_age_seconds'):
            if type(p[k]) is not int or not 0<=p[k]<=10**12: raise AutonomyDenied('invalid_limit')
        if not isinstance(p['allowed_actions'],list) or not p['allowed_actions'] or set(p['allowed_actions'])-{'set_budget','pause'}: raise AutonomyDenied('invalid_actions')
        if not isinstance(p['allowed_entity_ids'],list) or not p['allowed_entity_ids']: raise AutonomyDenied('entities_required')
        for entity in p['allowed_entity_ids']: text(entity)
        p['expires_at']=stamp(p['expires_at']).isoformat(); p['ends_at']=stamp(p['ends_at']).isoformat()
        p.update(id=str(uuid.uuid4()),tenant=tenant,account=account,daily_timezone='UTC')
        with self._db() as db:
            self._account(db,tenant,account)
            db.execute('INSERT INTO auto_grants VALUES(?,?,?,?,?)',(p['id'],tenant,account,canonical(p),self._sign(p)))
        return p
    def _grant(self,db,tenant,gid):
        row=db.execute('SELECT * FROM auto_grants WHERE id=? AND tenant=?',(gid,tenant)).fetchone()
        if not row: raise AutonomyDenied('unknown_grant')
        p=json.loads(row['payload'])
        if not hmac.compare_digest(row['signature'],self._sign(p)) or p['tenant']!=tenant or p['account']!=row['account'] or p['id']!=gid: raise AutonomyDenied('invalid_grant_signature')
        return p
    def create_intent(self,tenant,account,grant_id,entity_id,action,amount_minor,evidence_refs,evidence_at,decision_key=None):
        if decision_key is not None and (not isinstance(decision_key,str) or len(decision_key)!=64 or any(c not in '0123456789abcdef' for c in decision_key)):raise AutonomyDenied('invalid_decision_key')
        if type(amount_minor) is not int or amount_minor<0 or amount_minor>10**12: raise AutonomyDenied('invalid_amount')
        if not isinstance(evidence_refs,list) or not evidence_refs or len(evidence_refs)>50: raise AutonomyDenied('evidence_required')
        for ref in evidence_refs:
            if not isinstance(ref,str) or not ref.startswith('sha256:') or len(ref)!=71 or any(c not in '0123456789abcdef' for c in ref[7:]): raise AutonomyDenied('evidence_hash_required')
        with self._db() as db:
            acc=self._account(db,tenant,account)
            if acc['mode']=='observe' or acc['killed']: raise AutonomyDenied('proposal_disabled')
            grant=self._grant(db,tenant,grant_id)
            if grant['account']!=account or entity_id not in grant['allowed_entity_ids'] or action not in grant['allowed_actions']: raise AutonomyDenied('outside_scope')
            if action=='pause' and amount_minor!=0: raise AutonomyDenied('pause_amount_must_be_zero')
            p={'id':str(uuid.uuid4()),'tenant':tenant,'account':account,'grant_id':grant_id,'entity_id':entity_id,'action':action,'amount_minor':amount_minor,'evidence_refs':sorted(set(evidence_refs)),'evidence_at':stamp(evidence_at).isoformat()}
            if decision_key is not None:
                prior=db.execute('SELECT intent FROM auto_intent_keys WHERE tenant=? AND account=? AND decision_key=?',(tenant,account,decision_key)).fetchone()
                if prior:
                    row,old=self._job(db,tenant,prior['intent'])
                    if {k:v for k,v in p.items() if k!='id'}!={k:v for k,v in old.items() if k!='id'}:raise AutonomyDenied('decision_key_conflict')
                    return self._view(row)
            db.execute("INSERT INTO auto_jobs(id,tenant,account,state,payload,signature) VALUES(?,?,?,'pending',?,?)",(p['id'],tenant,account,canonical(p),self._sign(p)))
            if decision_key is not None:db.execute('INSERT INTO auto_intent_keys VALUES(?,?,?,?)',(tenant,account,decision_key,p['id']))
        return self.get(tenant,p['id'])
    def _job(self,db,tenant,jid):
        row=db.execute('SELECT * FROM auto_jobs WHERE id=? AND tenant=?',(jid,tenant)).fetchone()
        if not row: raise AutonomyDenied('unknown_intent')
        p=json.loads(row['payload'])
        if not hmac.compare_digest(row['signature'],self._sign(p)) or p['id']!=jid or p['tenant']!=tenant or p['account']!=row['account']: raise AutonomyDenied('intent_changed')
        return row,p
    def _view(self,row):
        return {**json.loads(row['payload']),'state':row['state'],'lease_end':row['lease_end'],'proof_ref':row['proof'],'live':False}
    def get(self,tenant,intent_id):
        with self._db() as db: return self._view(self._job(db,tenant,intent_id)[0])
    def list(self,tenant):
        with self._db() as db:
            ids=[r[0] for r in db.execute('SELECT id FROM auto_jobs WHERE tenant=? ORDER BY rowid',(tenant,))]
            return [self._view(self._job(db,tenant,i)[0]) for i in ids]
    def claim(self,tenant,intent_id,worker,context,now=None,lease_seconds=60):
        now=stamp(now); text(worker)
        if type(lease_seconds) is not int or not 1<=lease_seconds<=300: raise AutonomyDenied('invalid_lease')
        fields={'currency','budget_type','evidence_refs','evidence_at','data_quality_ok','stock_ok','offer_ok'}
        if not isinstance(context,dict) or set(context)!=fields: raise AutonomyDenied('invalid_context')
        with self._db() as db:
            row,p=self._job(db,tenant,intent_id)
            if row['state']!='pending': raise AutonomyDenied('already_claimed_no_retry')
            acc=self._account(db,tenant,p['account'])
            if acc['mode']!='execute_in_envelope' or acc['killed']: raise AutonomyDenied('execution_disabled')
            g=self._grant(db,tenant,p['grant_id'])
            if now>=stamp(g['expires_at']) or now>=stamp(g['ends_at']): raise AutonomyDenied('grant_expired_or_ended')
            if context['currency']!=g['currency'] or context['budget_type']!=g['budget_type']: raise AutonomyDenied('budget_scope_mismatch')
            if not isinstance(context['evidence_refs'],list) or sorted(set(context['evidence_refs']))!=p['evidence_refs']: raise AutonomyDenied('evidence_changed')
            if stamp(context['evidence_at']).isoformat()!=p['evidence_at']: raise AutonomyDenied('evidence_time_changed')
            age=(now-stamp(p['evidence_at'])).total_seconds()
            if age<0 or age>g['max_evidence_age_seconds']: raise AutonomyDenied('evidence_not_fresh')
            if any(context[k] is not True for k in ('data_quality_ok','stock_ok','offer_ok')): raise AutonomyDenied('quality_stock_offer_block')
            # Entity reservation prevents a second grant/job bypassing UNKNOWN or active work.
            others=db.execute("SELECT payload FROM auto_jobs WHERE tenant=? AND account=? AND state IN ('claimed','unknown')",(tenant,p['account'])).fetchall()
            if any(json.loads(r['payload'])['entity_id']==p['entity_id'] for r in others): raise AutonomyDenied('entity_reserved')
            previous=db.execute('SELECT MAX(at) FROM auto_reservations WHERE tenant=? AND account=?',(tenant,p['account'])).fetchone()[0]
            if previous and (now-stamp(previous)).total_seconds()<g['min_interval_seconds']: raise AutonomyDenied('cadence_block')
            bucket=now.date().isoformat() if g['budget_type']=='daily' else 'all'
            # A different currency/type cannot hide existing account exposure.
            mismatch=db.execute('SELECT 1 FROM auto_reservations WHERE tenant=? AND account=? AND released=0 AND (currency<>? OR kind<>?) LIMIT 1',(tenant,p['account'],g['currency'],g['budget_type'])).fetchone()
            if mismatch: raise AutonomyDenied('mixed_budget_ledger')
            # Unresolved exposure is counted across midnight, not discarded on rollover.
            used=db.execute("SELECT COALESCE(SUM(r.amount),0) FROM auto_reservations r JOIN auto_jobs j ON j.id=r.job WHERE r.tenant=? AND r.account=? AND r.currency=? AND r.kind=? AND r.released=0 AND (r.bucket=? OR j.state IN ('claimed','unknown'))",(tenant,p['account'],g['currency'],g['budget_type'],bucket)).fetchone()[0]
            if used+p['amount_minor']>g['cap_minor']: raise AutonomyDenied('aggregate_cap_exceeded')
            token=secrets.token_urlsafe(32)
            lease_end=min(now+timedelta(seconds=lease_seconds),stamp(g['expires_at']),stamp(g['ends_at']))
            db.execute('INSERT INTO auto_reservations(job,tenant,account,currency,kind,bucket,amount,at) VALUES(?,?,?,?,?,?,?,?)',(intent_id,tenant,p['account'],g['currency'],g['budget_type'],bucket,p['amount_minor'],now.isoformat()))
            db.execute("UPDATE auto_jobs SET state='claimed',claim_token=?,worker=?,lease_end=? WHERE id=?",(token,worker,lease_end.isoformat(),intent_id))
            return {**p,'state':'claimed','claim_token':token,'lease_end':lease_end.isoformat(),'live':False}
    def verify_claim(self,tenant,intent_id,claim_token,now=None):
        """Trusted coordinator pre-write guard; does not extend a lease or grant."""
        now=stamp(now)
        with self._db() as db:
            row,p=self._job(db,tenant,intent_id)
            acc=self._account(db,tenant,p['account']);g=self._grant(db,tenant,p['grant_id'])
            if row['state']!='claimed' or not isinstance(claim_token,str) or not hmac.compare_digest(row['claim_token'] or '',claim_token):raise AutonomyDenied('claim_not_owned')
            if acc['killed'] or acc['mode']!='execute_in_envelope':raise AutonomyDenied('execution_disabled')
            if now>=stamp(row['lease_end']) or now>=stamp(g['expires_at']) or now>=stamp(g['ends_at']):raise AutonomyDenied('lease_or_grant_expired')
            if not 0<=(now-stamp(p['evidence_at'])).total_seconds()<=g['max_evidence_age_seconds']:raise AutonomyDenied('evidence_not_fresh')
            return True

    def complete(self,tenant,intent_id,claim_token,outcome='verified',proof_ref=None,now=None):
        now=stamp(now)
        if outcome not in ('verified','unknown','not_applied'): raise AutonomyDenied('invalid_outcome')
        if outcome!='unknown': text(proof_ref)
        with self._db() as db:
            row,p=self._job(db,tenant,intent_id)
            if row['state']!='claimed' or not isinstance(claim_token,str) or not hmac.compare_digest(row['claim_token'] or '',claim_token): raise AutonomyDenied('claim_not_owned')
            if now>=stamp(row['lease_end']): outcome='unknown'
            db.execute('UPDATE auto_jobs SET state=?,proof=? WHERE id=?',(outcome,proof_ref,intent_id))
            if outcome=='not_applied': db.execute('UPDATE auto_reservations SET released=1 WHERE job=?',(intent_id,))
            return self._view(self._job(db,tenant,intent_id)[0])
    def expire_leases(self,now=None):
        now=stamp(now)
        with self._db() as db:
            rows=db.execute("SELECT id,lease_end FROM auto_jobs WHERE state='claimed'").fetchall()
            expired=[r['id'] for r in rows if now>=stamp(r['lease_end'])]
            for jid in expired: db.execute("UPDATE auto_jobs SET state='unknown' WHERE id=?",(jid,))
            return len(expired)

