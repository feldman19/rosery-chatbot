"""Local imported evidence; provenance is user-declared, never platform verified."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

class EvidenceDenied(ValueError):
    pass

def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False)

def _date(value):
    if isinstance(value,datetime):
        date=value
    elif isinstance(value,str):
        try: date=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError as exc: raise EvidenceDenied('invalid_timestamp') from exc
    else: raise EvidenceDenied('invalid_timestamp')
    if date.tzinfo is None or date.utcoffset() is None:
        raise EvidenceDenied('timezone_required')
    return date.astimezone(timezone.utc)

def _text(value,limit=200):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):
        raise EvidenceDenied('invalid_text')
    # Defense in depth; arbitrary strings cannot be guaranteed secret-free.
    if re.search(r'(?i)(bearer\s|access_token|api[_-]?key|password|secret\s*=)',value):
        raise EvidenceDenied('credential_like_text')
    return value

def _normalize(payload):
    required={'platform','account_id','captured_at','currency','account_timezone','window_start','window_end','completeness','synthetic','records'}
    if not isinstance(payload,dict) or not required<=set(payload) or set(payload)-required-{'attribution','provenance'}:
        raise EvidenceDenied('invalid_snapshot_fields')
    p=json.loads(_canonical(payload))
    if p['platform'] not in ('meta','shopify'): raise EvidenceDenied('unsupported_platform')
    _text(p['account_id'])
    if not isinstance(p['currency'],str) or not re.fullmatch('[A-Z]{3}',p['currency']): raise EvidenceDenied('invalid_currency')
    tz=_text(p['account_timezone'],100)
    if tz!='UTC' and not re.fullmatch(r'[A-Za-z_+-]+(?:/[A-Za-z0-9_+-]+)+',tz): raise EvidenceDenied('invalid_timezone_label')
    if tz not in ('UTC','Asia/Jerusalem'):
        try: ZoneInfo(tz)
        except ZoneInfoNotFoundError as exc: raise EvidenceDenied('timezone_database_unavailable_or_unknown_zone') from exc
    for field in ('captured_at','window_start','window_end'): p[field]=_date(p[field]).isoformat()
    if _date(p['window_start'])>=_date(p['window_end']): raise EvidenceDenied('invalid_window')
    if _date(p['window_end'])>_date(p['captured_at']): raise EvidenceDenied('window_after_capture')
    if p['completeness'] not in ('complete','partial','unknown'): raise EvidenceDenied('invalid_completeness')
    if type(p['synthetic']) is not bool: raise EvidenceDenied('invalid_synthetic_flag')
    for field in ('attribution','provenance'):
        if field in p: _text(p[field],500)
    if not isinstance(p['records'],list) or len(p['records'])>10000: raise EvidenceDenied('invalid_records')
    ids=set()
    allowed_metrics={'spend_minor','impressions','clicks','purchases','revenue_minor','orders','refunds_minor'}
    for row in p['records']:
        if not isinstance(row,dict) or not {'entity_id','name','metrics'}<=set(row) or set(row)-{'entity_id','name','metrics','status'}: raise EvidenceDenied('invalid_record_fields')
        _text(row['entity_id']); _text(row['name'])
        if row['entity_id'] in ids: raise EvidenceDenied('duplicate_entity')
        ids.add(row['entity_id'])
        if 'status' in row: _text(row['status'],50)
        metrics=row['metrics']
        if not isinstance(metrics,dict) or set(metrics)-allowed_metrics or any(type(v) is not int or v<0 for v in metrics.values()): raise EvidenceDenied('invalid_metrics')
    return p

class SnapshotStore:
    def __init__(self,db_path):
        self.db_path=str(db_path)
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS evidence_accounts(platform TEXT,account TEXT,tenant TEXT NOT NULL,PRIMARY KEY(platform,account));
            CREATE TABLE IF NOT EXISTS evidence_snapshots(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,platform TEXT NOT NULL,account TEXT NOT NULL,imported_at TEXT NOT NULL,hash TEXT NOT NULL,payload TEXT NOT NULL);
            ''')

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.db_path,timeout=10,isolation_level=None)
        db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            if db.in_transaction: db.commit()
        except BaseException:
            if db.in_transaction: db.rollback()
            raise
        finally: db.close()

    def bind_account(self,tenant_id,platform,account_id):
        _text(tenant_id); _text(account_id)
        if platform not in ('meta','shopify'): raise EvidenceDenied('unsupported_platform')
        with self._db() as db:
            old=db.execute('SELECT tenant FROM evidence_accounts WHERE platform=? AND account=?',(platform,account_id)).fetchone()
            if old and old['tenant']!=tenant_id: raise EvidenceDenied('account_already_bound')
            db.execute('INSERT OR IGNORE INTO evidence_accounts VALUES(?,?,?)',(platform,account_id,tenant_id))

    def ingest(self,tenant_id,payload):
        p=_normalize(payload)
        now=datetime.now(timezone.utc)
        if _date(p['captured_at'])>now: raise EvidenceDenied('future_capture')
        raw=_canonical(p)
        if len(raw.encode())>5_000_000: raise EvidenceDenied('snapshot_too_large')
        sid=str(uuid.uuid4())
        with self._db() as db:
            bound=db.execute('SELECT tenant FROM evidence_accounts WHERE platform=? AND account=?',(p['platform'],p['account_id'])).fetchone()
            if not bound or bound['tenant']!=tenant_id: raise EvidenceDenied('account_not_bound')
            db.execute('INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?)',(sid,tenant_id,p['platform'],p['account_id'],now.isoformat(),hashlib.sha256(raw.encode()).hexdigest(),raw))
        return self.get(tenant_id,sid,now=now)

    def _view(self,row,now,max_age_seconds):
        if type(max_age_seconds) is not int or max_age_seconds<0: raise EvidenceDenied('invalid_freshness_limit')
        now=_date(now or datetime.now(timezone.utc))
        p=json.loads(row['payload'])
        digest=hashlib.sha256(_canonical(p).encode()).hexdigest()
        if digest!=row['hash']: raise EvidenceDenied('snapshot_hash_mismatch')
        age=(now-_date(p['captured_at'])).total_seconds()
        window_age=(now-_date(p['window_end'])).total_seconds()
        if age<0 or window_age<0: freshness='future_relative_to_evaluation'
        elif max(age,window_age)>max_age_seconds: freshness='stale'
        else: freshness='recent_import_not_live'
        tz_verified=p['account_timezone']=='UTC'
        if not tz_verified:
            try: ZoneInfo(p['account_timezone']); tz_verified=True
            except ZoneInfoNotFoundError: pass
        missing=['provenance'] if 'provenance' not in p else []
        if p['platform']=='meta' and 'attribution' not in p: missing.append('attribution')
        return {'id':row['id'],'hash':row['hash'],'payload':p,'imported_at':row['imported_at'],
                'assessment':{'freshness':freshness,'capture_age_seconds':age,'window_age_seconds':window_age,
                              'historical':True,'current':False,'live':False,'live_action_allowed':False,
                              'source_verified':False,'provenance_kind':'user_declared_import',
                              'timezone_verified':tz_verified,'missing':missing,'completeness':p['completeness'],
                              'synthetic':p['synthetic']}}

    def get(self,tenant_id,snapshot_id,now=None,max_age_seconds=86400):
        with self._db() as db:
            row=db.execute('SELECT * FROM evidence_snapshots WHERE id=? AND tenant=?',(snapshot_id,tenant_id)).fetchone()
            if not row: raise EvidenceDenied('unknown_snapshot')
            return self._view(row,now,max_age_seconds)

    def list(self,tenant_id,now=None,max_age_seconds=86400):
        with self._db() as db:
            return [self._view(row,now,max_age_seconds) for row in db.execute('SELECT * FROM evidence_snapshots WHERE tenant=? ORDER BY rowid DESC',(tenant_id,))]

class ImportedSource:
    def __init__(self,store,tenant_id,snapshot_id):
        self.store,self.tenant_id,self.snapshot_id=store,tenant_id,snapshot_id

    def capabilities(self):
        return {'mode':'import_only','live':False,'get_entities':True,'get_insights':True,'apply_change':False,'live_action_allowed':False}

    def get_entities(self):
        snapshot=self.store.get(self.tenant_id,self.snapshot_id)
        return {'snapshot_id':snapshot['id'],'hash':snapshot['hash'],'assessment':snapshot['assessment'],'entities':[{k:v for k,v in row.items() if k!='metrics'} for row in snapshot['payload']['records']]}

    def get_insights(self):
        return self.store.get(self.tenant_id,self.snapshot_id)

