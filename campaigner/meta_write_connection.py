"""Separate encrypted Meta write credential. Never replaces reporting token.

Configuration requires explicit user permission outside this module. No mutation
or permission expansion happens here; it only verifies an already granted token.
Loading the credential does not authorize any campaign operation.
"""
from pathlib import Path
import re
import json,hashlib,hmac
from meta_direct import graph,APP_ID as READER_APP_ID,ACCOUNT
from shopify_direct import crypt
from business import ACTIVE as BUSINESS

def bind_executor_app(directory,app_id):
    """Host installation step only; never accept an app binding from browser JSON."""
    if not isinstance(app_id,str) or not app_id.isdigit() or app_id==READER_APP_ID:raise ValueError('Separate executor app required')
    directory=Path(directory);key=(directory/'signing.key').read_bytes()
    if len(key)<32:raise ValueError('Host signing key required')
    payload={'app_id':app_id,'account_id':ACCOUNT,'role':'executor'}
    raw=json.dumps(payload,sort_keys=True)
    envelope={'payload':payload,'signature':hmac.new(key,raw.encode(),hashlib.sha256).hexdigest()}
    path=directory/'meta-executor-app.json';pending=path.with_suffix('.pending');pending.write_text(json.dumps(envelope),encoding='utf-8');pending.replace(path)
    return {'bound':True,'app_id':app_id,'campaign_action_authorized':False}

def _app_id(directory):
    directory=Path(directory);envelope=json.loads((directory/'meta-executor-app.json').read_text(encoding='utf-8'))
    payload=envelope['payload'];raw=json.dumps(payload,sort_keys=True)
    key=(directory/'signing.key').read_bytes()
    if len(key)<32 or not hmac.compare_digest(envelope['signature'],hmac.new(key,raw.encode(),hashlib.sha256).hexdigest()):raise ValueError('Invalid executor binding')
    app_id=payload.get('app_id')
    if payload.get('account_id')!=ACCOUNT or payload.get('role')!='executor' or not isinstance(app_id,str) or not app_id.isdigit() or app_id==READER_APP_ID:raise ValueError('Invalid executor application')
    return app_id

def verify(token,expected_app_id):
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{30,4096}',token):raise ValueError('Invalid write credential')
    if not isinstance(expected_app_id,str) or not expected_app_id.isdigit() or expected_app_id==READER_APP_ID:raise ValueError('Separate executor binding required')
    if graph(token,'app',{'fields':'id'}).get('id')!=expected_app_id:raise ValueError('Wrong application')
    permissions=graph(token,'me/permissions').get('data') or []
    granted={p['permission'] for p in permissions if p.get('status')=='granted'}
    if not {'ads_read','ads_management'}<=granted or granted-{'ads_read','ads_management','public_profile'}:raise ValueError('Unexpected write scopes')
    account=graph(token,'act_'+ACCOUNT,{'fields':'account_id,currency'})
    if account.get('account_id')!=ACCOUNT or account.get('currency')!=BUSINESS.currency:raise ValueError('Wrong account or currency')
    return {'verified':True,'account_id':ACCOUNT,'app_id':expected_app_id,'scopes':sorted(granted),
            'campaign_action_authorized':False,'allowed_product_operations':['pause_campaign','set_campaign_daily_budget']}

def configure(directory,token):
    result=verify(token,_app_id(directory))
    path=Path(directory)/'meta-execution.dpapi';pending=path.with_suffix('.pending')
    pending.write_bytes(crypt(token.encode()));pending.replace(path)
    return {**result,'saved':True,'storage':'Windows current-user DPAPI'}

def load(directory):
    token=crypt((Path(directory)/'meta-execution.dpapi').read_bytes(),True).decode()
    verify(token,_app_id(directory))
    return token

def status(directory):
    path=Path(directory)/'meta-execution.dpapi'
    if not path.exists():return {'configured':False,'verified':False,'campaign_action_authorized':False}
    try:return {**verify(crypt(path.read_bytes(),True).decode(),_app_id(directory)),'configured':True}
    except (ValueError,KeyError,TypeError,OSError):return {'configured':True,'verified':False,'campaign_action_authorized':False}
