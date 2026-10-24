"""Fixed ROSERY Meta campaign transport. No activation, credentials, or approvals created.

Wire fields follow Meta's official campaign SDK api_update (status,daily_budget):
https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/campaign.py
The local SQLite ledger is trusted host storage: its SHA detects accidental
payload corruption, NOT malicious edits to outcome/success columns. It is not
a cryptographically authenticated receipt or tamperproof audit log.
A GET immediately before POST detects drift but is NOT atomic provider CAS; an
external actor can change state between those requests. Approval digest binds
content, while approval_instance is a distinct single-use host approval nonce.
No server route enables this module. Only a trusted host may inject verifier and
credential loader. Execute accepts an opaque operation ID, never browser flags.
"""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import urllib.request
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
from connections import NoRedirect
from business import ACTIVE as BUSINESS

ACCOUNT=BUSINESS.meta_account
CAMPAIGN=BUSINESS.meta_launch_campaign
ENDPOINT='https://graph.facebook.com/v26.0/'+CAMPAIGN


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def body_valid(body):
    return (body=={'status':'PAUSED'} or (isinstance(body,dict) and set(body)=={'daily_budget'} and isinstance(body['daily_budget'],str) and re.fullmatch(r'[1-9][0-9]{0,11}',body['daily_budget']) is not None))


class FixedMetaTransport:
    def _request(self,token,method,body=None):
        if not isinstance(token,str) or not token:raise ValueError('Credential unavailable')
        if method=='GET':
            url=ENDPOINT+'?'+urlencode({'fields':'id,account_id,status,effective_status,daily_budget,lifetime_budget'})
            encoded=None
        elif method=='POST' and body_valid(body):url=ENDPOINT;encoded=urlencode(body).encode()
        else:raise ValueError('Mutation not allowed')
        headers={'Authorization':'Bearer '+token,'Accept':'application/json'}
        if encoded is not None:headers['Content-Type']='application/x-www-form-urlencoded'
        request=urllib.request.Request(url,data=encoded,headers=headers,method=method)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:raw=response.read(1048577)
            if len(raw)>1048576:raise ValueError()
            result=json.loads(raw)
            if not isinstance(result,dict):raise ValueError()
            return result
        except Exception:raise ValueError('Meta transport result unavailable') from None
    def read(self,token):return self._request(token,'GET')
    def write(self,token,body):return self._request(token,'POST',body)


def state_matches(raw,expected):
    if not isinstance(raw,dict) or raw.get('error') or raw.get('id')!=CAMPAIGN or raw.get('account_id')!=ACCOUNT:return False
    if raw.get('status')!=expected.get('status'):return False
    if expected.get('budget_type')!='daily' or type(expected.get('budget_minor')) is not int:return False
    if str(raw.get('daily_budget'))!=str(expected['budget_minor']):return False
    return str(raw.get('lifetime_budget','0')) in ('0','None','')


class MetaExecutor:
    def __init__(self,directory,credential_loader=None,host_verifier=None,killed=None,transport=None):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'meta-execution.sqlite3';self.credentials=credential_loader;self.verifier=host_verifier;self.killed=killed
        self.transport=transport or FixedMetaTransport()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,digest TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,success INTEGER NOT NULL,reason TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS approval_consumption(instance TEXT PRIMARY KEY,operation TEXT NOT NULL UNIQUE)')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback();raise
        finally:db.close()
    def _get(self,identity):
        with self.db() as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM operations WHERE id=?',(identity,)).fetchone()
        if row is None:return None
        value=dict(row)
        if hashlib.sha256(value['payload'].encode()).hexdigest()!=value['digest']:raise ValueError('Operation ledger integrity mismatch')
        return value
    def _view(self,row):
        return {'operation_id':row['id'],'state':'unknown' if row['state']=='dispatching' else row['state'],
                'payload_digest':row['digest'],'provider_success_received':bool(row['success']),
                'reason':row['reason'],'automatic_retry_allowed':False,'business_success_verified':False}
    def _finish(self,identity,state,success,reason):
        with self.db() as db:
            db.execute("UPDATE operations SET state=?,success=?,reason=? WHERE id=? AND (state!='verified' OR ?='verified')",(state,int(success),reason,identity,state))
        return self._view(self._get(identity))
    def execute(self,operation_id):
        if not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',operation_id):raise ValueError('Invalid operation ID')
        old=self._get(operation_id)
        if old:return self._view(old)
        blocked={'operation_id':operation_id,'state':'blocked','automatic_retry_allowed':False,'business_success_verified':False}
        if not callable(self.credentials) or not callable(self.verifier) or not callable(self.killed):return {**blocked,'reason':'Trusted write authority and credential host are not configured'}
        try:
            if self.killed() is not False:return {**blocked,'reason':'Kill switch is active or unknown'}
            token=self.credentials();current=self.transport.read(token)
            checked=json.loads(canonical(self.verifier(operation_id,current)))
            preflight=checked['preflight'];before=checked['before'];after=checked['after'];preview=preflight.get('request_preview') or {}
            instance=checked.get('approval_instance')
            if not isinstance(instance,str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,128}',instance) or checked.get('approved_operation_id')!=operation_id:raise ValueError()
            if preflight.get('execution_ready') is not True or preflight.get('state')!='ready_for_executor' or not re.fullmatch('[0-9a-f]{64}',preflight.get('approval_digest','')):raise ValueError()
            if any(preview.get(k)!=v for k,v in {'method':'POST','origin':'https://graph.facebook.com','version':'v26.0','entity_id':CAMPAIGN}.items()):raise ValueError()
            body=preview.get('body')
            if not body_valid(body) or not state_matches(current,before):raise ValueError()
            if before.get('currency')!=after.get('currency') or before.get('budget_type')!=after.get('budget_type'):raise ValueError()
            diff={k:v for k,v in after.items() if before.get(k)!=v}
            if body=={'status':'PAUSED'}:
                if diff!={'status':'PAUSED'} or before.get('status')!='ACTIVE':raise ValueError()
            elif diff!={'budget_minor':int(body['daily_budget'])} or before.get('status')!='ACTIVE' or after.get('status')!='ACTIVE':raise ValueError()
            payload=canonical({'preview':preview,'approval_digest':preflight['approval_digest'],'approval_instance':instance,'approved_operation_id':operation_id,'before':before,'after':after})
        except Exception:return {**blocked,'reason':'Fresh state or trusted exact approval was not verified'}
        digest=hashlib.sha256(payload.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            active=db.execute("SELECT id FROM operations WHERE state IN ('dispatching','unknown') AND id!=? LIMIT 1",(operation_id,)).fetchone()
            if active:return {**blocked,'reason':'Prior campaign operation is dispatching or unresolved; no second mutation'}
            existing=db.execute('SELECT id FROM operations WHERE id=?',(operation_id,)).fetchone()
            if existing:return self._view(self._get(operation_id))
            consumed=db.execute('SELECT operation FROM approval_consumption WHERE instance=?',(instance,)).fetchone()
            if consumed:return {**blocked,'reason':'Single-use approval instance already consumed; a new operation requires a new exact approval'}
            inserted=db.execute('INSERT OR IGNORE INTO operations VALUES(?,?,?,\'dispatching\',0,\'Reserved before mutation; outcome unknown until readback\')',(operation_id,digest,payload)).rowcount
            if inserted:db.execute('INSERT INTO approval_consumption VALUES(?,?)',(instance,operation_id))
        if not inserted:return self._view(self._get(operation_id))
        try:
            # Check again AFTER durable reservation, immediately before the only POST.
            if self.killed() is not False:return self._finish(operation_id,'not_applied',False,'Kill switch prevented dispatch')
            # Approval expiry and fresh-state drift must be checked again by host.
            fresh_current=self.transport.read(token)
            if not state_matches(fresh_current,before):return self._finish(operation_id,'not_applied',False,'Fresh provider state changed before dispatch')
            last=self.verifier(operation_id,fresh_current)
            if canonical(last)!=canonical(checked):return self._finish(operation_id,'not_applied',False,'Exact host authorization changed before dispatch')
            if self.killed() is not False:return self._finish(operation_id,'not_applied',False,'Kill switch prevented final dispatch')
            answer=self.transport.write(token,body)
            success=answer.get('success') is True and not answer.get('error')
            if not success:return self._finish(operation_id,'unknown',False,'No affirmative provider success; do not retry')
            # Persist affirmative response before independent readback so recovery
            # can reconcile after a crash/timeout without resubmitting the write.
            self._finish(operation_id,'unknown',True,'Provider acknowledged; independent readback pending')
            readback=self.transport.read(token)
            if state_matches(readback,after):return self._finish(operation_id,'verified',True,'Provider success and independent target readback verified')
            return self._finish(operation_id,'unknown',True,'Provider success but target state not verified')
        except Exception:return self._finish(operation_id,'unknown',bool(self._get(operation_id)['success']),'Ambiguous transport or readback; do not retry')
    def reconcile(self,operation_id):
        row=self._get(operation_id)
        if row is None:raise ValueError('Unknown operation')
        if row['state']=='verified':return self._view(row)
        if not callable(self.credentials):return self._view(row)
        try:
            after=json.loads(row['payload'])['after'];observed=self.transport.read(self.credentials())
            if row['success'] and state_matches(observed,after):return self._finish(operation_id,'verified',True,'Stored provider success and later independent readback verified')
        except Exception:pass
        return self._view(row)
