"""Evidence-bound read-only launch review. No provider executor or approval endpoint."""
from datetime import datetime, timezone
from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3
from pathlib import Path
from business import ACTIVE as BUSINESS

ACCOUNT=BUSINESS.meta_account
CAMPAIGN=BUSINESS.meta_launch_campaign
ADSET='120253309362570730'
PIXEL='723893636602228'


def _date(value):
    value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('Aware time required')
    return value.astimezone(timezone.utc)


def _amount(value):
    if not isinstance(value,dict) or value.get('unit')!=BUSINESS.currency or value.get('value') in (None,''):
        raise ValueError('ILS amount required')
    try:n=Decimal(str(value['value']))
    except InvalidOperation as e:raise ValueError('Invalid amount') from e
    if not n.is_finite() or n<0:raise ValueError('Invalid amount')
    return n


def registration_report(root,now=None,data_dir=None,manifest_override=None):
    root=Path(root).resolve();data=Path(data_dir) if data_dir is not None else root/'.local'
    manifest=Path(manifest_override) if manifest_override is not None else data/'registration-manifest.json'
    if not manifest.exists():return {'available':False,'live_action_allowed':False}
    meta=json.loads(manifest.read_text(encoding='utf-8'))
    allowed={'.local/registration-meta.json','.local/registration-site.json',
             '../outputs/rosery-launch-registration-tracking-2026-10-16.json',
             '../outputs/rosery-registration-scale-recheck-2026-10-16.json'}
    if meta.get('tenant')!=BUSINESS.id or meta.get('account_id')!=ACCOUNT or meta.get('campaign_id')!=CAMPAIGN:
        raise ValueError('Case scope mismatch')
    if set(meta.get('files',{}))!=allowed:raise ValueError('Missing or unsupported case evidence')
    evidence={}
    for name,digest in meta['files'].items():
        source=data/Path(name).name if name.startswith('.local/') else root/name
        raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Evidence changed; collect a new revision')
        evidence[name]=json.loads(raw)
    m=evidence['.local/registration-meta.json'];s=evidence['.local/registration-site.json']
    if (m.get('tenant'),m.get('account_id'),m.get('campaign_id'))!=(BUSINESS.id,ACCOUNT,CAMPAIGN):raise ValueError('Meta binding mismatch')
    if s.get('tenant')!=BUSINESS.id or s.get('domain')!='roseryjewelry.com' or s.get('list_id')!='40428':raise ValueError('Site binding mismatch')
    c=m['campaign'];a=m['adset']
    if c.get('id')!=CAMPAIGN or a.get('id')!=ADSET or a.get('campaign_id')!=CAMPAIGN:raise ValueError('Entity binding mismatch')
    if any(row.get('campaign_id')!=CAMPAIGN or row.get('adset_id')!=ADSET for row in m['ads']):raise ValueError('Ad binding mismatch')
    now=now or datetime.now(timezone.utc)
    dates=[_date(m['captured_at']),_date(s['captured_at'])]
    if now.tzinfo is None or any(d>now for d in dates):raise ValueError('Invalid evidence time')
    # Read-review display policy only. This never authorizes a remote write.
    stale=any((now-d).total_seconds()>7200 for d in dates)
    revision=hashlib.sha256(json.dumps(meta['files'],sort_keys=True).encode()).hexdigest()
    checks=[]
    def check(key,title,state,summary,source):
        if stale and state=='pass':state='stale'
        checks.append({'id':key,'title':title,'state':state,'summary':summary,
                       'evidence_ref':{'file':source,'sha256':meta['files'][source]},
                       'scope':'read_only_review','source_authenticated':False})
    promoted=a.get('promoted_object',{})
    goal_ok=promoted.get('pixel_id')==PIXEL and promoted.get('custom_event_type')=='COMPLETE_REGISTRATION' and a.get('optimization_goal')=='OFFSITE_CONVERSIONS'
    check('settings','הגדרות קמפיין ההרשמה נקראו','pass' if goal_ok else 'fail',
          'קמפיין ואדסט תואמים; פיקסל ואירוע הרשמה נבחרו' if goal_ok else 'הגדרות האירוע אינן תואמות למסלול ההרשמה','.local/registration-meta.json')
    check('landing','דף ההרשמה הציבורי נבדק','pass' if s.get('public_static_page_verified') is True else 'missing',
          'הכניסה הציבורית מפנה לדף השקה; טופס דינמי בדפדפן נקי עדיין לא אומת','.local/registration-site.json')
    check('storage','רשימת ההשקה נמצאה ב־Flashy','pass' if type(s.get('list_contacts')) is int and s['list_contacts']>=0 else 'missing',
          'נבדקו זהות הרשימה והספירה המוצגת; זה אינו מספר נרשמים שמיוחסים למטא','.local/registration-site.json')
    historical=evidence['../outputs/rosery-launch-registration-tracking-2026-10-16.json']
    if historical.get('campaign_id')!=CAMPAIGN or historical.get('dataset_id')!=PIXEL:raise ValueError('Historical test scope mismatch')
    tested=historical.get('test_result',{})
    history_ok=tested.get('website_success') is True and tested.get('flashy_contact_subscription_verified') is True and type(tested.get('meta_registration_browser_requests_observed_in_controlled_submission')) is int and tested['meta_registration_browser_requests_observed_in_controlled_submission']==1
    check('history','תיעוד בדיקת ההרשמה הקודמת נסקר','reviewed_history' if history_ok else 'missing',
          'קיים תיעוד מ־16.10.2026 של שמירת נרשם ושיגור אירוע בדפדפן; זו אינה בדיקה חדשה או הוכחת ייחודיות' if history_ok else 'התיעוד אינו כולל את כל ראיות השמירה ושיגור האירוע הנדרשות',
          '../outputs/rosery-launch-registration-tracking-2026-10-16.json')
    check('dedup','ייחודיות והתאמה בין המקורות','missing',
          'טרם הושלמה התאמה עדכנית בין אדם ייחודי, אירוע שהתקבל וייחוס למודעה','.local/registration-meta.json')
    check('destination','כתובת היעד בכל מודעה','blocked',
          ('נבדקה PL07B בממשק: סימון הקישור שלה הוא pl06b; יתר הכתובות לא אומתו. מופעלים גם מוזיקה, קרוסלה ותוספי מוצרים; נדרשת בדיקה לפני שינוי' if m.get('ui_review') else 'הכלי החזיר קריאייטיבים אך לא חשף את כתובות היעד; אין להניח שהן זהות'),'.local/registration-meta.json')
    registrations=c.get('omni_complete_registration')
    if registrations is not None and (isinstance(registrations,bool) or not str(registrations).isdigit()):raise ValueError('Invalid registration count')
    n=int(registrations) if registrations is not None else None
    spend=_amount(c['amount_spent']);budget=_amount(c['daily_budget'])
    rows=[]
    for row in m['ads']:
        reg=row.get('omni_complete_registration');count=int(reg) if reg is not None and str(reg).isdigit() else None
        spent=_amount(row['amount_spent'])
        rows.append({'id':row['id'],'name':row['name'],'spend_ils':str(spent),
                     'registrations':count,'landing_page_views':row.get('landing_page_view'),
                     'attributed_cpl_ils':str((spent/count).quantize(Decimal('.01'))) if count else None,
                     'state':row.get('effective_status'),'assessment':'תוצאה תיאורית בלבד; אין החלטת עצירה או סקייל'})
    answer=None
    answer_db=data/'registration-answers.sqlite3'
    if answer_db.exists():
        with closing(sqlite3.connect(answer_db)) as db:
            row=db.execute('SELECT choice,at FROM answers WHERE revision=? ORDER BY rowid DESC LIMIT 1',(revision,)).fetchone()
            if row:answer={'choice':row[0],'at':row[1],'scope':'business_context_only','live_permission':False}
    question=None
    if budget!=Decimal('60'):
        question={'id':'budget_scope','title':'מהי מסגרת התקציב העדכנית?',
                  'question':f'בקריאה האחרונה התקציב הוא {budget} ₪ ליום; בתיעוד הקודם הופיעו 60 ₪. האם זו המסגרת המתוכננת?',
                  'why_needed':'כדי להכין הצעות עתידיות בגבולות הנכונים; המערכת לא שינתה את התקציב',
                  'checked':'נקראו התקציב הנוכחי ויומן הפעולות הקודם; לא נקבע מי ביצע את השינוי',
                  'grants_live_permission':False}
    return {'available':True,'tenant':BUSINESS.id,'case_id':'launch-registration','revision':revision,
            'captured_at':min(dates).isoformat(),'freshness':'stale' if stale else 'recent_owner_import',
            'state':'needs_answer' if question and not answer else 'blocked_evidence',
            'checks':checks,'completed_checks':sum(x['state']=='pass' for x in checks),
            'historical_reviews':sum(x['state']=='reviewed_history' for x in checks),
            'blocked_checks':sum(x['state'] not in ('pass','reviewed_history') for x in checks),'question':question,'answer':answer,
            'proposal':None,'approval_available':False,'live_action_allowed':False,'independent_refresh':False,
            'metrics':{'spend_ils':str(spend),'daily_budget_ils':str(budget),'registrations':n,
                       'landing_page_views':c.get('landing_page_view'),
                       'attributed_cpl_ils':str((spend/n).quantize(Decimal('.01'))) if n else None,
                       'window':m['window'],'budget_updated_at':c.get('updated_time')},
            'ads':rows,'flashy':{'list_contacts':s.get('list_contacts'),'popup_conversions':s.get('popup_conversions'),
                                 'scope':'תקופות שונות וללא ייחוס למטא; אין לחלק בהן את הוצאות הקמפיין'},
            'decision':{'action':'no_change','summary':'אין כרגע בסיס מספיק להצעת עצירה או הגדלת תקציב',
                        'reason':'אירועים מיוחסים אינם הוכחת רווח או נרשמים ייחודיים. יש להשלים התאמת מדידה וכתובות יעד לפני הצעת שינוי',
                        'next_step':'להשלים את הבדיקות החסרות ולברר את מסגרת התקציב; לא להחזיר תקציב ישן אוטומטית'},
            'end_to_end_verified':False,'test_registration_submitted':False,
            'method':'reused existing Meta/Flashy/browser tools and historical action evidence; no paid model/API'}


def answer_registration(root,data_dir,revision,choice):
    if choice not in ('observed_budget_intended','budget_needs_review'):raise ValueError('Unsupported answer')
    report=registration_report(root,data_dir=data_dir)
    if not report['available'] or report['revision']!=revision or report['freshness']=='stale':raise ValueError('Evidence changed or stale; refresh before answering')
    if not report['question']:raise ValueError('No matching question')
    path=Path(data_dir)/'registration-answers.sqlite3'
    with closing(sqlite3.connect(path,timeout=10)) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS answers(revision TEXT NOT NULL,choice TEXT NOT NULL,at TEXT NOT NULL)')
        db.execute('INSERT INTO answers VALUES(?,?,?)',(revision,choice,datetime.now(timezone.utc).isoformat()))
        db.commit()
    return {'saved':True,'scope':'business_context_only','live_permission':False,
            'note':'התשובה נשמרה בהקשר העסקי בלבד. לא בוצע שינוי בפרסום ולא ניתנה הרשאה לביצוע'}
