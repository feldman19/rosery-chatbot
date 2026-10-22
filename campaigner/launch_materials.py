"""Host-owned async Drive collection; current request only, no ad/provider writes."""
from datetime import datetime,timezone
import hashlib,hmac,json,threading,re,time
from pathlib import Path
from drive_direct import request_sources,status as drive_status
from drive_assets import read_all_assets


class LaunchMaterials:
    def __init__(self,directory,key,reader=None,asset_reader=None):
        self.directory=Path(directory);self.key=key;self.path=self.directory/'launch-materials.json'
        self.reader=reader or request_sources;self.asset_reader=asset_reader or read_all_assets
        self.lock=threading.RLock();self.running=False;self.error=None;self.pending=None;self.active=None
        self.job_path=self.directory/'launch-material-job.json'
        self.job={'state':'idle'}
        try:
            self.job=json.loads(self.job_path.read_text(encoding='utf-8'))
            if self.job.get('state') in ('running','queued'):
                self.job={**self.job,'state':'interrupted','detail':'האיסוף הופסק בעת סגירת המערכת; נדרשת קריאה מחדש','updated_at':datetime.now(timezone.utc).isoformat()}
                self.job_path.write_text(json.dumps(self.job),encoding='utf-8')
        except (OSError,ValueError):pass
        if not isinstance(key,bytes) or len(key)<32:raise ValueError('Host integrity key required')

    def _sign(self,raw):return hmac.new(self.key,raw.encode(),hashlib.sha256).hexdigest()

    def _job(self,state,request):
        self.job={'state':state,'request_id':request['id'],'request_revision':request['revision'],'updated_at':datetime.now(timezone.utc).isoformat()}
        temp=self.job_path.with_suffix('.pending');temp.write_text(json.dumps(self.job),encoding='utf-8');temp.replace(self.job_path)

    def status(self,request):
        result={'configured':bool(request and request.get('local_sources')) or drive_status(self.directory)['configured'],'refreshing':self.running,'error':self.error,'binding_verified':False,'job':dict(self.job)}
        if not request:return result
        try:
            envelope=json.loads(self.path.read_text(encoding='utf-8'));raw=envelope['payload']
            if not hmac.compare_digest(envelope['signature'],self._sign(raw)):raise ValueError()
            value=json.loads(raw)
            if (value['request_id'],value['request_revision'],value['drive_urls'])!=(request['id'],request['revision'],request['drive_urls']):return result
            if value.get('local_sources',[])!=request.get('local_sources',[]):return result
            return {**result,**value,'binding_verified':True}
        except (OSError,ValueError,KeyError,TypeError):return result

    def revalidate_cached(self,request):
        """Validate already-read image bytes locally; no provider read or visual review."""
        from image_validation import decode_image
        with self.lock:
            if self.running:raise ValueError('Source collection is running')
            before=self.path.read_bytes();envelope=json.loads(before);raw=envelope['payload']
            if not hmac.compare_digest(envelope['signature'],self._sign(raw)):raise ValueError('Material integrity failed')
            value=json.loads(raw)
            if not request or (value.get('request_id'),value.get('request_revision'),value.get('drive_urls'))!=(request.get('id'),request.get('revision'),request.get('drive_urls')):raise ValueError('Source request changed')
            assets=value.get('assets') or {};rows=assets.get('assets')
            if not isinstance(rows,list) or len(rows)>200:raise ValueError('Bounded image inventory required')
            eligible=sum(isinstance(row,dict) and row.get('content_read') is True and row.get('mime_type') in ('image/png','image/jpeg') for row in rows)
            deadline=time.monotonic()+60;decoded=failed=attempted=0
            for row in rows:
                if time.monotonic()>=deadline:break
                if not isinstance(row,dict) or row.get('content_read') is not True or row.get('mime_type') not in ('image/png','image/jpeg'):continue
                identity=row.get('id');sha=row.get('sha256');attempted+=1
                try:
                    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,150}',identity) or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError()
                    path=self.directory/'drive-assets'/(identity+'-'+sha+'.bin')
                    if path.parent.resolve()!=self.directory.resolve()/'drive-assets' or path.parent.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024*1024:raise ValueError()
                    data=path.read_bytes()
                    if hashlib.sha256(data).hexdigest()!=sha:raise ValueError()
                    actual='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else None
                    if actual!=row['mime_type']:raise ValueError()
                    dimensions=decode_image(str(path.resolve()),sha)
                    row['dimensions']={'width':dimensions['width'],'height':dimensions['height']};row['format_check']='decoded';decoded+=1
                except (OSError,ValueError,KeyError,TypeError):
                    row['format_check']='unverified';row.pop('dimensions',None);failed+=1
            summary={'attempted':attempted,'decoded':decoded,'failed':failed,'remaining':eligible-attempted,'complete':decoded==eligible,'checked_at':datetime.now(timezone.utc).isoformat(),'provider_reads':0,'visual_review':False}
            value['local_image_validation']=summary
            if self.path.read_bytes()!=before:raise ValueError('Source evidence changed during validation')
            new_raw=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
            pending=self.path.with_suffix('.validation.pending')
            pending.write_text(json.dumps({'payload':new_raw,'signature':self._sign(new_raw)},ensure_ascii=False),encoding='utf-8');pending.replace(self.path)
            return summary

    def refresh(self,request):
        # Only server-owned saved requests call this method, not arbitrary browser manifests.
        if not request or not (request.get('drive_urls') or request.get('local_sources')):return False
        request=json.loads(json.dumps(request))
        with self.lock:
            if self.running:
                if request==self.active or request==self.pending:return False
                self.pending=request;self._job('queued',request);return True
            self.running=True;self.error=None;self.active=request;self._job('running',request)
        # Freeze exact revision before any provider read; later requests cannot reuse it.
        request=json.loads(json.dumps(request))
        def work():
            try:
                local=request.get('local_sources')
                if local:
                    from local_uploads import LocalUploads
                    local_assets=LocalUploads(self.directory,self.key).resolve(local)
                    metadata={'verified':True,'complete':True,'source_kind':'device'}
                else:metadata=self.reader(self.directory,request['drive_urls'])
                if metadata.get('verified') is not True or metadata.get('complete') is not True:raise ValueError()
                assets={'complete':True,'assets':local_assets} if local else self.asset_reader(self.directory,metadata)
                value={'request_id':request['id'],'request_revision':request['revision'],'drive_urls':request['drive_urls'],
                       'verified':True,'checked_at':datetime.now(timezone.utc).isoformat(),'metadata':metadata,'assets':assets,'local_sources':request.get('local_sources',[])}
                raw=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
                if len(raw)>4000000:raise ValueError()
                pending=self.path.with_suffix('.pending');pending.write_text(json.dumps({'payload':raw,'signature':self._sign(raw)},ensure_ascii=False),encoding='utf-8');pending.replace(self.path)
                with self.lock:self._job('completed' if assets.get('complete') is True else 'partial',request)
            except Exception:
                with self.lock:
                    self.error='לא הושלמה קריאת החומרים. קישור שמור אינו אישור לתוכן שנבדק.'
                    self._job('failed',request)
            finally:
                with self.lock:
                    self.running=False;self.active=None;queued=self.pending;self.pending=None
                    if queued:self.refresh(queued)
        threading.Thread(target=work,daemon=True,name='launch-materials').start()
        return True
