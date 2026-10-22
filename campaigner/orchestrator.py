"""Isolated business-decision-to-mock-execution coordinator.

No network, credential readers or real-account fallback. A trusted host supplies
normalized evidence; this is acceptance infrastructure, not production authority.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3

from core import Campaigner, Denied
from autonomy import AutonomyStore
from strategy import evaluate


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def binding(profile, evidence, policy, candidate):
    material={k:candidate.get(k) for k in ('action','before','after','evidence_refs','rollback_condition')}
    return hashlib.sha256(canonical([profile,evidence,policy,material]).encode()).hexdigest()


class SandboxCoordinator:
    def __init__(self,directory,key):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'coordinator.sqlite3';self.key=key
        self.core=Campaigner(self.directory/'mock-provider.sqlite3',key)
        self.authority=AutonomyStore(self.directory/'authorization.sqlite3',key)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,tenant TEXT,account TEXT,entity TEXT,state TEXT,payload TEXT,proposal TEXT,intent TEXT,receipt TEXT,executed_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS artifacts(hash TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS run_signatures(id TEXT PRIMARY KEY,signature TEXT NOT NULL)')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()

    def _scope(self,profile):
        ids=[profile.get(k) for k in ('tenant_id','account_id','entity_id')]
        if any(not isinstance(v,str) or not v.startswith('sandbox-') for v in ids):
            raise Denied('sandbox_ids_required_no_live_execution')
        return ids

    def seal_evidence(self,evidence):
        """Trusted sandbox fixture builder only. Does not authenticate real data."""
        frozen=json.loads(canonical(evidence));frozen.setdefault('provenance',{}).pop('refs',None)
        raw=canonical(frozen);digest=hashlib.sha256(raw.encode()).hexdigest()
        with self.db() as db:db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?)',(digest,raw))
        frozen['provenance']['refs']=['sha256:'+digest]
        return frozen

    def _resolve(self,evidence):
        frozen=json.loads(canonical(evidence));refs=frozen.get('provenance',{}).pop('refs',[])
        raw=canonical(frozen);digest=hashlib.sha256(raw.encode()).hexdigest()
        if refs!=['sha256:'+digest]:raise Denied('evidence_not_content_bound')
        with self.db() as db:row=db.execute('SELECT payload FROM artifacts WHERE hash=?',(digest,)).fetchone()
        if row is None or row['payload']!=raw:raise Denied('evidence_artifact_missing_or_changed')

    def prepare(self,profile,evidence,policy,now=None):
        tenant,account,entity=self._scope(profile)
        self._resolve(evidence)
        candidate=evaluate(profile,evidence,policy,now)
        identity=binding(profile,evidence,policy,candidate)
        with self.db() as db:
            old=db.execute('SELECT * FROM runs WHERE id=?',(identity,)).fetchone()
            if old:return self._view(old,db.execute('SELECT signature FROM run_signatures WHERE id=?',(identity,)).fetchone())
        # Never hold coordinator while acquiring the core transaction. The
        # provider pre-write guard acquires these in core -> coordinator order.
        proposal=None;state=candidate['status']
        if state=='propose':
            current=self.core.get_entity(tenant,account,entity)
            if current['state']!=candidate['before']:raise Denied('provider_state_not_candidate_before')
            diff={k:v for k,v in candidate['after'].items() if candidate['before'].get(k)!=v}
            proposal=self.core.propose(tenant,'sandbox-coordinator',account,entity,diff,decision_digest=identity)['id']
            state='proposed'
        payload={'profile':profile,'evidence':evidence,'policy':policy,'candidate':candidate}
        with self.db() as db:
            old=db.execute('SELECT * FROM runs WHERE id=?',(identity,)).fetchone()
            if old:return self._view(old,db.execute('SELECT signature FROM run_signatures WHERE id=?',(identity,)).fetchone())
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL)',(identity,tenant,account,entity,state,canonical(payload),proposal))
            row=db.execute('SELECT * FROM runs WHERE id=?',(identity,)).fetchone()
            signature=self._seal(db,row)
            return self._view(row,{'signature':signature})

    def _seal(self,db,row):
        signature=hmac.new(self.key,canonical(dict(row)).encode(),hashlib.sha256).hexdigest()
        db.execute('INSERT OR REPLACE INTO run_signatures VALUES(?,?)',(row['id'],signature))
        return signature

    def _view(self,row,signature):
        expected=hmac.new(self.key,canonical(dict(row)).encode(),hashlib.sha256).hexdigest()
        if signature is None or not hmac.compare_digest(signature['signature'],expected):raise Denied('run_state_signature_invalid')
        value=dict(row);value['payload']=json.loads(value['payload'])
        p=value['payload']
        if binding(p['profile'],p['evidence'],p['policy'],p['candidate'])!=value['id']:raise Denied('run_binding_corrupt')
        value['receipt']=json.loads(value['receipt']) if value['receipt'] else None
        value.update(environment='sandbox',live_action_allowed=False,business_success_verified=False)
        return value

    def get(self,tenant,identity):
        with self.db() as db:
            row=db.execute('SELECT * FROM runs WHERE tenant=? AND id=?',(tenant,identity)).fetchone()
            if row is None:raise Denied('run_not_found')
            return self._view(row,db.execute('SELECT signature FROM run_signatures WHERE id=?',(identity,)).fetchone())

    def _finish(self,tenant,identity,state,receipt=None,at=None,expected=('dispatching',)):
        with self.db() as db:
            placeholders=','.join('?' for _ in expected)
            db.execute('UPDATE runs SET state=?,receipt=?,executed_at=? WHERE tenant=? AND id=? AND state IN ('+placeholders+')',
                       (state,canonical(receipt) if receipt else None,at,tenant,identity,*expected))
            self._seal(db,db.execute('SELECT * FROM runs WHERE tenant=? AND id=?',(tenant,identity)).fetchone())
        return self.get(tenant,identity)

    def dispatch(self,tenant,identity,grant_id,preflight,now=None,fault=None,clock=None):
        """Preflight is a trusted host callable, never values from a browser request."""
        clock=clock or ((lambda:now) if now is not None else (lambda:datetime.now(timezone.utc)))
        run=self.get(tenant,identity);saved=run['payload']
        self._scope(saved['profile'])
        if run['state']!='proposed':raise Denied('run_not_dispatchable_no_retry')
        profile,evidence,policy=preflight()
        self._scope(profile)
        self._resolve(evidence)
        fresh=evaluate(profile,evidence,policy,clock())
        if fresh['status']!='propose' or binding(profile,evidence,policy,fresh)!=identity:
            return self._finish(tenant,identity,'rejected',{'reason':'business_evidence_or_policy_changed'},expected=('proposed',))
        # Durable claim before contacting either subordinate ledger. A crash after
        # this point is UNKNOWN, never an invitation to repeat a provider write.
        with self.db() as db:
            changed=db.execute("UPDATE runs SET state='dispatching' WHERE tenant=? AND id=? AND state='proposed'",(tenant,identity)).rowcount
            if changed!=1:raise Denied('run_already_claimed')
            self._seal(db,db.execute('SELECT * FROM runs WHERE id=?',(identity,)).fetchone())
        claim=None
        try:
            candidate=saved['candidate'];refs=candidate['evidence_refs']+['sha256:'+identity]
            action=candidate['action'];amount=candidate['after']['budget_minor'] if action=='set_budget' else 0
            intent=self.authority.create_intent(tenant,run['account'],grant_id,run['entity'],action,amount,
                                                refs,evidence['observed_at'],decision_key=identity)
            with self.db() as db:
                db.execute('UPDATE runs SET intent=? WHERE id=?',(intent['id'],identity))
                self._seal(db,db.execute('SELECT * FROM runs WHERE id=?',(identity,)).fetchone())
            context={'currency':profile['currency'],'budget_type':candidate['before']['budget_type'],
                     'evidence_refs':refs,'evidence_at':evidence['observed_at'],
                     'data_quality_ok':True,'stock_ok':evidence['stock'].get('available') is True,
                     'offer_ok':evidence['offer'].get('current') is True}
            claim=self.authority.claim(tenant,intent['id'],'sandbox-coordinator',context,now=clock())
            proposal=self.core.get_proposal(tenant,run['proposal'])
            if proposal.get('decision_digest')!=identity:raise Denied('core_binding_changed')
            self.core.approve(tenant,'signed-sandbox-grant:'+grant_id,run['proposal'])
            def guard():
                current_time=clock()
                valid=evaluate(profile,evidence,policy,current_time)
                return (self.get(tenant,identity)['state']=='dispatching' and valid['status']=='propose' and binding(profile,evidence,policy,valid)==identity
                        and self.authority.verify_claim(tenant,intent['id'],claim['claim_token'],now=current_time))
            result=self.core.execute(tenant,run['proposal'],fault=fault,pre_write_check=guard)
            current=self.core.get_entity(tenant,run['account'],run['entity'])
            receipt=result.get('receipt') or {};verified=receipt.get('verified') or {}
            ok=(result['state']=='verified' and current['state']==candidate['after']
                and verified.get('state')==current['state'] and verified.get('version')==current['version']
                and verified.get('operation_id')==run['proposal'])
            outcome='verified' if ok else 'unknown'
            proof='mock-core:'+run['proposal']+':'+hashlib.sha256(canonical(receipt).encode()).hexdigest()
            completed_at=clock()
            completed=self.authority.complete(tenant,intent['id'],claim['claim_token'],outcome,proof,now=completed_at)
            state='verified' if ok and completed['state']=='verified' else 'unknown'
            return self._finish(tenant,identity,state,{'core_receipt':receipt,'readback':current,'authority_state':completed['state']},completed_at.isoformat() if state=='verified' else None)
        except Exception:
            if claim:
                try:self.authority.complete(tenant,claim['id'],claim['claim_token'],'unknown',now=clock())
                except Exception:pass
            return self._finish(tenant,identity,'unknown',{'reason':'dispatch_not_definitively_verified_no_retry'})

    def recover(self,tenant,identity):
        run=self.get(tenant,identity)
        if run['state']=='dispatching':
            return self._finish(tenant,identity,'unknown',{'reason':'restart_during_dispatch_requires_reconciliation'})
        return run

    def observe(self,tenant,identity,baseline,followup,other_changes,now=None):
        """Record descriptive comparison only, after verified execution/full days."""
        run=self.get(tenant,identity)
        if run['state']!='verified':raise Denied('verified_operation_required_before_outcome')
        core=self.core.get_proposal(tenant,run['proposal'])
        authority=self.authority.get(tenant,run['intent'])
        if core['state']!='verified' or core.get('receipt')!=(run.get('receipt') or {}).get('core_receipt') or authority['state']!='verified':raise Denied('verified_receipt_and_authority_required')
        executed=datetime.fromisoformat(run['executed_at'])
        now=now or datetime.now(timezone.utc)
        for w in (baseline,followup):
            if set(w)!={'started_at','ended_at','spend_minor','registrations','complete'} or w['complete'] is not True:
                raise Denied('complete_outcome_window_required')
            for k in ('spend_minor','registrations'):
                if type(w[k]) is not int or w[k]<0:raise Denied('missing_or_invalid_outcome_metric')
        b0,b1=[datetime.fromisoformat(baseline[k]) for k in ('started_at','ended_at')]
        f0,f1=[datetime.fromisoformat(followup[k]) for k in ('started_at','ended_at')]
        if any(t.tzinfo is None for t in (b0,b1,f0,f1)) or not b0<b1<=executed<f0<f1<=now or b1-b0!=f1-f0:
            raise Denied('incomparable_or_pre_execution_outcome')
        if any(t.utcoffset().total_seconds()!=0 or any((t.hour,t.minute,t.second,t.microsecond)) for t in (b0,b1,f0,f1)):
            raise Denied('sandbox_utc_full_day_boundaries_required')
        if not isinstance(other_changes,list):raise Denied('other_changes_disclosure_required')
        observation={'baseline':baseline,'followup':followup,'other_changes':other_changes,
                     'operational_success':True,'business_success_verified':False,'causal_effect_verified':False,
                     'note':'Synthetic descriptive before/after; not evidence of profit or incremental lift'}
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS outcomes(run TEXT PRIMARY KEY,payload TEXT)')
            db.execute('INSERT OR IGNORE INTO outcomes VALUES(?,?)',(identity,canonical(observation)))
            existing=json.loads(db.execute('SELECT payload FROM outcomes WHERE run=?',(identity,)).fetchone()[0])
            if existing!=observation:raise Denied('outcome_revision_conflict')
        return observation
