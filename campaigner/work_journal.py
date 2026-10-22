"""Local, persisted observations of workflow transitions; never execution receipts."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


class WorkJournal:
    def __init__(self,directory):
        self.path=Path(directory)/'work-journal.sqlite3'
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, fingerprint TEXT, at TEXT, state TEXT, text TEXT)')

    def observe(self,status):
        # Do not log a new event merely because a poll timestamp changed.
        stable={key:status.get(key) for key in ('goal','goal_binding','state','current_state','completed','blocked','owner_action')}
        fingerprint=hashlib.sha256(json.dumps(stable,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            last=db.execute('SELECT fingerprint FROM observations ORDER BY id DESC LIMIT 1').fetchone()
            if not last or last[0]!=fingerprint:
                db.execute('INSERT INTO observations(fingerprint,at,state,text) VALUES(?,?,?,?)',(fingerprint,datetime.now(timezone.utc).isoformat(),status.get('state','unknown'),status.get('current_state','מצב לא זמין')))
            rows=db.execute('SELECT id,at,state,text FROM observations ORDER BY id DESC LIMIT 30').fetchall()
        return [dict(zip(('id','at','state','text'),row)) for row in reversed(rows)]
