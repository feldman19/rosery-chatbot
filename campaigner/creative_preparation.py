"""Bounded Drive-to-reviewed-draft coordinator. Never calls advertising APIs."""
from batch_limits import MAX_ADSETS, MAX_ADS, MAX_CREATIVE_IMAGES, MAX_DRIVE_SOURCES
from creative_destination import resolve_destination
from creative_grouping import validate_proposed_adsets

import hashlib,json,threading,time
from pathlib import Path
from datetime import datetime,timezone

class CreativePairingError(ValueError):
 def __init__(self,request,source,missing_format):
  name=str(source.get('name') or source['id'])[:250]
  self.source_issue={'kind':'missing_pair','request_id':request['id'],'request_revision':request['revision'],'asset_id':source['id'],'sha256':source['sha256'],'source_name':name,'source_kind':source.get('source_kind','drive'),'missing_format':missing_format}
  super().__init__('לקובץ '+name+' חסרה גרסת '+('סטורי' if missing_format=='story' else 'פוסט')+('. בחר את שני הפורמטים מאותו קריאייטיב; אם חסרה גרסה, הגש בקשה חדשה עם שתי התמונות.' if source.get('source_kind')=='device' else '. בחר את שני הפורמטים מאותו קריאייטיב ב־Drive, או עדכן את התיקייה והגש בקשה חדשה.'))

class CreativePreparation:
 def __init__(self,directory,*,intake,workflow,materials,proposal,analyzer=None):
  self.directory=Path(directory);self.path=self.directory/'creative-preparation.json';self.intake=intake;self.workflow=workflow;self.materials=materials;self.proposal=proposal;self.analyzer=analyzer;self.lock=threading.Lock();self.status_lock=threading.RLock();self.active=None;self.pending=None;self.stage_clock=None
  self._recover_interrupted()
 def _recover_interrupted(self):
  """A new host instance owns no old worker; never replay model work on boot."""
  previous=self.status();request=self.intake.latest()
  if not isinstance(previous,dict) or previous.get('state') not in ('queued','collecting','analyzing','validating'):return
  if not request or (previous.get('request_id'),previous.get('request_revision'))!=(request['id'],request['revision']):return
  self._save('interrupted',request,previous_state=previous['state'],retry_required=True,detail='הכנת ההצעה הופסקה בעת סגירת המערכת. אפשר לנסות שוב; דבר לא פורסם במטא.')
 def status(self):
  try:value=json.loads(self.path.read_text(encoding='utf-8'))
  except (OSError,ValueError):return {'state':'idle','provider_writes':0}
  if not isinstance(value,dict):return {'state':'blocked','provider_writes':0,'detail':'מצב ההכנה השמור אינו תקין'}
  clock=self.stage_clock
  value['current_stage_elapsed_ms']=None
  if clock and (value.get('request_id'),value.get('request_revision'),value.get('state'))==clock[:3]:
   value['current_stage_elapsed_ms']=max(0,int((time.monotonic()-clock[3])*1000))
  return value
 def _save(self,state,request,**extra):
  with self.status_lock:
   if self.intake.latest()!=request:return {'state':'stale','provider_writes':0}
   previous=self.status();now=datetime.now(timezone.utc).isoformat();monotonic=time.monotonic()
   same=(previous.get('request_id'),previous.get('request_revision'))==(request['id'],request['revision'])
   history=previous.get('stage_history',[]) if same else []
   history=[dict(row) for row in history[-19:] if isinstance(row,dict)] if isinstance(history,list) else []
   same_stage=bool(history and history[-1].get('state')==state and history[-1].get('completed_at') is None)
   if not same_stage:
    if history and history[-1].get('completed_at') is None:
     clock=self.stage_clock;known=bool(clock and clock[:3]==(request['id'],request['revision'],history[-1].get('state')))
     history[-1].update(completed_at=now,elapsed_ms=max(0,int((monotonic-clock[3])*1000)) if known else None,duration_known=known)
     if not known:history[-1]['duration_note']='משך לא ידוע; אין לכלול זמן שבו התהליך לא פעל'
    history.append({'state':state,'entered_at':now,'completed_at':None,'elapsed_ms':None,'duration_known':False})
    self.stage_clock=(request['id'],request['revision'],state,monotonic) if state in ('queued','collecting','analyzing','validating') else None
   value={'state':state,'request_id':request['id'],'request_revision':request['revision'],'updated_at':now,'stage_history':history,'provider_writes':0,**extra}
   if self.intake.latest()!=request:return {'state':'stale','provider_writes':0}
   temp=self.path.with_suffix('.pending');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(self.path);return value
 def start(self,request):
  request=json.loads(json.dumps(request))
  with self.lock:
   if self.active:
    if self.active==(request['id'],request['revision']):return False
    self.pending=request;self._save('queued',request,detail='הבקשה החדשה תטופל לאחר עצירת ההכנה הקודמת');return True
   self.active=(request['id'],request['revision'])
  self._save('collecting',request,detail='קורא את חומרי הדרייב')
  threading.Thread(target=self._run,args=(request,),daemon=True,name='creative-preparation').start();return True
 def _current(self,r):
  if self.intake.latest()!=r:raise ValueError('הבקשה השתנתה; ההכנה הישנה לא תישמר')
 def _run(self,r):
  receipt_id=None
  try:
   self._current(r);resolve_destination(r.get('goal'));launch=self.workflow.report({}).get('request')
   if not launch or launch.get('brief')!=r['goal'] or launch.get('drive_urls')!=r['drive_urls'] or launch.get('local_sources',[])!=r.get('local_sources',[]):raise ValueError('אין התאמה בין הבקשה לחומרי המקור')
   self.materials.refresh(launch)
   deadline=time.monotonic()+180
   while True:
    self._current(r);material=self.materials.status(launch)
    if not material.get('refreshing'):break
    if time.monotonic()>deadline:raise ValueError('קריאת הדרייב לא הסתיימה בזמן; לא נשלחו תמונות לניתוח')
    time.sleep(.5)
   if not material.get('binding_verified') or material.get('assets',{}).get('complete') is not True:raise ValueError('קריאת חומרי המקור לא הושלמה')
   from creative_selection import CreativeSelection
   assets=CreativeSelection(self.directory).apply(r,material['assets']['assets'])
   if not 1<=len(assets)<=MAX_CREATIVE_IMAGES:raise ValueError('יש לבחור עד 30 קובצי תמונה להכנה אחת; תיקייה גדולה לא נשלחת אוטומטית לניתוח')
   if any(x.get('mime_type') not in ('image/jpeg','image/png') or not x.get('content_read') for x in assets):raise ValueError('המסלול הנוכחי תומך בתמונות PNG/JPEG בלבד; ניתוח וידאו טרם חובר')
   from image_validation import validate_upload_sources
   validate_upload_sources(self.directory,assets)
   self._current(r);self._save('analyzing',r,detail='מנתח תמונות, מסר וזווית; קהל הוא השערה לבדיקה')
   if r.get('local_sources') and r.get('local_image_review_consent') is not True:raise ValueError('יש לאשר במפורש שיתוף התמונות שהועלו עם Claude לפני ניתוח; הקבצים נשמרו במחשב בלבד')
   if self.analyzer:analysis=self.analyzer(assets,owner_brief=r['goal'])
   else:
    from creative_vision import CreativeVision
    analysis=CreativeVision(self.directory).analyze(assets,owner_brief=r['goal'],**({'local_image_review_consent':True} if r.get('local_sources') else {}))
   self._current(r)
   from creative_review_receipts import save_review_receipt
   receipt_id=save_review_receipt(self.directory,r,assets,analysis)
   self._save('validating',r,review_receipt_id=receipt_id,review_stage='analysis_received',detail='מאמת התאמת פוסט וסטורי ואת הקופי')
   draft=build_creative_draft(r,assets,analysis)
   saved=self.proposal.save(draft,r);self._current(r);self._save('ready_for_owner',r,review_receipt_id=receipt_id,review_stage='analysis_received',proposal_id=saved['id'],ads=len(saved['ads']),detail='ההצעה מוכנה לעיון; אישור ותקציב נדרשים לפני יצירה במטא')
  except Exception as e:
   from creative_claims import CreativeClaimReviewRequired
   claim_review={'fact_review':{'status':'needs_fact_review','issues':e.issues,'coverage':'heuristic_not_semantic_guarantee','blocked_review_receipt_id':getattr(e,'blocked_review_receipt_id',None),'review_preserved':bool(receipt_id or getattr(e,'blocked_review_receipt_id',None))}} if isinstance(e,CreativeClaimReviewRequired) else {}
   if self.intake.latest()==r:self._save('blocked',r,**({'review_receipt_id':receipt_id,'review_stage':'analysis_received'} if receipt_id else {}),**({'source_issue':e.source_issue} if isinstance(e,CreativePairingError) else {}),**claim_review,detail=str(e)[:350])
  finally:
   with self.lock:self.active=None;queued=self.pending;self.pending=None
   if queued and self.intake.latest()==queued:self.start(queued)

def build_creative_draft(request,assets,analysis):
 destination=resolve_destination(request.get('goal'))
 if not isinstance(assets,list) or not 1<=len(assets)<=MAX_CREATIVE_IMAGES:raise ValueError('נדרשים עד 30 קבצים, שהם עד 15 זוגות פוסט וסטורי')
 rows=analysis.get('assets') if isinstance(analysis,dict) else None
 if not isinstance(rows,list) or len(rows)!=len(assets):raise ValueError('הניתוח אינו מכסה את כל התמונות')
 from creative_claims import validate_creative_claims
 validate_creative_claims(rows,{a['id']:a.get('name') for a in assets})
 byid={a['id']:a for a in assets};reviewed={};pairs={}
 for row in rows:
  a=byid.get(row.get('asset_id'))
  if not a or row.get('sha256')!=a.get('sha256') or a['id'] in reviewed:raise ValueError('הניתוח לא קשור לקובץ המקור המדויק')
  if row.get('format') not in ('feed','story'):raise ValueError('לא זוהה פורמט פוסט או סטורי מתאים')
  key=row.get('concept_key')
  if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('לא זוהה שיוך יצירתי לפוסט וסטורי')
  pair=pairs.setdefault(key,{})
  if row['format'] in pair:raise ValueError('יש יותר מגרסה אחת לאותו פורמט; נדרשת בחירה')
  pair[row['format']]=row;reviewed[a['id']]=row
 for pair in pairs.values():
  if set(pair)!={'feed','story'}:
   row=next(iter(pair.values()));raise CreativePairingError(request,byid[row['asset_id']],'story' if 'feed' in pair else 'feed')
 provenance=[]
 if 'review_provenance' in analysis:
  records=analysis['review_provenance'];seen=set()
  if not isinstance(records,list) or len(records)!=len(assets):raise ValueError('Incomplete review provenance')
  fields={'asset_id','sha256','review_sha256','review_derivative','source_bytes','review_bytes','width','height'}
  for record in records:
   if not isinstance(record,dict) or set(record)!=fields:raise ValueError('Invalid review provenance')
   identity=record['asset_id'];original=byid.get(identity)
   if not original or identity in seen or record['sha256']!=original['sha256']:raise ValueError('Review provenance source mismatch')
   if not isinstance(record['review_sha256'],str) or len(record['review_sha256'])!=64 or any(c not in '0123456789abcdef' for c in record['review_sha256']):raise ValueError('Invalid review digest')
   if type(record['review_derivative']) is not bool or any(type(record[k]) is not int or record[k]<=0 for k in ('source_bytes','review_bytes','width','height')):raise ValueError('Invalid review dimensions or size')
   if not record['review_derivative'] and record['review_sha256']!=record['sha256']:raise ValueError('Unchanged review hash mismatch')
   seen.add(identity);provenance.append(dict(record))
 ads=[];groups=[];membership={}
 if 'proposed_adsets' in analysis:
  proposed=validate_proposed_adsets(analysis['proposed_adsets'],rows)
  for group in proposed:
   internal_key='creative_'+hashlib.sha256(('proposed:'+group['key']).encode()).hexdigest()[:10]
   groups.append({'key':internal_key,'advisory_key':group['key'],'label':group['label'],'rationale':group['rationale'],'audience_hypothesis':None,'targeting_mode':'broad_il_adult','grouping_source':'model_advisory'})
   for concept in group['concept_keys']:membership[concept]=internal_key
 for concept,pair in pairs.items():
  if set(pair)!={'feed','story'}:raise ValueError('חסר זוג פוסט וסטורי עבור '+concept)
  f,t=pair['feed'],pair['story']
  if f.get('paired_asset_id')!=t['asset_id'] or t.get('paired_asset_id')!=f['asset_id']:raise ValueError('התאמת הפוסט והסטורי לא אושרה בניתוח')
  angle=f.get('angle')
  if not isinstance(angle,str) or not angle.strip():raise ValueError('חסרה זווית מהניתוח')
  key=membership.get(concept) or 'creative_'+hashlib.sha256(angle.encode()).hexdigest()[:10]
  if not any(g['key']==key for g in groups):groups.append({'key':key,'label':angle,'audience_hypothesis':f.get('audience_hypothesis'),'rationale':'קבוצת ניסוי חדשה לפי מסר חזותי משותף; הקהל אינו מסקנת ביצועים','targeting_mode':'broad_il_adult'})
  def source(row):
   a=byid[row['asset_id']]
   return {'asset_id':a['id'],'source_name':a.get('name',''),'source_kind':a.get('source_kind','drive'),'sha256':a['sha256'],'review':{'visual_checked':True,'method':'Claude image analysis; exact image SHA bound','notes':row.get('visual_description','')}}
  copy={k:f.get(k) for k in ('primary_text','headline','description')}
  # Description may be empty, matching CreativeVision's schema. Required text
  # remains strict; absence/non-string is malformed rather than optional copy.
  if any(not isinstance(v,str) or (k!='description' and not v.strip()) for k,v in copy.items()):raise ValueError('חסר קופי מלא למודעה')
  ads.append({**source(f),'story':source(t),**copy,'primary_text_options':[copy['primary_text']],'adset_key':key,'cta':'SHOP_NOW','url_tags':'utm_source={{site_source_name}}&utm_medium=paid_social&utm_campaign={{campaign.id}}&utm_content={{ad.id}}&adset_id={{adset.id}}','destination_url':destination['url'],'destination_source':destination['source'],'destination_verified_live':False,'classification':{k:f.get(k) for k in ('observed_product','angle','audience_hypothesis','confidence','warnings')}})
 if not 1<=len(ads)<=MAX_ADS:raise ValueError('ניתן להכין עד 15 מודעות בהצעה אחת')
 if not 1<=len(groups)<=MAX_ADSETS:raise ValueError('הניתוח הציע יותר משלושה אדסטים; נדרשת בחירה לפני יצירה')
 return {**({'review_provenance':provenance} if 'review_provenance' in analysis else {}),'id':'creative-'+hashlib.sha256((request['id']+str(request['revision'])).encode()).hexdigest()[:16],'summary':'הצעה אוטומטית מהתמונות: קופי, פוסט וסטורי ושיוך לפי זווית. קהל הוא השערה, לא עובדה.','required_format':'dedicated_feed_story_single_copy','creation_budget_entry_required':True,'adsets':groups,'ads':ads,'gaps':['יש לאשר יעד URL, תקציב לכל אדסט, טרגוט ותצוגות במטא.','המדיה והקופי נותחו; לא הוכחה רווחיות או עדיפות קהל.'],'current_authorization':{},'activation_available':False}
