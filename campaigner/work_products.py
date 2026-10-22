"""Durable, signed preparation artifacts. Saving is not approval or execution."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib,hmac,json,re,sqlite3
from pathlib import Path

class WorkProducts:
    def __init__(self,directory,key):
        if not isinstance(key,bytes) or len(key)<32:raise ValueError('Signing key required')
        self.path=Path(directory)/'prepared-work.sqlite3';self.key=key
        with self.db() as db:db.execute('CREATE TABLE IF NOT EXISTS products(kind TEXT,version TEXT,payload TEXT,signature TEXT,first_seen TEXT,last_seen TEXT,PRIMARY KEY(kind,version))')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def sign(self,kind,version,payload,first,last):
        return hmac.new(self.key,json.dumps([kind,version,payload,first,last],ensure_ascii=False,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
    def _verify(self,row):
        if not hmac.compare_digest(row['signature'],self.sign(row['kind'],row['version'],row['payload'],row['first_seen'],row['last_seen'])):raise ValueError('Prepared work integrity failed')
    def save(self,kind,version,payload):
        if kind not in ('experiment','tracking') or not isinstance(version,str) or not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('Invalid prepared artifact')
        raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False)
        if len(raw)>1000000:raise ValueError('Prepared artifact too large')
        now=datetime.now(timezone.utc).isoformat()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM products WHERE kind=? AND version=?',(kind,version)).fetchone()
            if old:
                self._verify(old)
                # Version retains the first exact artifact. Refresh is an observation, not a new approval.
                raw=old['payload'];first=old['first_seen']
            else:first=now
            sig=self.sign(kind,version,raw,first,now)
            db.execute('INSERT OR REPLACE INTO products VALUES(?,?,?,?,?,?)',(kind,version,raw,sig,first,now))
        return {'saved':True,'version':version,'new':old is None,'live_action_allowed':False}
    def latest(self,kind):
        if kind not in ('experiment','tracking'):raise ValueError('Invalid artifact kind')
        # save() serializes INSERT OR REPLACE under BEGIN IMMEDIATE. The new
        # rowid therefore records committed observation order even when wall
        # clocks tie or move backwards; signed timestamps remain untouched.
        with self.db() as db:row=db.execute('SELECT * FROM products WHERE kind=? ORDER BY rowid DESC LIMIT 1',(kind,)).fetchone()
        if not row:return None
        self._verify(row)
        return {'kind':kind,'version':row['version'],'first_seen':row['first_seen'],'last_seen':row['last_seen'],
                'payload':json.loads(row['payload']),'integrity_verified':True,'live_action_allowed':False,
                'note':'תוצר הכנה שמור; יש לבדוק את תוקף המקור מחדש לפני ביצוע.'}
