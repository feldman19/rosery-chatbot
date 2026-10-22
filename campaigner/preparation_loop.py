"""Durable local preparation consumer. No model API and no provider writes.

An independent reviewer uses immutable bundles and an exact-bound inbox reply.
Only mechanical text cleanup is automatic; semantic fixes need a real author.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib,hmac,json,secrets,sqlite3,threading,time
from launch_proposal import LaunchProposal
from work_intake import WorkIntake

REVIEWER='01a13ae0-12d9-7530-9fa4-744818de4bb7'
def encode(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()
def semantic_digest(value):return digest({k:v for k,v in value.items() if k!='prepared_at'})
class RevisionConflict(ValueError):pass

class PreparationLoop:
 def __init__(self,directory,key,clock=time.time):
  self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
  self.path=self.directory/'preparation-loop.sqlite3';self.key=key;self.clock=clock
  self.store=LaunchProposal(self.directory,key);self.intake=WorkIntake(self.directory)
  self.bundles=self.directory/'preparation-review';self.bundles.mkdir(exist_ok=True)
  self.stop_event=threading.Event();self.thread=None;self.owner=secrets.token_hex(12);self.lock=threading.RLock();self.last_error=None
  with self.db() as db:
   db.executescript('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,payload TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS corrections(id TEXT PRIMARY KEY,payload TEXT NOT NULL);''')
 @contextmanager
 def db(self):
  db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
  try:
   db.execute('BEGIN IMMEDIATE');yield db;db.commit()
  except BaseException:db.rollback();raise
  finally:db.close()
 def context(self):
  request=self.intake.latest();report=self.store.report(request)
  if not request or not report.get('available'):raise ValueError('current_reviewed_proposal_missing_or_source_changed')
  proposal=report['proposal'];sources=[]
  for ad in proposal['ads']:
   for row in [ad]+([ad['story']] if isinstance(ad.get('story'),dict) else []):sources.append({'id':row['asset_id'],'sha256':row['sha256']})
  binding={'request_id':request['id'],'request_revision':request['revision'],'request_hash':digest({k:request[k] for k in ('id','revision','goal','drive_urls')}),'artifact_hash':digest(proposal),'source_hash':digest(sorted(sources,key=lambda x:x['id']))}
  return request,proposal,binding
 def _save(self,db,job):
  current=db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
  version=json.loads(current[0]).get('version',0) if current else 0
  if current and version!=job.get('version',0):raise RevisionConflict('Preparation job changed')
  job['version']=version+1;db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?)',(job['id'],encode(job)))
 def _latest(self,db):
  rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM jobs')]
  return max(rows,key=lambda r:(r.get('creation_sequence',0),r['created_at'])) if rows else None
 def start(self,expected_binding=None):
  with self.lock:
   request,proposal,binding=self.context();identity=digest(binding)
   if expected_binding is not None and binding!=expected_binding:raise ValueError("Preparation evidence changed before queueing review")
   with self.db() as db:
    active=self._latest(db)
    if active and all(active.get(k)==v for k,v in binding.items()):return active
    row=db.execute('SELECT payload FROM jobs WHERE id=?',(identity,)).fetchone()
    if row:return json.loads(row[0])
    job={'id':identity,**binding,'state':'queued','round':0,'attempts':0,'created_at':self.clock(),'expires_at':self.clock()+7*86400,'active_seconds':0,'completed_steps':[],'findings':[],'gaps':[],'blocked_reason':None,'reviewer_connected':False,'lease_until':0,'lease_owner':None,'metrics':{'review_rounds':0,'repair_rounds':0,'user_interventions':0}}
    if active and active.get('request_hash')==binding['request_hash']:
     job.update(parent_job_id=active['id'],round=active['round'],lineage_created_at=active.get('lineage_created_at',active['created_at']),expires_at=active.get('expires_at',active['created_at']+7*86400),active_seconds=active.get('active_seconds',0),prior_review_rounds=active['metrics']['review_rounds'])
     job['metrics'].update(review_rounds=active['metrics']['review_rounds'],repair_rounds=active['metrics']['repair_rounds']+int(active['artifact_hash']!=binding['artifact_hash']))
    existing=[json.loads(r[0]) for r in db.execute('SELECT payload FROM jobs')]
    job['creation_sequence']=max((r.get('creation_sequence',0) for r in existing),default=0)+1
    self._save(db,job);return job
 def report(self):
  try:_,_,current_binding=self.context()
  except (OSError,ValueError,KeyError,TypeError):current_binding=None
  with self.db() as db:job=self._latest(db)
  if not job:
   return {'available':current_binding is not None,'current_binding':current_binding,'handoff':None,'job':None,'no_live_actions':True,'consumer_running':bool(self.thread and self.thread.is_alive())}
  if current_binding is None:job={**job,'state':'stale','blocked_reason':'current_evidence_unavailable'}
  elif any(job.get(k)!=v for k,v in current_binding.items()):job={**job,'state':'stale','blocked_reason':'request_artifact_or_sources_changed'}
  job['metrics']['elapsed_seconds']=max(0,round(self.clock()-job.get('lineage_created_at',job['created_at'])))
  job['metrics']['current_revision_elapsed_seconds']=max(0,round(self.clock()-job['created_at']))
  with self.db() as db:corrections=[json.loads(row[0]) for row in db.execute('SELECT payload FROM corrections')]
  job['metrics']['user_interventions']=sum(row.get('request_id')==job['request_id'] and row.get('request_revision')==job['request_revision'] and row['recorded_at']>=job.get('lineage_created_at',job['created_at']) and row['source'].startswith(('owner_instruction:','user:')) for row in corrections)
  job['metrics']['user_time_seconds']=None;job['metrics']['baseline']=None;job['metrics']['measurement_scope']='local_owner_notes_since_loop_start_only; external chat interventions and user time not measured'
  return {'available':True,'current_binding':current_binding,'handoff':self._handoff(job),'job':job,'no_live_actions':True,'consumer_running':bool(self.thread and self.thread.is_alive()),'model_connected':False,'consumer_error':self.last_error}
 def _handoff(self,job):
  if job.get('state') not in ('awaiting_review','reviewing') or not job.get('review_id') or not job.get('bundle_hash'):return None
  template={'review_id':job['review_id'],'job_id':job['id'],'bundle_hash':job['bundle_hash'],'binding':{k:job[k] for k in ('request_id','request_revision','request_hash','artifact_hash','source_hash')},'reviewer_id':REVIEWER,'verdict':'pass or revise','findings':[],'limitations':[]}
  return {'request_file':job['review_id']+'.request.json','bundle_hash':job['bundle_hash'],'reply_template':template,'note':'Choose verdict and provide actual findings/limitations. Hash covers exact request-file bytes; it cannot be embedded into those same bytes.'}
 def correction(self,text,source,scope='unclassified_not_applied'):
  if not isinstance(text,str) or not 1<=len(text)<=6000 or not isinstance(source,str) or not 1<=len(source)<=300:raise ValueError('Bounded correction provenance required')
  request=self.intake.latest();record={'text':text,'source':source,'scope':scope,'request_id':request['id'] if request else None,'request_revision':request['revision'] if request else None,'recorded_at':self.clock(),'automatically_applied':False}
  identity=digest({'source':source,'text':text,'scope':scope})
  with self.db() as db:db.execute('INSERT OR IGNORE INTO corrections VALUES(?,?)',(identity,encode(record)))
  return identity
 def dispatch(self,review_id):
  """Reserve one external send before transport; never auto-repeat an uncertain send."""
  with self.db() as db:
   job=self._latest(db)
   if not job or job.get('review_id')!=review_id or job['state']!='awaiting_review' or job.get('dispatch_state'):raise ValueError('Review dispatch already reserved or obsolete')
   job.update(dispatch_at=self.clock(),dispatch_state='reserved_transport_not_confirmed');self._save(db,job)
  return {**job,'review_handoff':self._handoff(job)}
 def confirm_dispatch(self,review_id):
  with self.db() as db:
   job=self._latest(db)
   if not job or job.get('review_id')!=review_id or job.get('dispatch_state')!='reserved_transport_not_confirmed':raise ValueError('No reserved dispatch')
   job.update(state='reviewing',dispatch_state='confirmed',blocked_reason='independent_review_in_progress');self._save(db,job)
 def attest_review(self,review_id,reply_hash,source_thread_id,source_message_id):
  """Trusted host only, after reading this reviewer's actual thread response.
  No HTTP endpoint. This is a host receipt, not a provider-signed attestation.
  """
  if source_thread_id!=REVIEWER or not isinstance(source_message_id,str) or not 8<=len(source_message_id)<=200:raise ValueError('Independent transport provenance required')
  with self.db() as db:job=self._latest(db)
  if not job or job.get('review_id')!=review_id or not isinstance(reply_hash,str) or len(reply_hash)!=64:raise ValueError('Current review required')
  reply=self.bundles/(review_id+'.reply.json')
  if reply.is_symlink() or reply.stat().st_size>200000 or hashlib.sha256(reply.read_bytes()).hexdigest()!=reply_hash:raise ValueError('Reply content changed')
  receipt={'review_id':review_id,'job_id':job['id'],'bundle_hash':job['bundle_hash'],'reply_hash':reply_hash,'source_thread_id':source_thread_id,'source_message_id':source_message_id,'kind':'trusted_host_observed_thread_response'}
  raw=encode(receipt);signed={'payload':raw,'signature':hmac.new(self.key,raw.encode(),hashlib.sha256).hexdigest()}
  target=self.bundles/(review_id+'.receipt.json')
  with target.open('x',encoding='utf-8') as stream:stream.write(encode(signed))
 def _receipt_valid(self,job,path):
  try:
   target=self.bundles/(job['review_id']+'.receipt.json')
   if target.is_symlink() or target.stat().st_size>20000:return False
   signed=json.loads(target.read_text(encoding='utf-8'));raw=signed['payload']
   if not hmac.compare_digest(signed['signature'],hmac.new(self.key,raw.encode(),hashlib.sha256).hexdigest()):return False
   receipt=json.loads(raw)
   return receipt['review_id']==job['review_id'] and receipt['job_id']==job['id'] and receipt['bundle_hash']==job['bundle_hash'] and receipt['source_thread_id']==REVIEWER and receipt['reply_hash']==hashlib.sha256(path.read_bytes()).hexdigest()
  except (OSError,ValueError,KeyError,TypeError):return False
 def tick(self):
  with self.lock:
   with self.db() as db:job=self._latest(db)
   if not job or job['state'] in ('reviewed','reviewed_with_gaps','blocked','stale'):return
   try:request,proposal,binding=self.context()
   except (OSError,ValueError,KeyError,TypeError):return self._block(job,'current_evidence_unavailable','stale')
   if job['state']=='repairing':
    if any(job.get(k)!=binding[k] for k in ('request_id','request_revision','request_hash','source_hash')):return self._block(job,'request_or_source_changed_during_repair','stale')
    if binding['artifact_hash']!=job['artifact_hash']:
     if semantic_digest(proposal)!=job.get('repair_expected_hash'):return self._block(job,'unexpected_artifact_after_interrupted_repair','stale')
     job.update(binding);job.update(state='queued',lease_until=0);job['metrics']['repair_rounds']+=1
     with self.db() as db:self._save(db,job)
     return
   if any(job.get(k)!=v for k,v in binding.items()):return self._block(job,'request_artifact_or_sources_changed','stale')
   if self.clock()>job.get('expires_at',job['created_at']+7*86400) or job.get('active_seconds',0)>=1800:return self._block(job,'bounded_run_deadline_exceeded')
   if job['state'] in ('queued','preparing'):
    with self.db() as db:
     current=json.loads(db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
     if current['state'] not in ('queued','preparing') or current['lease_until']>self.clock():return
     if current['attempts']>=3:
      current.update(state='blocked',blocked_reason='preparation_attempt_limit');self._save(db,current);return
     job=current;job.update(state='preparing',lease_until=self.clock()+180,lease_owner=self.owner,attempts=job['attempts']+1);self._save(db,job)
    started=time.monotonic()
    try:return self._prepare(job,request,proposal)
    finally:self._active_time(job['id'],time.monotonic()-started)
   if job['state'] in ('awaiting_review','reviewing','repairing'):
    if self.clock()>job.get('review_expires_at',job['created_at']+86400):return self._block(job,'external_review_expired')
    reply=self.bundles/(job['review_id']+'.reply.json')
    if reply.exists():
     with self.db() as db:
      latest=json.loads(db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
      if latest.get('version',0)!=job.get('version',0) or latest['lease_until']>self.clock():return
      job=latest;job.update(lease_until=self.clock()+180,lease_owner=self.owner);self._save(db,job)
     if reply.is_symlink() or reply.stat().st_size>200000:return self._block(job,'invalid_reviewer_reply_file')
     if not self._receipt_valid(job,reply):
      job.update(blocked_reason='review_reply_waiting_transport_attestation',lease_until=0)
      with self.db() as db:self._save(db,job)
      return
     started=time.monotonic()
     try:return self._review(job,request,proposal,reply)
     finally:self._active_time(job['id'],time.monotonic()-started)
 def _active_time(self,identity,seconds):
  with self.db() as db:
   row=db.execute('SELECT payload FROM jobs WHERE id=?',(identity,)).fetchone()
   if row:
    job=json.loads(row[0]);job['active_seconds']=job.get('active_seconds',0)+max(0,seconds);self._save(db,job)
 def _block(self,job,reason,state='blocked'):
  job.update(state=state,blocked_reason=reason,lease_until=0)
  with self.db() as db:self._save(db,job)
 def _prepare(self,job,request,proposal):
  if job['round']>=3:return self._block(job,'review_round_limit')
  job['round']+=1;review_id=digest({'job':job['id'],'round':job['round'],'artifact':job['artifact_hash']})
  bundle={'schema_version':1,'review_id':review_id,'reviewer_id':REVIEWER,'job_id':job['id'],'binding':{k:job[k] for k in ('request_id','request_revision','request_hash','artifact_hash','source_hash')},'round':job['round'],'goal':request['goal'],'proposal':proposal,'sources_checked':True,'checks_scope':'Source membership, actual byte hashes and image decode validated. Existing visual review is historical agent evidence; no new visual or live account check claimed.','reply_contract':{'review_id':review_id,'job_id':job['id'],'binding':'copy exact binding','reviewer_id':REVIEWER,'verdict':'pass or revise','findings':[{'id':'short_unique_id','severity':'P1/P2/P3','detail':'evidence-based finding','repair':'optional: trim_whitespace or deduplicate_copy_options'}],'limitations':['explicit limits of review']},'no_live_actions':True}
  bundle['reply_contract']['bundle_hash']='Required: SHA-256 of exact request-file UTF-8 bytes; copy actual value from report.handoff.bundle_hash'
  bundle['reply_contract']['binding']=dict(bundle['binding'])
  path=self.bundles/(review_id+'.request.json');raw=encode(bundle)
  if path.exists() and path.read_text(encoding='utf-8')!=raw:return self._block(job,'immutable_review_bundle_collision')
  if not path.exists():
   with path.open('x',encoding='utf-8') as stream:stream.write(raw)
  job.update(state='awaiting_review',review_id=review_id,bundle_hash=hashlib.sha256(raw.encode()).hexdigest(),dispatch_state=None,review_expires_at=self.clock()+86400,blocked_reason='independent_chat_review_required_no_model_transport',lease_until=0,completed_steps=['canonical_request_bound','source_membership_and_hashes_checked','proposal_prepared','immutable_review_bundle_created'],gaps=proposal.get('gaps',[]))
  with self.db() as db:self._save(db,job)
 def _review(self,job,request,proposal,path):
  try:
   if path.is_symlink() or path.stat().st_size>200000:raise ValueError()
   review=json.loads(path.read_text(encoding='utf-8'))
   bundle=self.bundles/(job['review_id']+'.request.json')
   if bundle.is_symlink() or hashlib.sha256(bundle.read_bytes()).hexdigest()!=job['bundle_hash'] or review.get('bundle_hash')!=job['bundle_hash']:raise ValueError()
   if review.get('review_id')!=job['review_id'] or review.get('job_id')!=job['id'] or review.get('reviewer_id')!=REVIEWER or review.get('binding')!={k:job[k] for k in ('request_id','request_revision','request_hash','artifact_hash','source_hash')}:raise ValueError()
   findings=review.get('findings');limits=review.get('limitations')
   if review.get('verdict') not in ('pass','revise') or not isinstance(findings,list) or len(findings)>30 or not isinstance(limits,list) or len(limits)>30:raise ValueError()
   if any(not isinstance(x,dict) or not isinstance(x.get('id'),str) or len(x['id'])>100 or x.get('severity') not in ('P1','P2','P3') or not isinstance(x.get('detail'),str) or not 1<=len(x['detail'])<=4000 for x in findings):raise ValueError()
   if len({x['id'] for x in findings})!=len(findings) or any(not isinstance(x,str) or len(x)>4000 for x in limits):raise ValueError()
   if review['verdict']=='pass' and any(x['severity'] in ('P1','P2') for x in findings):raise ValueError()
  except (OSError,ValueError,TypeError,KeyError):return self._block(job,'invalid_or_unbound_reviewer_reply')
  with self.db() as db:
   prior=db.execute('SELECT payload FROM reviews WHERE id=?',(job['review_id'],)).fetchone()
   if prior:
    if prior[0]!=encode(review):
     job.update(state='blocked',blocked_reason='review_reply_changed');self._save(db,job)
     return
   else:db.execute('INSERT INTO reviews VALUES(?,?,?)',(job['review_id'],job['id'],encode(review)))
   rounds=db.execute('SELECT COUNT(*) FROM reviews WHERE job_id=?',(job['id'],)).fetchone()[0]
  job['metrics']['review_rounds']=job.get('prior_review_rounds',0)+rounds;job['findings']=findings;job['review_limitations']=limits
  if review['verdict']=='pass':
   _,_,current=self.context()
   if any(job.get(k)!=v for k,v in current.items()):return self._block(job,'artifact_changed_before_review_commit','stale')
   job.update(state='reviewed_with_gaps' if proposal.get('gaps') else 'reviewed',lease_until=0,blocked_reason='readiness_gaps_remain' if proposal.get('gaps') else None,review_hash=digest(review));job['completed_steps'].append('independent_review_received_exact_version')
   with self.db() as db:self._save(db,job)
   return
  repairs={row.get('repair') for row in findings}
  if not repairs or not repairs<={'trim_whitespace','deduplicate_copy_options'}:return self._block(job,'semantic_revision_requires_author_no_connected_model')
  before=digest(proposal)
  for ad in proposal['ads']:
   if 'trim_whitespace' in repairs:
    for key in ('headline','primary_text','description'):
     if isinstance(ad.get(key),str):ad[key]=ad[key].strip()
   if 'deduplicate_copy_options' in repairs:
    for key in ('primary_text_options','headline_options'):
     if isinstance(ad.get(key),list):ad[key]=list(dict.fromkeys(ad[key]))
  if digest(proposal)==before:return self._block(job,'requested_mechanical_repair_did_not_change_artifact')
  job.update(state='repairing',blocked_reason=None,repair_expected_hash=semantic_digest(proposal))
  with self.db() as db:self._save(db,job)
  if self.intake.latest()!=request:return self._block(job,'request_changed_before_repair','stale')
  self.store.save(proposal,request,expected_hash=job['artifact_hash'])
  _,_,binding=self.context();job.update(binding);job.update(state='queued',lease_until=0);job['metrics']['repair_rounds']+=1
  with self.db() as db:self._save(db,job)
 def start_worker(self):
  if self.thread and self.thread.is_alive():return
  def run():
   while not self.stop_event.is_set():
    try:self.tick();self.last_error=None
    except RevisionConflict:pass
    except Exception:self.last_error='local_worker_failed_no_provider_action'
    self.stop_event.wait(5)
  self.thread=threading.Thread(target=run,name='preparation-consumer',daemon=True);self.thread.start()
 def stop(self):self.stop_event.set();self.thread.join(timeout=2) if self.thread else None
