"""Fixed-account Flashy GET-only connection. No contact or messaging writes.

Paths and x-api-key authentication from official flashy/flashy-php SDK.
Regular credential may allow writes at provider; this adapter does not.
No contact-list member endpoint has been independently verified yet.
"""
import json
from pathlib import Path
import re
import urllib.request
from connections import NoRedirect
from shopify_direct import crypt

ACCOUNT='11126'
ALLOWED_PATHS=frozenset({'account','lists'})

def get(key,path):
    if path not in ALLOWED_PATHS:raise ValueError('Unverified Flashy path')
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}',key):raise ValueError('Invalid Flashy credential')
    req=urllib.request.Request('https://api.flashy.app/'+path,headers={'x-api-key':key,'Accept':'application/json'},method='GET')
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=20) as response:
            raw=response.read(1048577)
        if len(raw)>1048576:raise ValueError()
        return json.loads(raw)
    except Exception:raise ValueError('Flashy read unavailable') from None

def verified_account(response):
    data=response.get('data') if isinstance(response,dict) else None
    if not isinstance(data,dict) or str(data.get('id'))!=ACCOUNT:
        raise ValueError('Flashy account identity not verified')
    return ACCOUNT

def configure(directory,key):
    verified_account(get(key,'account'))
    path=Path(directory)/'flashy-read.dpapi';pending=path.with_suffix('.pending')
    pending.write_bytes(crypt(key.encode()));pending.replace(path)
    return {'saved':True,'account_id':ACCOUNT,'verified':True,'provider_read_only_scope':False,
            'adapter_methods':['GET'],'registration_source_connected':False,'storage':'Windows current-user DPAPI'}

def status(directory):
    path=Path(directory)/'flashy-read.dpapi'
    if not path.exists():return {'configured':False,'verified':False,'registration_source_connected':False}
    verified_account(get(crypt(path.read_bytes(),True).decode(),'account'))
    return {'configured':True,'verified':True,'account_id':ACCOUNT,'registration_source_connected':False,
            'provider_read_only_scope':False,'adapter_methods':['GET']}
