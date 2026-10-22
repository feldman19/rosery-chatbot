"""Prepared read-only Google Drive adapter. No authorization flow or remote writes.

Primary docs: developers.google.com/workspace/drive/api/reference/rest/v3/files
and developers.google.com/identity/protocols/oauth2/web-server (refresh tokens).
Only exact drive.readonly and the fixed ROSERY Google identity are accepted.
Metadata inventories do not prove creative content has been read or reviewed.
"""
from batch_limits import MAX_ADSETS, MAX_ADS, MAX_CREATIVE_IMAGES, MAX_DRIVE_SOURCES

from datetime import datetime,timezone
import json
from pathlib import Path
import re
import threading
import time
import urllib.request
from urllib.parse import urlencode,urlsplit,parse_qsl
from connections import NoRedirect
from shopify_direct import crypt
from launch_workflow import drive_url

SCOPE='https://www.googleapis.com/auth/drive.readonly'
EMAIL='inforosery@gmail.com'
OAUTH='https://oauth2.googleapis.com/token'
TOKENINFO='https://oauth2.googleapis.com/tokeninfo'
API='https://www.googleapis.com/drive/v3'
TOP_FIELDS=('id','name','mimeType','size','md5Checksum','parents','modifiedTime','resourceKey','imageMediaMetadata','videoMediaMetadata')
FIELDS='id,name,mimeType,size,md5Checksum,parents,modifiedTime,resourceKey,imageMediaMetadata(width,height,rotation),videoMediaMetadata(width,height,durationMillis)'
ID=re.compile(r'^[A-Za-z0-9_-]{10,150}$')
_cache={};_lock=threading.RLock()


def _json(operation,params=None,token=None,form=None,resource_key=None,deadline=None):
    """Logical endpoint allowlist; no caller-provided origin or arbitrary URL."""
    params=params or {};headers={'Accept':'application/json'};data=None;method='GET'
    if operation=='refresh':
        if params or token or not isinstance(form,dict) or set(form)!={'grant_type','client_id','client_secret','refresh_token'} or form.get('grant_type')!='refresh_token':raise ValueError('Invalid OAuth request')
        url=OAUTH;data=urlencode(form).encode();method='POST';headers['Content-Type']='application/x-www-form-urlencoded'
    elif operation=='tokeninfo':
        if not token or form:raise ValueError('Invalid token verification')
        url=TOKENINFO+'?'+urlencode({'access_token':token})
    elif operation in ('about','files') or (operation.startswith('files/') and ID.fullmatch(operation[6:])):
        if not token or form:raise ValueError('Read token required')
        url=API+'/'+operation+('?' + urlencode(params) if params else '');headers['Authorization']='Bearer '+token
        if resource_key:headers['X-Goog-Drive-Resource-Keys']=resource_key
    else:raise ValueError('Unapproved Drive endpoint')
    remaining=25 if deadline is None else min(25,deadline-time.monotonic())
    if remaining<=0:raise ValueError('Drive read deadline exceeded')
    req=urllib.request.Request(url,data=data,headers=headers,method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=remaining) as response:raw=response.read(2097153)
        if len(raw)>2097152:raise ValueError()
        result=json.loads(raw)
        if not isinstance(result,dict) or result.get('error'):raise ValueError()
        return result
    except Exception:raise ValueError('Google Drive read unavailable') from None


def _credentials(value):
    required={'type','client_id','client_secret','refresh_token'}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-{'token_uri'} or value.get('type')!='authorized_user':raise ValueError('Authorized-user OAuth credentials required')
    if value.get('token_uri',OAUTH)!=OAUTH:raise ValueError('Only fixed Google OAuth token endpoint accepted')
    client=value['client_id']
    if not isinstance(client,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{10,256}\.apps\.googleusercontent\.com',client):raise ValueError('Invalid OAuth client ID')
    for key in ('client_secret','refresh_token'):
        item=value[key]
        if not isinstance(item,str) or not 10<=len(item)<=4096 or any(ord(c)<33 or ord(c)>126 for c in item):raise ValueError('Invalid OAuth credential')
    return {k:value[k] for k in required}


def _refresh(credentials):
    value=_credentials(credentials)
    result=_json('refresh',form={'grant_type':'refresh_token','client_id':value['client_id'],'client_secret':value['client_secret'],'refresh_token':value['refresh_token']})
    token=result.get('access_token')
    if not isinstance(token,str) or not token or len(token)>8192 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('Invalid Google token response')
    try:expires=int(result['expires_in'])
    except (KeyError,ValueError,TypeError):raise ValueError('Token validity missing') from None
    if not 120<=expires<=86400:raise ValueError('Token validity outside supported range')
    info=_json('tokeninfo',token=token)
    if set(str(info.get('scope','')).split())!={SCOPE}:raise ValueError('Exact Drive read-only scope not verified')
    audience=info.get('aud') or info.get('audience') or info.get('issued_to')
    if audience!=value['client_id']:raise ValueError('OAuth client binding not verified')
    about=_json('about',{'fields':'user(emailAddress)'},token=token)
    user=about.get('user') or {}
    if not isinstance(user,dict) or str(user.get('emailAddress','')).lower()!=EMAIL:raise ValueError('ROSERY Drive identity not verified')
    return token,time.monotonic()+expires-90


def configure(directory,credentials):
    value=_credentials(credentials);token,expires=_refresh(value)
    path=Path(directory)/'drive-read.dpapi';pending=path.with_suffix('.pending')
    pending.write_bytes(crypt(json.dumps(value).encode()));pending.replace(path)
    with _lock:_cache[str(path.resolve())]=(token,expires)
    return {'saved':True,'verified':True,'email':EMAIL,'scopes':[SCOPE],'adapter_operations':['metadata_get','folder_metadata_list'],
            'provider_read_only_scope':True,'content_read':False,'storage':'Windows current-user DPAPI','live_action_allowed':False}


def _token(directory):
    path=Path(directory)/'drive-read.dpapi';key=str(path.resolve())
    if not path.exists():raise ValueError('Drive connection not configured')
    with _lock:
        cached=_cache.get(key)
        if cached and time.monotonic()<cached[1]:return cached[0]
        try:value=json.loads(crypt(path.read_bytes(),True))
        except Exception:raise ValueError('Drive connection not configured or unreadable') from None
        token,expires=_refresh(value);_cache[key]=(token,expires);return token


def status(directory,verify=False):
    configured=(Path(directory)/'drive-read.dpapi').exists()
    result={'configured':configured,'verified':False,'email':EMAIL if configured else None,'content_read':False,'live_action_allowed':False}
    if not verify or not configured:return result
    try:_token(directory);return {**result,'verified':True,'scopes':[SCOPE]}
    except Exception:return {**result,'error':'Drive identity, scope or connection could not be verified'}


def _source(url):
    value=drive_url(url);parsed=urlsplit(value);pairs=parse_qsl(parsed.query,keep_blank_values=True)
    if parsed.path.startswith('/drive/folders/'):identity=parsed.path.split('/')[3];kind='folder'
    elif parsed.path.startswith('/file/d/'):identity=parsed.path.split('/')[3];kind='file'
    else:identity=next(v for k,v in pairs if k=='id');kind='unknown'
    keys=[v for k,v in pairs if k=='resourcekey']
    if len(keys)>1 or (keys and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',keys[0])):raise ValueError('Invalid Drive resource key')
    return {'url':value,'id':identity,'kind':kind,'resource_header':identity+'/'+keys[0] if keys else None}


def _safe_metadata(row):
    result={k:row[k] for k in TOP_FIELDS if k in row}
    if 'resourceKey' in result and (not isinstance(result['resourceKey'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',result['resourceKey'])):raise ValueError('Invalid Drive resource key')
    for key,allowed in [('imageMediaMetadata',('width','height','rotation')),('videoMediaMetadata',('width','height','durationMillis'))]:
        if key in result:
            value=result[key]
            result[key]={k:value[k] for k in allowed if isinstance(value,dict) and k in value}
    return result


def _metadata(token,identity,resource_key=None,deadline=None):
    if not isinstance(identity,str) or not ID.fullmatch(identity):raise ValueError('Invalid Drive file ID')
    result=_json('files/'+identity,{'fields':FIELDS,'supportsAllDrives':'true'},token=token,resource_key=resource_key,deadline=deadline)
    if result.get('id')!=identity or not isinstance(result.get('mimeType'),str):raise ValueError('Drive metadata identity missing')
    return _safe_metadata(result)


def _folder(token,source,max_pages,deadline=None,max_items=None):
    if type(max_pages) is not int or not 1<=max_pages<=10:raise ValueError('Folder page bound must be 1–10')
    deadline=deadline if deadline is not None else time.monotonic()+120
    if time.monotonic()>deadline:raise ValueError('Drive read deadline exceeded')
    root=_metadata(token,source['id'],source['resource_header'],deadline)
    if root['mimeType']!='application/vnd.google-apps.folder':raise ValueError('Source is not a Drive folder')
    files=[];cursor=None;seen=set();file_ids=set();complete=False;limited=False
    for _ in range(max_pages):
        if time.monotonic()>deadline:break
        params={'q':"'"+source['id']+"' in parents and trashed = false",'pageSize':1000,'fields':'nextPageToken,incompleteSearch,files('+FIELDS+')','supportsAllDrives':'true','includeItemsFromAllDrives':'true'}
        if cursor:params['pageToken']=cursor
        result=_json('files',params,token=token,resource_key=source['resource_header'],deadline=deadline)
        rows=result.get('files')
        if not isinstance(rows,list) or len(rows)>1000:raise ValueError('Invalid Drive folder page')
        for row in rows:
            if max_items is not None and len(files)>=max_items:limited=True;break
            if not isinstance(row,dict) or not ID.fullmatch(str(row.get('id',''))) or row['id'] in file_ids or source['id'] not in (row.get('parents') or []):raise ValueError('Folder membership or duplicate identity not verified')
            file_ids.add(row['id']);files.append(_safe_metadata(row))
        if limited or result.get('incompleteSearch') is True:break
        cursor=result.get('nextPageToken')
        if not cursor:complete=True;break
        if not isinstance(cursor,str) or len(cursor)>4096 or cursor in seen:raise ValueError('Invalid or repeated Drive pagination cursor')
        seen.add(cursor)
    return {'source_url':source['url'],'source_id':source['id'],'source_metadata':root,'files':files,'complete':complete,
            'limit_reached':limited,'checked_at':datetime.now(timezone.utc).isoformat(),'metadata_read':True,'content_read':False,'recursive':False,'verified_identity':EMAIL,'live_action_allowed':False}


def list_folder(directory,folder_url,max_pages=10):
    source=_source(folder_url)
    return _folder(_token(directory),source,max_pages)


def _resource_context(inherited,identity,key):
    if key is None:return inherited
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',key):raise ValueError('Invalid provider resource key')
    pair=identity+'/'+key
    parts=[x for x in (inherited or '').split(',') if x]
    if pair not in parts:parts.append(pair)
    return ','.join(parts)


def _recursive_source(token,source,metadata,deadline,budget):
    result={'source_url':source['url'],'source_id':source['id'],'source_metadata':metadata,'files':[],
            'complete':False,'recursive':False,'metadata_read':True,'content_read':False,'verified_identity':EMAIL,
            'coverage_reasons':[],'resource_contexts':{},'folder_paths':{},'live_action_allowed':False}
    reasons=result['coverage_reasons'];files={};visited=set()
    queue=[(source['id'],0,(source['id'],),_resource_context(source['resource_header'],source['id'],metadata.get('resourceKey')))]
    while queue:
        identity,depth,path,context=queue.pop(0)
        if identity in visited:continue
        if time.monotonic()>deadline:reasons.append('deadline_reached');break
        if depth>4:reasons.append('maximum_depth_reached');continue
        if identity not in budget['cache'] and len(budget['cache'])>=50:reasons.append('maximum_folders_reached');continue
        if identity not in budget['cache'] and len(budget['entries'])>=2000:reasons.append('maximum_files_reached');continue
        visited.add(identity);result['folder_paths'][identity]=list(path)
        try:
            if identity not in budget['cache']:
                # Reserve before I/O: errors count against the folder work bound.
                budget['cache'][identity]=None
                child={'id':identity,'url':source['url'],'resource_header':context}
                budget['cache'][identity]=_folder(token,child,10,deadline,max_items=2000)
            page=budget['cache'][identity]
            if page is None:raise ValueError('Previous folder read failed')
            if not page['complete']:reasons.append('folder_listing_incomplete')
            if page.get('limit_reached'):reasons.append('maximum_files_reached')
            for row in page['files']:
                item_id=row['id']
                if item_id not in budget['entries'] and len(budget['entries'])>=2000:
                    reasons.append('maximum_files_reached');continue
                budget['entries'].add(item_id)
                if item_id in files and files[item_id]!=row:
                    reasons.append('conflicting_file_metadata');continue
                files[item_id]=row
                item_context=_resource_context(context,item_id,row.get('resourceKey'))
                if item_context:result['resource_contexts'][item_id]=item_context
                if row.get('mimeType')=='application/vnd.google-apps.folder':
                    if item_id in path:reasons.append('folder_cycle_detected');continue
                    if depth>=4:reasons.append('maximum_depth_reached');continue
                    if item_id not in visited:queue.append((item_id,depth+1,path+(item_id,),item_context))
        except Exception:
            # Provider/credential URLs are never copied into a user report.
            reasons.append('folder_read_failed')
    result['files']=list(files.values());result['coverage_reasons']=sorted(set(reasons))
    result['complete']=not reasons;result['recursive']=not reasons
    result['folders_scanned']=len(visited);result['checked_at']=datetime.now(timezone.utc).isoformat()
    return result


def request_sources(directory,urls):
    if not isinstance(urls,list) or not 1<=len(urls)<=MAX_DRIVE_SOURCES:raise ValueError('Provide 1–30 Google Drive sources')
    sources=[_source(url) for url in urls]
    token=_token(directory);results=[];deadline=time.monotonic()+120;budget={'cache':{},'entries':set()}
    for source in sources:
        if time.monotonic()>deadline:
            results.append({'source_url':source['url'],'source_id':source['id'],'files':[],'complete':False,'recursive':False,
                            'coverage_reasons':['deadline_reached'],'metadata_read':False,'content_read':False,'verified_identity':EMAIL,'live_action_allowed':False})
            continue
        try:
            metadata=_metadata(token,source['id'],source['resource_header'],deadline)
            if metadata['mimeType']=='application/vnd.google-apps.folder':results.append(_recursive_source(token,source,metadata,deadline,budget))
            elif metadata['id'] not in budget['entries'] and len(budget['entries'])>=2000:
                results.append({'source_url':source['url'],'source_id':source['id'],'files':[],'complete':False,'recursive':False,
                                'coverage_reasons':['maximum_files_reached'],'metadata_read':True,'content_read':False,'verified_identity':EMAIL,'live_action_allowed':False})
            else:
                budget['entries'].add(metadata['id'])
                context=_resource_context(source['resource_header'],metadata['id'],metadata.get('resourceKey'))
                results.append({'source_url':source['url'],'source_id':source['id'],'files':[metadata],'complete':True,'recursive':True,
                                'coverage_reasons':[],'resource_contexts':{metadata['id']:context} if context else {},'metadata_read':True,'content_read':False,'verified_identity':EMAIL,'live_action_allowed':False})
        except Exception:
            results.append({'source_url':source['url'],'source_id':source['id'],'files':[],'complete':False,'recursive':False,
                            'coverage_reasons':['source_read_failed'],'metadata_read':False,'content_read':False,'verified_identity':EMAIL,'live_action_allowed':False})
    return {'verified':True,'email':EMAIL,'scopes':[SCOPE],'sources':results,'complete':all(r['complete'] for r in results),
            'checked_at':datetime.now(timezone.utc).isoformat(),'unread_source_count':sum(not r.get('metadata_read') for r in results),
            'coverage_reasons':sorted({reason for r in results for reason in r['coverage_reasons']}),
            'bounds':{'max_depth':4,'max_folders':50,'max_files':2000,'deadline_seconds':120},
            'content_read':False,'provider_writes':0,'live_action_allowed':False}
