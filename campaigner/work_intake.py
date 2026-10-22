"""Single atomic business-request record, independent of downstream projections."""
from batch_limits import MAX_ADSETS, MAX_ADS, MAX_CREATIVE_IMAGES, MAX_DRIVE_SOURCES

from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import json,re,secrets,sqlite3,unicodedata
from launch_workflow import drive_url
from strategy_desk import category


class WorkIntake:
    def __init__(self,directory):
        self.path=Path(directory)/'work-intake.sqlite3'
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS requests(revision INTEGER PRIMARY KEY,payload TEXT NOT NULL)')

    def latest(self):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT payload FROM requests ORDER BY revision DESC LIMIT 1').fetchone()
            return json.loads(row[0]) if row else None

    def create(self,body,*,expected=None,force_new=False):
        # Host-only fresh-start intent; never accepted as browser authority.
        # The preparation route supplies a CAS binding and durable receipt.
        if type(force_new) is not bool or (force_new and expected is None):raise ValueError('Fresh request requires exact previous binding')
        if not isinstance(body,dict) or set(body) not in ({'goal','drive_urls'},{'goal','drive_urls','local_sources','local_image_review_consent'}) or not isinstance(body['goal'],str):raise ValueError('Only goal and Drive links accepted')
        goal=''.join(c for c in body['goal'] if c in '\n\t' or not unicodedata.category(c).startswith('C')).strip()
        if not 3<=len(goal)<=6000 or re.search(r'(?:access_token|api[_ -]?key|client_secret|password)\s*[:=]',goal,re.I):raise ValueError('Invalid business goal')
        local=body.get('local_sources',[])
        if local and (body['drive_urls'] or type(body.get('local_image_review_consent')) is not bool):raise ValueError('Separate source mode and explicit image review consent required')
        if 'local_sources' in body:
            if not isinstance(local,list) or not 1<=len(local)<=30 or any(not isinstance(x,dict) or set(x)!={'id','sha256'} or not re.fullmatch('local_[a-f0-9]{32}',str(x['id'])) or not re.fullmatch('[a-f0-9]{64}',str(x['sha256'])) for x in local):raise ValueError('Exact local sources required')
        local_fields={'local_sources':local,'local_image_review_consent':body['local_image_review_consent']} if local else {}
        urls=body['drive_urls']
        if not isinstance(urls,list) or len(urls)>MAX_DRIVE_SOURCES:raise ValueError('At most 30 Drive links')
        urls=list(dict.fromkeys(drive_url(url) for url in urls));now=datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT payload FROM requests ORDER BY revision DESC LIMIT 1').fetchone()
            prior=json.loads(previous[0]) if previous else None
            actual=(prior.get('id'),prior.get('revision')) if prior else (None,None)
            if expected is not None and expected != actual:
                raise ValueError('Current preparation request changed; reload before saving')
            if not force_new and prior and prior['goal']==goal and prior['drive_urls']==urls and prior.get('local_sources',[])==local and prior.get('local_image_review_consent')==body.get('local_image_review_consent'):return prior
            request={'id':prior['id'] if prior else secrets.token_hex(16),'revision':prior['revision']+1 if prior else 1,'goal':goal,'drive_urls':urls,'category':category(goal)[0],'saved_at':now,'execution_approval':False,**local_fields}
            db.execute('INSERT INTO requests VALUES(?,?)',(request['revision'],json.dumps(request,ensure_ascii=False)))
        return request

    def projection(self,request,succeeded):
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            row=db.execute('SELECT payload FROM requests WHERE revision=?',(request['revision'],)).fetchone()
            current=json.loads(row[0]) if row else None
            if not current or current['id']!=request['id']:raise ValueError('Request binding changed')
            current['preparation_state']='projected' if succeeded else 'failed'
            db.execute('UPDATE requests SET payload=? WHERE revision=?',(json.dumps(current,ensure_ascii=False),request['revision']))
        return current
