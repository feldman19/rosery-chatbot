"""ROSERY launch decision desk. No live write or implicit approval."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from meta_direct import ACCOUNT,LAUNCH
from connections import SHOP
from business import ACTIVE as BUSINESS


def context(data_dir,values=None):
    with closing(sqlite3.connect(Path(data_dir)/'decision-context.sqlite3')) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS context (id INTEGER PRIMARY KEY, target_cpl_minor INTEGER, daily_cap_minor INTEGER, saved_at TEXT)')
        if values is not None:
            if set(values)!={'target_cpl_minor','daily_cap_minor'}:raise ValueError('Only target and cap accepted')
            for v in values.values():
                if v is not None and (type(v) is not int or not 1<=v<=10000000):raise ValueError('Invalid amount')
            db.execute('INSERT OR REPLACE INTO context VALUES (1,?,?,?)',(values['target_cpl_minor'],values['daily_cap_minor'],datetime.now(timezone.utc).isoformat()))
        row=db.execute('SELECT target_cpl_minor,daily_cap_minor,saved_at FROM context WHERE id=1').fetchone()
    return dict(zip(['target_cpl_minor','daily_cap_minor','saved_at'],row)) if row else {'target_cpl_minor':None,'daily_cap_minor':None,'saved_at':None}


def build_report(snapshot,settings,now=None):
    now=now or datetime.now(timezone.utc)
    source=snapshot.get('sources',{}).get('meta',{});data=source.get('data') or {}
    try:fresh=0<=(now-datetime.fromisoformat(source['last_success_at'])).total_seconds()<=720 and not source.get('error')
    except (ValueError,TypeError,KeyError):fresh=False
    valid=data.get('account_id')==ACCOUNT and data.get('currency')==BUSINESS.currency and data.get('complete') is True and data.get('verified') is True
    inventory=data.get('inventory') or {};campaign=inventory.get('campaign') or {}
    inventory_ok=valid and inventory.get('verified') is True and campaign.get('id')==LAUNCH and campaign.get('account_id')==ACCOUNT
    windows=[]
    if valid and data.get('today'):
        today=datetime.fromisoformat(data['today']).date()
        for days in [1,3,7]:
            start=(today-timedelta(days=days)).isoformat();end=(today-timedelta(days=1)).isoformat()
            rows=[r for r in data.get('rows',[]) if r.get('campaign_id')==LAUNCH and start<=r.get('date_start','')<=end]
            spend=sum((Decimal(str(r.get('spend','0'))) for r in rows),Decimal(0));registrations=0
            for r in rows:
                actions={a['action_type']:a['value'] for a in r.get('actions',[])}
                registrations+=float(actions.get('omni_complete_registration',actions.get('complete_registration',0)))
            windows.append({'days':days,'since':start,'until':end,'spend':str(spend),'registrations_attributed':registrations,'cpl_attributed':str(spend/Decimal(str(registrations))) if registrations else None})
    blockers=[]
    if not fresh or not valid:blockers.append({'id':'fresh','title':'נדרשים נתונים עדכניים','detail':'יש להשלים קריאה מלאה מ־Meta לפני הכנת שינוי'})
    if not inventory_ok:blockers.append({'id':'inventory','title':'נדרשת קריאת ההגדרות','detail':'התקציב, המצב ואירוע האופטימיזציה חייבים להגיע מהחשבון עצמו'})
    economics=derive_economics(snapshot,now)
    # Never promote old aggregate/list evidence to unique verified registrations.
    blockers.append({'id':'measurement','title':'אימות הרשמות מול האתר','detail':'התאמה בין נרשם ייחודי, אירוע וייחוס למודעה עדיין לא הושלמה'})
    blockers.append({'id':'execution','title':'חיבור ביצוע טרם הופעל','detail':'הטוקן הנוכחי מאפשר קריאה בלבד; אין ביצוע שינויים מתוך המסך'})
    return {'account_id':ACCOUNT,'campaign_id':LAUNCH,'campaign_name':campaign.get('name','קמפיין ההרשמה להשקה'),
            'state':'blocked','live_action_allowed':False,'proposals':[],'blockers':blockers,'settings':settings,'windows':windows,
            'inventory_verified':bool(inventory_ok and fresh),'current':campaign if inventory_ok and fresh else None,
            'ads_count':len(inventory.get('ads',[])) if inventory_ok and fresh else None,
            'measurement_verified':False,'write_connected':False,'economics':economics,
            'note':'אין הצעת שינוי עד להשלמת התנאים. יעד תקציב אינו אישור לביצוע.'}


def derive_economics(snapshot,now=None):
    source=snapshot.get('sources',{}).get('shopify',{});data=source.get('data') or {}
    rows=(data.get('economics_28d') or {}).get('rows') or []
    result={'target_cpl':None,'recommended_daily_budget':None,'state':'missing_cost_data','scenarios':[],
            'method':'עלות הרשמה לאיזון = תרומה להזמנה × שיעור נרשמים שרכשו; יעד רווח נמוך מעלות האיזון',
            'missing':['שיעור רכישה של נרשמים ייחודיים','עלות משלוח בפועל ועמלות סליקה','אימות טיפול המע״מ בנתוני החנות'],
            'assumptions':{'packaging_ils':4,'vat_rate':0.18,'courier_ils_estimate':25,'processing_rate_estimate':0.02},
            'source':'Shopify aggregate sales, last 28 complete days','verified_profit':False}
    required={'orders','net_sales','shipping_charges','cost_of_goods_sold','net_sales_with_cost_recorded','net_sales_without_cost_recorded'}
    if not rows or not required.issubset(rows[0]) or any(rows[0][k] in (None,'') for k in required):return result
    if data.get('shop')!=SHOP or data.get('currency')!=BUSINESS.currency:
        result['missing'].append('זהות חנות ומטבע תואמים');return result
    try:
        age=((now or datetime.now(timezone.utc))-datetime.fromisoformat(source['last_success_at'])).total_seconds()
        if source.get('error') or not 0<=age<=720:raise ValueError('Stale economics')
        if not all(Decimal(str(rows[0][k])).is_finite() for k in required):raise ValueError('Nonfinite')
    except (ValueError,KeyError,TypeError,ArithmeticError):
        result['missing'].append('נתוני עלות עדכניים ותקינים');return result
    row=rows[0];orders=Decimal(str(row.get('orders',0)));net=Decimal(str(row.get('net_sales',0)));shipping=Decimal(str(row.get('shipping_charges',0)));cost=Decimal(str(row.get('cost_of_goods_sold',0)));missing_cost=Decimal(str(row.get('net_sales_without_cost_recorded',0)))
    result['raw']={key:row.get(key) for key in ['orders','net_sales','shipping_charges','cost_of_goods_sold','net_sales_with_cost_recorded','net_sales_without_cost_recorded']}
    result['last_success_at']=source.get('last_success_at');result['source_error']=source.get('error')
    if orders<=0 or net<=0:return result
    if missing_cost!=0:
        result['missing'].append('כיסוי עלות מוצר לכל המכירות; חלק מהמכירות ללא עלות רשומה')
        result['state']='incomplete_cost_estimate';return result
    # VAT, delivery and processing are explicit working assumptions, not audited profit.
    contribution=(net+shipping)/Decimal('1.18')/orders-cost/orders-Decimal(4)-Decimal(25)-(net+shipping)/orders*Decimal('.02')
    result.update({'state':'incomplete_cost_estimate' if missing_cost else 'scenario_estimate','estimated_contribution_per_order':str(contribution.quantize(Decimal('.01'))),
                   'net_product_sales_per_order':str((net/orders).quantize(Decimal('.01')))})
    if contribution>0:
        result['scenarios']=[{'subscriber_purchase_rate_assumed':rate,'break_even_cpl_estimate':str((contribution*Decimal(str(rate))).quantize(Decimal('.01')))} for rate in [.01,.03,.05,.10]]
    return result
