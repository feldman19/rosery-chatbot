"""Durable, revision-bound preparation orchestration. No advertising writes or model calls.

Adapters are host-owned existing services. Explicit save changes preparation intake only;
import reads Drive, and review queues the existing local review loop. Receipts make
ambiguous interrupted operations non-replayable under the same idempotency key.
"""
from image_validation import MAX_IMAGE_PIXELS
from creative_selection import CreativeSelection
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import hashlib,json,re,sqlite3,threading


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


class PreparationActionError(ValueError):
    def __init__(self,message,*,status=409,code='stale_preparation',ambiguous=False):
        super().__init__(message);self.status=status;self.code=code;self.ambiguous=ambiguous


class PreparationTask:
    def __init__(self,directory,*,intake,workflow,materials,proposal,loop,strategy=None,creative=None):
        self.path=Path(directory)/'preparation-task.sqlite3'
        self.intake=intake;self.workflow=workflow;self.materials=materials;self.proposal=proposal;self.loop=loop;self.strategy=strategy;self.creative=creative
        self.lock=threading.RLock()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS receipts(key TEXT PRIMARY KEY,digest TEXT NOT NULL,state TEXT NOT NULL,response TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS tasks(binding TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def _launch(self,request):
        launch=self.workflow.report({}).get('request')
        if not request or not launch:return None
        return launch if launch.get('brief')==request.get('goal') and launch.get('drive_urls')==request.get('drive_urls') and launch.get('local_sources',[])==request.get('local_sources',[]) else None

    def submit_work(self,body):
        """Persist then dispatch the same coordinator used by the preparation screen.

        Coordinator owns consent, source validation and provider failure handling.
        This method never approves or writes advertising objects.
        """
        with self.lock:
            prior=self.intake.latest()
            saved=self.intake.create(body);projected=False;started=False;projection_failed=False
            try:
                if self.strategy:self.strategy.create({'goal':saved['goal']})
                if saved['category']=='sales' or (saved['category']=='unclear' and self._launch(prior)):
                    self.workflow.create({'brief':saved['goal'],'drive_urls':saved['drive_urls'],**{k:saved[k] for k in ('local_sources','local_image_review_consent') if k in saved}})
                    projected=True
            except Exception:projection_failed=True
            # An unsupported objective is not a failed projection. Its strategy
            # remains saved, while no sales workflow is falsely created for it.
            try:saved=self.intake.projection(saved,not projection_failed)
            except Exception:projected=False
            self._bind(saved)
            if projected and (saved['drive_urls'] or saved.get('local_sources')) and self.creative and self.intake.latest()==saved:
                status=self.creative.status()
                already_bound=(status.get('request_id'),status.get('request_revision'))==(saved['id'],saved['revision'])
                if not already_bound:started=self.creative.start(saved)
            return {'saved':True,'request':saved,'projection_saved':projected,'preparation_started_or_queued':bool(started),'collection_started_or_queued':bool(started),'live_action_allowed':False}

    def report(self):
        request=self.intake.latest();launch=self._launch(request)
        material=self.materials.status(launch) if launch else {}
        proposal=self.proposal.report(request) if request else {}
        loop=self.loop.report() if request else {};job=loop.get('job')
        if job and (job.get('request_id'),job.get('request_revision'))!=(request['id'],request['revision']):job=None
        binding=f"{request['id']}:{request['revision']}" if request else None
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT payload FROM tasks WHERE binding=?',(binding,)).fetchone() if binding else None
        task=json.loads(row[0]) if row else None
        material_job=material.get('job')
        if material_job and (not launch or (material_job.get('request_id'),material_job.get('request_revision'))!=(launch.get('id'),launch.get('revision'))):material_job=None
        bound=material.get('binding_verified') is True
        assets=material.get('assets') or {}
        source_ready=bound and assets.get('complete') is True
        proposal_ready=proposal.get('available') is True
        reasons=[]
        if not request:reasons.append('יש לשמור בקשת הכנה לפני שמתחילים')
        elif not launch:reasons.append('בקשת ההכנה אינה קשורה לתוכנית מכירות מעודכנת')
        elif not request.get('drive_urls') and not request.get('local_sources'):reasons.append('חסרים קישורים לחומרי המקור')
        elif not material.get('configured'):reasons.append('נדרש חיבור Google Drive לקריאה בלבד')
        if request and not proposal_ready:reasons.append('טרם קיימת הצעה מאומתת לבקשה הנוכחית; נדרשת הכנת תוכן וביקורת')
        state=job.get('state') if job else ('importing' if material.get('refreshing') else 'proposal_ready' if proposal_ready else 'sources_ready' if source_ready else 'blocked' if reasons else 'saved')
        if task:task.update(state=state)
        stages=[{'id':'request','title':'בקשת הכנה','state':'saved' if request else 'missing','detail':request.get('goal') if request else 'לא נשמרה בקשה'},
                {'id':'sources','title':'חומרי המקור','state':'verified' if source_ready else 'running' if material.get('refreshing') else 'missing','detail':'תוכן שנקרא אינו אישור יצירתי או אישור לפרסום'},
                {'id':'proposal','title':'הצעה לביקורת','state':'verified' if proposal_ready else 'blocked','detail':'ההצעה חייבת להיות קשורה לגרסת הבקשה ולמקורות הנוכחיים'},
                {'id':'review','title':'ביקורת ותיקון','state':job.get('state') if job else 'missing','detail':'תור ביקורת מקומי; אין חיבור אוטומטי למודל'}]
        candidates=self._candidates(request,material)
        selection=CreativeSelection(self.path.parent).current(request) if request else None
        return {'selected_sources':selection,'candidates':candidates,'task':task,'request':request,'stages':stages,'actions':{'can_select':bool(source_ready and self.creative and not proposal_ready and not getattr(self.creative,'active',None)),'can_save':True,'can_revalidate':bool(bound and not material.get('refreshing')),'can_import':bool(launch and request.get('drive_urls') and material.get('configured') and not material.get('refreshing')),'can_review':proposal_ready},'source_ready':source_ready,'proposal_ready':proposal_ready,'selection_ready':proposal_ready,'review_ready':bool(job and job.get('state')=='reviewed'),'blocked_reasons':reasons,'job':job,'material_job':material_job,'no_live_actions':True,'creative_preparation':self.creative.status() if self.creative else {'state':'unavailable'},'model_connected':False,'checked_at':datetime.now(timezone.utc).isoformat()}

    def _candidates(self,request,material):
        result={'items':[],'unsupported_items':[],'total':0,'complete':False,'binding_verified':False,'request_id':request.get('id') if request else None,'request_revision':request.get('revision') if request else None}
        if not request or material.get('binding_verified') is not True:return result
        rows=(material.get('assets') or {}).get('assets',[])
        if not isinstance(rows,list) or len(rows)>200:return result
        seen=set()
        for row in rows:
            if not isinstance(row,dict):continue
            identity=row.get('id');sha=row.get('sha256');dimensions=row.get('dimensions') or {}
            if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,150}',identity) or identity in seen:continue
            seen.add(identity)
            mime=row.get('mime_type')
            if isinstance(mime,str) and mime not in ('image/png','image/jpeg'):
                name=row.get('name') if isinstance(row.get('name'),str) else 'קובץ ללא שם'
                result['unsupported_items'].append({'id':identity,'name':name[:250],'mime':mime[:150],'reason':'ניתוח והעלאת וידאו עדיין אינם נתמכים במסלול הזה' if mime.startswith('video/') else 'המסלול להכנת מודעות תומך ב־JPG ו־PNG בלבד','publication_ready':False})
                continue
            if row.get('content_read') is not True or row.get('format_check')!='decoded' or row.get('mime_type') not in ('image/png','image/jpeg'):continue
            if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):continue
            width=dimensions.get('width');height=dimensions.get('height')
            if type(width) is not int or type(height) is not int or min(width,height)<=0 or width*height>MAX_IMAGE_PIXELS:continue
            name=row.get('name')
            if not isinstance(name,str):name='image'
            result['items'].append({'id':identity,'name':name[:250],'source_kind':row.get('source_kind','drive'),'sha256':sha,'width':width,'height':height,'mime':row['mime_type'],'media_url':'/api/preparation-task/media/'+identity+'~'+sha,'preview_url':'/api/preparation-task/media/thumb/'+identity+'~'+sha,'review_state':'not_reviewed'})
        result.update(total=len(result['items']),complete=(material.get('assets') or {}).get('complete') is True,binding_verified=True)
        return result

    def media(self,media_key):
        if not isinstance(media_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,150}~[a-f0-9]{64}',media_key):raise ValueError('Invalid source identity')
        with self.lock:
            current=self.intake.latest();launch=self._launch(current)
            if not launch:raise ValueError('Current source binding unavailable')
            material=self.materials.status(launch);candidates=self._candidates(current,material)
            identity,sha=media_key.split('~')
            row=next((r for r in candidates['items'] if r['id']==identity and r['sha256']==sha),None)
            if row is None:raise ValueError('Image is not in current bound sources')
            from image_validation import decode_image
            path=self.path.parent/'drive-assets'/(identity+'-'+sha+'.bin')
            expected_root=self.path.parent.resolve()/'drive-assets'
            if path.parent.resolve()!=expected_root:raise ValueError('Source cache location changed')
            if path.parent.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024*1024:raise ValueError('Bounded source missing')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Source bytes changed')
            decode_image(str(path.resolve()),sha)
            mime='image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None
            if mime!=row['mime']:raise ValueError('Image MIME changed')
            if self.intake.latest()!=current:raise ValueError('Preparation changed during image read')
            return raw,mime

    def thumbnail(self,media_key):
        from image_validation import thumbnail_image
        with self.lock:
            current=self.intake.latest()
            self.media(media_key)  # Exact current membership, SHA and decode before cache lookup.
            identity,sha=media_key.split('~')
            path=self.path.parent/'drive-assets'/(identity+'-'+sha+'.bin')
            raw=thumbnail_image(str(path.resolve()),sha)
            if self.intake.latest()!=current:raise ValueError('Preparation changed during thumbnail read')
            return raw,'image/jpeg'

    def act(self,body):
        if not isinstance(body,dict):raise ValueError('Object required')
        action=body.get('action');key=body.get('idempotency_key')
        if action not in ('save','start','import','review','revalidate','select') or not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):raise ValueError('Valid action and idempotency key required')
        fields={'action','idempotency_key','request_id','request_revision'}
        if action=='select':fields=fields|{'selected_sources'}
        if action=='save':fields={'action','idempotency_key','mode','brief','drive_urls','expected_request_id','expected_request_revision'}
        if action=='save' and 'local_sources' in body:fields=fields|{'local_sources','local_image_review_consent'}
        if set(body)!=fields:raise ValueError('Exact action fields required')
        digest=hashlib.sha256(encoded(body).encode()).hexdigest()
        with self.lock:
            with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT digest,state,response FROM receipts WHERE key=?',(key,)).fetchone()
                if old:
                    if old[0]!=digest:raise PreparationActionError('Idempotency key belongs to a different action',code='idempotency_conflict')
                    if old[1]=='done':return json.loads(old[2])
                    raise PreparationActionError('Earlier action outcome needs a status check; it will not be repeated',status=503,code='outcome_unknown',ambiguous=True)
                db.execute('INSERT INTO receipts VALUES(?,?,?,NULL)',(key,digest,'pending'))
            may_have_changed=False
            try:
                current=self.intake.latest()
                if action=='save':
                    if body['mode'] not in ('new','update'):raise ValueError('Explicit save mode required')
                    expected=(body['expected_request_id'],body['expected_request_revision'])
                    if expected[0] is not None and (not isinstance(expected[0],str) or type(expected[1]) is not int):raise ValueError('Current request identity required')
                    if expected[0] is None and expected[1] is not None:raise ValueError('Invalid empty binding')
                    actual=(current['id'],current['revision']) if current else (None,None)
                    if expected!=actual:raise PreparationActionError('Preparation request changed; reload')
                    local_fields={}
                    if 'local_sources' in body:
                        from local_uploads import LocalUploads
                        LocalUploads(self.path.parent,self.materials.key).resolve(body['local_sources'])
                        local_fields={k:body[k] for k in ('local_sources','local_image_review_consent')}
                    may_have_changed=True
                    request=self.intake.create({'goal':body['brief'],'drive_urls':body['drive_urls'],**local_fields},expected=expected,force_new=body['mode']=='new')
                    projected=False
                    try:
                        if self.strategy:self.strategy.create({'goal':request['goal']})
                        if request['category']=='sales' or (request['category']=='unclear' and self._launch(current)):
                            self.workflow.create({'brief':request['goal'],'drive_urls':request['drive_urls'],**{k:request[k] for k in ('local_sources','local_image_review_consent') if k in request}})
                            projected=True
                    except Exception:pass
                    self._bind(request)
                    result={'saved':True,'projection_saved':projected}
                    if projected and self.creative:self.creative.start(request)
                else:
                    if not current or (body['request_id'],body['request_revision'])!=(current['id'],current['revision']) or type(body['request_revision']) is not int:raise PreparationActionError('Preparation request changed; reload')
                    result={'started':True}
                    if action=='start' and self.creative and self.proposal.report(current).get('available') is not True:self.creative.start(current)
                    if action=='select':
                        if not self.creative:raise ValueError('Creative preparation unavailable')
                        if self.proposal.report(current).get('available') is True:raise PreparationActionError('כבר קיימת הצעה לבקשה הזאת. שמור בקשה חדשה כדי לשנות את התמונות.',code='selection_locked')
                        launch=self._launch(current)
                        material=self.materials.status(launch) if launch else {}
                        candidates=self._candidates(current,material)
                        if not candidates['binding_verified'] or not candidates['complete']:raise PreparationActionError('קריאת המקורות עדיין לא הושלמה; רענן לפני הבחירה.',code='selection_unavailable')
                        with self.creative.lock:
                            if self.creative.active or self.creative.pending:raise PreparationActionError('הכנה עדיין פועלת. המתן לסיומה לפני שינוי הבחירה.',code='selection_busy')
                            if self.intake.latest()!=current:raise PreparationActionError('Preparation request changed; reload')
                            selection=CreativeSelection(self.path.parent).save(current,body['selected_sources'],candidates['items'])
                            may_have_changed=True
                        result={'selection_saved':True,'selected_count':len(selection['items']),'preparation_started_or_queued':bool(self.creative.start(current))}
                    elif action=='import':
                        launch=self._launch(current)
                        if not launch or not current.get('drive_urls') or not self.materials.status(launch).get('configured'):raise ValueError('Current saved sources and Drive connection required')
                        if self.intake.latest()!=current:raise PreparationActionError('Preparation request changed; reload')
                        may_have_changed=True
                        result={'collection_started_or_queued':self.materials.refresh(launch)}
                    elif action=='revalidate':
                        launch=self._launch(current)
                        if not launch or self.intake.latest()!=current:raise PreparationActionError('Preparation source binding changed')
                        may_have_changed=True
                        result={'validation':self.materials.revalidate_cached(launch)}
                    elif action=='review':
                        if self.proposal.report(current).get('available') is not True:raise ValueError('Verified proposal for current request required')
                        _,_,binding=self.loop.context()
                        if (binding.get('request_id'),binding.get('request_revision'))!=(current['id'],current['revision']):raise PreparationActionError('Preparation evidence changed; reload')
                        may_have_changed=True
                        job=self.loop.start(expected_binding=binding);self.loop.start_worker();result={'review_started':True,'job':job}
                    may_have_changed=True
                    self._bind(current)
                result={**result,**self.report()}
                with closing(sqlite3.connect(self.path)) as db,db:db.execute('UPDATE receipts SET state=?,response=? WHERE key=?',('done',encoded(result),key))
                return result
            except Exception as error:
                with closing(sqlite3.connect(self.path)) as db,db:db.execute('UPDATE receipts SET state=? WHERE key=?',('blocked',key))
                if isinstance(error,PreparationActionError):raise
                if may_have_changed:raise PreparationActionError('Action outcome requires a status check; do not repeat',status=503,code='outcome_unknown',ambiguous=True) from None
                raise PreparationActionError('Preparation prerequisites could not be verified; reload status',status=409,code='preparation_rejected') from None

    def _bind(self,request):
        binding=f"{request['id']}:{request['revision']}"
        task={'id':hashlib.sha256(binding.encode()).hexdigest()[:24],'revision':request['revision'],'request_id':request['id'],'request_revision':request['revision'],'brief':request['goal'],'drive_urls':request['drive_urls'],'checked_at':datetime.now(timezone.utc).isoformat(),'state':'saved'}
        with closing(sqlite3.connect(self.path)) as db,db:db.execute('INSERT OR IGNORE INTO tasks VALUES(?,?)',(binding,encoded(task)))
