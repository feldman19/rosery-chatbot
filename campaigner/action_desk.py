"""Durable local review queue. Operator review never grants provider permissions."""
import hashlib
import json
import sqlite3
import re
from contextlib import contextmanager
import threading
from datetime import datetime,timezone,timedelta
from pathlib import Path
from decisions import build_report
from meta_direct import ACCOUNT,LAUNCH
from creative_audit import audit as audit_creatives


def stamp():return datetime.now(timezone.utc).isoformat()


def findings(snapshot,report):
    result=[]
    for item in audit_creatives(snapshot)['findings']:
        result.append({'key':item['id'],'title':item['title'],'detail':item['explanation'],
                       'priority':1,'steps':[item['next_step'],item['check_after']],
                       'source':'direct_meta_creative_audit','entity_id':LAUNCH,
                       'condition':item['fingerprint'],'evidence':item['evidence'],'prepared_change':item})
    steps={
        'measurement':['בדוק שנרשם מופיע ברשימת ההשקה לאחר הרשמה מוצלחת','השווה מזהה אירוע אחד לאותה הרשמה ובדוק כפילויות','חבר את הנרשמים להזמנות לאחר ההשקה כדי למדוד שיעור רכישה'],
        'execution':['קרא את תיאור השינוי והראיות לפני מתן הרשאת ביצוע','חיבור ads_management עדיין לא הוקם במוצר','לאחר החיבור יידרשו אישור מדויק, קריאת מצב מחדש ואימות שמירה'],
        'inventory':['פתח את הגדרות הקמפיין ב־Meta','בדוק שהחיבור יכול לקרוא תקציב, קהל ואירוע אופטימיזציה'],
        'fresh':['בדוק את מסך ההגדרות ואת זמן הקריאה האחרון','חדש הרשאה אם פג תוקפה; אין צורך לשנות קמפיינים']}
    for b in report['blockers']:
        priority=1 if b['id'] in ('fresh','inventory','measurement') else 3
        result.append({'key':b['id'],'title':b['title'],'detail':b['detail'],'priority':priority,
                       'steps':steps.get(b['id'],[]),'source':'meta_direct' if b['id'] not in ('measurement','execution') else 'readiness_review',
                       'entity_id':LAUNCH,'condition':b['id'],'evidence':{'observed_at':snapshot.get('checked_at'),'measurement_verified':False}})
    e=report['economics']
    if e.get('missing'):
        result.append({'key':'economics','title':'דיוק העלויות ושיעור הרכישה','detail':'החישוב הנוכחי הוא תרחיש. אין עדיין יעד רווח שנמדד עבור נרשמי ההשקה.',
                       'priority':2,'steps':e['missing'],'source':'shopify_direct','entity_id':LAUNCH,
                       'condition':{'state':e['state'],'missing':e['missing']},'evidence':{'observed_at':e.get('last_success_at'),'raw':e.get('raw'),'assumptions':e['assumptions']}})
    meta=((snapshot.get('sources') or {}).get('meta') or {}).get('data') or {}
    inventory=meta.get('inventory') or {}
    if report['inventory_verified']:
        for adset in inventory.get('adsets',[]):
            if adset.get('effective_status')!='ACTIVE':continue
            event=(adset.get('promoted_object') or {}).get('custom_event_type')
            if event is not None and event!='COMPLETE_REGISTRATION':
                result.append({'key':'event-'+adset['id'],'title':'בדיקת אירוע האופטימיזציה','detail':'האדסט פעיל אך לא התקבל ממנו COMPLETE_REGISTRATION. יש לבדוק את ההגדרה לפני שינוי.',
                     'priority':1,'steps':['פתח את האדסט ובדוק את מיקום ההמרה והאירוע','ודא שהאירוע הנבחר תואם להרשמה שהושלמה באתר'],
                     'source':'meta_direct','entity_id':adset['id'],'condition':event,'evidence':adset})
    return result


class ActionDesk:
    def __init__(self,data_dir):
        self.path=Path(data_dir)/'action-desk.sqlite3'
        self.sync_lock=threading.RLock();self.latest_checked_at='';self.latest_report=None
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS findings(
              id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL, revision INTEGER NOT NULL, first_seen TEXT NOT NULL,
              last_seen TEXT NOT NULL, snoozed_until TEXT, note TEXT NOT NULL DEFAULT '');
              CREATE TABLE IF NOT EXISTS history(seq INTEGER PRIMARY KEY, finding_id TEXT NOT NULL,
              time TEXT NOT NULL, event TEXT NOT NULL, old_state TEXT, new_state TEXT, note TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS sync_watermark(id INTEGER PRIMARY KEY, checked_at TEXT NOT NULL, sources TEXT NOT NULL);''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback();raise
        finally:db.close()

    def sync(self,snapshot,settings):
        with self.sync_lock:
            checked=snapshot.get('checked_at','')
            if checked and self.latest_checked_at and checked<self.latest_checked_at:
                return self.read(self.latest_report)
            result=self._sync(snapshot,settings)
            self.latest_checked_at=max(checked,self.latest_checked_at);self.latest_report=result['report']
            return result

    def _sync(self,snapshot,settings):
        report=build_report(snapshot,settings);current=findings(snapshot,report);seen=set();time=stamp()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            checked=snapshot.get('checked_at','')
            versions={key:source.get('last_success_at') for key,source in snapshot.get('sources',{}).items() if source.get('last_success_at')}
            saved=db.execute('SELECT * FROM sync_watermark WHERE id=1').fetchone()
            prior=json.loads(saved['sources']) if saved else {}
            stale=bool(saved and checked and saved['checked_at'] and checked<saved['checked_at'])
            stale=stale or any(key in versions and versions[key]<value for key,value in prior.items())
            if stale:return self.read(self.latest_report)
            prior.update(versions)
            db.execute('INSERT OR REPLACE INTO sync_watermark VALUES(1,?,?)',(max(checked,saved['checked_at'] if saved else ''),json.dumps(prior)))
            for item in current:
                identity=hashlib.sha256((ACCOUNT+':'+item['key']).encode()).hexdigest()[:24];seen.add(identity)
                semantic={'condition':item['condition'],'evidence':{k:v for k,v in item['evidence'].items() if k not in ('observed_at','checked_at','last_success_at')}}
                fingerprint=hashlib.sha256(json.dumps(semantic,sort_keys=True).encode()).hexdigest()
                old=db.execute('SELECT * FROM findings WHERE id=?',(identity,)).fetchone()
                payload=json.dumps(item,ensure_ascii=False)
                if old is None:
                    db.execute('INSERT INTO findings VALUES (?,?,?,\'open\',1,?,?,NULL,\'\')',(identity,fingerprint,payload,time,time))
                    db.execute('INSERT INTO history VALUES(NULL,?,?,\'detected\',NULL,\'open\',\'\')',(identity,time))
                else:
                    reopen=old['fingerprint']!=fingerprint or old['state']=='resolved' or (old['state']=='snoozed' and old['snoozed_until']<=time)
                    state='open' if reopen else old['state']
                    db.execute('UPDATE findings SET payload=?,fingerprint=?,state=?,last_seen=?,revision=revision+?,snoozed_until=CASE WHEN ? THEN NULL ELSE snoozed_until END WHERE id=?',
                               (payload,fingerprint,state,time,1 if reopen else 0,1 if reopen else 0,identity))
                    if reopen:db.execute('INSERT INTO history VALUES(NULL,?,?,\'reopened_by_evidence\',?,\'open\',\'\')',(identity,time,old['state']))
            for old in db.execute('SELECT * FROM findings WHERE state!=\'resolved\'').fetchall():
                if old['id'] not in seen:
                    if json.loads(old['payload'])['key'].startswith('tracking-collision-'):
                        tracking=audit_creatives(snapshot)
                        if not tracking['verified'] or tracking['unknown_ads']:continue
                    if json.loads(old['payload'])['key'].startswith('event-') and not report['inventory_verified']:continue
                    db.execute('UPDATE findings SET state=\'resolved\',revision=revision+1 WHERE id=?',(old['id'],))
                    db.execute('INSERT INTO history VALUES(NULL,?,?,\'condition_cleared\',?,\'resolved\',\'התנאי אינו מופיע בבדיקה הנוכחית\')',(old['id'],time,old['state']))
        return self.read(report)

    def read(self,report=None):
        with self.connect() as db:
            items=[]
            for row in db.execute('SELECT * FROM findings ORDER BY first_seen'):
                item=dict(row);item['payload']=json.loads(item['payload']);items.append(item)
            history=[dict(r) for r in db.execute('SELECT * FROM history ORDER BY seq DESC LIMIT 60')]
        items.sort(key=lambda x:(x['payload']['priority'],x['first_seen']))
        return {'findings':items,'history':history,'report':report,'live_action_allowed':False,'actor_scope':'local workstation; no multi-user identity',
                'note':'נסקר ידנית אינו אימות במקור. שינוי מצב בתור אינו שינוי בפרסום.'}

    def update(self,body):
        if set(body)!={'id','expected_revision','action','note'}:raise ValueError('Invalid review fields')
        if not isinstance(body['id'],str) or not re.fullmatch('[0-9a-f]{24}',body['id']):raise ValueError('Invalid finding ID')
        if body['action'] not in ('start','reviewed','snooze','reopen'):raise ValueError('Invalid review action')
        if type(body['expected_revision']) is not int or not isinstance(body['note'],str) or len(body['note'])>2000:raise ValueError('Invalid review')
        note=body['note'].strip()
        if body['action']=='reviewed' and len(note)<4:raise ValueError('יש לתאר מה נבדק; סקירה אינה אימות מדידה')
        time=stamp();state={'start':'in_review','reviewed':'reviewed','snooze':'snoozed','reopen':'open'}[body['action']]
        until=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat() if state=='snoozed' else None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM findings WHERE id=?',(body['id'],)).fetchone()
            if old is None or old['revision']!=body['expected_revision']:raise ValueError('הממצא השתנה; רענן לפני שמירת הטיפול')
            if old['state']=='resolved' and state!='open':raise ValueError('הממצא כבר נסגר בבדיקה אוטומטית')
            db.execute('UPDATE findings SET state=?,note=?,snoozed_until=?,revision=revision+1 WHERE id=?',(state,note,until,body['id']))
            db.execute('INSERT INTO history VALUES(NULL,?,?,?,?,?,?)',(body['id'],time,'operator_'+body['action'],old['state'],state,note))
        return {'saved':True,'state':state,'live_action_allowed':False,'measurement_verified':False}
