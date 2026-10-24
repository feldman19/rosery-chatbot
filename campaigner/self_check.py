"""Read-only daily reliability checks. Never persist provider responses or errors.

ROSERY_PHONE_GATEWAY_HOST is the gateway's Tailscale bind address;
ROSERY_PHONE_GATEWAY_PORT defaults to 8768. Missing configuration stays unknown.
ShopifyQL uses the existing read-only GraphQL helper (HTTP POST, no mutation).
"""
import json
import math
import os
import socket
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from business import ACTIVE as BUSINESS


def israel_now():
    return datetime.now(timezone.utc).astimezone(ZoneInfo(BUSINESS.timezone))


def number(value):
    if isinstance(value, bool) or value is None:return None
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):return None


def read_meta(directory, day, settings):
    import meta_direct as meta
    if settings!=meta.QUERY_SETTINGS:raise ValueError('Unknown attribution settings')
    # Separate delivery/actions reads avoid the mixed-field response issue.
    token=meta.get_token(directory)
    rows={}
    for kind, fields in (('delivery','spend'),('actions','actions,action_values')):
        page=meta.graph(token,'act_'+meta.ACCOUNT+'/insights',{
            'level':'account','fields':'account_id,date_start,date_stop,'+fields,
            'time_range':json.dumps({'since':day,'until':day}),
            'time_increment':'1','use_unified_attribution_setting':'true','limit':'500'})
        data=page.get('data')
        if (page.get('paging') or {}).get('next') or not isinstance(data,list) or len(data)!=1:
            raise ValueError('Missing or incomplete account row')
        row=data[0]
        if row.get('account_id')!=meta.ACCOUNT or row.get('date_start')!=day or row.get('date_stop')!=day:
            raise ValueError('Account/date mismatch')
        rows[kind]=row
    row=rows['actions']
    def event(field):
        values=row.get(field)
        if not isinstance(values,list):return None
        for kind in ('offsite_conversion.fb_pixel_purchase','omni_purchase'):
            found=[number(v.get('value')) for v in values if v.get('action_type')==kind]
            if found:return found[0] if len(found)==1 else None
        return 0
    return {'spend':number(rows['delivery'].get('spend')),
            'purchases':event('actions'),'attributed_revenue':event('action_values')}


def read_shopify(directory, day):
    import shopify_direct as shop
    date.fromisoformat(day)
    q=f"FROM sales SHOW orders, total_sales TIMESERIES day SINCE {day} UNTIL {day}"
    result=shop.graph(shop.get_token(directory),
        'query($q:String!){ shopifyqlQuery(query:$q){ tableData{ columns{name dataType} rows } parseErrors } }',
        {'q':q})['shopifyqlQuery']
    if result.get('parseErrors'):raise ValueError('Query unavailable')
    rows=(result.get('tableData') or {}).get('rows')
    if not isinstance(rows,list):raise ValueError('Missing rows')
    selected=[r for r in rows if r.get('day')==day]
    if len(selected)!=1:raise ValueError('Missing or duplicate day')
    return {'store_orders':number(selected[0].get('orders')),
            'store_revenue':number(selected[0].get('total_sales'))}


def gateway_reachable():
    # The gateway binds to Tailscale, not localhost. Never read its private auth file.
    host=os.environ.get('ROSERY_PHONE_GATEWAY_HOST')
    if not host:return None
    port=int(os.environ.get('ROSERY_PHONE_GATEWAY_PORT','8768'))
    with socket.create_connection((host,port),timeout=3):return True


def brief_line(result):
    if not result:return 'בדיקה עצמית: טרם בוצעה'
    if result.get('status')=='ok':return 'בדיקה עצמית: הכל תקין'
    problems=[c['reason'] for c in result.get('checks',[]) if c['status']!='ok' and not c.get('skipped')]
    return 'בדיקה עצמית: '+('; '.join(problems) or 'חסרים נתונים')


class SelfCheck:
    def __init__(self, directory, *, meta_reader=read_meta, shopify_reader=read_shopify,
                 token_checks=None, gateway=gateway_reachable, rulebook=None, clock=israel_now):
        self.directory=Path(directory);self.path=self.directory/'self-check.json'
        self.meta_reader=meta_reader;self.shopify_reader=shopify_reader
        if token_checks is None:
            from meta_direct import status as report_status
            from meta_write_connection import status as write_status
            token_checks={'meta_report_token':report_status,'meta_write_token':write_status}
        if rulebook is None:
            from rulebook import load_rulebook
            rulebook=load_rulebook
        self.token_checks=token_checks;self.gateway=gateway;self.rulebook=rulebook;self.clock=clock
        self.lock=threading.RLock();self.started=False;self.morning_day=None

    def history(self):
        try:
            runs=json.loads(self.path.read_text(encoding='utf-8'))['runs']
            return runs[-30:] if isinstance(runs,list) else []
        except (OSError, ValueError, KeyError, TypeError):return []

    def latest(self):
        # Atomic replacement permits reads while slow provider checks run.
        runs=self.history()
        return runs[-1] if runs else {'status':'warn','checks':[], 'reason':'טרם בוצעה בדיקה עצמית'}

    def run_if_due(self, snapshot, report):
        with self.lock:
            now=self.clock();local=now.astimezone(ZoneInfo(BUSINESS.timezone))
            morning=local.hour*60+local.minute>=450 and self.morning_day!=local.date()
            if not snapshot.get('last_cycle_at') or (self.started and not morning):return None
            result=self.run(snapshot,report() if callable(report) else report,now=now)
            self.started=True
            if local.hour*60+local.minute>=450:self.morning_day=local.date()
            return result

    def run(self, snapshot, report, *, now=None):
        with self.lock:return self._run(snapshot,report,now or self.clock())

    def _run(self, snapshot, report, now):
        checks=[]
        labels={'spend':'הוצאה במטא','purchases':'רכישות במטא',
                'attributed_revenue':'הכנסה מיוחסת במטא','store_orders':'הזמנות בחנות',
                'store_revenue':'הכנסות החנות','meta':'מטא','shopify':'שופיפיי',
                'meta_report_token':'אסימון דוחות מטא','meta_write_token':'אסימון כתיבה במטא'}
        def add(identity,status,reason,**values):
            checks.append({'id':identity,'status':status,'reason':reason,**values})
        day=(now.astimezone(ZoneInfo(BUSINESS.timezone)).date()-timedelta(days=1)).isoformat()
        window=((report or {}).get('windows') or {}).get('1') or {}
        metrics=window.get('metrics') or {}
        bound=window.get('since')==day and window.get('until')==day
        sources=snapshot.get('sources') or {}
        settings=((sources.get('meta') or {}).get('data') or {}).get('query_settings')
        for source,reader,keys in (
                ('meta',lambda:self.meta_reader(self.directory,day,settings),('spend','purchases','attributed_revenue')),
                ('shopify',lambda:self.shopify_reader(self.directory,day),('store_orders','store_revenue'))):
            try:direct=reader() if bound else {}
            except Exception:direct={}
            for key in keys:
                reported=number(metrics.get(key)) if bound else None
                actual=number(direct.get(key));tolerance=0 if key in ('purchases','store_orders') else .01 if source=='meta' else 0
                missing=reported is None or actual is None
                match=not missing and abs(reported-actual)<=abs(actual)*tolerance+1e-9
                status='warn' if missing else 'ok' if match else 'fail'
                add(source+'_'+key,status,('חסר נתון להשוואה: ' if missing else 'הנתונים תואמים: ' if match else 'פער בנתונים: ')+labels[key],
                    reported=reported,direct=actual,tolerance=tolerance,date=day)
        for source in ('meta','shopify'):
            stamp=(sources.get(source) or {}).get('last_success_at');age=None
            try:age=(now-datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds()
            except (ValueError,TypeError,AttributeError):pass
            status='warn' if age is None else 'ok' if 0<=age<1800 else 'fail'
            add(source+'_freshness',status,labels[source]+': '+('קריאה עדכנית' if status=='ok' else 'חסרה קריאה עדכנית'),age_seconds=age,limit_seconds=1800)
        for name,helper in self.token_checks.items():
            try:
                connection=helper(self.directory)
                verified=connection.get('verified')
                status='ok' if verified is True else 'fail' if verified is False else 'warn'
                if connection.get('state')=='missing_credential' or connection.get('configured') is False:status='warn'
            except Exception:verified=None;status='warn'
            add(name,status,'אימות חיבור '+labels.get(name,'מטא')+': '+('תקין' if status=='ok' else 'לא אומת'),verified=verified)
        try:reachable=self.gateway()
        except Exception:reachable=None
        add('phone_gateway','ok' if reachable is True else 'warn','שער הטלפון: '+('נגיש' if reachable is True else 'לא אומתה נגישות'),reachable=reachable)
        try:
            book=self.rulebook();available=book.get('status')=='available'
            missing=str(book.get('detail','')).startswith('[Errno 2]')
            add('rulebook','ok' if available else 'warn' if missing else 'fail','ספר הכללים: '+('קשירת המקור אומתה' if available else 'קשירת המקור לא אומתה'),verified=available)
        except Exception:add('rulebook','warn','ספר הכללים: חסר מידע לאימות',verified=None)
        # Optional test evidence has no existing producer in this export.
        add('full_tests','warn','ריצת בדיקות מלאה: אין ראיה זמינה',skipped=True)
        status='fail' if any(c['status']=='fail' for c in checks) else 'warn' if any(c['status']=='warn' and not c.get('skipped') for c in checks) else 'ok'
        result={'status':status,'checked_at':now.isoformat(),'date':day,'checks':checks,'provider_writes':0,'live_action_allowed':False}
        runs=(self.history()+[result])[-30:]
        pending=self.path.with_suffix('.pending');pending.write_text(json.dumps({'runs':runs},ensure_ascii=False,allow_nan=False),encoding='utf-8');pending.replace(self.path)
        return result
