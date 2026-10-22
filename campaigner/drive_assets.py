"""Bounded private creative downloads from verified Drive metadata, not ad approval.

Official API: developers.google.com/workspace/drive/api/guides/manage-downloads.
No arbitrary URL, redirects, execution of downloaded content, or image editing.
The host supplies an actual authenticated inventory; browser JSON is not accepted.
"""
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlencode
from drive_direct import API, EMAIL, SCOPE, ID, _token, _metadata, _source
from connections import NoRedirect

MAX_FILE=64*1024*1024
MAX_TOTAL=256*1024*1024


def read_all_assets(directory, inventory):
    """Collect up to 200 files automatically, retaining the 20-file batch bounds."""
    if not isinstance(inventory,dict) or inventory.get('verified') is not True or inventory.get('complete') is not True or inventory.get('email')!=EMAIL or set(inventory.get('scopes',[]))!={SCOPE}:
        raise ValueError('Authenticated complete Drive inventory required')
    import copy
    target=Path(directory)/'drive-assets';target.mkdir(parents=True,exist_ok=True)
    identities=list(dict.fromkeys(item['id'] for source in inventory.get('sources',[]) for item in source.get('files',[]) if item.get('mimeType')!='application/vnd.google-apps.folder'))
    rows=[]; deadline=time.monotonic()+600; total=0
    for start in range(0,min(len(identities),200),20):
        if time.monotonic()>=deadline or total>=MAX_TOTAL:break
        selected=set(identities[start:start+20]); batch=copy.deepcopy(inventory)
        for source in batch['sources']:
            source['files']=[item for item in source['files'] if item['id'] in selected]
        report=read_assets(directory,batch,max_total=MAX_TOTAL-total,deadline=deadline)
        rows.extend(report['assets']);total+=sum(row.get('size',0) for row in report['assets'] if row.get('content_read'))
        if report['files_processed']<len(selected):break
    result={'verified_identity':EMAIL,'assets':rows,'files_total':len(identities),'files_processed':len(rows),
            'complete':len(rows)==len(identities) and all(row.get('content_read') for row in rows),
            'content_read_count':sum(row.get('content_read') is True for row in rows),
            'creative_reviewed':False,'publication_ready':False,'provider_writes':0}
    result['stop_reason']=None if result['complete'] else ('file_limit' if len(identities)>200 and len(rows)>=200 else 'time_limit' if time.monotonic()>=deadline else 'byte_limit' if total>=MAX_TOTAL else 'unread_or_failed_files')
    (Path(directory)/'drive-assets'/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def _download(token,metadata,deadline,resource_key=None,max_bytes=MAX_FILE):
    identity=metadata.get('id');mime=metadata.get('mimeType','')
    if not isinstance(identity,str) or not ID.fullmatch(identity):raise ValueError('Invalid file identity')
    native=mime=='application/vnd.google-apps.document'
    if not native and not (mime.startswith('image/') or mime.startswith('video/') or mime in ('text/plain','application/json')):
        raise ValueError('Unsupported creative file type')
    if not native:
        size=metadata.get('size')
        if not isinstance(size,(str,int)) or not str(size).isdigit() or not 0<int(size)<=MAX_FILE:raise ValueError('File size unavailable or exceeds supported bound')
    route='/files/'+identity+('/export?'+urlencode({'mimeType':'text/plain'}) if native else '?alt=media')
    remaining=min(25,deadline-time.monotonic())
    if remaining<=0 or max_bytes<=0:raise ValueError('Creative read bound exceeded')
    headers={'Authorization':'Bearer '+token}
    if resource_key:headers['X-Goog-Drive-Resource-Keys']=resource_key
    req=urllib.request.Request(API+route,headers=headers,method='GET')
    data=bytearray()
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=remaining) as response:
            while True:
                if time.monotonic()>deadline:raise ValueError()
                chunk=response.read(min(262144,max_bytes+1-len(data)))
                if not chunk:break
                data.extend(chunk)
                if len(data)>max_bytes:raise ValueError()
        if not data or (not native and len(data)!=int(metadata['size'])):raise ValueError()
        checksum=metadata.get('md5Checksum')
        if checksum and hashlib.md5(data).hexdigest()!=checksum:raise ValueError()
        return bytes(data)
    except Exception:raise ValueError('Creative download not verified') from None


def read_assets(directory,inventory,limit=20,max_total=None,deadline=None):
    if (not isinstance(inventory,dict) or inventory.get('verified') is not True or inventory.get('complete') is not True or
        inventory.get('email')!=EMAIL or set(inventory.get('scopes',[]))!={SCOPE}):raise ValueError('Authenticated complete Drive inventory required')
    if type(limit) is not int or not 1<=limit<=20:raise ValueError('Creative batch bound required')
    max_total=MAX_TOTAL if max_total is None else max_total
    if type(max_total) is not int or not 0<max_total<=MAX_TOTAL:raise ValueError('Creative byte bound required')
    directory=Path(directory);target=directory/'drive-assets';target.mkdir(parents=True,exist_ok=True)
    files={};contexts={}
    for source in inventory.get('sources',[]):
        if source.get('complete') is not True or source.get('verified_identity')!=EMAIL:raise ValueError('Source not verified')
        context=_source(source['source_url'])['resource_header'] if source.get('source_url') else None
        for item in source.get('files',[]):
            if item.get('mimeType')!='application/vnd.google-apps.folder':
                files[item['id']]=item
                own=item.get('resourceKey')
                contexts[item['id']]=(source.get('resource_contexts') or {}).get(item['id']) or ','.join(x for x in (context,item['id']+'/'+own if own else None) if x) or None
    token=_token(directory);results=[];total=0;deadline=min(deadline,time.monotonic()+120) if deadline is not None else time.monotonic()+120
    for identity,listed in list(files.items())[:limit]:
        if time.monotonic()>deadline or total>=max_total:break
        entry={'id':identity,'name':listed.get('name'),'mime_type':listed.get('mimeType'),'content_read':False,'creative_reviewed':False}
        try:
            current=_metadata(token,identity,contexts[identity],deadline)
            if any(current.get(k)!=listed.get(k) for k in ('id','mimeType','modifiedTime','md5Checksum','size')):raise ValueError('Source changed')
            if str(current.get('size','0')).isdigit() and total+int(current.get('size','0'))>max_total:raise ValueError('Batch size bound')
            data=None
            # Reuse bytes only against a current provider checksum and local hash.
            # Metadata is still read before and after; a cache hit is not creative review.
            if current.get('md5Checksum'):
                for cached in list(target.glob(identity+'-*.bin'))[:5]:
                    if cached.is_symlink() or cached.stat().st_size>min(MAX_FILE,max_total-total):continue
                    candidate=cached.read_bytes();sha=hashlib.sha256(candidate).hexdigest()
                    if cached.name==identity+'-'+sha+'.bin' and len(candidate)==int(current.get('size',-1)) and hashlib.md5(candidate).hexdigest()==current['md5Checksum']:
                        data=candidate;entry['cache_reused']=True;break
            if data is None:data=_download(token,current,deadline,contexts[identity],min(MAX_FILE,max_total-total))
            if total+len(data)>max_total:raise ValueError('Batch size bound')
            total+=len(data)
            if time.monotonic()>deadline:raise ValueError('Creative read deadline')
            after=_metadata(token,identity,contexts[identity],deadline)
            if any(after.get(k)!=current.get(k) for k in ('id','mimeType','modifiedTime','md5Checksum','size')):raise ValueError('Source changed during download')
            digest=hashlib.sha256(data).hexdigest();path=target/(identity+'-'+digest+'.bin')
            path.write_bytes(data)
            entry.update(content_read=True,sha256=digest,size=len(data),source_modified_at=current.get('modifiedTime'))
            mime=current['mimeType']
            if mime.startswith('image/'):
                try:
                    from image_validation import decode_image
                    dimensions=decode_image(str(path.resolve()),digest)
                    entry['dimensions']={'width':dimensions['width'],'height':dimensions['height']}
                    entry['format_check']='decoded'
                except Exception:entry['format_check']='unverified'
            elif mime in ('text/plain','application/json','application/vnd.google-apps.document'):
                try:entry['text_excerpt']=data.decode('utf-8')[:32000];entry['text_truncated']=len(data.decode('utf-8'))>32000
                except UnicodeError:entry['text_decoded']=False
            else:entry['audio_and_overlay_review']='not_reviewed'
        except Exception:entry['error']='לא הושלמה קריאת הקובץ; אין להסיק שהקריאייטיב מוכן'
        results.append(entry)
    report={'verified_identity':EMAIL,'assets':results,'files_total':len(files),'files_processed':len(results),
            'complete':len(results)==len(files) and all(x['content_read'] for x in results),'content_read_count':sum(x['content_read'] for x in results),
            'creative_reviewed':False,'publication_ready':False,'provider_writes':0}
    (target/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
