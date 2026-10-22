"""Private device-image intake; never sends images to any external provider."""
import hashlib,hmac,json,re,threading
from pathlib import Path
from image_validation import decode_image
from artifact_lock import artifact_lock
from batch_limits import MAX_UPLOAD_IMAGE_BYTES
class LocalUploads:
 def __init__(self,directory,key):
  if not isinstance(key,bytes) or len(key)<32:raise ValueError('Host key required')
  self.directory=Path(directory);self.key=key;self.lock=threading.RLock()
 def _receipt(self,identity):return self.directory/'local-upload-receipts'/(identity+'.json')
 def put(self,stream,*,size,name,mime,upload_id):
  if type(size) is not int or not 0<size<=MAX_UPLOAD_IMAGE_BYTES:raise ValueError('יש לבחור תמונת JPG/PNG עד 20 MiB')
  if mime not in ('image/jpeg','image/png'):raise ValueError('רק JPG ו־PNG נתמכים')
  if not isinstance(upload_id,str) or not re.fullmatch('[A-Za-z0-9_-]{16,80}',upload_id):raise ValueError('Invalid upload identity')
  if not isinstance(name,str) or not 1<=len(name)<=160 or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):raise ValueError('שם קובץ לא תקין')
  identity='local_'+hashlib.sha256(upload_id.encode()).hexdigest()[:32]
  root=self.directory/'drive-assets';receipts=self.directory/'local-upload-receipts'
  if root.is_symlink() or receipts.is_symlink():raise ValueError('Private storage required')
  root.mkdir(exist_ok=True);receipts.mkdir(exist_ok=True)
  chunks=[];remaining=size
  while remaining:
   chunk=stream.read(min(65536,remaining))
   if not chunk:break
   chunks.append(chunk);remaining-=len(chunk)
  raw=b"".join(chunks)
  if len(raw)!=size:raise ValueError('ההעלאה לא הושלמה; נסה שוב')
  actual='image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None
  if actual!=mime:raise ValueError('סוג הקובץ אינו תואם לתמונה')
  sha=hashlib.sha256(raw).hexdigest();path=root/(identity+'-'+sha+'.bin')
  with self.lock,artifact_lock(receipts/(identity+'.lock')):
   receipt=self._receipt(identity)
   if receipt.exists():
    old=self.resolve([{'id':identity,'sha256':sha}])[0]
    if old['name']!=name or old['mime_type']!=mime:raise ValueError('Upload identity changed')
    return old
   path.write_bytes(raw)
   try:dimensions=decode_image(str(path.resolve()),sha)
   except Exception:
    path.unlink(missing_ok=True);raise ValueError('לא ניתן לקרוא את התמונה או שהיא גדולה מ־40 מיליון פיקסלים') from None
   asset={'id':identity,'sha256':sha,'name':name,'mime_type':mime,'bytes':size,'width':dimensions['width'],'height':dimensions['height'],'dimensions':{'width':dimensions['width'],'height':dimensions['height']},'source_kind':'device','content_read':True,'format_check':'decoded'}
   payload=json.dumps(asset,sort_keys=True,ensure_ascii=False);envelope={'payload':payload,'signature':hmac.new(self.key,payload.encode(),hashlib.sha256).hexdigest()}
   pending=receipt.with_suffix('.pending');pending.write_text(json.dumps(envelope),encoding='utf-8');pending.replace(receipt)
   return asset
 def resolve(self,sources):
  if not isinstance(sources,list) or not 1<=len(sources)<=30:raise ValueError('בחר עד 30 תמונות')
  if (self.directory/'drive-assets').is_symlink() or (self.directory/'local-upload-receipts').is_symlink():raise ValueError('Private storage required')
  result=[];seen=set();hashes=set()
  for source in sources:
   if not isinstance(source,dict) or set(source)!={'id','sha256'}:raise ValueError('Exact uploaded source required')
   identity=source['id'];sha=source['sha256']
   if not isinstance(identity,str) or not re.fullmatch('local_[a-f0-9]{32}',identity) or identity in seen:raise ValueError('Invalid local identity')
   receipt=self._receipt(identity)
   if receipt.is_symlink():raise ValueError('Private receipt required')
   envelope=json.loads(receipt.read_text(encoding='utf-8'));raw=envelope['payload']
   if not hmac.compare_digest(envelope['signature'],hmac.new(self.key,raw.encode(),hashlib.sha256).hexdigest()):raise ValueError('Upload receipt changed')
   asset=json.loads(raw)
   if asset['id']!=identity or asset['sha256']!=sha:raise ValueError('Uploaded source changed')
   path=self.directory/'drive-assets'/(identity+'-'+sha+'.bin')
   if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_UPLOAD_IMAGE_BYTES or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('Uploaded bytes changed')
   if sha in hashes:raise ValueError('אותה תמונה נבחרה יותר מפעם אחת; בחר כל קובץ מקור פעם אחת')
   hashes.add(sha);seen.add(identity);result.append(asset)
  return result
