"""Local owner-session pilot. Imported observations never authorize live actions."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def pilot_report(path, now=None):
    path = Path(path)
    if not path.exists():
        return {'available':False,'mode':'observe','live_action_allowed':False}
    raw = path.read_bytes()
    p = json.loads(raw)
    if p.get('tenant') != 'rosery' or p.get('schema_version') != 1:
        raise ValueError('Invalid pilot identity')
    identity=p.get('identity',{})
    if identity.get('meta_account')!='393059510164294' or identity.get('shop_domain')!='21cc82-3.myshopify.com':
        raise ValueError('Pilot account binding mismatch')
    captured=datetime.fromisoformat(p['captured_at'])
    now=now or datetime.now(timezone.utc)
    if captured.tzinfo is None or now.tzinfo is None:
        raise ValueError('Timezone required')
    age=(now-captured).total_seconds()
    if age<0: raise ValueError('Future pilot capture')
    # Display freshness only; this is not a campaign waiting/decision threshold.
    stale=age>7200
    allowed={'schema_version','tenant','captured_at','facts','observations','limitations'}
    result={k:deepcopy(v) for k,v in p.items() if k in allowed}
    result['business']='ROSERY'
    result['identity']=identity
    result['source_verified']=False
    result['verification_kind']='owner_session_import_not_autonomous_or_source_authenticated'
    observed=[]
    fields={'meta_account':{'id','name','timezone_name','amount_spent','impressions','landing_page_view','omni_complete_registration'},
            'meta_campaign':{'id','name','status','effective_status','daily_budget','objective','amount_spent','landing_page_view','omni_complete_registration'},
            'shopify_sales':{'orders','net_sales','shipping_charges','taxes','total_sales','currency','channel_filter','timezone_label','timezone_iana_verified'}}
    for observation in p['observations']:
        key=observation['source_id']
        if key not in fields: raise ValueError('Unsupported pilot source')
        data=observation['data']
        if key=='meta_account' and data.get('id')!=identity['meta_account']:
            raise ValueError('Observation account mismatch')
        if key=='meta_campaign' and data.get('id')!='120253309360290730':
            raise ValueError('Observation campaign mismatch')
        expected_scope={'meta_account':identity['meta_account'],'meta_campaign':'120253309360290730','shopify_sales':identity['shop_domain']}[key]
        if observation.get('scope') not in (None,expected_scope):
            raise ValueError('Observation scope mismatch')
        observed.append({k:observation[k] for k in ('source_id','source','query','scope') if k in observation}|
                        {'data':{k:deepcopy(v) for k,v in data.items() if k in fields[key]},
                         'source_verified':False,'scope_verified':False})
    result['observations']=observed
    result.update(available=True,mode='observe',live_action_allowed=False,
                  autonomous_refresh=False,source_hash=hashlib.sha256(raw).hexdigest(),
                  freshness='stale_import' if stale else 'recent_owner_import',
                  freshness_policy='2 hours for display only, never action authority')
    exceptions=[]
    def issue(code,title,owner,reason,next_step,priority='normal'):
        exceptions.append({'id':code,'title':title,'owner':owner,'reason':reason,
                           'next_step':next_step,'priority':priority,'status':'open',
                           'action_allowed':False,'evidence_hash':result['source_hash']})
    if stale:
        issue('refresh','נדרש רענון נתונים','system','הייבוא המקומי התיישן; אינו מצב חי',
              'לקרוא שוב את המקורות המורשים לפני החלטה')
    campaign=next((o for o in observed if o['source_id']=='meta_campaign'),None)
    if campaign and campaign['data'].get('effective_status')=='ACTIVE' and campaign['data'].get('objective')=='OUTCOME_LEADS':
        issue('registration-proof','אימות מדידת הרשמה בקמפיין פעיל','system',
              'קמפיין ההרשמה פעיל; הייבוא אינו כולל הוכחת שמירת נרשם ואירוע יחיד',
              'לבדוק שמירה, אירוע הצלחה וייחוס; אין להסיק אפס הרשמות מערך חסר','high')
    issue('economics','חישוב כלכלת העסק אינו שלם','system',
          'אין יישוב עדכני של עלות מוצר, משלוח, עמלות, החזרות ומע״מ',
          'לאסוף תחילה מהמקורות הזמינים; לשאול אדם רק על פער שנותר')
    issue('launch-phase','אימות שלב ההשקה והאתר','system',
          'היסטוריית העסק כוללת סגירת אתר והשקה; המצב הנוכחי לא נבדק בפיילוט',
          'לאמת דף יעד, טופס ומועד לפני הצעת קמפיין מכירות')
    result['exceptions']=exceptions
    result['strategy']={'stage':'registration_launch_observation',
                        'decision':'no_change_proposed',
                        'reason':'נדרשות ראיות מדידה וכלכלת עסק לפני החלטת סקייל',
                        'scale_allowed':False,'causal_effect_proven':False}
    result['operator_metrics']={'businesses_in_pilot':1,'target_businesses_per_operator':50,
                                'target_is_validated':False,'human_minutes_measured':None,
                                'open_exceptions':len(exceptions)}
    return result


def employee_pilot(report):
    """Explicit projection: no internal economics, metrics or raw source payloads."""
    if not report['available']: return report
    return {'available':True,'business':report['business'],'mode':'observe',
            'captured_at':report['captured_at'],'freshness':report['freshness'],
            'live_action_allowed':False,'autonomous_refresh':False,
            'summary':'פיילוט ROSERY: הכנת חומרים ובדיקת פערים; אין הפעלה אוטומטית',
            'next_steps':['הכנת חומרים לפי המשימות','העברת שאלות שלא נפתרו למנהל'],
            'note':'נתוני הביצועים, העלויות והחלטות התקציב זמינים בסביבת המנהל בלבד'}
