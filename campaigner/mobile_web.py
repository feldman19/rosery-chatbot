"""Authenticated temporary mobile pilot. Loopback only behind HTTPS tunnel.
Remote capability: summary, reviewed proposal and owner notes. No ad writes.
"""
import json,secrets,time,hmac,re,threading,hashlib,urllib.request,urllib.error
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.cookies import SimpleCookie,CookieError
from business import ACTIVE as BUSINESS

ROOT=Path(__file__).resolve().parent
class MobileAccess:
 def __init__(self,key,expires,used_directory=None,session_file=None,session_seconds=8*3600):
  self.key=key;self.expires=expires;self.used=False;self.sessions={};self.attempts=[];self.lock=threading.Lock()
  self.used_directory=Path(used_directory) if used_directory is not None else None
  self.session_file=Path(session_file) if session_file is not None else None
  self.session_seconds=session_seconds
  if self.session_file and self.session_file.exists():
   from shopify_direct import crypt
   self.sessions=json.loads(crypt(self.session_file.read_bytes(),True))
 def save(self):
  if self.session_file:
   from shopify_direct import crypt
   temporary=self.session_file.with_suffix('.pending')
   temporary.write_bytes(crypt(json.dumps(self.sessions).encode()));temporary.replace(self.session_file)
 def revoke(self,value):
  with self.lock:
   self.sessions.pop(hashlib.sha256(value.encode()).hexdigest(),None);self.save()
 def login(self,key,now=None):
  now=time.time() if now is None else now
  with self.lock:
   self.attempts=[t for t in self.attempts if now-t<60]
   if len(self.attempts)>=30:return None
   self.attempts.append(now)
   if self.used or now>self.expires or not isinstance(key,str) or not hmac.compare_digest(key,self.key):return None
   if self.used_directory is not None:
    try:
     self.used_directory.mkdir(parents=True,exist_ok=True)
     with (self.used_directory/(hashlib.sha256(key.encode()).hexdigest()+'.used')).open('xb'):pass
    except OSError:return None
   self.used=True;session=secrets.token_urlsafe(32)
   self.sessions={k:v for k,v in self.sessions.items() if v>now}
   self.sessions[hashlib.sha256(session.encode()).hexdigest()]=now+self.session_seconds;self.save();return session
 def valid(self,cookie,now=None):
  try:c=SimpleCookie();c.load(cookie or '');value=c['rosery_mobile'].value
  except (KeyError,ValueError,CookieError):return False
  with self.lock:return self.sessions.get(hashlib.sha256(value.encode()).hexdigest(),0)>(time.time() if now is None else now)

class BackendRejected(ValueError):
 def __init__(self,status):self.status=status

class Backend:
 def __init__(self,backend_port=8767):
  if type(backend_port) is not int or not 1<=backend_port<=65535:raise ValueError('Valid loopback manager port required')
  self.base='http://127.0.0.1:'+str(backend_port)
  self.token=None;self.media_cookie='';self.lock=threading.Lock()
 def request(self,path,body=None):
  with self.lock:
   for attempt in range(2 if body is None else 1):
    if not self.token:
     with urllib.request.urlopen(self.base+'/',timeout=15) as r:
      page=r.read(4*1024*1024).decode();match=re.search(r"const token='([A-Za-z0-9_-]{32,100})'",page)
      if not match:raise ValueError('Local session unavailable')
      self.token=match.group(1)
      cookie_headers=r.headers.get_all('Set-Cookie') if hasattr(r.headers,'get_all') else [r.headers.get('Set-Cookie','')]
      self.media_cookie='; '.join(value.split(';')[0] for value in (cookie_headers or []) if value and value.split('=',1)[0] in ('campaigner_media','campaigner_preparation_media'))
    headers={'X-Local-Session':self.token,'Cookie':self.media_cookie,'Sec-Fetch-Site':'same-origin'}
    if body is not None:headers.update({'Origin':self.base,'Content-Type':'application/json'})
    req=urllib.request.Request(self.base+path,data=body,headers=headers)
    try:
     with urllib.request.urlopen(req,timeout=25) as r:
      raw=r.read(8*1024*1024+1)
      if len(raw)>8*1024*1024:raise ValueError('Response too large')
      return raw,r.headers.get('Content-Type','application/json')
    except urllib.error.HTTPError as e:
     if e.code==403:
      self.token=None;self.media_cookie=''
      # A manager restart rotates its local session. Only a read may be
      # repeated once after obtaining a new token; writes are NEVER replayed.
      if body is None and attempt==0:continue
     if e.code==400:raise BackendRejected(400) from None
     raise ValueError('Local request unavailable') from None

def make_server(directory=None,port=8768,access=None,backend=None):
 directory=Path(directory or ROOT/'.local');backend=backend or Backend()
 if access is None:
  from shopify_direct import crypt
  value=json.loads(crypt((directory/'mobile-access.dpapi').read_bytes(),True));access=MobileAccess(value['key'],value['expires'],directory/'mobile-access-used',directory/'mobile-sessions.dpapi',8*3600)
 def origin():
  value=json.loads((directory/'mobile-origin.json').read_text())['origin']
  if not re.fullmatch(r'https://[a-z0-9-]+\.(?:trycloudflare\.com|ngrok-free\.dev|ngrok-free\.app)',value):raise ValueError('Pinned HTTPS origin required')
  return value
 def execution_enabled():
  try:return json.loads((directory/'mobile-origin.json').read_text()).get('remote_execution_enabled') is True
  except (OSError,ValueError,TypeError):return False
 class Handler(BaseHTTPRequestHandler):
  def setup(self):super().setup();self.connection.settimeout(10)
  def log_message(self,*args):pass
  def reply(self,status,value,kind='application/json; charset=utf-8',cookie=None):
   raw=value if isinstance(value,bytes) else (json.dumps(value,ensure_ascii=False) if kind.startswith('application/json') else value).encode()
   self.send_response(status)
   for key,val in {'Content-Type':kind,'Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' https://*.fbcdn.net https://*.cdninstagram.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",'Permissions-Policy':'camera=(), microphone=(), geolocation=()'}.items():self.send_header(key,val)
   if cookie:self.send_header('Set-Cookie',cookie)
   self.end_headers();self.wfile.write(raw)
  def host_ok(self):
   try:return self.headers.get('Host')==origin()[8:] and self.headers.get('X-Forwarded-Proto')=='https'
   except (OSError,ValueError,KeyError):return False
  def do_GET(self):
   if not self.host_ok():return self.reply(403,{'error':'HTTPS origin required'})
   if self.path=='/':return self.reply(200,(ROOT/'mobile-web.html').read_bytes(),'text/html; charset=utf-8')
   if not access.valid(self.headers.get('Cookie')):return self.reply(401,{'error':'Sign in required'})
   try:
    if self.path=='/api/remote-capabilities':return self.reply(200,{'can_manage_campaigns':execution_enabled(),'approval_required':True,'account_id':BUSINESS.meta_account})
    if self.path=='/api/campaign-operations':
     if not execution_enabled():return self.reply(403,{'error':'Campaign management is not authorized remotely'})
     raw,_=backend.request('/api/campaign-operations');return self.reply(200,raw)
    if self.path=='/api/status':
     raw,_=backend.request('/api/work/status');report=json.loads(raw)
     identity=report.get('request') or {}
     return self.reply(200,{**{k:report.get(k) for k in ('goal','state','current_state','next_output','completed','blocked','checked_at')},'request_identity':{'id':identity.get('id'),'revision':identity.get('revision')}})
    if self.path=='/api/notes':
     raw,_=backend.request('/api/workspace');return self.reply(200,{'notes':json.loads(raw).get('questions',[])[-20:]})
    if self.path=='/api/proposal':
     raw,_=backend.request('/api/launch/proposal');return self.reply(200,raw)
    if self.path=='/api/preparation-task':
     raw,_=backend.request('/api/preparation-task');return self.reply(200,raw)
    if self.path=='/api/visual-workspace':
     raw,_=backend.request('/api/visual-workspace');return self.reply(200,raw)
    if self.path=='/api/management-report':
     raw,_=backend.request('/api/management-report');return self.reply(200,raw)
    if re.fullmatch(r'/api/preparation-task/media/(?:thumb/)?[A-Za-z0-9_-]{10,150}~[a-f0-9]{64}',self.path):
     raw,kind=backend.request(self.path)
     if not kind.startswith(('image/png','image/jpeg')):raise ValueError('Not an image')
     return self.reply(200,raw,kind)
    if re.fullmatch(r'/api/media/[A-Za-z0-9_-]{5,160}',self.path):
     raw,kind=backend.request(self.path.replace('/api/media/','/api/launch/proposal/media/'))
     if not kind.startswith(('image/png','image/jpeg')):raise ValueError('Not an image')
     return self.reply(200,raw,kind)
    return self.reply(404,{'error':'Not found'})
   except (OSError,ValueError,KeyError):return self.reply(503,{'error':'Data currently unavailable'})
  def do_POST(self):
   if not self.host_ok() or self.headers.get('Origin')!=origin():return self.reply(403,{'error':'Same HTTPS origin required'})
   operation_paths={'/api/campaign-operations/preview','/api/campaign-operations/approve','/api/campaign-operations/execute','/api/campaign-operations/schedule','/api/campaign-operations/cancel'}
   if self.path not in ('/auth','/api/notes','/logout') and self.path not in operation_paths:return self.reply(403,{'error':'Operation unavailable remotely'})
   try:
    size=int(self.headers.get('Content-Length','0'))
    if not 1<=size<=8192 or self.headers.get('Transfer-Encoding') or not self.headers.get('Content-Type','').startswith('application/json'):raise ValueError()
    body=json.loads(self.rfile.read(size))
    if not isinstance(body,dict):raise ValueError()
   except (ValueError,TypeError):return self.reply(400,{'error':'Invalid request'})
   if self.path=='/auth':
    session=access.login(body.get('key'))
    if not session:return self.reply(401,{'error':'Link expired or already used'})
    return self.reply(200,{'signed_in':True},cookie='rosery_mobile='+session+'; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age='+str(access.session_seconds))
   if not access.valid(self.headers.get('Cookie')):return self.reply(401,{'error':'Sign in required'})
   if self.path in operation_paths:
    if not execution_enabled():return self.reply(403,{'error':'Campaign management is not authorized remotely'})
    if not valid_operation_body(self.path,body):return self.reply(400,{'error':'Exact operation fields required'})
    try:
     raw,_=backend.request(self.path,json.dumps(body).encode());return self.reply(200,raw)
    except BackendRejected:return self.reply(400,{'error':'The operation was rejected; inspect the proposal before retrying'})
    except (OSError,ValueError):return self.reply(503,{'error':'Execution is unconfirmed. Check status before retrying.'})
   if self.path=='/logout':
    c=SimpleCookie();c.load(self.headers.get('Cookie',''))
    access.revoke(c['rosery_mobile'].value)
    return self.reply(200,{'signed_out':True},cookie='rosery_mobile=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0')
   if set(body) not in ({'question'},{'question','request_id'}):return self.reply(400,{'error':'Note only'})
   try:
    raw,_=backend.request('/api/help/escalate',json.dumps(body).encode());return self.reply(200,raw)
   except BackendRejected:return self.reply(400,{'error':'Note rejected; remove personal details or secrets'})
   except (OSError,ValueError):return self.reply(503,{'error':'Note save unconfirmed'})
 return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def valid_operation_body(path,body):
 """A bounded relay, never an arbitrary manager API proxy. Approval stays in the engine."""
 if not isinstance(body,dict):return False
 operation=lambda: isinstance(body.get('operation_id'),str) and re.fullmatch('[a-f0-9]{32}',body['operation_id']) is not None
 if path.endswith(('/approve','/cancel')):return set(body)=={'operation_id','digest'} and operation() and isinstance(body.get('digest'),str) and re.fullmatch('[a-f0-9]{64}',body['digest']) is not None
 if path.endswith('/execute'):return set(body)=={'operation_id'} and operation()
 if path.endswith('/schedule'):return set(body)=={'operation_id','at'} and operation() and isinstance(body.get('at'),str) and len(body['at'])<=40
 if not path.endswith('/preview'):return False
 allowed={'action','scheduled_at'}
 if body.get('action') in ('pause','activate','set_budget'):
  allowed|={'entity_type','entity_id'}
  if body.get('entity_type') not in ('campaign','adset','ad') or not isinstance(body.get('entity_id'),str) or not re.fullmatch('[0-9]{5,30}',body['entity_id']):return False
  if body['action']=='set_budget':
   allowed.add('budget_minor')
   if type(body.get('budget_minor')) is not int or not 0<body['budget_minor']<=100000000:return False
 elif body.get('action')=='prepare_launch':
  allowed|={'selected_asset_ids','selected_adset_keys','copy_variant_indices','expected_source','format_mode','creation_budget_minor'}
  if 'creation_budget_minor' in body and (type(body['creation_budget_minor']) is not int or not 1<=body['creation_budget_minor']<=200000):return False
  if 'format_mode' in body and body['format_mode'] not in ('feed_original','dedicated_feed_story_single_copy'):return False
  if 'expected_source' in body:
   binding=body['expected_source']
   if not isinstance(binding,dict) or set(binding)!={'request_id','request_revision','proposal_hash'} or not isinstance(binding['request_id'],str) or not 1<=len(binding['request_id'])<=150 or type(binding['request_revision']) is not int or binding['request_revision']<1 or not isinstance(binding['proposal_hash'],str) or not re.fullmatch('[a-f0-9]{64}',binding['proposal_hash']):return False
  if 'copy_variant_indices' in body:
   values=body['copy_variant_indices']
   if not isinstance(values,dict) or len(values)>10 or any(not isinstance(k,str) or not re.fullmatch('[A-Za-z0-9_-]{10,150}',k) or type(v) is not int or not 0<=v<5 for k,v in values.items()):return False
  for field,maximum,pattern in (('selected_asset_ids',10,'[A-Za-z0-9_-]{10,150}'),('selected_adset_keys',2,'[A-Za-z0-9_-]{1,80}')):
   if field in body:
    values=body[field]
    if not isinstance(values,list) or not 1<=len(values)<=maximum or not all(isinstance(value,str) and re.fullmatch(pattern,value) for value in values) or len(set(values))!=len(values):return False
 else:return False
 if 'scheduled_at' in body and (not isinstance(body['scheduled_at'],str) or len(body['scheduled_at'])>40):return False
 return set(body)<=allowed

if __name__=='__main__':make_server().serve_forever()
