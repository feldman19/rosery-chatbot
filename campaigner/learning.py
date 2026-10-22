"""Append-only local decision/outcome register. A record is not causal proof."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import uuid


class LearningDenied(ValueError):
    pass


class LearningLedger:
    def __init__(self, path):
        self.path = str(path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS learning(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,kind TEXT NOT NULL,parent TEXT,payload TEXT NOT NULL,hash TEXT NOT NULL,at TEXT NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            with db: yield db
        finally: db.close()

    def _append(self, tenant, kind, payload, parent=None):
        if not isinstance(tenant, str) or not tenant:
            raise LearningDenied('tenant_required')
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        if len(raw) > 10000: raise LearningDenied('payload_too_large')
        record = {'id':str(uuid.uuid4()),'tenant':tenant,'kind':kind,'parent':parent,'payload':payload,
                  'hash':hashlib.sha256(raw.encode()).hexdigest(),'at':datetime.now(timezone.utc).isoformat()}
        with self._db() as db:
            if parent and not db.execute('SELECT 1 FROM learning WHERE tenant=? AND id=? AND kind=?',(tenant,parent,'decision')).fetchone():
                raise LearningDenied('decision_not_found')
            db.execute('INSERT INTO learning VALUES(?,?,?,?,?,?,?)',
                       (record['id'],tenant,kind,parent,raw,record['hash'],record['at']))
        return record

    def decide(self, tenant, hypothesis, evidence_refs, action, evaluation_plan):
        if not all(isinstance(s,str) and 1<=len(s)<=2000 for s in (hypothesis,action,evaluation_plan)):
            raise LearningDenied('explicit_plan_required')
        if not isinstance(evidence_refs,list) or not evidence_refs or any(not isinstance(s,str) or not s for s in evidence_refs):
            raise LearningDenied('evidence_required')
        return self._append(tenant,'decision',{'hypothesis':hypothesis,'evidence_refs':evidence_refs,
                             'action':action,'evaluation_plan':evaluation_plan,'live_permission':False})

    def observe(self, tenant, decision_id, evidence_refs, design, note):
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise LearningDenied('decision_required')
        if design not in ('before_after','randomized','geo_experiment','unknown'):
            raise LearningDenied('invalid_design')
        if not isinstance(evidence_refs,list) or not evidence_refs or any(not isinstance(s,str) or not s for s in evidence_refs):
            raise LearningDenied('evidence_required')
        if not isinstance(note,str) or not 1<=len(note)<=2000:
            raise LearningDenied('note_required')
        return self._append(tenant,'observation',{'evidence_refs':evidence_refs,'declared_design':design,
                             'note':note,'causal_effect_verified':False,
                             'reason':'Study design and data need independent verification; observational improvement is not proof'}, decision_id)

    def list(self, tenant):
        with self._db() as db:
            rows = db.execute('SELECT id,kind,parent,payload,hash,at FROM learning WHERE tenant=? ORDER BY rowid',(tenant,)).fetchall()
        result=[]
        for rid,kind,parent,raw,digest,at in rows:
            if hashlib.sha256(raw.encode()).hexdigest()!=digest: raise LearningDenied('record_hash_mismatch')
            result.append({'id':rid,'kind':kind,'parent':parent,'payload':json.loads(raw),'hash':digest,'at':at})
        return result
