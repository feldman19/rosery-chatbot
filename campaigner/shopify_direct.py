"""ROSERY-only client-credentials reader. Windows-user encrypted secret, no writes."""
import ctypes
import json
import os
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from connections import SHOP,QUERY,NoRedirect

CLIENT_ID='2c94dca57695507f715c151b553e70ba'
class SetupError(ValueError):pass
_lock=threading.Lock()
_cache={}


class Blob(ctypes.Structure):
    _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]


def crypt(raw,decrypt=False):
    if os.name!='nt':raise ValueError('Windows user encryption required')
    buffer=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):raise ValueError('Credential encryption unavailable')
    try:return ctypes.string_at(out.data,out.size)
    finally:ctypes.windll.kernel32.LocalFree(out.data)


def request_json(url,payload,headers=None):
    request=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json',**(headers or {})})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
        raw=response.read(1048577)
    if len(raw)>1048576:raise ValueError('Response too large')
    return json.loads(raw)


def exchange(secret):
    try:d=request_json(f'https://{SHOP}/admin/oauth/access_token',{'grant_type':'client_credentials','client_id':CLIENT_ID,'client_secret':secret})
    except urllib.error.HTTPError as e:raise SetupError('token_exchange_http_'+str(e.code)) from None
    if set(d.get('scope','').split(',')) not in ({'read_reports'},{'read_reports','read_orders'}) or not d.get('access_token'):raise SetupError('unexpected_granted_scope')
    return d['access_token'],time.time()+int(d['expires_in'])


def graph(token,query,variables=None):
    d=request_json(f'https://{SHOP}/admin/api/2026-10/graphql.json',{'query':query,'variables':variables or {}},{'X-Shopify-Access-Token':token})
    if d.get('errors'):raise ValueError('Shopify query denied or unavailable')
    return d['data']


def configure(data_dir,client_id,secret):
    if client_id!=CLIENT_ID or not isinstance(secret,str) or not 20<=len(secret)<=256:raise ValueError('Invalid app credentials')
    token,expires=exchange(secret)
    try:identity=graph(token,QUERY)
    except (ValueError,OSError):raise SetupError('identity_query_unavailable') from None
    if identity['shop']['myshopifyDomain']!=SHOP:raise ValueError('Shop identity mismatch')
    scopes={r['handle'] for r in identity['currentAppInstallation']['accessScopes']}
    if scopes not in ({'read_reports'},{'read_reports','read_orders'}):raise ValueError('Unexpected app scopes')
    path=Path(data_dir)/'shopify-client.dpapi';temporary=path.with_suffix('.pending')
    temporary.write_bytes(crypt(secret.encode()));temporary.replace(path)
    with _lock:_cache[str(path)]=(token,expires)
    return {'saved':True,'shop':SHOP,'scopes':sorted(scopes),'encryption':'Windows current-user DPAPI','identity_verified':True}


def get_token(data_dir):
    path=Path(data_dir)/'shopify-client.dpapi'
    with _lock:
        cached=_cache.get(str(path))
        if cached and cached[1]>time.time()+90:return cached[0]
        secret=crypt(path.read_bytes(),True).decode()
        token,expires=exchange(secret);_cache[str(path)]=(token,expires)
        return token


def status(data_dir):
    identity=graph(get_token(data_dir),QUERY)
    if identity['shop']['myshopifyDomain']!=SHOP:raise ValueError('Shop identity mismatch')
    scopes={r['handle'] for r in identity['currentAppInstallation']['accessScopes']}
    if scopes not in ({'read_reports'},{'read_reports','read_orders'}):raise ValueError('Unexpected scope')
    return {'checked_at':datetime.now().astimezone().isoformat(),'independent_collection':False,'live_action_allowed':False,
            'shopify':{'state':'identity_verified','verified':True,'reports_scope_present':True,'analytics_verified':False},
            'meta':{'state':'missing_credential','verified':False}}


def read_reports(data_dir):
    token=get_token(data_dir)
    identity=graph(token,QUERY.replace('currencyCode', 'currencyCode ianaTimezone'))
    if identity['shop']['myshopifyDomain']!=SHOP:raise ValueError('Shop identity mismatch')
    # Fixed aggregate-only query; no customer records or user-supplied query accepted.
    # Relative dates are evaluated by Shopify in the store timezone, not Windows.
    q='FROM sales SHOW orders, net_sales, shipping_charges, sales_reversals, total_sales TIMESERIES day SINCE -7d UNTIL today'
    data=graph(token,'query($q:String!){ shopifyqlQuery(query:$q){ tableData{ columns{name dataType} rows } parseErrors } }',{'q':q})['shopifyqlQuery']
    if data.get('parseErrors') or not data.get('tableData'):raise ValueError('Report unavailable')
    days=[str(r['day']) for r in data['tableData']['rows'] if isinstance(r,dict) and r.get('day')]
    economics=None
    try:
        cost_query='FROM sales SHOW orders, net_sales, shipping_charges, cost_of_goods_sold, net_sales_with_cost_recorded, net_sales_without_cost_recorded SINCE -28d UNTIL yesterday'
        cost_data=graph(token,'query($q:String!){ shopifyqlQuery(query:$q){ tableData{ columns{name dataType} rows } parseErrors } }',{'q':cost_query})['shopifyqlQuery']
        if not cost_data.get('parseErrors') and cost_data.get('tableData'):economics=cost_data['tableData']
    except (ValueError,KeyError,TypeError,OSError):pass
    try:profit=read_profit_tables(token)
    except Exception:profit=None  # optional; never blocks the main report
    return {'verified':True,'shop':SHOP,'currency':identity['shop']['currencyCode'],'since':min(days) if days else None,'until':max(days) if days else None,
            'window':'Last 7 full days plus today so far in Shopify store timezone; dates are row coverage',
            'timezone':identity['shop'].get('ianaTimezone'),'partial':True,'partial_date':datetime.now(ZoneInfo(identity['shop']['ianaTimezone'])).date().isoformat() if identity['shop'].get('ianaTimezone') else None,
            'table':data['tableData'],'economics_28d':economics,'profit':profit,'source':'direct_shopify_api','live_action_allowed':False}


PROFIT_COLUMNS='orders, gross_sales, discounts, returns, net_sales, taxes, shipping_charges, total_sales, cost_of_goods_sold, net_sales_with_cost_recorded, net_sales_without_cost_recorded, net_items_sold'


def read_profit_tables(token):
    """Fixed aggregate ShopifyQL: totals for the last 7 and 90 full days and cost per product. No customer data."""
    query='query($q:String!){ shopifyqlQuery(query:$q){ tableData{ columns{name dataType} rows } parseErrors } }'
    def table(q):
        result=graph(token,query,{'q':q})['shopifyqlQuery']
        if result.get('parseErrors') or not result.get('tableData'):raise ValueError('Profit table unavailable')
        return result['tableData']['rows']
    windows={}
    for days in (7,90):
        rows=table(f'FROM sales SHOW {PROFIT_COLUMNS} SINCE -{days}d UNTIL yesterday')
        if len(rows)!=1 or not isinstance(rows[0],dict):raise ValueError('Profit totals shape')
        windows[str(days)]=rows[0]
    products=table('FROM sales SHOW net_sales, cost_of_goods_sold, net_sales_with_cost_recorded, net_sales_without_cost_recorded, net_items_sold '
                   'GROUP BY product_title SINCE -90d UNTIL yesterday ORDER BY net_sales DESC LIMIT 50')
    return {'windows':windows,'products_90':[r for r in products if isinstance(r,dict)],'checked_at':datetime.now(timezone.utc).isoformat()}
