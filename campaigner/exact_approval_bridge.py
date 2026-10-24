"""Local-host exact approval bridge; no recommendation engine or browser trust flags.

Local manager presence is NOT multi-user authentication. Route handlers must
verify local session/origin and expose only opaque ID+digest approval calls.
HMAC protects proposal/approval contents, not the separate executor outcome
ledger. No operation is approved by preparing a preview or loading a token.
"""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
from execution_preflight import prepare as preflight
from meta_execution import MetaExecutor,FixedMetaTransport,ACCOUNT,CAMPAIGN
from business import ACTIVE as BUSINESS


def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)


class ExactApprovalBridge:
    def __init__(self,directory,signing_key,credential_loader=None,transport=None,clock=None,ttl_seconds=600):
        if not isinstance(signing_key,bytes) or len(signing_key)<32:raise ValueError('Host signing key of at least 32 bytes required')
        if type(ttl_seconds) is not int or not 60<=ttl_seconds<=1800:raise ValueError('Invalid proposal expiry')
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);self.path=self.directory/'exact-meta-approvals.sqlite3'
        self.credential_verified=False;self.last_verified_at=None
        self.key=signing_key;self.transport=transport or FixedMetaTransport();self.clock=clock or (lambda:datetime.now(timezone.utc));self.ttl=ttl_seconds
        if credential_loader is None:
            from meta_write_connection import load
            self.loader=lambda:load(self.directory)
            self.injected_loader=False
        else:self.loader=credential_loader;self.injected_loader=True
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS envelopes(id TEXT PRIMARY KEY,payload TEXT NOT NULL,signature TEXT NOT NULL)')
            if db.execute("SELECT id FROM envelopes WHERE id='control'").fetchone() is None:self._save(db,'control',{'generation':0})
            else:
                control=self._load(db,'control');control.update(generation=control['generation']+1);self._save(db,'control',control)
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()
    def _signature(self,identity,raw):return hmac.new(self.key,(identity+'\n'+raw).encode(),hashlib.sha256).hexdigest()
    def _save(self,db,identity,value):
        raw=canonical(value);db.execute('INSERT OR REPLACE INTO envelopes VALUES(?,?,?)',(identity,raw,self._signature(identity,raw)))
    def _load(self,db,identity):
        row=db.execute('SELECT * FROM envelopes WHERE id=?',(identity,)).fetchone()
        if row is None:raise ValueError('Proposal not found')
        if not hmac.compare_digest(row['signature'],self._signature(identity,row['payload'])):raise ValueError('Signed proposal integrity failed')
        return json.loads(row['payload'])
    def _now(self):
        now=self.clock()
        if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('Host clock requires timezone')
        return now
    def _id(self,identity):
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('Invalid operation ID')
    def _valid(self,p):
        now=self._now()
        if datetime.fromisoformat(p['created_at'])>now or now>=datetime.fromisoformat(p['expires_at']):raise ValueError('Proposal expired; prepare a new preview')
    def _current(self,raw,now):
        if raw.get('id')!=CAMPAIGN or raw.get('account_id')!=ACCOUNT or raw.get('error'):raise ValueError('Fixed campaign identity not verified')
        value=raw.get('daily_budget')
        if not isinstance(value,(str,int)) or isinstance(value,bool) or not str(value).isdigit() or int(value)<=0 or str(raw.get('lifetime_budget','0')) not in ('0','None',''):raise ValueError('Daily campaign budget not verified')
        return {'tenant_id':BUSINESS.approval_tenant,'account_id':ACCOUNT,'entity_id':CAMPAIGN,
                'status':raw.get('status'),'budget_minor':int(value),'currency':BUSINESS.currency,'budget_type':'daily',
                'parent_active':raw.get('effective_status')=='ACTIVE','verified':True,
                'checked_at':now.isoformat(),'valid_until':(now+timedelta(seconds=60)).isoformat()}
    def _binding(self):return {'tenant_id':BUSINESS.approval_tenant,'account_id':ACCOUNT,'entity_id':CAMPAIGN,'platform':'meta','level':'campaign','currency':BUSINESS.currency,'budget_type':'daily'}
    def _public(self,p,control):
        generation=control['generation']
        valid=self._now()<datetime.fromisoformat(p['expires_at'])
        approved=p.get('approval') is not None and p['approval']['generation']==generation and valid
        operation=MetaExecutor(self.directory)._get(p['operation_id'])
        operation_state=('unknown' if operation['state']=='dispatching' else operation['state']) if operation else None
        approval_status='approved' if approved else 'expired' if not valid else 'revoked' if p.get('approval') else 'prepared'
        return {'operation_id':p['operation_id'],'digest':p['digest'],'status':operation_state or approval_status,'approval_status':approval_status,'operation_state':operation_state,'approval_consumed':operation is not None,
                'account_id':ACCOUNT,'campaign_id':CAMPAIGN,'currency':BUSINESS.currency,'before':p['candidate']['before'],'after':p['candidate']['after'],
                'expires_at':p['expires_at'],'manual_intent':True,'is_recommendation':False,'exact_action_approved':approved,'execution_ready':approved and self.credential_verified and operation is None,
                'provider_write_performed':True if operation_state=='verified' else None if operation_state=='unknown' else False,'local_identity_scope':'single local workstation; not multi-user authentication'}
    def _control(self):
        with self.db() as db:return self._load(db,'control')
    def status(self):
        with self.db() as db:
            control=self._load(db,'control');generation=control['generation'];proposals=[]
            for row in db.execute("SELECT id FROM envelopes WHERE id!='control'").fetchall():proposals.append(self._public(self._load(db,row['id']),control))
        return {'account_id':ACCOUNT,'campaign_id':CAMPAIGN,'currency':BUSINESS.currency,'credential_configured':self.injected_loader or (self.directory/'meta-execution.dpapi').exists(),
                'credential_state':{'configured':self.injected_loader or (self.directory/'meta-execution.dpapi').exists(),'verified':self.credential_verified,'last_verified_at':self.last_verified_at,'verification_kind':'last successful host check; not live-poll verification'},'stop_generation':self._control()['generation'],'proposals':proposals,'default_action_authorized':False,
                'live_action_allowed':False,'automatic_execution':False,'identity_scope':'local workstation, not authenticated multi-user roles'}
    def get(self,operation_id):
        self._id(operation_id)
        with self.db() as db:return self._public(self._load(db,operation_id),self._load(db,'control'))
    def preview_action(self,action,budget_minor=None):
        if action not in ('pause','set_budget'):raise ValueError('Only pause or daily budget changes are supported')
        if action=='pause' and budget_minor is not None:raise ValueError('Pause does not accept a budget')
        if action=='set_budget' and (type(budget_minor) is not int or budget_minor<=0):raise ValueError('Positive integer budget required')
        try:raw=self.transport.read(self.loader())
        except Exception:raise ValueError('Write credential or fresh campaign read unavailable') from None
        now=self._now();current=self._current(raw,now);self.credential_verified=True;self.last_verified_at=now.isoformat();before={k:current[k] for k in ('status','budget_minor','currency','budget_type')};after=dict(before)
        if action=='pause':after['status']='PAUSED'
        else:after['budget_minor']=budget_minor
        evidence='sha256:'+hashlib.sha256(canonical(raw).encode()).hexdigest()
        candidate={'status':'propose','action':action,'entity':{k:current[k] for k in ('tenant_id','account_id','entity_id')},'before':before,'after':after,
                   'evidence_refs':[evidence],'risk':'Manual budget or pause request; no business-performance recommendation is implied',
                   'next_review_at':(now+timedelta(days=1)).isoformat(),'rollback_condition':'No automatic reversal; new exact approval and current-state read required'}
        return self._prepare(candidate,self._binding(),current,now)
    def prepare(self,candidate,binding):
        if binding!=self._binding():raise ValueError('Only fixed ROSERY campaign binding accepted')
        try:raw=self.transport.read(self.loader())
        except Exception:raise ValueError('Write credential or fresh campaign read unavailable') from None
        now=self._now();current=self._current(raw,now);self.credential_verified=True;self.last_verified_at=now.isoformat();return self._prepare(candidate,binding,current,now)
    def _prepare(self,candidate,binding,current,now):
        preview=preflight(candidate,binding,current,scopes=['ads_management'],now=now)
        if preview['request_preview'] is None:raise ValueError('Fresh campaign state does not support this exact preview')
        identity=secrets.token_hex(16)
        p={'operation_id':identity,'digest':preview['approval_digest'],'candidate':candidate,'binding':binding,
           'created_at':now.isoformat(),'expires_at':(now+timedelta(seconds=self.ttl)).isoformat(),'approval':None}
        with self.db() as db:self._save(db,identity,p);control=self._load(db,'control')
        return self._public(p,control)
    def approve(self,operation_id,digest):
        self._id(operation_id)
        with self.db() as db:
            p=self._load(db,operation_id);self._valid(p);control=self._load(db,'control');generation=control['generation']
            if not isinstance(digest,str) or not hmac.compare_digest(p['digest'],digest):raise ValueError('Exact preview digest mismatch')
            if p['approval'] is not None:
                if p['approval']['generation']!=generation:raise ValueError('Approval revoked; prepare a new preview')
            else:
                p['approval']={'generation':generation,'instance':secrets.token_hex(24),'issued_at':self._now().isoformat()};self._save(db,operation_id,p)
        return self._public(p,control)
    def stop(self):
        with self.db() as db:
            control=self._load(db,'control');control['generation']+=1;self._save(db,'control',control)
        return {'stopped':True,'existing_approvals_revoked':True,'stop_generation':control['generation'],'provider_write_performed':False}
    def _killed(self,operation_id):
        try:
            with self.db() as db:
                p=self._load(db,operation_id);self._valid(p)
                control=self._load(db,'control')
                return p.get('approval') is None or p['approval']['generation']!=control['generation']
        except Exception:return True
    def _verify(self,operation_id,raw):
        with self.db() as db:
            p=self._load(db,operation_id);self._valid(p);control=self._load(db,'control');generation=control['generation']
        approved=p.get('approval')
        if not approved or approved['generation']!=generation:raise ValueError('Exact local approval required')
        current=self._current(raw,self._now());binding=p['binding']
        auth={'verified':True,'digest':p['digest'],**{k:binding[k] for k in ('tenant_id','account_id','entity_id')},'issued_at':approved['issued_at'],'expires_at':p['expires_at']}
        result=preflight(p['candidate'],binding,current,auth,['ads_management'],self._now())
        if result['approval_digest']!=p['digest'] or not result['execution_ready']:raise ValueError('Exact approved state no longer valid')
        return {'preflight':result,'before':p['candidate']['before'],'after':p['candidate']['after'],'approval_instance':approved['instance'],'approved_operation_id':operation_id}
    def _executor(self,operation_id):return MetaExecutor(self.directory,self.loader,self._verify,lambda:self._killed(operation_id),self.transport)
    def execute(self,operation_id):
        self._id(operation_id)
        return self._executor(operation_id).execute(operation_id)
    def reconcile(self,operation_id):
        self._id(operation_id)
        return self._executor(operation_id).reconcile(operation_id)
