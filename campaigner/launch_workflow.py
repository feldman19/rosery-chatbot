"""Durable sales-launch intake and evidence-bound preparation, no remote fetch.

A brief is business intent, never campaign approval. Drive URLs are references
only. Account inventory is trusted host input; user readiness flags are rejected.
"""
from batch_limits import MAX_ADSETS, MAX_ADS, MAX_CREATIVE_IMAGES, MAX_DRIVE_SOURCES

from contextlib import contextmanager
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata
from urllib.parse import urlsplit,parse_qsl,urlencode,urlunsplit
from meta_direct import ACCOUNT,LAUNCH
from business import ACTIVE as BUSINESS

SALES_OBJECTIVES={'SALES','OUTCOME_SALES','PRODUCT_CATALOG_SALES'}


def _stamp(value):
    try:
        t=datetime.fromisoformat(value)
        return t if t.tzinfo is not None else None
    except (ValueError,TypeError):return None


def drive_url(value):
    if not isinstance(value,str) or len(value)>2048:raise ValueError('A direct Google Drive link is required')
    try:
        p=urlsplit(value)
        if p.scheme!='https' or p.hostname!='drive.google.com' or p.username or p.password or p.port not in (None,443) or p.fragment:raise ValueError()
        pairs=parse_qsl(p.query,keep_blank_values=True)
        if any(k not in ('usp','resourcekey','id') for k,v in pairs):raise ValueError()
        resource=re.fullmatch(r'/drive/folders/[A-Za-z0-9_-]{10,150}/?|/file/d/[A-Za-z0-9_-]{10,150}(?:/view|/preview)?/?',p.path)
        if not resource:
            ids=[v for k,v in pairs if k=='id']
            if p.path!='/open' or len(ids)!=1 or not re.fullmatch('[A-Za-z0-9_-]{10,150}',ids[0]):raise ValueError()
        return urlunsplit(('https','drive.google.com',p.path,urlencode(pairs),''))
    except (ValueError,TypeError):raise ValueError('Only direct HTTPS Google Drive folder/file links are accepted') from None


class LaunchWorkflow:
    def __init__(self,directory,clock=None,history=None):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);self.path=self.directory/'launch-workflow.sqlite3'
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        # Host-supplied selected historical records only; never full chat/customer data.
        self.history=json.loads(json.dumps(history or {'records':[{'source_ref':'outputs/rosery-action-ledger.json','kind':'historical_action_log','current_state_verified':False},{'source_ref':'outputs/rosery-media-operations-guide-v1-2026-10-15.md','kind':'operating_safeguards','current_state_verified':False}], 'known_registration_campaign_id':LAUNCH},ensure_ascii=False))
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT,revision INTEGER,payload TEXT NOT NULL,PRIMARY KEY(id,revision))')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()
    def _now(self):
        now=self.clock()
        if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('Aware host time required')
        return now
    def _latest(self,db):
        row=db.execute('SELECT payload FROM requests ORDER BY revision DESC LIMIT 1').fetchone()
        return json.loads(row['payload']) if row else None
    def _work(self,request):
        return None if request is None else {'id':'sales-launch-'+request['id'],'type':'launch_sales_preparation','goal':'sales','state':'preparation_required','request_revision':request['revision'],'execution_available':False}
    def create(self,body):
        if not isinstance(body,dict) or set(body) not in ({'brief','drive_urls'},{'brief','drive_urls','local_sources','local_image_review_consent'}):raise ValueError('Only business brief and Drive links are accepted; these are not execution approval')
        brief=body['brief']
        if not isinstance(brief,str) or not 3<=len(brief.strip())<=6000:raise ValueError('Brief must contain 3–6000 characters')
        brief=''.join(c for c in brief if c in '\n\t' or not unicodedata.category(c).startswith('C')).strip()
        if len(brief)<3:raise ValueError('Brief is empty after sanitization')
        if re.search(r'(?:access_token|api[_ -]?key|client_secret|password)\s*[:=]',brief,re.I):raise ValueError('Do not put credentials in the brief')
        local=body.get('local_sources',[])
        if local and (body['drive_urls'] or type(body.get('local_image_review_consent')) is not bool):raise ValueError('Separate source mode and explicit image review consent required')
        if 'local_sources' in body:
            if not isinstance(local,list) or not 1<=len(local)<=30 or any(not isinstance(x,dict) or set(x)!={'id','sha256'} or not re.fullmatch('local_[a-f0-9]{32}',str(x['id'])) or not re.fullmatch('[a-f0-9]{64}',str(x['sha256'])) for x in local):raise ValueError('Exact local sources required')
        local_fields={'local_sources':local,'local_image_review_consent':body['local_image_review_consent']} if local else {}
        urls=body['drive_urls']
        if not isinstance(urls,list) or not 0<=len(urls)<=MAX_DRIVE_SOURCES:raise ValueError('Provide at most 30 direct Drive links')
        urls=list(dict.fromkeys(drive_url(url) for url in urls));now=self._now().isoformat()
        with self.db() as db:
            prior=self._latest(db)
            if prior and prior['brief']==brief and prior['drive_urls']==urls and prior.get('local_sources',[])==local and prior.get('local_image_review_consent')==body.get('local_image_review_consent'):
                return {'saved':True,'unchanged':True,'request':prior,'work_item':self._work(prior),'live_action_allowed':False,'provider_writes':0}
            request={'id':prior['id'] if prior else secrets.token_hex(16),'revision':prior['revision']+1 if prior else 1,
                     'brief':brief,'drive_urls':urls,'created_at':prior['created_at'] if prior else now,'saved_at':now,
                     'goal':'sales','execution_approval':False,'asset_content_fetched':False,**local_fields}
            db.execute('INSERT INTO requests VALUES(?,?,?)',(request['id'],request['revision'],json.dumps(request,ensure_ascii=False)))
        return {'saved':True,'request':request,'work_item':self._work(request),'live_action_allowed':False,'provider_writes':0}
    def versions(self):
        with self.db() as db:return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM requests ORDER BY revision')]
    def report(self,snapshot,materials=None):
        now=self._now()
        with self.db() as db:request=self._latest(db)
        source=(snapshot.get('sources') or {}).get('meta') or {};data=source.get('data') or {};inventory=data.get('account_campaigns') or {}
        source_at=_stamp(source.get('last_success_at'));inventory_at=_stamp(inventory.get('checked_at'))
        fresh=lambda t:t is not None and 0<=(now-t).total_seconds()<=720
        verified=(fresh(source_at) and fresh(inventory_at) and not source.get('error') and data.get('verified') is True and data.get('account_id')==ACCOUNT and data.get('currency')==BUSINESS.currency and inventory.get('verified') is True and inventory.get('complete') is True and inventory.get('account_id')==ACCOUNT)
        campaigns=inventory.get('campaigns')
        if not isinstance(campaigns,list):verified=False;campaigns=[]
        ids=[r.get('id') for r in campaigns if isinstance(r,dict)]
        if len(ids)!=len(campaigns) or any(not isinstance(i,str) or not i.isdigit() for i in ids) or len(set(ids))!=len(ids) or any(r.get('account_id')!=ACCOUNT for r in campaigns):verified=False
        candidates=[];excluded=[]
        if verified:
            for row in campaigns:
                objective=row.get('objective')
                if row['id']==LAUNCH or objective not in SALES_OBJECTIVES|{'CONVERSIONS'}:
                    excluded.append({'id':row['id'],'objective':objective,'reason':'Registration launch or objective does not establish a sales campaign'})
                    continue
                candidates.append({k:row.get(k) for k in ('id','name','objective','status','effective_status','daily_budget','lifetime_budget','updated_time')})
                candidates[-1].update(currency=BUSINESS.currency,observed_at=inventory.get('checked_at'),source='direct_meta_inventory',is_recommendation=False,
                    goal_verified_by_objective=objective in SALES_OBJECTIVES,requires_purchase_event_check=True,selected=False,
                    metrics=self._metrics(data,row['id']),promotion_review={'required':any(flag in str(row.get('name','')) for flag in ('30%','חג')),'basis':'Campaign name only; old sale text in creative has not been inspected'})
        count=len(candidates)
        recent=[c for c in candidates if c['goal_verified_by_objective'] and c.get('metrics') and c['metrics']['recent_spend_minor']>0]
        suggested=recent[0]['id'] if len(recent)==1 else None
        candidates.sort(key=lambda c:(not c['goal_verified_by_objective'],-((c.get('metrics') or {}).get('recent_spend_minor',-1)),c['id']))
        candidates=candidates[:3]
        selection={'state':'suggested_for_review' if suggested else 'choice_required' if count>1 else 'no_recent_evidence',
                   'suggested_campaign_id':suggested,'selected_campaign_id':None,
                   'reason':'Only one sales-objective campaign had reported spend in the last 7 complete days' if suggested else 'Do not infer a winner or choose from campaign names alone',
                   'is_execution_approval':False}
        material=self._materials(request,materials,now)
        stages=[{'id':'brief','title':'בקשת השקת מכירות','state':'saved' if request else 'missing','detail':'הבקשה נשמרה ככוונה עסקית בלבד; לא ניתן אישור לפרסום'},
                {'id':'assets','title':'חומרי הפרסום','state':'saved' if request and request['drive_urls'] else 'missing','detail':'קישורי Drive נשמרו בלבד. לא נקראו קבצים, תמונות, קופי, מידות או זכויות שימוש' if request and request['drive_urls'] else 'הבריף יכול להתקדם, אך טרם נשמרו קישורי חומרי הפרסום'},
                {'id':'campaigns','title':'בדיקת קמפיינים קיימים','state':'verified' if verified else 'blocked','detail':str(count)+' קמפייני מכירות/בדיקה נמצאו בקריאה. לא נבחר קמפיין אוטומטית' if verified else 'נדרשת קריאה מלאה ועדכנית של קמפייני החשבון; ההיסטוריה אינה מצב חי'},
                {'id':'asset_review','title':'בדיקת מדיה וקופי','state':'missing','detail':'יש לקרוא את חומרי המקור ולבדוק מוצר, הצעה, פורמטים ותצוגה לפני הכנת מודעות'},
                {'id':'landing','title':'דף מכירה','state':'missing','detail':'כתובת דף מכירה, זמינות מוצר והצעה טרם אומתו עבור בקשת המכירות הזאת'},
                {'id':'measurement','title':'מדידת רכישה','state':'missing','detail':'טרם אומתו אירוע Purchase, ערך, מטבע, כפילויות והזמנה נשמרת עבור התכנון הזה'},
                {'id':'budget','title':'תקציב ואישור השקה','state':'missing','detail':'לא אושר תקציב מכירות או פרסום עבור הבקשה. 60 ₪ להרשמות אינו אישור להשקת מכירות'}]
        if material['metadata_verified']:
            asset_stage=next(s for s in stages if s['id']=='assets')
            asset_stage.update(state='verified',detail='אומתו נתוני מקור של '+str(material['metadata_files'])+' קבצים בדרייב. זו אינה בדיקת הקריאייטיב או מוכנות להשקה')
        stages.insert(3,{'id':'asset_content','title':'קריאת קובצי המקור','state':'verified' if material['content_read_count']>0 and material['assets_complete'] else 'saved' if material['content_read_count']>0 else 'missing',
                         'detail':str(material['content_read_count'])+' קובצי מקור נקראו ותועדו בחתימת תוכן; בדיקת הקריאייטיב עדיין פתוחה' if material['content_read_count'] else material['detail']})
        gaps=[{'id':s['id'],'title':s['title'],'detail':s['detail']} for s in stages if s['state'] in ('missing','blocked')]
        if request and request['drive_urls'] and not material['metadata_verified']:gaps.insert(0,{'id':'drive_access','title':'גישה לתוכן הדרייב','detail':'אין בחיבור העצמאי קורא Drive. שמירת קישור אינה הורדה או בדיקת הקריאייטיב'})
        history={**self.history,'live':False,'execution_approval':False,'note':'תיעוד קודם בלבד. נדרש יישוב מול מצב חי והבקשה החדשה; אין להעתיק תקציבים או אישורים'}
        return {'request':request,'work_item':self._work(request),'goal':'sales','stages':stages,'gaps':gaps,'campaign_candidates':candidates,
                'materials':material,'candidate_total':count,'omitted_candidates':max(0,count-3),'campaign_selection':selection,'excluded_campaigns':excluded,'historical_context':history,
                'sources':[{'source':'direct_meta_account_campaigns','observed_at':inventory.get('checked_at'),'verified':bool(verified),'detail':'Fresh account-wide inventory required; no recommendation or publication follows'}],
                'checked_at':now.isoformat(),'live_action_allowed':False,'provider_writes':0,'publication_available':False,'automatic_selection':False}
    def _materials(self,request,materials,now):
        """Consumes a trusted host's signature-verified report, never client flags."""
        result={'metadata_verified':False,'metadata_files':0,'assets_complete':False,'content_read_count':0,
                'assets':[],'creative_reviewed':False,'detail':'עדיין לא נקראו קובצי מקור מאומתים עבור הבקשה הזאת'}
        if not request or not isinstance(materials,dict):return result
        if materials.get('error'):
            result['detail']='הקריאה האחרונה נכשלה; תיעוד קודם אינו אימות עדכני של החומרים';return result
        if materials.get('verified') is not True or materials.get('binding_verified') is not True:
            result['detail']='שיוך חומרי המקור או חתימת התיעוד לא אומתו';return result
        if materials.get('request_id')!=request['id'] or type(materials.get('request_revision')) is not int or materials['request_revision']!=request['revision'] or materials.get('drive_urls')!=request['drive_urls']:
            result['detail']='החומרים שייכים לגרסה אחרת של הבקשה או לקישורי דרייב אחרים';return result
        observed=_stamp(materials.get('checked_at'))
        if observed is None or not 0<=(now-observed).total_seconds()<=720:
            result['detail']='תיעוד החומרים התיישן או שחסר מועד קריאה תקין';return result
        metadata=materials.get('metadata') or {}
        if not isinstance(metadata,dict) or metadata.get('verified') is not True or metadata.get('complete') is not True or metadata.get('email')!='inforosery@gmail.com':
            result['detail']='נדרשת קריאת נתוני מקור מלאה מחשבון הדרייב המורשה של רוסרי';return result
        sources=metadata.get('sources')
        if not isinstance(sources,list) or len(sources)!=len(request['drive_urls']) or any(not isinstance(s,dict) or s.get('complete') is not True for s in sources):
            result['detail']='נתוני המקור אינם מכסים את כל הקישורים שבבקשה';return result
        if any(not isinstance(s.get('source_url'),str) for s in sources) or sorted(s['source_url'] for s in sources)!=sorted(request['drive_urls']):
            result['detail']='קישורי המקור שנקראו שונים מאלו שנשמרו בבקשה';return result
        files={}
        for source in sources:
            rows=source.get('files')
            if not isinstance(rows,list):return result
            for file in rows:
                if not isinstance(file,dict) or not isinstance(file.get('id'),str):return result
                if file['id'] in files and files[file['id']]!=file:
                    result['detail']='נמצאו נתוני מקור סותרים לאותו קובץ בדרייב';return result
                files[file['id']]=file
        result.update(metadata_verified=True,metadata_files=len(files),observed_at=materials['checked_at'],detail='נתוני המקור נקראו; קריאת התוכן והתאמת הקריאייטיב הן בדיקות נפרדות')
        assets=materials.get('assets') or {};rows=assets.get('assets') if isinstance(assets,dict) else None
        if not isinstance(rows,list):return result
        seen=set();invalid=False
        for row in rows:
            if not isinstance(row,dict) or row.get('id') not in files or row.get('id') in seen:
                invalid=True;continue
            seen.add(row['id'])
            checksum=row.get('sha256','')
            if not isinstance(checksum,str) or not re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}',checksum) or row.get('content_read') is not True:
                invalid=True;continue
            mime=row.get('mime_type')
            if not isinstance(mime,str) or mime!=files[row['id']].get('mimeType'):
                invalid=True;continue
            item={key:row[key] for key in ('id','name','mime_type','sha256','dimensions','format_check') if key in row}
            item.update(content_read=True,creative_reviewed=False)
            if isinstance(row.get('text_excerpt'),str):item.update(text_excerpt=row['text_excerpt'][:2000],untrusted_text=True)
            result['assets'].append(item)
        result['content_read_count']=len(result['assets'])
        expected={identity for identity,row in files.items() if row.get('mimeType')!='application/vnd.google-apps.folder'}
        actual={row['id'] for row in result['assets']}
        result['assets_complete']=assets.get('complete') is True and not invalid and bool(actual) and actual==expected
        return result

    def _metrics(self,data,campaign_id):
        if data.get('complete') is not True:return None
        try:
            today=datetime.fromisoformat(data['today']).date()
        except (KeyError,ValueError,TypeError):return None
        from datetime import timedelta
        start=(today-timedelta(days=7)).isoformat();end=(today-timedelta(days=1)).isoformat()
        if not isinstance(data.get('since'),str) or not isinstance(data.get('until'),str) or data['since']>start or data['until']<end:return None
        rows=data.get('rows')
        if not isinstance(rows,list):return None
        selected=[r for r in rows if isinstance(r,dict) and r.get('campaign_id')==campaign_id and isinstance(r.get('date_start'),str) and start<=r['date_start']<=end]
        if not selected:return None
        spend=Decimal(0);purchases=Decimal(0)
        try:
            for r in selected:
                value=Decimal(str(r['spend']))
                if not value.is_finite() or value<0:return None
                spend+=value
                actions={a['action_type']:a['value'] for a in r.get('actions',[]) if isinstance(a,dict) and 'action_type' in a and 'value' in a}
                for key in ('omni_purchase','purchase','offsite_conversion.fb_pixel_purchase'):
                    if key in actions:
                        value=Decimal(str(actions[key]))
                        if not value.is_finite() or value<0:return None
                        purchases+=value;break
        except (ValueError,TypeError,KeyError,InvalidOperation):return None
        return {'available':True,'daily_ad_rows':len(selected),'source':'Meta attributed reporting',
                'recent_spend_minor':int(spend*100),'attributed_purchases':float(purchases),
                'window':{'since':start,'until':end,'full_days':True,'days':7},
                'last_reported_activity':max(r['date_start'] for r in selected),
                'winner_verified':False,'purchases_reconciled_to_orders':False}
