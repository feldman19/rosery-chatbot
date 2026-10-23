"""Durable, exact-approved ROSERY campaign operations; no import-time I/O.

proposal_loader is a HOST trust boundary: it must return LaunchProposal.report
after signature/current-request/source membership verification, plus host-only
operation_config. Never bind it to browser JSON. Config has account_id,
campaign_id,source_ref,page_id,instagram_user_id,pixel_id,adsets:[{key,name,
targeting,optimization_goal,billing_event,promoted_object,start_time,end_time,
budget:{type:'daily'|'lifetime',minor:int,approved:True},asset_ids:[...]}].
The legacy feed_original recipe uses one original image/copy per standard
object_story_spec. Story matching and extra text options are NOT assigned.

Transport calls are never retried after ambiguous POST. A crash with a persisted
inflight step becomes unknown on restart. Provider readback is not an atomic CAS
and cannot establish future delivery, UI editability, ROAS or measurement quality.
Official field references are recorded in campaign_operations_transport.py.
"""
from batch_limits import MAX_ADSETS, MAX_ADS, MAX_CREATIVE_IMAGES, MAX_DRIVE_SOURCES, MAX_UPLOAD_IMAGE_BYTES

from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import hashlib,hmac,json,re,secrets,sqlite3,threading,time
from pathlib import Path
from urllib.parse import urlsplit
from campaign_operations_transport import CampaignTransport,ProviderRejected,ACCOUNT,PARENT
from image_validation import decode_image
from launch_blueprint import build_blueprint,attach_probe,execution_workflow

PAGE='170018166205299';INSTAGRAM='17841461774110293';PIXEL='723893636602228'
ID=re.compile(r'^[0-9]{5,30}$');ASSET=re.compile(r'^[A-Za-z0-9_-]{10,150}$')

class OperationBlocked(ValueError):
    pass

class DispatchStopped(OperationBlocked):
    """A dispatch was prevented before entering the transport, not unknown."""
    pass

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

class CampaignOperations:
    def __init__(self,directory,key,proposal_loader,credential_loader=None,transport=None,clock=None,monotonic=None):
        if not isinstance(key,bytes) or len(key)<32 or not callable(proposal_loader):raise OperationBlocked('Host key and verified proposal loader required')
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);self.path=self.directory/'campaign-operations.sqlite3'
        self.key=key;self.proposal_loader=proposal_loader;self.transport=transport or CampaignTransport();self.clock=clock or (lambda:datetime.now(timezone.utc));self.lock=threading.RLock();self.monotonic=monotonic or time.monotonic
        if credential_loader is None:
            from meta_write_connection import load
            self.credentials=lambda:load(self.directory)
        else:self.credentials=credential_loader
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,payload TEXT NOT NULL,signature TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS operation_stops(id TEXT PRIMARY KEY,payload TEXT NOT NULL,signature TEXT NOT NULL)')
            for row in db.execute('SELECT id FROM operations').fetchall():
                job=self._load(db,row[0])
                if job['state']=='running' or any(s['state']=='inflight' for s in job['steps']):
                    job['state']='unknown';job['reason']='Interrupted execution; no automatic retry';self._save(db,job)
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()
    def _now(self):
        value=self.clock()
        if not isinstance(value,datetime) or value.tzinfo is None:raise OperationBlocked('Aware host clock required')
        return value.astimezone(timezone.utc)
    @contextmanager
    def _measure(self,job,phase):
        started=self.monotonic()
        try:yield
        finally:
            elapsed=max(0.0,self.monotonic()-started)
            row=job['timings']['phases'][phase]
            row['seconds']=round((row['seconds'] or 0.0)+elapsed,6)
            row['samples']+=1

    def _measured_call(self,job,phase,call,*args,**kwargs):
        with self._measure(job,phase):return call(*args,**kwargs)

    def _sign(self,identity,raw):return hmac.new(self.key,(identity+'\n'+raw).encode(),hashlib.sha256).hexdigest()
    def _save(self,db,job):
        # Every state write, including approval/enqueue/schedule, is a conditional
        # transition. A per-instance lock alone cannot stop another process from
        # completing a job after this instance read its old prepared state.
        # Older signed records start at revision zero for a compatible upgrade.
        revision=job.get('revision',0)
        if type(revision) is not int or revision<0:raise OperationBlocked('Invalid operation revision')
        existing=db.execute('SELECT id FROM operations WHERE id=?',(job['operation_id'],)).fetchone()
        if existing:
            current=self._load(db,job['operation_id'])
            if current.get('revision',0)!=revision:raise OperationBlocked('Operation changed concurrently; reload status before continuing')
        elif revision!=0:raise OperationBlocked('Operation revision has no matching record')
        job['revision']=revision+1
        raw=canonical(job);db.execute('INSERT OR REPLACE INTO operations VALUES(?,?,?)',(job['operation_id'],raw,self._sign(job['operation_id'],raw)))
    def _load(self,db,identity):
        if not isinstance(identity,str) or not re.fullmatch('[a-f0-9]{32}',identity):raise OperationBlocked('Invalid operation ID')
        row=db.execute('SELECT payload,signature FROM operations WHERE id=?',(identity,)).fetchone()
        if not row or not hmac.compare_digest(row[1],self._sign(identity,row[0])):raise OperationBlocked('Operation absent or integrity invalid')
        return json.loads(row[0])
    def _get(self,identity):
        with self.db() as db:return self._load(db,identity)
    def _put(self,job):
        with self.db() as db:
            if job.get('run_id'):
                current=self._load(db,job['operation_id'])
                if current.get('run_id')!=job['run_id'] or current['state']!='running':raise OperationBlocked('Execution ownership changed; reconciliation required')
            if job['state'] in ('queued','scheduled') or job['state']=='prepared' and job['approval_status']=='approved':self._assert_no_competing_creation(db,job)
            self._save(db,job)
    def _creation_key(self,job):
        """Effective content identity, including legacy records without selectors.

        Generated names, unrelated parent fields and execution dates do not make
        re-uploading identical approved content safe after a provider attempt.
        """
        if job.get('action')!='prepare_launch' or not job.get('source_digest'):return None
        manifest=job.get('manifest') or {};groups=manifest.get('adsets') or [];files=job.get('files') or manifest.get('files') or []
        if manifest.get('account_id')!=ACCOUNT or (manifest.get('schema_version')!=2 and manifest.get('campaign_id')!=PARENT) or not groups or not files:return None
        keys=[x.get('key') for x in groups];assets=[x.get('id') for x in files]
        if not all(isinstance(x,str) for x in keys+assets):return None
        return digest({'source_digest':job['source_digest'],'account_id':ACCOUNT,'campaign_id':manifest.get('campaign_id'),
                       'adset_keys':sorted(set(keys)),'asset_ids':sorted(set(assets)),
                       'format_mode':manifest.get('format_mode','feed_original'),'copy_variant_indices':manifest.get('copy_variant_indices',{})})
    def _assert_no_competing_creation(self,db,job):
        key=self._creation_key(job)
        if key is None:return
        for row in db.execute('SELECT id FROM operations').fetchall():
            if row[0]==job['operation_id']:continue
            other=self._load(db,row[0])
            if self._creation_key(other)!=key:continue
            attempted=any(step['state']!='pending' for step in other['steps'])
            live_authority=other['state'] in ('queued','scheduled','running') or (other['state']=='prepared' and other['approval_status']=='approved' and self._stamp(other['expires_at'])>self._now())
            if attempted or live_authority:raise OperationBlocked('Same-content work already exists; inspect or cancel the pending job before approving another')
    def _stop_requested(self,job):
        # Plain SELECT (no BEGIN IMMEDIATE) also works while the caller holds
        # its own write transaction. Pending insertion is returned explicitly
        # by cancel; subsequent reads see the durable signed signal.
        db=sqlite3.connect(self.path,timeout=10)
        try:row=db.execute('SELECT payload,signature FROM operation_stops WHERE id=?',(job['operation_id'],)).fetchone()
        finally:db.close()
        if not row:return False
        if not hmac.compare_digest(row[1],self._sign('stop:'+job['operation_id'],row[0])):raise OperationBlocked('Stop signal integrity invalid')
        signal=json.loads(row[0])
        if signal.get('operation_id')!=job['operation_id'] or signal.get('digest')!=job['digest']:raise OperationBlocked('Stop signal scope mismatch')
        return True

    def _public(self,job):
        fresh=self._stamp(job['created_at'])<=self._now()<self._stamp(job['expires_at'])
        eligible=job['approval_status']=='approved' and job['state']=='prepared' and not job['blocked_reasons'] and fresh
        uncertain=any(s['state'] in ('inflight','unknown','abandoned') for s in job['steps'])
        cancellable=job['state'] in ('prepared','approved','scheduled','queued') and all(s['state']=='pending' for s in job['steps'])
        return {k:job.get(k) for k in ('operation_id','digest','action','state','approval_status','before','after','budget','files','blocked_reasons','approval_requirements','expires_at','manifest','scheduled_at','reason','timings','created_at','historical_source_reuse')}|{'full_launch_ready':False if job['action']=='prepare_launch' else None,'completion_scope':('single_ad_five_copy_api_readback' if job.get('manifest',{}).get('schema_version')==2 else 'dedicated_feed_story_single_copy_api_readback' if job.get('manifest',{}).get('format_mode')=='dedicated_feed_story_single_copy' else 'feed_only_single_copy_api_readback') if job['action']=='prepare_launch' and job['state']=='verified' else None,'execution_ready':eligible and not job['scheduled_at'],'schedule_ready':eligible and bool(job['scheduled_at']) and self._stamp(job['scheduled_at'])>self._now(),'can_cancel':cancellable,'provider_write_attempted':any(s['state']!='pending' for s in job['steps']),'provider_write_performed':None if uncertain else any(s['state']=='verified' for s in job['steps'])}
    def _response(self,job):
        stopped=self._stop_requested(job)
        return {'operation':self._public(job)|{'stop_requested':stopped},'job':{'id':job['operation_id'],'action':job['action'],'created_at':job['created_at'],'state':job['state'],'steps':job['steps'],'reason':job.get('reason'),'timings':job.get('timings'),'stop_requested':stopped}}
    def status(self):
        with self.db() as db:jobs=[self._load(db,row[0]) for row in db.execute('SELECT id FROM operations').fetchall()]
        entities={PARENT:{'entity_type':'campaign','entity_id':PARENT,'name':'ROSERY sales campaign — fresh preview required','status':None,'observed_at':None}}
        for job in jobs:
            if isinstance(job.get('before'),dict) and job['before'].get('id'):
                raw=job['before'];kind=job['manifest'].get('entity_type','campaign');entities[raw['id']]={'entity_type':kind,'entity_id':raw['id'],'name':raw.get('name',raw['id']),'status':raw.get('status'),'observed_at':job['created_at'],'currency':'ILS'}
                descendants=raw.get('descendants') or {}
                if descendants.get('complete') is True:
                    for child in descendants.get('adsets',[]):
                        if child.get('account_id')==ACCOUNT and child.get('campaign_id')==PARENT:
                            entities[child['id']]={'entity_type':'adset','entity_id':child['id'],
                                'name':child.get('name',child['id']),'status':child.get('status'),
                                'observed_at':job['created_at'],'currency':'ILS',
                                'budget_minor':child.get('daily_budget',child.get('lifetime_budget')),
                                'requires_fresh_preview':True}
            for step in job['steps']:
                if step['state']=='verified' and step['kind'] in ('adsets','ads'):
                    raw=step['result']['verified_fields'];identity=step['result']['id'];entities[identity]={'entity_type':'adset' if step['kind']=='adsets' else 'ad','entity_id':identity,'name':raw.get('name',identity),'status':raw.get('status'),'observed_at':job.get('finished_at',job['created_at']),'currency':'ILS','budget_minor':raw.get('daily_budget',raw.get('lifetime_budget'))}
        try:
            source=self._proposal()
            if source.get('schema_version')==2:
                cached=getattr(self,'_campaign_cache',None)
                if cached is None or self.monotonic()-cached[0]>=30:
                    token=self.credentials();self._account(token)
                    cached=(self.monotonic(),self._v2_campaigns(token),self._now().isoformat());self._campaign_cache=cached
                for row in cached[1]:
                    entities[row['id']]={'entity_type':'campaign','entity_id':row['id'],'name':row.get('name',row['id']),
                                         'status':row.get('status'),'observed_at':cached[2],'currency':'ILS','requires_fresh_preview':True}
            blueprint=attach_probe(build_blueprint(source),self.directory,self.key)
            workflow=execution_workflow(blueprint,source.get('required_format'))
            workflow['owner_budget_entry']=(source.get('operation_config') or {}).get('owner_budget_entry') is True
        except Exception:
            blueprint={'execution_available':False,'input_complete':False,'issues':[{'reason':'current_signed_proposal_unavailable'}]}
            workflow=execution_workflow(blueprint)
        return {'account_id':ACCOUNT,'campaign_id':PARENT,'capabilities':{'prepare_launch':True,'set_budget':True,'pause':True,'activate':True,'schedule':True,'full_launch':False,'paired_images_single_copy':True},'full_launch_blueprint':blueprint,'launch_workflow':workflow,'entities':list(entities.values()), 'proposals':[self._response(x)['operation'] for x in jobs[-30:]],'jobs':[self._response(x)['job'] for x in jobs[-30:]],'default_creation_status':'PAUSED','placement_customization':workflow.get('request_format')=='dedicated_feed_story_single_copy','limitations':['Campaign activation requires complete exact descendant budget/status inventory and fresh readback','Cached entity states require fresh preview before changes','Paired-image single-copy creation is separate from rendered QA, editability and full-launch readiness']}
    def _proposal(self,expected_source=None):
        report=self.proposal_loader()
        if not isinstance(report,dict) or report.get('available') is not True or not isinstance(report.get('proposal'),dict):raise OperationBlocked('Current signed proposal unavailable')
        if expected_source is not None and report.get('source_binding')!=expected_source:raise OperationBlocked('Displayed proposal changed; reload and select the exact content again')
        return json.loads(canonical(report['proposal']))
    def _source(self,row):
        identity=row.get('asset_id');sha=row.get('sha256')
        if not isinstance(identity,str) or not ASSET.fullmatch(identity) or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise OperationBlocked('Exact source identity/hash required')
        path=self.directory/'drive-assets'/(identity+'-'+sha+'.bin')
        if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=MAX_UPLOAD_IMAGE_BYTES:raise OperationBlocked('Bounded cached source missing')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sha:raise OperationBlocked('Source hash changed')
        dimensions=decode_image(str(path.resolve()),sha)
        mime='image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None
        if not mime:raise OperationBlocked('PNG/JPEG source required')
        source_kind=row.get('source_kind','drive')
        if source_kind not in ('drive','device') or (identity.startswith('local_') != (source_kind=='device')):raise OperationBlocked('Source type does not match exact source identity')
        return raw,{'id':identity,'name':str(row.get('source_name') or identity)[:160],'source_kind':source_kind,'sha256':sha,'mime':mime,'size':len(raw),'dimensions':dimensions}
    def _account(self,token):
        raw=self.transport.read(token,'act_'+ACCOUNT,'account_id,currency')
        if raw.get('account_id')!=ACCOUNT or raw.get('currency')!='ILS':raise OperationBlocked('Fixed account/currency not verified')
    def _entity(self,token,kind,identity):
        if kind not in ('campaign','adset','ad') or not isinstance(identity,str) or not ID.fullmatch(identity):raise OperationBlocked('Invalid entity')
        fields='id,account_id,name,status,effective_status,daily_budget,lifetime_budget,objective' if kind=='campaign' else 'id,account_id,name,status,effective_status,campaign_id,daily_budget,lifetime_budget' if kind=='adset' else 'id,account_id,name,status,effective_status,campaign_id,adset_id,creative{id}'
        raw=self.transport.read(token,identity,fields)
        if raw.get('id')!=identity or raw.get('account_id')!=ACCOUNT:raise OperationBlocked('Entity account mismatch')
        if kind=='campaign' and identity!=PARENT or kind!='campaign' and raw.get('campaign_id')!=PARENT:raise OperationBlocked('Entity outside fixed parent campaign')
        return raw
    def _descendants(self,token):
        result=self.transport.inventory(token,PARENT)
        if not isinstance(result,dict) or result.get('complete') is not True:raise OperationBlocked('Complete descendant inventory required')
        snapshot={'complete':True}
        for edge in ('adsets','ads'):
            rows=result.get(edge)
            if not isinstance(rows,list) or len(rows)>1000:raise OperationBlocked('Bounded descendant inventory required')
            clean=[];seen=set()
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not ID.fullmatch(row['id']) or row['id'] in seen or row.get('account_id')!=ACCOUNT or row.get('campaign_id')!=PARENT or row.get('status') not in ('ACTIVE','PAUSED','ARCHIVED','DELETED') or not isinstance(row.get('effective_status'),str):raise OperationBlocked('Descendant scope/status not verified')
                seen.add(row['id']);keys=('id','account_id','campaign_id','name','status','effective_status','daily_budget','lifetime_budget') if edge=='adsets' else ('id','account_id','campaign_id','adset_id','name','status','effective_status')
                item={k:row.get(k) for k in keys}
                if edge=='adsets':
                    for key in ('daily_budget','lifetime_budget'):
                        value=item[key]
                        if value is None or isinstance(value,bool) or not str(value).isdigit():raise OperationBlocked('Descendant budget not verified')
                        else:item[key]=str(value)
                clean.append(item)
            snapshot[edge]=sorted(clean,key=lambda x:x['id'])
        sets={x['id'] for x in snapshot['adsets']}
        if any(x.get('adset_id') not in sets for x in snapshot['ads']):raise OperationBlocked('Descendant parent coverage mismatch')
        return snapshot
    def _stamp(self,value):
        if not isinstance(value,str):raise OperationBlocked('Aware schedule required')
        try:stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:raise OperationBlocked('Invalid schedule') from None
        if stamp.tzinfo is None:raise OperationBlocked('Aware schedule required')
        return stamp.astimezone(timezone.utc)
    def _step(self,key,kind,params,expected,**extra):return {'key':key,'kind':kind,'params':params,'expected':expected,'state':'pending','result':None,**extra}
    def _assert_format_supported(self,proposal):
        if 'schema_version' in proposal and proposal['schema_version'] != 1:
            from launch_proposal_schema import validate_proposal_v2
            try:validate_proposal_v2(proposal)
            except ValueError as error:raise OperationBlocked(str(error)) from None
            return
        required=proposal.get('required_format')
        if required=='dedicated_feed_story_five_copy':
            raise OperationBlocked('Full launch requires dedicated feed/story, five copy options, verified placement mapping and UI editability; feed-only/single-copy cannot fulfill or approve this requirement')
        if required not in (None,'feed_original_single_copy','dedicated_feed_story_single_copy'):
            raise OperationBlocked('Unknown required format; cannot silently narrow scope')

    def _prepare(self,body,proposal,jobid):
        self._assert_format_supported(proposal)
        if proposal.get('schema_version')==2:
            token=self.credentials();self._account(token)
            return self._prepare_v2(body,proposal,jobid,self._v2_context(token,proposal))
        config=proposal.get('operation_config') or {}
        if (config.get('account_id'),config.get('campaign_id'),config.get('page_id'),config.get('instagram_user_id'),config.get('pixel_id'))!=(ACCOUNT,PARENT,PAGE,INSTAGRAM,PIXEL) or not config.get('source_ref'):raise OperationBlocked('Verified fixed identity operation_config missing')
        placement=proposal.get('required_format')=='dedicated_feed_story_single_copy'
        entered_budget=body.get('creation_budget_minor')
        if config.get('owner_budget_entry') is True:
            if type(entered_budget) is not int or not 1<=entered_budget<=200000:raise OperationBlocked('Enter an explicit daily budget for each new paused adset')
        elif 'creation_budget_minor' in body:raise OperationBlocked('Owner budget entry is unavailable for this proposal')
        mode=body.get('format_mode','feed_original')
        if mode!=('dedicated_feed_story_single_copy' if placement else 'feed_original'):raise OperationBlocked('Explicit host format and matching selector required')
        groups=config.get('adsets');ads=proposal.get('ads')
        if not isinstance(groups,list) or not 1<=len(groups)<=MAX_ADSETS or not isinstance(ads,list) or not 1<=len(ads)<=MAX_ADS:raise OperationBlocked('Bounded adsets/ads required')
        if len({x.get('key') for x in groups})!=len(groups):raise OperationBlocked('Duplicate adset keys')
        chosen=body.get('selected_adset_keys',[x['key'] for x in groups])
        if not isinstance(chosen,list) or not 1<=len(chosen)<=MAX_ADSETS or not all(isinstance(x,str) for x in chosen) or len(set(chosen))!=len(chosen) or set(chosen)-{x['key'] for x in groups}:raise OperationBlocked('Unknown selected adset key')
        byid={x.get('asset_id'):x for x in ads}
        if len(byid)!=len(ads):raise OperationBlocked('Duplicate source ads')
        selected=body.get('selected_asset_ids')
        assignments={}
        for group in (x for x in groups if x['key'] in chosen):
            sources=group.get('asset_ids')
            if not isinstance(sources,list) or not sources or not all(isinstance(x,str) for x in sources) or len(set(sources))!=len(sources) or set(sources)-set(byid):raise OperationBlocked('Explicit source-to-adset assignment required')
            assignments[group['key']]=sources
        if 'selected_asset_ids' in body:
            if not isinstance(selected,list) or not 1<=len(selected)<=MAX_ADS or not all(isinstance(x,str) and ASSET.fullmatch(x) for x in selected) or len(set(selected))!=len(selected):raise OperationBlocked('Bounded unique source selection required')
            assigned={asset for sources in assignments.values() for asset in sources}
            if set(selected)-assigned:raise OperationBlocked('Selected source is outside the reviewed proposal or selected adsets')
            assignments={key:[x for x in sources if x in selected] for key,sources in assignments.items()}
        copy_indices=body.get('copy_variant_indices',{})
        assigned={asset for sources in assignments.values() for asset in sources}
        if not isinstance(copy_indices,dict) or len(copy_indices)>MAX_ADS or any(not isinstance(k,str) or k not in assigned or type(v) is not int or not 0<=v<5 for k,v in copy_indices.items()):raise OperationBlocked('Invalid reviewed copy selection')
        copy_indices={k:v for k,v in sorted(copy_indices.items()) if v}
        steps=[];files=[];manifest_groups=[];copy_choices=[];adcount=0
        for index,group in enumerate(x for x in groups if x['key'] in chosen):
            sources=assignments[group['key']]
            if not sources:continue  # Never create an empty adset for an unselected group.
            budget=group.get('budget') or {};amount=budget.get('minor');budgettype=budget.get('type')
            if config.get('owner_budget_entry') is True:
                amount=entered_budget;budgettype='daily';budget={'approved':True}
            if budget.get('approved') is not True or budgettype not in ('daily','lifetime') or type(amount) is not int or not 1<=amount<=10000000:raise OperationBlocked('Explicit host-approved bounded budget missing')
            unscheduled=config.get('paused_unscheduled') is True and group.get('start_time') is None and group.get('end_time') is None and budgettype=='daily'
            start=None if unscheduled else self._stamp(group.get('start_time'));end=self._stamp(group['end_time']) if group.get('end_time') else None
            if not unscheduled and (start<=self._now() or end and (end<=start or end-start>timedelta(days=31)) or budgettype=='lifetime' and end is None):raise OperationBlocked('Future start and bounded lifetime dates required')
            promoted=group.get('promoted_object')
            if promoted!={'pixel_id':PIXEL,'custom_event_type':'PURCHASE'} or group.get('optimization_goal')!='OFFSITE_CONVERSIONS' or group.get('billing_event')!='IMPRESSIONS':raise OperationBlocked('Purchase optimization config not verified')
            targeting=group.get('targeting')
            if not isinstance(targeting,dict) or (targeting.get('geo_locations') or {}).get('countries')!=['IL'] or type(targeting.get('age_min')) is not int or targeting['age_min']<18:raise OperationBlocked('Explicit IL adult targeting required')
            adcount+=len(sources)
            if adcount>MAX_ADS:raise OperationBlocked('Maximum fifteen ads')
            name=(str(group.get('name') or group['key'])[:100]+' | OPS '+jobid[:8])
            params={'name':name,'campaign_id':PARENT,'status':'PAUSED','targeting':targeting,'optimization_goal':group['optimization_goal'],'billing_event':group['billing_event'],'promoted_object':promoted,'start_time':group['start_time'],'end_time':group['end_time'],budgettype+'_budget':amount,'bid_strategy':'LOWEST_COST_WITHOUT_CAP','destination_type':'WEBSITE','is_dynamic_creative':False}
            if end is None:params.pop('end_time')
            if unscheduled:params.pop('start_time')
            setkey='set'+str(index);steps.append(self._step(setkey,'adsets',params,params));manifest_groups.append({'key':group['key'],'params':params})
            for n,assetid in enumerate(sources):
                ad=byid[assetid]
                if (ad.get('review') or {}).get('visual_checked') is not True:raise OperationBlocked('Reviewed source required')
                raw,info=self._source(ad);files.append(info);prefix=setkey+'ad'+str(n)
                # Drive IDs may be 150 characters. Keep provider filenames
                # bounded without truncating the exact source ID/hash binding.
                image_name=jobid[:8]+'-'+hashlib.sha256(assetid.encode()).hexdigest()+('.png' if info['mime']=='image/png' else '.jpg')
                steps.append(self._step(prefix+'image','image',{'asset_id':assetid,'sha256':info['sha256'],'source_kind':info['source_kind'],'name':image_name,'mime':info['mime']},{'name':image_name}))
                if placement:
                    story_source=ad.get('story')
                    if not isinstance(story_source,dict) or (story_source.get('review') or {}).get('visual_checked') is not True:raise OperationBlocked('Reviewed dedicated story required')
                    _,story_info=self._source(story_source)
                    if story_info['sha256']==info['sha256']:raise OperationBlocked('Dedicated story cannot reuse feed bytes')
                    files.append(story_info)
                    story_name=jobid[:8]+'-'+hashlib.sha256(story_info['id'].encode()).hexdigest()+('.png' if story_info['mime']=='image/png' else '.jpg')
                    steps.append(self._step(prefix+'storyimage','image',{'asset_id':story_info['id'],'sha256':story_info['sha256'],'source_kind':story_info['source_kind'],'name':story_name,'mime':story_info['mime']},{'name':story_name}))
                url=ad.get('destination_url');parsed=urlsplit(url) if isinstance(url,str) else None
                if not parsed or parsed.scheme!='https' or parsed.hostname!='roseryjewelry.com' or parsed.username or parsed.password:raise OperationBlocked('Exact ROSERY destination required')
                options=[ad.get('primary_text')]+(ad.get('primary_text_options') if isinstance(ad.get('primary_text_options'),list) else [])
                options=list(dict.fromkeys(x for x in options if isinstance(x,str) and x.strip()))[:5]
                choice=copy_indices.get(assetid,0)
                if choice>=len(options):raise OperationBlocked('Reviewed copy option is unavailable')
                primary=options[choice];copy_choices.append({'asset_id':assetid,'source_name':ad.get('source_name',assetid),'variant_index':choice,'primary_text':primary});headline=ad.get('headline');description=ad.get('description','');cta=ad.get('cta','SHOP_NOW')
                if not isinstance(primary,str) or not primary or not isinstance(headline,str) or not headline or not isinstance(description,str):raise OperationBlocked('Exact single copy required')
                if cta not in ('SHOP_NOW','LEARN_MORE','SIGN_UP'):raise OperationBlocked('Explicit supported CTA required')
                story={'page_id':PAGE,'instagram_user_id':INSTAGRAM,'link_data':{'image_hash':{'ref':prefix+'image','field':'hash'},'link':url,'message':primary,'name':headline,'description':description,'call_to_action':{'type':cta,'value':{'link':url}}}}
                creative={'name':name+' | '+str(n+1),'object_story_spec':story}
                if ad.get('url_tags'):creative['url_tags']=ad['url_tags']
                if placement:
                    from canary_plan import build_deferred_candidate
                    creative=build_deferred_candidate(dict(ad,primary_text=primary),prefix+'image',prefix+'storyimage')
                    creative['name']=name+' | '+str(n+1)
                steps.append(self._step(prefix+'creative','adcreatives',creative,creative))
                adparams={'name':name+' | '+str(ad.get('source_name') or assetid)[:80],'adset_id':{'ref':setkey,'field':'id'},'creative':{'creative_id':{'ref':prefix+'creative','field':'id'}},'status':'PAUSED'}
                steps.append(self._step(prefix,'ads',adparams,adparams))
        return steps,files,{'adsets':manifest_groups,'creation_budget_minor':entered_budget,'copy_variant_indices':copy_indices,'copy_choices':copy_choices,'format_mode':mode,'story_images_assigned':placement,'additional_text_options_assigned':False,'ui_edit_save_reopen_verified':False,'editable_recipe':'UI editability requires separate manual verification','old_adsets_changed':False,'limitations':config.get('limitations',[])}

    def _video_source(self,row):
        identity=row.get('asset_id');sha=row.get('sha256')
        if not isinstance(identity,str) or not ASSET.fullmatch(identity) or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise OperationBlocked('Video source identity/hash required')
        path=self.directory/'drive-assets'/(identity+'-'+sha+'.bin')
        if path.is_symlink() or not path.is_file() or not 12<=path.stat().st_size<=64*1024*1024:raise OperationBlocked('Bounded cached video missing')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sha or raw[4:8]!=b'ftyp':raise OperationBlocked('Video hash/container mismatch')
        return raw,{'id':identity,'sha256':sha,'size':len(raw),'mime':'video/quicktime' if raw[8:12]==b'qt  ' else 'video/mp4','source_kind':row.get('source_kind','drive')}

    def _v2_context(self,token,proposal,check_time=True):
        from launch_proposal_schema import validate_proposal_v2
        from rulebook import load_rulebook
        try:validate_proposal_v2(proposal)
        except ValueError as error:raise OperationBlocked(str(error)) from None
        if check_time and not self._stamp(proposal['prepared_at'])<=self._now()<self._stamp(proposal['expires_at']):raise OperationBlocked('Proposal expired or future dated')
        if proposal['blockers'] or proposal['owner_questions']:raise OperationBlocked('Unresolved proposal blockers/questions')
        book=load_rulebook()
        if book.get('status')!='available' or digest(book)!=proposal['rules']['rulebook_sha256']:raise OperationBlocked('Verified rulebook changed or unavailable')
        required={'full_copy_set','ads_per_adset','new_creatives_new_dated_adset','cta_shop_now','no_carousel','offer_in_headline'}
        cited=set(proposal['rules']['citations'])
        if not required<=cited or not cited<=set(book['rules']):raise OperationBlocked('Required rule not cited or unavailable')
        if book['rules']['full_copy_set'].get('params')!={'texts':5,'headlines':5} or book['rules']['cta_shop_now'].get('params')!={'api_value':'SHOP_NOW'}:raise OperationBlocked('Unsupported copy/CTA rule condition')
        listing=self._v2_campaigns(token)
        campaign=proposal['strategy']['campaign_id']
        if campaign not in {row['id'] for row in listing}:raise OperationBlocked('Chosen campaign outside verified account list')
        parent=self.transport.read(token,campaign,'id,account_id,name,status,effective_status,objective,daily_budget,lifetime_budget')
        if parent.get('id')!=campaign or parent.get('account_id')!=ACCOUNT:raise OperationBlocked('Chosen campaign scope mismatch')
        inventory=self.transport.inventory_v2(token,campaign)
        if not isinstance(inventory,dict) or inventory.get('complete') is not True:raise OperationBlocked('Complete live inventory required')
        for edge in ('adsets','ads'):
            rows=inventory.get(edge)
            if not isinstance(rows,list) or len(rows)>1000:raise OperationBlocked('Bounded live inventory required')
            seen=set()
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not ID.fullmatch(row['id']) or row['id'] in seen or row.get('account_id')!=ACCOUNT or row.get('campaign_id')!=campaign or row.get('status') not in ('ACTIVE','PAUSED','ARCHIVED','DELETED'):raise OperationBlocked('Live inventory scope/status mismatch')
                seen.add(row['id'])
            inventory[edge]=sorted(rows,key=lambda row:row['id'])
        sets={row['id'] for row in inventory['adsets']}
        if any(row.get('adset_id') not in sets for row in inventory['ads']):raise OperationBlocked('Live inventory parent coverage mismatch')
        return dict(parent,launch_inventory=inventory,rulebook=book)

    def _execution_context(self,context,proposal,selection):
        chosen=selection.get('selected_adset_keys',[g['key'] for g in proposal['adsets']])
        targets={g['target']['adset_id'] for g in proposal['adsets'] if g['key'] in chosen and g['target']['mode']=='existing'}
        clean={k:v for k,v in context.items() if k not in ('effective_status','launch_inventory')}
        clean['launch_inventory']={edge:[{k:v for k,v in row.items() if k!='effective_status'} for row in context['launch_inventory'][edge] if (row['id'] if edge=='adsets' else row['adset_id']) in targets] for edge in ('adsets','ads')}
        return clean

    def _v2_campaigns(self,token):
        listing=self.transport.campaigns(token)
        if not isinstance(listing,dict) or listing.get('complete') is not True or not isinstance(listing.get('data'),list) or not 1<=len(listing['data'])<=1000:raise OperationBlocked('Complete verified campaign list required')
        ids=[]
        for row in listing['data']:
            if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not ID.fullmatch(row['id']) or row.get('account_id')!=ACCOUNT:raise OperationBlocked('Campaign list scope mismatch')
            ids.append(row['id'])
        if len(set(ids))!=len(ids):raise OperationBlocked('Duplicate campaign inventory')
        return sorted(listing['data'],key=lambda row:row['id'])

    def _prepare_v2(self,body,proposal,jobid,context):
        # Only explicit selectors; no v1 single-copy narrowing, scheduling or overrides.
        if set(body)-{'action','expected_source','selected_adset_keys','selected_asset_ids','format_mode','owner_confirmations'} or body.get('format_mode','single_ad_flexible')!='single_ad_flexible':raise OperationBlocked('Unsupported v2 selector/override')
        confirmations=body.get('owner_confirmations',[])
        if not isinstance(confirmations,list) or len(confirmations)>5 or any(not isinstance(r,dict) or set(r)!={'adset_id','same_angle'} or not isinstance(r['adset_id'],str) or not ID.fullmatch(r['adset_id']) or r['same_angle'] is not True for r in confirmations) or len({r['adset_id'] for r in confirmations})!=len(confirmations):raise OperationBlocked('Explicit unique owner confirmations required')
        groups=proposal['adsets'];ads=proposal['ads'];campaign=proposal['strategy']['campaign_id']
        chosen=body.get('selected_adset_keys',[g['key'] for g in groups])
        selected=body.get('selected_asset_ids',[a['asset_id'] for a in ads if a['adset_key'] in chosen])
        for values,known in ((chosen,{g['key'] for g in groups}),(selected,{a['asset_id'] for a in ads if a['adset_key'] in chosen})):
            if not isinstance(values,list) or not values or any(not isinstance(x,str) for x in values) or len(set(values))!=len(values) or not set(values)<=known:raise OperationBlocked('Unknown or duplicate v2 selection')
        recipe=proposal.get('creation_recipe')
        if not isinstance(recipe,dict) or set(recipe)!={'adset_fields','creative_fields','optimization_type'}:raise OperationBlocked('Explicit creation_recipe required; live PAUSED canary approval pending')
        if set(recipe['adset_fields'])-{'is_dynamic_creative','destination_type','bid_strategy'} or set(recipe['creative_fields'])-{'degrees_of_freedom_spec'}:raise OperationBlocked('Unsupported recipe fields')
        book=context['rulebook'];count_rule=book['rules']['ads_per_adset']['params']
        if set(count_rule)!={'min','max'} or any(type(count_rule[k]) is not int for k in ('min','max')) or not 1<=count_rule['min']<=count_rule['max']<=15:raise OperationBlocked('Unsupported ad count rule')
        steps=[];files=[];manifest_groups=[];copy_choices=[]
        for index,group in enumerate(g for g in groups if g['key'] in chosen):
            members=[a for a in ads if a['adset_key']==group['key'] and a['asset_id'] in selected]
            if not members:raise OperationBlocked('Empty selected adset')
            target=group['target'];setkey='set'+str(index)
            if target['mode']=='existing':
                rule=book['rules'].get('existing_adset_reuse')
                params=rule.get('params') if rule else None
                # Supported declarative predicate only. Unknown policy is never guessed.
                if not isinstance(params,dict) or set(params)!={'max_active_ads','same_product','same_angle'} or type(params['max_active_ads']) is not int or not 1<=params['max_active_ads']<=15 or params['same_product'] is not True or params['same_angle'] is not True:raise OperationBlocked('existing_adset_reuse: missing or unsupported rule condition')
                matches=[r for r in context['launch_inventory']['adsets'] if r['id']==target['adset_id']]
                if len(matches)!=1 or matches[0]['status'] not in ('ACTIVE','PAUSED'):raise OperationBlocked('Existing adset unavailable')
                live=matches[0]
                from launch_strategy import product_of
                product=product_of(live.get('name'),*[r.get('name') for r in context['launch_inventory']['ads'] if r['adset_id']==live['id']])
                confirmations=body.get('owner_confirmations',[])
                if product!=group['product']:raise OperationBlocked('Existing product unknown or mismatched')
                if {'adset_id':live['id'],'same_angle':True} not in confirmations:raise OperationBlocked('Explicit owner same-angle confirmation required')
                existing=[r for r in context['launch_inventory']['ads'] if r['adset_id']==live['id']]
                if sum(r['status']=='ACTIVE' for r in existing)>=params['max_active_ads']:raise OperationBlocked('Existing reuse active count condition failed')
                total=sum(r['status'] not in ('ARCHIVED','DELETED') for r in existing)+len(members)
                setref=live['id'];setparams={'existing_adset_id':live['id']}
            else:
                total=len(members)
                if any(str(context.get(k,'0'))!='0' for k in ('daily_budget','lifetime_budget')):raise OperationBlocked('Campaign budget conflicts with adset budget')
                name=group['label']+' | '+self._stamp(proposal['prepared_at']).date().isoformat()+' | OPS '+jobid[:8]
                setparams={'name':name,'campaign_id':campaign,'status':'PAUSED','daily_budget':group['budget_minor'],
                           'targeting':{'geo_locations':{'countries':['IL']},'age_min':18,'publisher_platforms':['facebook','instagram'],'facebook_positions':['feed'],'instagram_positions':['stream']},
                           'optimization_goal':'OFFSITE_CONVERSIONS','billing_event':'IMPRESSIONS','promoted_object':{'pixel_id':PIXEL,'custom_event_type':'PURCHASE'}}
                setparams.update(recipe['adset_fields'])
                steps.append(self._step(setkey,'adsets',setparams,setparams));setref={'ref':setkey,'field':'id'}
            if not count_rule['min']<=total<=count_rule['max']:raise OperationBlocked('ads_per_adset: live plus proposed count outside rule')
            manifest_groups.append({'key':group['key'],'mode':target['mode'],'params':setparams})
            for n,ad in enumerate(members):
                prefix=setkey+'ad'+str(n);kind=ad['media_kind']
                _,info=self._video_source(ad) if kind=='video' else self._source(ad)
                files.append(info)
                media={'asset_id':ad['asset_id'],'sha256':ad['sha256'],'source_kind':ad['source_kind'],'name':prefix+'-'+jobid[:8]+('.mov' if info['mime']=='video/quicktime' else '.mp4' if kind=='video' else '.png' if info['mime']=='image/png' else '.jpg'),'mime':info['mime']}
                steps.append(self._step(prefix+'media',kind,media,{}))
                feed={'bodies':[{'text':x} for x in ad['primary_text_options']], 'titles':[{'text':x} for x in ad['headline_options']],
                      'descriptions':[{'text':ad['description']}], 'link_urls':[{'website_url':ad['destination_url']}], 'call_to_action_types':['SHOP_NOW'], 'ad_formats':['SINGLE_VIDEO' if kind=='video' else 'SINGLE_IMAGE']}
                if kind=='image':feed['images']=[{'hash':{'ref':prefix+'media','field':'hash'}}]
                else:
                    thumbnail={'asset_id':ad['video']['thumbnail_asset_id'],'sha256':ad['video']['thumbnail_sha256'],'source_kind':'device' if ad['video']['thumbnail_asset_id'].startswith('local_') else 'drive'}
                    _,thumbinfo=self._source(thumbnail);files.append(thumbinfo)
                    thumbparams=dict(thumbnail,name=prefix+'-thumbnail'+('.png' if thumbinfo['mime']=='image/png' else '.jpg'),mime=thumbinfo['mime'])
                    steps.append(self._step(prefix+'thumbnail','image',thumbparams,{}))
                    feed['videos']=[{'video_id':{'ref':prefix+'media','field':'id'},'thumbnail_hash':{'ref':prefix+'thumbnail','field':'hash'}}]
                creative={'name':prefix+' | OPS '+jobid[:8], 'object_story_spec':{'page_id':PAGE,'instagram_user_id':INSTAGRAM}, 'asset_feed_spec':feed,'url_tags':ad['url_tags']}
                if proposal.get('schema_version')==2:
                    feed['optimization_type']=recipe['optimization_type'];creative.update(recipe['creative_fields'])
                steps.append(self._step(prefix+'creative','adcreatives',creative,creative))
                adparams={'name':ad['source_name']+' | OPS '+jobid[:8],'adset_id':setref,'creative':{'creative_id':{'ref':prefix+'creative','field':'id'}},'status':'PAUSED'}
                steps.append(self._step(prefix,'ads',adparams,adparams))
                copy_choices.append({'asset_id':ad['asset_id'],'primary_texts':ad['primary_text_options'],'headlines':ad['headline_options']})
        return steps,files,{'schema_version':2,'creation_recipe':recipe,'owner_confirmations':confirmations,'adsets':manifest_groups,'selection':body,'copy_choices':copy_choices,'format_mode':'single_ad_flexible','additional_text_options_assigned':True,'old_adsets_changed':False,'live_context_digest':digest(self._execution_context(context,proposal,body)),'proposal_digest':digest(proposal)}

    def preview(self,body):
        if not isinstance(body,dict) or set(body)-{'action','expected_source','selected_adset_keys','selected_asset_ids','copy_variant_indices','format_mode','creation_budget_minor','entity_type','entity_id','budget_minor','budget_type','scheduled_at','owner_confirmations'}:raise OperationBlocked('Unsupported preview fields')
        action=body.get('action')
        if action not in ('prepare_launch','set_budget','pause','activate'):raise OperationBlocked('Unsupported action')
        if action!='prepare_launch' and any(key in body for key in ('expected_source','selected_adset_keys','selected_asset_ids','copy_variant_indices','format_mode','creation_budget_minor')):raise OperationBlocked('Creative selection is only supported for preparation')
        if action=='prepare_launch':
            expected=body.get('expected_source')
            if not isinstance(expected,dict) or set(expected)!={'request_id','request_revision','proposal_hash'} or not isinstance(expected['request_id'],str) or not 1<=len(expected['request_id'])<=150 or type(expected['request_revision']) is not int or expected['request_revision']<1 or not isinstance(expected['proposal_hash'],str) or not re.fullmatch('[a-f0-9]{64}',expected['proposal_hash']):raise OperationBlocked('Exact displayed proposal binding required')
        body=dict(body)
        # Selector ordering does not change intent or authorize duplicate work.
        for field in ('selected_adset_keys','selected_asset_ids'):
            if isinstance(body.get(field),list) and all(isinstance(x,str) for x in body[field]):body[field]=sorted(body[field])
        now=self._now();identity=secrets.token_hex(16);blocked=[];proposal=None;steps=[];files=[];manifest={};before=None;after=None;budget=None
        source=None
        try:
            # Reject a stale displayed choice locally before loading credentials
            # or spending a network round trip on account verification.
            if action=='prepare_launch':
                proposal=self._proposal(body['expected_source']);source=digest(proposal)
                if proposal.get('required_format'):
                    manifest['required_format']=proposal['required_format']
                    manifest['format_requirements']=build_blueprint(proposal)
                self._assert_format_supported(proposal)
            token=self.credentials();self._account(token)
            if action=='prepare_launch':
                before=self._v2_context(token,proposal) if proposal.get('schema_version')==2 else self._entity(token,'campaign',PARENT)
                if before.get('status') not in ('ACTIVE','PAUSED') or before.get('objective') not in ('OUTCOME_SALES','SALES','PRODUCT_CATALOG_SALES'):raise OperationBlocked('Active or paused parent sales campaign required; new children remain paused')
                steps,files,manifest=(self._prepare_v2(body,proposal,identity,before) if proposal.get('schema_version')==2 else self._prepare(body,proposal,identity));after={'new_adsets':'PAUSED','new_ads':'PAUSED','parent_unchanged':True};budget=[x['params'].get('daily_budget',x['params'].get('lifetime_budget')) for x in manifest['adsets']]
                manifest['source_binding']=json.loads(canonical(body['expected_source']))
            else:
                kind=body.get('entity_type');entity=body.get('entity_id');before=self._entity(token,kind,entity)
                if before.get('status') not in ('ACTIVE','PAUSED'):raise OperationBlocked('Current mutable entity status not verified')
                if action=='activate' and kind=='campaign':before['descendants']=self._descendants(token)
                if action=='set_budget':
                    amount=body.get('budget_minor');btype=body.get('budget_type','daily')
                    if kind not in ('campaign','adset') or type(amount) is not int or not 1<=amount<=10000000 or btype not in ('daily','lifetime'):raise OperationBlocked('Explicit bounded budget required')
                    opposite='lifetime_budget' if btype=='daily' else 'daily_budget'
                    if opposite not in before or str(before.get(opposite))!='0':raise OperationBlocked('Budget type conversion or missing budget coverage not supported')
                    params={btype+'_budget':amount};budget={'type':btype,'minor':amount,'currency':'ILS'}
                else:params={'status':'PAUSED' if action=='pause' else 'ACTIVE'}
                after={**before,**params};manifest={'entity_type':kind,'entity_id':entity,'before':before,'changes':params};steps=[self._step('mutation','update',params,params,node=entity,entity_type=kind)]
        except OperationBlocked as error:
            blocked.append(str(error))
        except Exception:
            # External exceptions may contain credentials/provider bodies.
            blocked.append('Operation prerequisites could not be verified; inspect host identity/config, source review, budget approval and future dates')
        scheduled=None
        if body.get('scheduled_at') is not None:
            at=self._stamp(body['scheduled_at'])
            if not now<at<=now+timedelta(days=7):raise OperationBlocked('Schedule must be within seven days')
            if action=='prepare_launch' and manifest.get('adsets') and any(at>=self._stamp(x['params']['start_time']) for x in manifest['adsets']):raise OperationBlocked('Paused preparation must be scheduled before every adset start')
            scheduled=at.isoformat()
        campaign=(before or {}).get('id') if proposal and proposal.get('schema_version')==2 else PARENT
        manifest.update(account_id=ACCOUNT,campaign_id=campaign,action=action,source_digest=source,files=files,steps=steps,scheduled_at=scheduled)
        # Deduplicate semantic intent BEFORE generated names/IDs, even after success.
        intent=digest({'body':body,'source':source,'before':before})
        effective=self._creation_key({'action':action,'source_digest':source,'manifest':manifest,'files':files})
        with self.db() as db:
            priors=[self._load(db,row[0]) for row in db.execute('SELECT id FROM operations ORDER BY id').fetchall()]
            matching=[prior for prior in priors if effective is not None and self._creation_key(prior)==effective]
            # Consider ALL records before returning an exact pending preview.
            # SQLite row order must not let a pending alternative hide an
            # attempted or already authorized same-content job.
            for prior in matching:
                if any(s['state']!='pending' for s in prior['steps']):return self._response(prior)
            for prior in matching:
                active=prior['state'] in ('queued','scheduled','running') or (prior['state']=='prepared' and prior['approval_status']=='approved' and self._stamp(prior['expires_at'])>now)
                if active:return self._response(prior)
            for prior in matching:
                if prior['state']=='prepared' and prior['scheduled_at']==scheduled and self._stamp(prior['expires_at'])>now:return self._response(prior)
            for prior in priors:
                if prior['state']=='cancelled':continue
                if prior['intent']==intent and (self._stamp(prior['expires_at'])>now or action=='prepare_launch' and any(s['state']!='pending' for s in prior['steps'])):return self._response(prior)
            job={'operation_id':identity,'digest':digest(manifest),'intent':intent,'action':action,'state':'blocked' if blocked else 'prepared','approval_status':'pending','before':before,'after':after,'budget':budget,'files':files,'manifest':manifest,'steps':steps,'blocked_reasons':blocked,'approval_requirements':['exact_manifest','media_transfer'] if action=='prepare_launch' else ['exact_manifest'],'created_at':now.isoformat(),'expires_at':(now+timedelta(days=7) if scheduled else now+timedelta(minutes=15)).isoformat(),'scheduled_at':scheduled,'source_digest':source,'reason':None}
            if action=='prepare_launch':
                from creative_reuse import historical_source_reuse
                job['historical_source_reuse']=historical_source_reuse(priors,files,ACCOUNT,manifest.get('campaign_id',PARENT))
            self._save(db,job)
        return self._response(job)

    def _valid(self,job,check_time=True):
        if job['blocked_reasons']:raise OperationBlocked('Hard prerequisites remain blocked')
        now=self._now()
        if check_time and not self._stamp(job['created_at'])<=now<self._stamp(job['expires_at']):raise OperationBlocked('Operation expired')
        if digest(job['manifest'])!=job['digest']:raise OperationBlocked('Manifest changed')
        if job['source_digest'] and digest(self._proposal())!=job['source_digest']:raise OperationBlocked('Proposal or request revision changed')
        if job['manifest'].get('schema_version')==2:
            from rulebook import load_rulebook
            proposal=self._proposal()
            if check_time and not job.get('approved_at') and not self._stamp(proposal['prepared_at'])<=now<self._stamp(proposal['expires_at']):raise OperationBlocked('V2 proposal expired')
            if digest(load_rulebook())!=proposal['rules']['rulebook_sha256']:raise OperationBlocked('V2 rulebook changed')

    def approve(self,operation_id,digest_value):
        with self.lock:
            job=self._get(operation_id);self._valid(job)
            if not isinstance(digest_value,str) or not hmac.compare_digest(job['digest'],digest_value):raise OperationBlocked('Exact displayed manifest digest required')
            if job['state'] not in ('prepared','scheduled') or job['approval_status']!='pending':raise OperationBlocked('Operation cannot be reapproved')
            job['approval_status']='approved';job['approved_at']=self._now().isoformat()
            if job['manifest'].get('schema_version')==2:job['expires_at']=(self._now()+timedelta(minutes=15)).isoformat()
            self._put(job)
            return self._response(job)

    def cancel(self,operation_id,digest_value):
        """Revoke pending work, or durably request no further running dispatch.

        Expired/source-obsolete proposals can still be revoked. No provider
        credential or network access is needed, and history is preserved.
        """
        # Do not wait on execute's instance lock or mutate its revision while
        # provider I/O is in flight. The separate signed signal is durable.
        with self.db() as db:
            job=self._load(db,operation_id)
            if (not isinstance(digest_value,str) or not hmac.compare_digest(job['digest'],digest_value)
                    or digest(job['manifest'])!=job['digest']):raise OperationBlocked('Exact displayed manifest digest required')
            if job['state']=='cancelled':return self._response(job)
            if job['state']=='running':
                raw=canonical({'operation_id':operation_id,'digest':digest_value,'requested_at':self._now().isoformat()})
                db.execute('INSERT OR IGNORE INTO operation_stops VALUES(?,?,?)',(operation_id,raw,self._sign('stop:'+operation_id,raw)))
                response=self._response(job)
                response['operation']['stop_requested']=True;response['job']['stop_requested']=True
                return response|{'stop_requested':True,'stop_scope':'Prevent subsequent dispatches; already dispatched requests still require readback; no rollback'}
            if job['state'] not in ('prepared','approved','scheduled','queued') or any(s['state']!='pending' for s in job['steps']):raise OperationBlocked('Only unattempted pending work can be cancelled; inspect execution status')
            job['state']='cancelled';job['approval_status']='revoked';job['cancelled_at']=self._now().isoformat()
            job['reason']='Cancelled before any provider attempt; no provider change was made'
            self._save(db,job)
            return self._response(job)

    def _submit(self,job,call,*args):
        """Last guard before each write/whole batch; readbacks remain allowed.

        A batch is one dispatch: a stop arriving after this guard cannot recall
        items already handed to transport. It applies to the next dispatch.
        """
        try:
            self._valid(job,check_time=job['manifest'].get('schema_version')!=2)
            if getattr(self,'stop_event',None) is not None and self.stop_event.is_set():raise OperationBlocked('Worker stop requested')
            with self.db() as db:
                row=db.execute('SELECT payload,signature FROM operation_stops WHERE id=?',(job['operation_id'],)).fetchone()
                if row:
                    if not hmac.compare_digest(row[1],self._sign('stop:'+job['operation_id'],row[0])):raise OperationBlocked('Stop signal integrity invalid')
                    signal=json.loads(row[0])
                    if signal.get('digest')!=job['digest']:raise OperationBlocked('Stop signal scope mismatch')
                    raise OperationBlocked('Owner requested stop')
        except Exception:
            raise DispatchStopped('Remaining dispatch stopped: approval expired/changed, stop requested or validation unavailable') from None
        return self._measured_call(job,'submission',call,*args)

    def _resolve(self,value,steps):
        if isinstance(value,dict):
            if set(value)=={'ref','field'}:
                step=next(x for x in steps if x['key']==value['ref'])
                if step['state']!='verified' or value['field'] not in step['result']:raise OperationBlocked('Unverified step dependency')
                return step['result'][value['field']]
            return {k:self._resolve(v,steps) for k,v in value.items()}
        if isinstance(value,list):return [self._resolve(x,steps) for x in value]
        return value

    def _match(self,actual,expected,schema_version=1):
        if isinstance(expected,dict):
            if not isinstance(actual,dict):return False
            for k,v in expected.items():
                if k not in actual:return False
                if k in ('start_time','end_time'):
                    try:
                        if self._stamp(actual[k])!=self._stamp(v):return False
                    except ValueError:return False
                elif not self._match(actual[k],v,schema_version):return False
            return True
        if isinstance(expected,list):
            if schema_version!=2:return actual==expected
            return isinstance(actual,list) and len(actual)==len(expected) and all(self._match(a,e,schema_version) for a,e in zip(actual,expected))
        if type(expected) is int:return not isinstance(actual,bool) and str(actual)==str(expected)
        return actual==expected

    def _uploaded_image_receipt(self,sha,byte_count,current_step):
        """Reuse signed step receipts, not names, caller hashes or a second cache."""
        candidate=None
        with self.db() as db:
            for row in db.execute('SELECT id FROM operations ORDER BY id').fetchall():
                prior=self._load(db,row[0])
                if prior.get('manifest',{}).get('account_id')!=ACCOUNT:continue
                environment='meta_http' if type(self.transport) is CampaignTransport else 'injected_not_meta'
                if prior.get('timings',{}).get('transport_kind')!=environment:continue
                for step in prior.get('steps',[]):
                    if (prior['operation_id'],step.get('key'))==current_step:continue
                    if step.get('kind')=='image' and step.get('params',{}).get('sha256')==sha and step.get('state') in ('inflight','unknown'):
                        raise OperationBlocked('Prior image upload outcome unknown; reconcile before reuse or upload')
                    result=step.get('result') or {};proof=result.get('upload_provenance') or {}
                    if (step.get('kind')!='image' or step.get('state')!='verified' or result.get('reused') is not False
                        or proof!={'method':'local_bytes_upload_then_library_readback','account_id':ACCOUNT,'sha256':sha,'byte_count':byte_count,'transport_kind':environment}
                        or step.get('params',{}).get('sha256')!=sha or result.get('source_sha256')!=sha
                        or step.get('provider_image_hash')!=result.get('hash')
                        or not isinstance(result.get('hash'),str) or not re.fullmatch('[A-Za-z0-9]{8,128}',result['hash'])):continue
                    candidate={'operation_id':prior['operation_id'],'step_key':step['key'],'result':result}
        return candidate

    def _perform(self,token,step,job):
        params=self._resolve(step['params'],job['steps']);kind=step['kind']
        if kind=='video':
            raw,info=self._video_source(params)
            # Across revised proposals too: ambiguous uploads cannot be hidden
            # by changing request revisions, names or campaign selections.
            with self.db() as db:
                priors=[self._load(db,row[0]) for row in db.execute('SELECT id FROM operations').fetchall()]
            prior=None
            for old in priors:
                for candidate in old['steps']:
                    if old['operation_id']==job['operation_id'] and candidate['key']==step['key']:continue
                    if candidate['kind']=='video' and candidate['params'].get('sha256')==info['sha256'] and candidate['state'] not in ('pending','rejected','abandoned'):
                        if candidate['state']!='verified':raise OperationBlocked('Prior video upload requires reconciliation; no re-upload')
                        prior=candidate
            if prior:
                identity=prior.get('provider_id')
                step['provider_id']=identity;self._put(job)
                read=self._measured_call(job,'receipt_readback',self.transport.video_info,token,identity)
                if read.get('id')!=identity or read.get('account_id')!=ACCOUNT or (read.get('status') or {}).get('video_status')!='ready':raise OperationBlocked('Prior video no longer verified; no re-upload')
                step['provider_id']=identity;self._put(job)
                return {'id':identity,'source_sha256':info['sha256'],'processing_status':'ready','reused':True}
            result=self._submit(job,self.transport.upload_video,token,raw,params['name'])
            identity=result.get('id')
            if not isinstance(identity,str) or not ID.fullmatch(identity):raise OperationBlocked('Video upload identity missing')
            step['provider_id']=identity;self._put(job)
            # A saved identity is never permission to re-upload after timeout.
            deadline=self.monotonic()+60
            while self.monotonic()<deadline:
                read=self._measured_call(job,'receipt_readback',self.transport.video_info,token,identity)
                if not read or read.get('pending') is True:
                    time.sleep(1)
                    continue
                if read.get('id')!=identity or read.get('account_id')!=ACCOUNT:raise OperationBlocked('Video account readback mismatch')
                status=(read.get('status') or {}).get('video_status')
                if status=='ready':return {'id':identity,'source_sha256':info['sha256'],'processing_status':'ready'}
                if status in ('error','rejected'):
                    raise OperationBlocked('Video processing rejected; explicit owner abandon required')
                if status not in ('processing','uploading'):raise OperationBlocked('Video processing failed or unknown')
                time.sleep(1)
            raise OperationBlocked('Video processing timeout; no re-upload allowed')
        if kind=='image':
            row={'asset_id':params['asset_id'],'sha256':params['sha256'],'source_kind':params.get('source_kind','drive')};raw,info=self._source(row)
            prior=self._uploaded_image_receipt(info['sha256'],len(raw),(job['operation_id'],step['key']))
            if prior:
                old=prior['result'];read=self._measured_call(job,'receipt_readback',self.transport.image_info,token,old['hash'])
                data=read.get('data')
                if not isinstance(data,list):raise OperationBlocked('Image reuse library result unavailable; no fallback upload')
                matches=[x for x in data if isinstance(x,dict) and x.get('hash')==old['hash']]
                if matches:
                    if len(matches)!=1 or matches[0].get('account_id')!=ACCOUNT:raise OperationBlocked('Image reuse account mismatch; no fallback upload')
                    step['provider_image_hash']=old['hash'];self._put(job)
                    return {'hash':old['hash'],'source_sha256':info['sha256'],'name':matches[0].get('name'),
                            'remote_byte_equality_verified':False,'reused':True,
                            'source_receipt':{'operation_id':prior['operation_id'],'step_key':prior['step_key']}}
                if data:raise OperationBlocked('Unexpected image library rows; no fallback upload')
                # A definite empty library result is a cache miss. Transport
                # exceptions propagate; unknown outcomes never trigger upload.
            result=self._submit(job,self.transport.upload,token,raw,info['mime'],params['name']);images=result.get('images') or {}
            values=list(images.values()) if isinstance(images,dict) else []
            if len(values)!=1 or not isinstance(values[0],dict) or not isinstance(values[0].get('hash'),str):raise OperationBlocked('Image upload result incomplete')
            image_hash=values[0]['hash'];step['provider_image_hash']=image_hash;self._put(job);read=self._measured_call(job,'receipt_readback',self.transport.image_info,token,image_hash)
            if not any(x.get('hash')==image_hash and x.get('name')==params['name'] and x.get('account_id')==ACCOUNT for x in read.get('data',[])):raise OperationBlocked('Image library readback mismatch')
            return {'hash':image_hash,'source_sha256':info['sha256'],'name':params['name'],'remote_byte_equality_verified':False,
                    'reused':False,'upload_provenance':{'method':'local_bytes_upload_then_library_readback','account_id':ACCOUNT,'sha256':info['sha256'],'byte_count':len(raw),'transport_kind':'meta_http' if type(self.transport) is CampaignTransport else 'injected_not_meta'}}
        if kind=='update':
            self._submit(job,self.transport.update,token,step['node'],params);read=self._measured_call(job,'receipt_readback',self._entity,token,step['entity_type'],step['node'])
            if not self._match(read,params):raise OperationBlocked('Mutation readback mismatch')
            return {k:read.get(k) for k in ('id','account_id','status','daily_budget','lifetime_budget','campaign_id') if k in read}
        if job['manifest'].get('schema_version')==2:
            result=self._submit(job,self.transport.create_v2,token,kind,params,job['manifest']['campaign_id'])
        else:result=self._submit(job,self.transport.create,token,kind,params)
        identity=result.get('id')
        if not isinstance(identity,str) or not ID.fullmatch(identity):raise OperationBlocked('Created object identity missing')
        step['provider_id']=identity;self._put(job)
        return self._verify_created(token,step,job,params,identity)

    def _verify_created(self,token,step,job,params,identity):
        kind=step['kind']
        fields={'adsets':'id,account_id,name,status,campaign_id,targeting,optimization_goal,billing_event,promoted_object,start_time,end_time,daily_budget,lifetime_budget,bid_strategy,destination_type,is_dynamic_creative','adcreatives':'id,account_id,name,object_story_spec,url_tags','ads':'id,account_id,name,status,adset_id,campaign_id,creative{id}'}[kind]
        if job['manifest'].get('schema_version')==2:
            fields=','.join(['id','account_id']+[('creative{id}' if k=='creative' else k) for k in params]+(['campaign_id'] if kind=='ads' else []))
        elif kind=='adcreatives' and 'asset_feed_spec' in params:fields+=',asset_feed_spec'
        read=self._measured_call(job,'receipt_readback',self.transport.read,token,identity,fields)
        if read.get('id')!=identity or read.get('account_id')!=ACCOUNT:raise OperationBlocked('Created object account mismatch')
        expected=dict(params)
        if kind=='ads':expected['creative']={'id':expected['creative']['creative_id']};expected['campaign_id']=job['manifest']['campaign_id']
        if kind=='adcreatives' and 'asset_feed_spec' in params and job['manifest'].get('schema_version')!=2:
            from canary_plan import placement_readback_matches
            if not placement_readback_matches(read,expected):raise OperationBlocked('Placement creative readback mismatch')
        elif not self._match(read,expected,job['manifest'].get('schema_version',1)):raise OperationBlocked('Created object readback mismatch')
        return {'id':identity,'verified_fields':expected}

    def _creation_wave(self,token,job):
        """Batch only dependency-ready creates; persist unknown before network.

        Accepted IDs are saved before any readback. No item is retried after an
        ambiguous response, including when another item in the wave succeeded.
        """
        from meta_batch import parse_batch_results
        from urllib.parse import urlencode
        ready=[]
        images_pending=any(s['kind']=='image' and s['state']=='pending' for s in job['steps'])
        for step in job['steps']:
            if step['state']!='pending' or step['kind'] not in ('adsets','adcreatives','ads'):continue
            # Complete independent uploads before creative creation so a wave
            # contains all ready creatives, rather than one POST per image.
            if images_pending and step['kind']!='adsets':continue
            try:params=self._resolve(step['params'],job['steps'])
            except OperationBlocked:continue
            ready.append((step,params))
            if len(ready)==50:break
        if not ready:return False
        wire=[]
        for step,params in ready:
            values={k:json.dumps(v,separators=(',',':')) if isinstance(v,(dict,list,bool)) else str(v) for k,v in params.items()}
            wire.append({'method':'POST','relative_url':'act_'+ACCOUNT+'/'+step['kind'],'body':urlencode(values)})
            step['state']='unknown'
        self._put(job)
        try:response=self._submit(job,self.transport.batch,token,wire)
        except DispatchStopped:
            for step,params in ready:step['state']='pending'
            self._put(job)
            raise
        except ProviderRejected:
            for step,params in ready:step['state']='rejected'
            self._put(job)
            raise
        outcomes=self._measured_call(job,'receipt_readback',parse_batch_results,[{'id':s['key'],'method':'POST'} for s,p in ready],response)
        for (step,params),outcome in zip(ready,outcomes):
            step['state']={'accepted':'inflight','rejected':'rejected','unknown':'unknown'}[outcome['state']]
            if outcome.get('provider_id'):step['provider_id']=outcome['provider_id']
        self._put(job)
        failed=False
        for step,params in ready:
            identity=step.get('provider_id')
            if step['state']!='inflight' or not identity:
                failed=True;continue
            try:
                step['result']=self._verify_created(token,step,job,params,identity)
                step['state']='verified'
            except Exception:
                step['state']='unknown';failed=True
            self._put(job)
        if failed:
            if any(s['state']=='unknown' for s,p in ready):raise OperationBlocked('Batch requires reconciliation')
            raise ProviderRejected('One or more batch items rejected')
        return True

    def abandon_unknown_video(self,operation_id,digest_value,step_key,owner_confirmation=False):
        """Explicit owner waiver of an unresolved upload, never an automatic retry.

        A remote orphan may exist. This records that risk and permits a new
        proposal to upload the source; it never resumes this consumed job.
        """
        with self.lock,self.db() as db:
            job=self._load(db,operation_id)
            if owner_confirmation is not True or not isinstance(digest_value,str) or not hmac.compare_digest(job['digest'],digest_value) or digest(job['manifest'])!=job['digest']:raise OperationBlocked('Exact owner waiver required')
            step=next((s for s in job['steps'] if s['key']==step_key),None)
            if job['state'] not in ('unknown','partial') or job.get('approval_status')!='consumed' or not step or step['kind']!='video' or step['state']!='unknown':raise OperationBlocked('Only unknown video can be abandoned')
            step['state']='abandoned';step['owner_waiver']={'confirmed_at':self._now().isoformat(),'remote_orphan_possible':True}
            self._save(db,job)
            return self._response(job)

    def reconcile(self,operation_id,digest_value):
        """Read saved identities only; never resume creation or discover by name.

        Historic approval may have expired or the current proposal may differ:
        this reads the immutable approved manifest, not permission for new work.
        SDK api_get uses GET on the saved node ID. Existing verification compares
        every requested setting; it is not evidence of delivery or rendered QA.
        """
        with self.lock, self.db() as db:
            job=self._load(db,operation_id)
            if (not isinstance(digest_value,str) or not hmac.compare_digest(job['digest'],digest_value)
                or digest(job['manifest'])!=job['digest']):raise OperationBlocked('Exact original manifest digest required')
            if (job['action']!='prepare_launch' or job['state'] not in ('unknown','partial','verified')
                or job.get('approval_status')!='consumed' or not job.get('approved_at')):
                raise OperationBlocked('Only previously attempted approved creation can be reconciled')
            manifest=job['manifest']
            if manifest.get('account_id')!=ACCOUNT or (manifest.get('schema_version')!=2 and manifest.get('campaign_id')!=PARENT):raise OperationBlocked('Reconciliation scope mismatch')
            originals=manifest.get('steps',[])
            if len(originals)!=len(job['steps']):raise OperationBlocked('Step manifest mismatch')
            for original,step in zip(originals,job['steps']):
                if any(step.get(k)!=original.get(k) for k in ('key','kind','params','expected')):raise OperationBlocked('Step manifest mismatch')
            # Transaction excludes competing process changes while readback is
            # in flight. It never changes run ownership or permits a retry.
            token=self.credentials();self._account(token)
            previous_timings=json.loads(canonical(job.get('timings')))
            started=self.monotonic();observations=[]
            # Preserve the execution receipt before any recovery changes. A
            # later drift/GET failure does not erase what was verified earlier.
            if 'execution_evidence' not in job:
                job['execution_evidence']=json.loads(canonical({k:job.get(k) for k in ('state','reason','finished_at','steps')}))
            try:
                for step in job['steps']:
                    if step['state'] in ('pending','rejected','abandoned'):continue
                    was_verified=step['state']=='verified';verification_started=False
                    try:
                        params=self._resolve(step['params'],job['steps'])
                        if step['kind']=='image':
                            image_hash=step.get('provider_image_hash')
                            if not isinstance(image_hash,str) or not re.fullmatch('[A-Za-z0-9]{8,128}',image_hash):raise OperationBlocked('Saved image identity missing')
                            verification_started=True
                            rows=self.transport.image_info(token,image_hash).get('data')
                            matches=[r for r in rows if isinstance(r,dict) and r.get('hash')==image_hash] if isinstance(rows,list) else []
                            if len(matches)!=1 or matches[0].get('account_id')!=ACCOUNT:raise OperationBlocked('Image account readback mismatch')
                            old=step.get('result') or {}
                            expected_name=old.get('name') if old.get('reused') is True else params['name']
                            if matches[0].get('name')!=expected_name:raise OperationBlocked('Image name readback mismatch')
                            result=old or {'hash':image_hash,'source_sha256':params['sha256'],'name':params['name'],
                                          'remote_byte_equality_verified':False,'reconciled_library_identity_only':True}
                        elif step['kind']=='video':
                            identity=step.get('provider_id')
                            if not isinstance(identity,str) or not ID.fullmatch(identity):raise OperationBlocked('Saved video identity missing')
                            verification_started=True
                            read=self.transport.video_info(token,identity)
                            if read.get('id')!=identity or read.get('account_id')!=ACCOUNT or (read.get('status') or {}).get('video_status')!='ready':raise OperationBlocked('Video not ready or wrong account')
                            result={'id':identity,'source_sha256':params['sha256'],'processing_status':'ready'}
                        elif step['kind'] in ('adsets','adcreatives','ads'):
                            identity=step.get('provider_id')
                            if not isinstance(identity,str) or not ID.fullmatch(identity):raise OperationBlocked('Saved provider identity missing')
                            verification_started=True
                            result=self._verify_created(token,step,job,params,identity)
                        else:raise OperationBlocked('Unsupported reconciliation step')
                        if not was_verified:
                            step['result']=result;step['state']='verified'
                            step['recovered_at']=self._now().isoformat()
                            step['verification_basis']='Current readback matches approved settings; origination time and actor are not established'
                        observations.append({'key':step['key'],'state':'verified','current_state':'match'})
                    except Exception as error:
                        if not was_verified:step['state']='unknown'
                        status='drift' if verification_started and isinstance(error,OperationBlocked) else 'unavailable'
                        observations.append({'key':step['key'],'state':step['state'],'current_state':status,'reason':'Current saved identity, dependency or exact field readback unavailable or mismatched; historical receipts retained'})
            finally:
                job['timings']=previous_timings
            job['state']='verified' if all(s['state']=='verified' for s in job['steps']) else 'unknown' if any(s['state'] in ('unknown','inflight') for s in job['steps']) else 'partial'
            job['reason']='Read-only reconciliation completed; no creation resumed. Pending work remains unexecuted; delivery and rendered QA are not verified.'
            job['reconciliation']={'checked_at':self._now().isoformat(),'read_only':True,'observations':observations,'seconds':round(max(0,self.monotonic()-started),6)}
            job['reconciliation']['current_state']='drift' if any(x['current_state']=='drift' for x in observations) else 'unavailable' if any(x['current_state']=='unavailable' for x in observations) else 'match'
            job.setdefault('reconciliation_history',[]).append(json.loads(canonical(job['reconciliation'])))
            self._save(db,job)
            return self._response(job)|{'reconciliation':job['reconciliation'],'reconciliation_history':job['reconciliation_history'],'execution_evidence':job['execution_evidence']}

    def enqueue(self,operation_id):
        with self.lock:
            job=self._get(operation_id)
            if job['state'] in ('queued','running','verified','unknown','not_applied','cancelled'):return self._response(job)
            if job['state']!='prepared':raise OperationBlocked('Terminal/partial operation cannot be retried')
            self._valid(job)
            if job['approval_status']!='approved':raise OperationBlocked('Exact approval required')
            if job['scheduled_at'] is not None:raise OperationBlocked('Use exact schedule route')
            job['state']='queued';self._put(job);return self._response(job)

    def execute(self,operation_id):
        with self.lock:
            job=self._get(operation_id)
            if job['state'] in ('verified','unknown','not_applied','partial','running','stale','blocked','cancelled'):return self._response(job)
            self._valid(job)
            if job['approval_status']!='approved' or job['state'] not in ('prepared','queued','scheduled'):raise OperationBlocked('Exact approval required')
            if job['scheduled_at'] and self._now()<self._stamp(job['scheduled_at']):raise OperationBlocked('Scheduled time not reached')
            # Claim durably before I/O. Another service instance sees running.
            with self.db() as db:
                current=self._load(db,operation_id)
                if current['state'] not in ('prepared','queued','scheduled'):return self._response(current)
                self._assert_no_competing_creation(db,current)
                job=current;job['state']='running';job['approval_status']='consumed';job['run_id']=secrets.token_hex(16);self._save(db,job)
            started=self.monotonic()
            job['timings']={'operation_id':operation_id,'clock':'monotonic','scope':'claimed_execution_only',
                'transport_kind':'meta_http' if type(self.transport) is CampaignTransport else 'injected_not_meta',
                'started_at':self._now().isoformat(),'complete':False,'total_seconds':None,
                'phases':{p:{'seconds':None,'samples':0} for p in ('preflight','submission','receipt_readback')},
                'missing':{'preparation':None,'ui':None,'queue_or_provider_review_wait':None}}
            self._put(job)
            wrote=False
            try:
                with self._measure(job,'preflight'):
                    token=self.credentials();self._account(token)
                    if job['action']=='prepare_launch':
                        proposal=self._proposal()
                        if job['manifest'].get('schema_version')==2:
                            current=self._v2_context(token,proposal,check_time=False)
                        else:current=self._entity(token,'campaign',PARENT)
                        if job['manifest'].get('schema_version')==2:
                            if self._execution_context(current,proposal,job['manifest']['selection'])!=self._execution_context(job['before'],proposal,job['manifest']['selection']):raise OperationBlocked('Selected campaign context changed since preview')
                        elif current!=job['before']:raise OperationBlocked('Parent campaign changed since preview')
                        # Revalidate every cached source/config immediately before first write.
                        proposal=self._proposal()
                        if digest(proposal)!=job['source_digest']:raise OperationBlocked('Proposal or request revision changed during preflight')
                        if job['manifest'].get('schema_version')==2:
                            rebuilt,files,details=self._prepare_v2(job['manifest']['selection'],proposal,job['operation_id'],current)
                            if rebuilt!=job['manifest']['steps'] or files!=job['files']:raise OperationBlocked('Execution plan changed')
                        else:self._prepare({'selected_adset_keys':[x['key'] for x in job['manifest']['adsets']],
                                       'selected_asset_ids':sorted({x['asset_id'] for x in job['manifest']['copy_choices']}),'copy_variant_indices':job['manifest'].get('copy_variant_indices',{}),
                                       'format_mode':job['manifest'].get('format_mode','feed_original'),
                                       **({'creation_budget_minor':job['manifest']['creation_budget_minor']} if job['manifest'].get('creation_budget_minor') is not None else {})},proposal,job['operation_id'])
                    else:
                        current=self._entity(token,job['manifest']['entity_type'],job['manifest']['entity_id'])
                        if job['action']=='activate' and job['manifest']['entity_type']=='campaign':current['descendants']=self._descendants(token)
                        if current!=job['before']:raise OperationBlocked('Entity changed since preview')
                    # Credential/provider preflight can take time. Recheck approval
                    # expiry and source revision after those reads, before any POST.
                    self._valid(job)
                while any(step['state']!='verified' for step in job['steps']):
                    if job['action']=='prepare_launch' and job['manifest'].get('schema_version')!=2 and callable(getattr(self.transport,'batch',None)):
                        wrote=True
                        if self._creation_wave(token,job):continue
                    remaining=[s for s in job['steps'] if s['state']!='verified']
                    step=(next((s for s in remaining if s['kind']=='image' and s['state']=='pending'),remaining[0])
                          if job['action']=='prepare_launch' and callable(getattr(self.transport,'batch',None)) else remaining[0])
                    if step['state']!='pending':raise OperationBlocked('Attempted step cannot be retried')
                    step['state']='inflight';self._put(job);wrote=True
                    try:step['result']=self._perform(token,step,job);step['state']='verified';self._put(job)
                    except DispatchStopped:step['state']='pending';raise
                    except ProviderRejected:
                        if step['kind']=='video' and step.get('provider_id'):
                            step['state']='unknown'
                            raise OperationBlocked('Video status unavailable; reconcile saved identity') from None
                        step['state']='rejected';raise
                    except Exception:step['state']='unknown';raise
                job['state']='verified';job['reason']=('All created API objects read back; creation remains PAUSED. Delivery and UI editability are not verified.' if job['action']=='prepare_launch' else 'Requested mutation fields read back. Delivery and downstream business effects are not verified.')
            except DispatchStopped:
                job['state']='partial' if any(x['state']=='verified' for x in job['steps']) else 'blocked'
                job['reason']='Remaining work stopped before dispatch; existing receipts retained, no rollback and no automatic retry'
            except ProviderRejected:
                job['state']='not_applied' if not any(x['state']=='verified' for x in job['steps']) else 'partial';job['reason']='Meta explicitly rejected a step; no retry performed'
            except Exception:
                job['state']='unknown' if wrote else 'blocked';job['reason']='Outcome requires reconciliation; no retry performed' if wrote else 'Preflight changed or unavailable; no write attempted'
            job['timings']['total_seconds']=round(max(0.0,self.monotonic()-started),6)
            job['timings']['complete']=True;job['timings']['outcome']=job['state']
            job['finished_at']=self._now().isoformat();self._put(job);return self._response(job)

    def schedule(self,operation_id,at):
        with self.lock:
            job=self._get(operation_id);self._valid(job);stamp=self._stamp(at).isoformat()
            if job['approval_status']!='approved' or job['state']!='prepared' or stamp!=job['scheduled_at'] or self._stamp(stamp)<=self._now():raise OperationBlocked('Schedule must match exact approved preview')
            job['state']='scheduled';self._put(job);return self._response(job)

    def tick(self):
        with self.db() as db:jobs=[self._load(db,row[0]) for row in db.execute('SELECT id FROM operations').fetchall()]
        results=[]
        for job in jobs:
            if job['state']=='queued' or job['state']=='scheduled' and self._stamp(job['scheduled_at'])<=self._now():
                try:results.append(self.execute(job['operation_id']))
                except ValueError:
                    job=self._get(job['operation_id']);job['state']='stale';job['reason']='Approval expired or source changed before execution';self._put(job);results.append(self._response(job))
        return results

    def start_worker(self):
        if getattr(self,'worker',None) and self.worker.is_alive():return
        self.stop_event=threading.Event()
        def run():
            while not self.stop_event.wait(2):
                try:self.tick()
                except Exception:pass
        self.worker=threading.Thread(target=run,daemon=True,name='campaign-operations');self.worker.start()
    def stop(self):
        if hasattr(self,'stop_event'):self.stop_event.set()
