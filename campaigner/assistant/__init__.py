"""Curated FAQ lookup and local manager queue; no model, messaging or live chat.
Trusted host owns authentication. Queue is a single-workspace demo, NOT tenant RBAC.
Knowledge is manually curated from handoff; no user claim can modify it via API.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
import uuid

class AssistantDenied(ValueError):
    pass

def _clean(text,limit=1000):
    if not isinstance(text,str) or not text.strip() or len(text)>limit:
        raise AssistantDenied('invalid_text')
    text=unicodedata.normalize('NFKC',text).strip()
    if any(unicodedata.category(c) in ('Cc','Cf') for c in text):
        raise AssistantDenied('unsupported_control_characters')
    if re.search(r'(?i)(bearer\s|access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|password\s*[:=]|secret\s*[:=]|sk-[a-z0-9]{12,}|סיסמה\s*[:=])',text):
        raise AssistantDenied('sensitive_text_not_stored')
    if re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:\+?\d[\d ()-]{7,}\d)',text):
        raise AssistantDenied('personal_contact_not_stored')
    return text

def _norm(text):
    return ' '.join(re.sub(r'[^\w\s]',' ',text.casefold()).split())

UNKNOWN='אין לי תשובה מבוססת במדריך לשאלה הזאת. אפשר לשמור אותה בתור המקומי למנהל. לא נשלחה הודעה ולא בוצעה פעולה.'

class KnowledgeAssistant:
    def __init__(self):
        data=json.loads(Path(__file__).with_name('knowledge.json').read_text(encoding='utf-8-sig'))
        self.sources={s['id']:s for s in data['sources']}
        self.entries={}
        for entry in data['entries']:
            if not entry['sources'] or any(s not in self.sources for s in entry['sources']):
                raise ValueError('knowledge_source_missing')
            for phrase in entry['questions']:
                key=_norm(phrase)
                if key in self.entries: raise ValueError('ambiguous_knowledge_question')
                self.entries[key]=entry

    def answer(self,question):
        # Exact normalized FAQ matching is intentionally conservative. No execution,
        # retrieval of raw chat, or freeform generation takes place here.
        question=_clean(question)
        entry=self.entries.get(_norm(question))
        if entry is None:
            return {'answer':UNKNOWN,'sources':[],'mode':'knowledge_search','ai_connected':False,'needs_manager':True}
        return {'answer':entry['answer'],'sources':[dict(self.sources[s]) for s in entry['sources']],
                'mode':'knowledge_search','ai_connected':False,'needs_manager':entry['needs_manager']}

class HandoffQueue:
    """Local-only queue. issue creates a record, not a delivered notification.
    list/resolve must be manager-only at the HOST boundary. Labels are not auth.
    """
    def __init__(self,db_path):
        self.db_path=str(db_path)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS assistant_issues(
                id TEXT PRIMARY KEY, question TEXT NOT NULL, user TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, resolution TEXT,
                manager TEXT, resolved_at TEXT)''')

    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.db_path,timeout=10,isolation_level=None)
        db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            if db.in_transaction: db.rollback()
            raise
        finally: db.close()

    def issue(self,question,user='local-demo',request_id=None):
        question=_clean(question)
        user=_clean(user,80)
        try:
            issue_id=str(uuid.UUID(request_id)) if request_id is not None else str(uuid.uuid4())
        except (ValueError,TypeError,AttributeError) as exc:
            raise AssistantDenied('invalid_request_id') from exc
        row={'id':issue_id,'question':question,'user':user,'status':'pending','created_at':datetime.now(timezone.utc).isoformat(),'resolution':None,'manager':None,'resolved_at':None}
        with self._db() as db:
            prior=db.execute('SELECT * FROM assistant_issues WHERE id=?',(issue_id,)).fetchone()
            if prior is not None:
                if prior['question']!=question or prior['user']!=user:
                    raise AssistantDenied('request_id_conflict')
                return {**dict(prior),'notification_sent':False,'replayed':True}
            db.execute('INSERT INTO assistant_issues VALUES(:id,:question,:user,:status,:created_at,:resolution,:manager,:resolved_at)',row)
        return {**row,'notification_sent':False}

    def list(self,status=None):
        if status not in (None,'pending','resolved'): raise AssistantDenied('invalid_status')
        with self._db() as db:
            sql='SELECT * FROM assistant_issues'
            args=()
            if status is not None: sql+=' WHERE status=?'; args=(status,)
            return [dict(row) for row in db.execute(sql+' ORDER BY created_at,id',args)]

    def resolve(self,issue_id,resolution,manager='local-manager'):
        try:
            issue_id=str(uuid.UUID(issue_id))
        except (ValueError,TypeError,AttributeError) as exc:
            raise AssistantDenied('invalid_issue_id') from exc
        resolution=_clean(resolution)
        manager=_clean(manager,80)
        with self._db() as db:
            row=db.execute('SELECT * FROM assistant_issues WHERE id=?',(issue_id,)).fetchone()
            if row is None: raise AssistantDenied('unknown_issue')
            if row['status']!='pending': raise AssistantDenied('already_resolved')
            db.execute("UPDATE assistant_issues SET status='resolved',resolution=?,manager=?,resolved_at=? WHERE id=? AND status='pending'",(resolution,manager,datetime.now(timezone.utc).isoformat(),issue_id))
            return dict(db.execute('SELECT * FROM assistant_issues WHERE id=?',(issue_id,)).fetchone())

