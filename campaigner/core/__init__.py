"""Local mock campaigner only. Trusted host supplies authenticated tenant/actor.
No network adapters. SQLite mock conditional writes are stronger than many real APIs.
"""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

class Denied(ValueError):
    pass

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

class Campaigner:
    def __init__(self, db_path, signing_key, max_budget_minor=20000):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError('signing_key must be at least 32 bytes; persist outside DB')
        self.db_path = str(db_path)
        self.key = signing_key
        if type(max_budget_minor) is not int or max_budget_minor < 0:
            raise ValueError('invalid mock budget limit')
        self.max_budget_minor = max_budget_minor
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS accounts(account TEXT PRIMARY KEY, tenant TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS entities(tenant TEXT, account TEXT, entity TEXT, version INTEGER, data TEXT, PRIMARY KEY(tenant,account,entity));
            CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, account TEXT NOT NULL, entity TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, at REAL NOT NULL, kind TEXT NOT NULL, proposal TEXT, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decision_keys(tenant TEXT,account TEXT,entity TEXT,digest TEXT,proposal TEXT NOT NULL,PRIMARY KEY(tenant,account,entity,digest));
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=10000')
            db.execute('BEGIN IMMEDIATE')
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, tenant, kind, pid=None, data=None):
        db.execute('INSERT INTO events(tenant,at,kind,proposal,data) VALUES(?,?,?,?,?)', (tenant,time.time(),kind,pid,canonical(data or {})))

    def _bound(self, db, tenant, account):
        row = db.execute('SELECT tenant FROM accounts WHERE account=?', (account,)).fetchone()
        if not row or row['tenant'] != tenant:
            raise Denied('account_not_bound')

    def bind_account(self, tenant_id, account_id):
        if not tenant_id or not account_id:
            raise Denied('empty_identity')
        with self._db() as db:
            row = db.execute('SELECT tenant FROM accounts WHERE account=?',(account_id,)).fetchone()
            if row and row['tenant'] != tenant_id:
                raise Denied('account_already_bound')
            db.execute('INSERT OR IGNORE INTO accounts VALUES(?,?)',(account_id,tenant_id))
            self._event(db,tenant_id,'account_bound',data={'account':account_id})

    def _validate_diff(self, diff):
        if not isinstance(diff,dict) or not diff or set(diff)-{'budget_minor','status'}:
            raise Denied('unsupported_diff')
        if 'budget_minor' in diff and (type(diff['budget_minor']) is not int or diff['budget_minor'] < 0):
            raise Denied('invalid_budget')
        if diff.get('budget_minor', 0) > self.max_budget_minor:
            raise Denied('mock_budget_limit_exceeded')
        if 'status' in diff and diff['status'] not in ('ACTIVE','PAUSED'):
            raise Denied('invalid_status')

    def seed_entity(self, tenant_id, account_id, entity_id, state):
        """Mock admin helper, also simulates external manual edits. Never expose publicly."""
        self._validate_diff({k:v for k,v in state.items() if k in ('budget_minor','status')})
        if set(state) != {'currency','budget_type','budget_minor','status'} or state['budget_type'] not in ('daily','total') or not isinstance(state['currency'],str) or len(state['currency']) != 3:
            raise Denied('invalid_mock_state')
        with self._db() as db:
            self._bound(db,tenant_id,account_id)
            db.execute('INSERT INTO entities VALUES(?,?,?,?,?) ON CONFLICT(tenant,account,entity) DO UPDATE SET version=version+1,data=excluded.data',(tenant_id,account_id,entity_id,1,canonical(state)))
            self._event(db,tenant_id,'mock_entity_seeded',data={'entity':entity_id})

    def _entity(self,db,tenant,account,entity):
        self._bound(db,tenant,account)
        row=db.execute('SELECT * FROM entities WHERE tenant=? AND account=? AND entity=?',(tenant,account,entity)).fetchone()
        if not row:
            raise Denied('unknown_entity')
        return {'version':row['version'], 'state':json.loads(row['data'])}

    def get_entity(self,tenant_id,account_id,entity_id):
        with self._db() as db:
            return self._entity(db,tenant_id,account_id,entity_id)

    def _load(self,db,tenant,pid):
        row=db.execute('SELECT data FROM proposals WHERE id=? AND tenant=?',(pid,tenant)).fetchone()
        if not row:
            raise Denied('unknown_proposal')
        return json.loads(row['data'])

    def _save(self,db,p):
        db.execute('UPDATE proposals SET state=?,data=? WHERE id=? AND tenant=?',(p['state'],canonical(p),p['id'],p['tenant_id']))

    def _public(self,p):
        return {k:v for k,v in p.items() if k != 'approval'}

    def propose(self,tenant_id,actor,account_id,entity_id,diff,decision_digest=None):
        self._validate_diff(diff)
        if decision_digest is not None and (not isinstance(decision_digest,str) or len(decision_digest)!=64 or any(c not in '0123456789abcdef' for c in decision_digest)):
            raise Denied('invalid_decision_digest')
        if not actor:
            raise Denied('actor_required')
        with self._db() as db:
            if decision_digest is not None:
                prior=db.execute('SELECT proposal FROM decision_keys WHERE tenant=? AND account=? AND entity=? AND digest=?',(tenant_id,account_id,entity_id,decision_digest)).fetchone()
                if prior:
                    existing=self._load(db,tenant_id,prior['proposal'])
                    if existing['diff']!=diff or existing['actor']!=actor:raise Denied('decision_key_conflict')
                    return self._public(existing)
            current=self._entity(db,tenant_id,account_id,entity_id)
            p={'id':str(uuid.uuid4()),'tenant_id':tenant_id,'actor':actor,'account_id':account_id,'entity_id':entity_id,'revision':1,'state':'awaiting_approval','before':current['state'],'before_version':current['version'],'diff':dict(diff),'created_at':time.time(),'receipt':None}
            if decision_digest is not None:p['decision_digest']=decision_digest
            db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?)',(p['id'],tenant_id,account_id,entity_id,p['state'],canonical(p)))
            if decision_digest is not None:db.execute('INSERT INTO decision_keys VALUES(?,?,?,?,?)',(tenant_id,account_id,entity_id,decision_digest,p['id']))
            self._event(db,tenant_id,'proposed',p['id'],{'diff':diff})
            return self._public(p)

    def _material(self,p):
        fields=('id','tenant_id','actor','account_id','entity_id','revision','before','before_version','diff')
        return {**{k:p[k] for k in fields},**({'decision_digest':p['decision_digest']} if 'decision_digest' in p else {})}

    def _sign(self,payload):
        return hmac.new(self.key,canonical(payload).encode(),hashlib.sha256).hexdigest()

    def approve(self,tenant_id,actor,proposal_id,ttl_seconds=300):
        if not actor or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise Denied('invalid_approval')
        with self._db() as db:
            p=self._load(db,tenant_id,proposal_id)
            if p['state'] != 'awaiting_approval':
                raise Denied('not_awaiting_approval')
            payload={'proposal':self._material(p),'approver':actor,'expires_at':time.time()+ttl_seconds,'nonce':secrets.token_hex(16)}
            p['approval']={'payload':payload,'signature':self._sign(payload),'used':False}
            p['state']='approved'
            self._save(db,p)
            self._event(db,tenant_id,'approved',proposal_id,{'approver':actor})
            return self._public(p)

    def _finish(self,db,p,state,reason,verified=None):
        p['state']=state
        p['receipt']={'proposal_id':p['id'],'state':state,'reason':reason,'verified':verified,'at':time.time()}
        self._save(db,p)
        self._event(db,p['tenant_id'],state,p['id'],p['receipt'])
        return self._public(p)

    def execute(self,tenant_id,proposal_id,fault=None,pre_write_check=None):
        if fault not in (None,'before_call','after_success_timeout','after_success_crash'):
            raise Denied('unknown_fault')
        with self._db() as db:
            p=self._load(db,tenant_id,proposal_id)
            if p['state'] != 'approved':
                raise Denied('not_approved_or_already_claimed')
            try:
                self._validate_diff(p['diff'])
            except Denied as exc:
                return self._finish(db,p,'rejected',str(exc))
            approval=p.get('approval',{})
            payload=approval.get('payload',{})
            if approval.get('used') or not hmac.compare_digest(approval.get('signature',''),self._sign(payload)) or payload.get('proposal') != self._material(p) or payload.get('expires_at',0) <= time.time():
                return self._finish(db,p,'rejected','invalid_or_expired_approval')
            current=self._entity(db,tenant_id,p['account_id'],p['entity_id'])
            if current['version'] != p['before_version'] or current['state'] != p['before']:
                return self._finish(db,p,'rejected','state_drift')
            # Reserve entity until a definitive outcome. Unknown blocks later proposals.
            busy=db.execute("SELECT id FROM proposals WHERE tenant=? AND account=? AND entity=? AND state IN ('executing','unknown') AND id<>?",(tenant_id,p['account_id'],p['entity_id'],proposal_id)).fetchone()
            if busy:
                raise Denied('entity_reserved')
            p['approval']['used']=True
            p['state']='executing'
            self._save(db,p)
            self._event(db,tenant_id,'claimed',proposal_id)
        if fault == 'before_call':
            with self._db() as db:
                return self._finish(db,self._load(db,tenant_id,proposal_id),'unknown','simulated_crash_before_submission')
        # Mock provider has an atomic conditional write and durable operation ID.
        with self._db() as db:
            p=self._load(db,tenant_id,proposal_id)
            current=self._entity(db,tenant_id,p['account_id'],p['entity_id'])
            if current['version'] != p['before_version']:
                return self._finish(db,p,'rejected','provider_version_conflict')
            if pre_write_check is not None:
                try:allowed=pre_write_check() is True
                except Exception:allowed=False
                if not allowed:return self._finish(db,p,'rejected','host_pre_write_guard_denied')
            updated={**current['state'],**p['diff']}
            db.execute('UPDATE entities SET data=?,version=version+1 WHERE tenant=? AND account=? AND entity=? AND version=?',(canonical(updated),tenant_id,p['account_id'],p['entity_id'],p['before_version']))
            result={'state':updated,'version':current['version']+1,'operation_id':proposal_id}
            db.execute('INSERT INTO operations VALUES(?,?,?)',(proposal_id,tenant_id,canonical(result)))
        if fault == 'after_success_crash':
            # Leave durable EXECUTING as a real process death would. Reconcile explicitly.
            raise RuntimeError('simulated_process_crash')
        with self._db() as db:
            p=self._load(db,tenant_id,proposal_id)
            if fault == 'after_success_timeout':
                return self._finish(db,p,'unknown','provider_timeout_no_retry')
            current=self._entity(db,tenant_id,p['account_id'],p['entity_id'])
            if current['version'] != result['version'] or current['state'] != result['state']:
                return self._finish(db,p,'unknown','operation_succeeded_but_current_state_drifted',result)
            return self._finish(db,p,'verified','mock_operation_and_readback',result)

    def reconcile(self,tenant_id,proposal_id):
        with self._db() as db:
            p=self._load(db,tenant_id,proposal_id)
            if p['state'] not in ('unknown','executing'):
                return self._public(p)
            op=db.execute('SELECT result FROM operations WHERE id=? AND tenant=?',(proposal_id,tenant_id)).fetchone()
            if not op:
                return self._finish(db,p,'unknown','no_definitive_provider_evidence_no_retry')
            result=json.loads(op['result'])
            current=self._entity(db,tenant_id,p['account_id'],p['entity_id'])
            if current['version'] != result['version'] or current['state'] != result['state']:
                return self._finish(db,p,'unknown','operation_succeeded_but_current_state_drifted',result)
            return self._finish(db,p,'verified','reconciled_mock_operation_and_readback',result)

    def get_proposal(self,tenant_id,proposal_id):
        with self._db() as db:
            return self._public(self._load(db,tenant_id,proposal_id))

    def list_proposals(self,tenant_id):
        with self._db() as db:
            return [self._public(json.loads(r['data'])) for r in db.execute('SELECT data FROM proposals WHERE tenant=? ORDER BY rowid DESC',(tenant_id,))]

    def audit(self,tenant_id):
        with self._db() as db:
            return [dict(r) for r in db.execute('SELECT seq,at,kind,proposal,data FROM events WHERE tenant=? ORDER BY seq',(tenant_id,))]


