"""Decode previews with Pillow, using the installed bundled runtime if needed."""
import json,re,subprocess,sys
from pathlib import Path
from functools import lru_cache
from batch_limits import MAX_UPLOAD_IMAGE_BYTES

MAX_IMAGE_PIXELS=40_000_000
THUMB_MAX_BYTES=400*1024
SOURCE_MAX_BYTES=64*1024*1024
REVIEW_MAX_BYTES=600*1024
REVIEW_MAX_EDGE=1568


def validate_upload_sources(directory,assets):
    """Early upload readiness check before model review; never modifies files.

    This is not a replacement for SHA verification immediately before execution.
    Review-only consumers can still accept the separate 64 MiB source bound.
    """
    root=Path(directory)/'drive-assets'
    if root.is_symlink() or not root.is_dir():raise ValueError('תיקיית חומרי המקור אינה זמינה; יש לייבא שוב את התמונות')
    for row in assets:
        identity=row.get('id',row.get('asset_id'));digest=row.get('sha256')
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,150}',identity) or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('חסר זיהוי מדויק של קובץ המקור; יש לייבא שוב')
        name=str(row.get('name') or identity)[:120]
        path=root/(identity+'-'+digest+'.bin')
        if path.is_symlink() or path.resolve().parent!=root.resolve() or not path.is_file():raise ValueError('קובץ המקור '+name+' אינו זמין; יש לייבא שוב')
        size=path.stat().st_size
        if size<=0:raise ValueError('קובץ המקור '+name+' ריק; יש להחליף אותו')
        if size>MAX_UPLOAD_IMAGE_BYTES:
            raise ValueError('הקובץ '+name+' גדול מדי להעלאה במסלול הנוכחי ('+format(size/1024/1024,'.1f')+' MiB). יש לייצא JPG או PNG עד 20 MiB ולהעלות מחדש. הניתוח לא התחיל והמקור לא שונה.')

def _image_python():
    try:
        import PIL
        return sys.executable
    except ImportError:
        executable=str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe')
        if not Path(executable).is_file():raise ValueError('Image decoder unavailable; preview not approved')
        return executable


def review_image(path,digest):
    """Hash-verified, metadata-free JPEG derivative; never rewrites the source."""
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Exact image digest required')
    program="""import hashlib,io,sys,warnings
from PIL import Image,ImageOps
Image.MAX_IMAGE_PIXELS=__MAX_IMAGE_PIXELS__
warnings.simplefilter('error',Image.DecompressionBombWarning)
with open(sys.argv[1],'rb') as stream:raw=stream.read(64*1024*1024+1)
if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()!=sys.argv[2]:raise ValueError('source changed')
with Image.open(io.BytesIO(raw)) as source:
 if source.format not in ('JPEG','PNG') or source.width*source.height>__MAX_IMAGE_PIXELS__:raise ValueError('unsupported image')
 source.load()
 image=ImageOps.exif_transpose(source)
 image.thumbnail((1568,1568),Image.Resampling.LANCZOS)
 rgba=image.convert('RGBA');image=Image.new('RGB',rgba.size,'white');image.paste(rgba,mask=rgba.getchannel('A'))
 for edge in (1568,1280,1024,768):
  image.thumbnail((edge,edge),Image.Resampling.LANCZOS)
  for quality in (88,80,70):
   output=io.BytesIO();image.save(output,format='JPEG',quality=quality,optimize=True)
   result=output.getvalue()
   if len(result)<=600*1024:sys.stdout.buffer.write(result);sys.exit(0)
 raise ValueError('review derivative bound')
"""
    program=program.replace("__MAX_IMAGE_PIXELS__",str(MAX_IMAGE_PIXELS))
    try:result=subprocess.run([_image_python(),'-I','-c',program,str(path),digest],capture_output=True,timeout=30,check=False)
    except (subprocess.SubprocessError,OSError):raise ValueError('Review image unavailable') from None
    if result.returncode or not result.stdout.startswith(b'\xff\xd8\xff') or len(result.stdout)>REVIEW_MAX_BYTES:raise ValueError('Review image validation failed')
    return result.stdout

@lru_cache(maxsize=256)
def decode_image(path,digest):
    # Caller hashes actual bytes before consulting this cache.
    program="""import json,sys,warnings
from PIL import Image
Image.MAX_IMAGE_PIXELS=__MAX_IMAGE_PIXELS__
warnings.simplefilter('error',Image.DecompressionBombWarning)
with Image.open(sys.argv[1]) as im:
 if im.format not in ('JPEG','PNG') or im.width*im.height>__MAX_IMAGE_PIXELS__:raise ValueError('unsupported image')
 im.load()
 print(json.dumps({'width':im.width,'height':im.height,'format':im.format}))
"""
    program=program.replace("__MAX_IMAGE_PIXELS__",str(MAX_IMAGE_PIXELS))
    try:
        result=subprocess.run([_image_python(),'-I','-c',program,str(path)],capture_output=True,timeout=15,check=False)
    except (subprocess.SubprocessError,OSError):
        raise ValueError('Image decoder unavailable') from None
    if result.returncode or len(result.stdout)>1000:raise ValueError('Image decode failed')
    value=json.loads(result.stdout)
    if type(value.get('width')) is not int or type(value.get('height')) is not int or not 0<value['width']*value['height']<=MAX_IMAGE_PIXELS:raise ValueError('Image dimension bound')
    return value


@lru_cache(maxsize=64)
def thumbnail_image(path,digest):
    """Bounded derivative only. Caller must rehash source before each cache lookup."""
    if not isinstance(path,str) or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Exact image digest required')
    program="""import hashlib,io,sys,warnings
from PIL import Image,ImageOps
Image.MAX_IMAGE_PIXELS=__MAX_IMAGE_PIXELS__
warnings.simplefilter('error',Image.DecompressionBombWarning)
with open(sys.argv[1],'rb') as stream:raw=stream.read(64*1024*1024+1)
if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()!=sys.argv[2]:raise ValueError('source changed')
with Image.open(io.BytesIO(raw)) as source:
 if source.format not in ('JPEG','PNG') or source.width*source.height>__MAX_IMAGE_PIXELS__:raise ValueError('unsupported image')
 source.load()
 image=ImageOps.exif_transpose(source)
 image.thumbnail((480,480),Image.Resampling.LANCZOS)
 if image.mode in ('RGBA','LA') or image.mode=='P' and 'transparency' in image.info:
  rgba=image.convert('RGBA');canvas=Image.new('RGB',image.size,'white');canvas.paste(rgba,mask=rgba.getchannel('A'));image=canvas
 else:image=image.convert('RGB')
 output=io.BytesIO();image.save(output,format='JPEG',quality=80,optimize=True,progressive=True)
 result=output.getvalue()
 if not result or len(result)>400*1024:raise ValueError('thumbnail bound')
 sys.stdout.buffer.write(result)
"""
    program=program.replace("__MAX_IMAGE_PIXELS__",str(MAX_IMAGE_PIXELS))
    try:
        result=subprocess.run([_image_python(),'-I','-c',program,path,digest],capture_output=True,timeout=15,check=False)
    except (subprocess.SubprocessError,OSError):raise ValueError('Thumbnail unavailable') from None
    if result.returncode or not result.stdout.startswith(b'\xff\xd8\xff') or len(result.stdout)>THUMB_MAX_BYTES:raise ValueError('Thumbnail validation failed')
    return result.stdout
