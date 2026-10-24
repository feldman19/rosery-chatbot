"""Bridge live aggregate reports into the business engine without inventing cohorts.

Live reports authenticate report sources only. They cannot authenticate unique
registrants, purchase cohorts, product-level costs, stock or an execution grant.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
from zoneinfo import ZoneInfo
from connections import SHOP
from meta_direct import ACCOUNT, LAUNCH
from strategy import evaluate
from business import ACTIVE as BUSINESS


def report(snapshot, now=None,orders=None):
    now=now or datetime.now(timezone.utc)
    sources=snapshot.get('sources') or {};meta=sources.get('meta') or {};shop=sources.get('shopify') or {}
    data=meta.get('data') or {};store=shop.get('data') or {};inventory=data.get('inventory') or {}
    campaign=inventory.get('campaign') or {}
    def fresh(source):
        try:return not source.get('error') and 0<=(now-datetime.fromisoformat(source['last_success_at'])).total_seconds()<=720
        except (TypeError,ValueError,KeyError):return False
    reports_ok=(fresh(meta) and fresh(shop) and data.get('verified') is True and data.get('complete') is True
                and data.get('account_id')==ACCOUNT and data.get('currency')==BUSINESS.currency
                and store.get('verified') is True and store.get('shop')==SHOP and store.get('currency')==BUSINESS.currency)
    scope_ok=(inventory.get('verified') is True and campaign.get('id')==LAUNCH and campaign.get('account_id')==ACCOUNT)
    profile={'tenant_id':BUSINESS.id,'account_id':ACCOUNT,'entity_id':LAUNCH,'currency':BUSINESS.currency,'timezone':data.get('timezone')}
    raw_budget=campaign.get('daily_budget')
    budget=int(raw_budget) if isinstance(raw_budget,(str,int)) and str(raw_budget).isdigit() else None
    entity={**profile,'status':campaign.get('status'),'budget_minor':budget,'budget_type':'daily' if budget is not None else None,
            'last_changed_at':campaign.get('updated_time')}
    windows={}
    if reports_ok:
        try:
            tz=ZoneInfo(profile['timezone']);end=now.astimezone(tz).replace(hour=0,minute=0,second=0,microsecond=0)
            rows=data.get('rows') or []
            if len({(r['ad_id'],r['date_start']) for r in rows})!=len(rows):raise ValueError('Duplicate grain')
            for days in (1,3,7):
                start=end-timedelta(days=days);since=start.date().isoformat();until=(end.date()-timedelta(days=1)).isoformat()
                if not data['since']<=since<=until<=data['until']:continue
                spend=Decimal(0);count=Decimal(0)
                for row in rows:
                    if row.get('campaign_id')!=LAUNCH or not since<=row['date_start']<=until:continue
                    if row.get('date_stop')!=row['date_start']:raise ValueError('Daily grain required')
                    value=Decimal(str(row['spend']))
                    actions={a['action_type']:a['value'] for a in row.get('actions',[])}
                    registrations=Decimal(str(actions.get('omni_complete_registration',actions.get('complete_registration',0))))
                    if not value.is_finite() or not registrations.is_finite() or value<0 or registrations<0:raise ValueError('Invalid metrics')
                    spend+=value;count+=registrations
                minor=spend*100
                if minor!=minor.to_integral_value() or count!=count.to_integral_value():raise ValueError('Unsupported fractional grain')
                windows[str(days)]={'full_days':True,'spend_minor':int(minor),'registrations':int(count),
                                    'started_at':start.isoformat(),'ended_at':end.isoformat()}
        except (ValueError,TypeError,KeyError,ArithmeticError):windows={}
    digest=hashlib.sha256(json.dumps({'windows':windows,'campaign':campaign},sort_keys=True).encode()).hexdigest()
    evidence={'observed_at':meta.get('last_success_at'),'valid_until':None,'entity':entity,'windows':windows,
              'provenance':{'verified':bool(reports_ok and scope_ok),'refs':['sha256:'+digest]},
              'measurement':{'verified':False,'unique_registrations':False},
              'cohort':{'verified':False,'subscribers':None,'purchasers':None},
              'costs':{'verified':False,'contribution_lower_minor':None},
              'maturity':{'verified':False,'attribution_complete':False,'comparable_cohort':False},
              'stock':{'verified':False,'available':False},'offer':{'verified':False,'current':False}}
    try:evidence['valid_until']=(datetime.fromisoformat(meta['last_success_at'])+timedelta(seconds=720)).isoformat()
    except (ValueError,TypeError,KeyError):pass
    # No approved operating envelope has been installed. A budget setting is not one.
    candidate=evaluate(profile,evidence,{},now)
    orders=orders or {}
    try:orders_ready=(orders.get('source_verified') is True and orders.get('complete') is True and not orders.get('error') and 0<=(now-datetime.fromisoformat(orders['last_success_at'])).total_seconds()<=2520)
    except (ValueError,TypeError,KeyError):orders_ready=False
    return {'account_id':ACCOUNT,'campaign_id':LAUNCH,'live_action_allowed':False,'write_connected':False,
            'mode':'live_read_only_business_engine','candidate':candidate,'windows':windows,
            'orders_source':orders,
            'readiness':[
                {'id':'reports','title':'קריאת נתוני הפרסום והחנות','ready':bool(reports_ok)},
                {'id':'orders','title':'קריאת הזמנות לצורך התאמה','ready':bool(orders_ready),'reason':'קריאה מוצפנת בשרת בלבד; ללא פרטי לקוחות בדאשבורד' if orders_ready else 'ממתינים לקריאת הזמנות מלאה ועדכנית'},
                {'id':'cohort','title':'התאמת הרשמה לרכישה','ready':False,'reason':'טרם חובר מקור רשומות הרשמה וההתאמה מולו'},
                {'id':'costs','title':'עלויות מלאות לפי הזמנה','ready':False,'reason':'נדרשים עלות מוצר, החזרות, מס, משלוח ועמלות בפועל'},
                {'id':'stock_offer','title':'מלאי והצעה נוכחיים','ready':False,'reason':'טרם חוברה קריאה מאומתת למוצרים שבקמפיין'},
                {'id':'grant','title':'מסגרת ביצוע מאושרת','ready':False,'reason':'אין הרשאת ביצוע חיה מוגבלת ומתועדת במוצר'},
                {'id':'writer','title':'ביצוע ואימות בחשבון חי','ready':False,'reason':'חיבור Meta המקומי הוא ads_read בלבד; אין מתאם ביצוע חי'}],
            'next_dependency':('חיבור רשימת ההרשמה והתאמתה להזמנות, לפני חיבור הרשאת כתיבה' if orders_ready else 'חיבור מקומי לקריאת הזמנות ולרשומות הרשמה, לפני חיבור הרשאת כתיבה'),
            'note':'המנוע נבדק בנפרד בסביבה סינתטית. דוחות חיים אינם מוכיחים חיבור אוטונומי מקצה לקצה.'}
