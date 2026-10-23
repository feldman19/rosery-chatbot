"""Local video checks and review material. Uses ffmpeg/ffprobe on this machine only.

A video is never sent anywhere from here. probe() reads technical facts; frames()
returns a few still images (the first one is the hook) for the image reviewer;
audio() returns 16 kHz mono WAV bytes for local transcription. Anything the tools
cannot read is an error, never a guessed value.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

MAX_VIDEO_BYTES = 300 * 1024 * 1024
MAX_SECONDS = 180
FRAME_COUNT = 6
FRAME_EDGE = 1280
VIDEO_MIME = {'video/mp4': 'mp4', 'video/quicktime': 'mov'}


def tool(name):
    """ffmpeg/ffprobe from PATH, or from the winget install when PATH is not refreshed yet."""
    found = shutil.which(name)
    if found:
        return found
    base = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Packages'
    hits = sorted(base.glob(f'Gyan.FFmpeg_*/ffmpeg-*/bin/{name}.exe'))
    if not hits:
        raise ValueError('כלי הווידאו ffmpeg לא מותקן במחשב')
    return str(hits[-1])


def sniff(raw_head):
    """MIME from the container signature (ISO base media 'ftyp' box), never from the file name."""
    if len(raw_head) < 12 or raw_head[4:8] != b'ftyp':
        return None
    brand = raw_head[8:12]
    return 'video/quicktime' if brand == b'qt  ' else 'video/mp4'


def _run(args, timeout=60):
    done = subprocess.run(args, capture_output=True, timeout=timeout)
    if done.returncode:
        raise ValueError('ffmpeg לא הצליח לקרוא את הסרטון')
    return done.stdout


def probe(path):
    raw = _run([tool('ffprobe'), '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(path)])
    data = json.loads(raw.decode('utf-8', 'replace'))
    streams = data.get('streams') or []
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    if not video:
        raise ValueError('לא נמצא ערוץ וידאו בקובץ')
    try:
        duration = float((data.get('format') or {}).get('duration') or video.get('duration'))
        width, height = int(video['width']), int(video['height'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('חסרים משך או מידות בסרטון') from None
    rotation = 0
    for side in video.get('side_data_list') or []:
        if isinstance(side.get('rotation'), (int, float)):
            rotation = int(side['rotation'])
    if abs(rotation) in (90, 270):
        width, height = height, width
    if not 0 < duration <= MAX_SECONDS:
        raise ValueError(f'הסרטון ארוך מ-{MAX_SECONDS} שניות או שמשכו לא ידוע')
    ratio = width / height
    placement = 'story' if abs(ratio - 9 / 16) < 0.02 else 'feed' if 0.79 <= ratio <= 1.01 else 'other'
    return {'duration_ms': int(duration * 1000), 'width': width, 'height': height, 'codec': video.get('codec_name'),
            'has_audio': audio is not None, 'aspect': f'{width}:{height}', 'format': placement}


def frames(path, duration_ms, count=FRAME_COUNT):
    """Evenly spaced JPEG stills; the first at 0.5 s is the opening hook. Returns [(ms, bytes)]."""
    times = [min(500, duration_ms // 2)] + [int(duration_ms * (i + 1) / (count + 1)) for i in range(1, count)]
    out = []
    with tempfile.TemporaryDirectory(prefix='video-frames-') as temp:
        for index, ms in enumerate(times):
            target = Path(temp) / f'{index}.jpg'
            _run([tool('ffmpeg'), '-v', 'error', '-ss', f'{ms / 1000:.3f}', '-i', str(path), '-frames:v', '1',
                  '-vf', f"scale='if(gt(iw,ih),{FRAME_EDGE},-2)':'if(gt(iw,ih),-2,{FRAME_EDGE})'", '-q:v', '4', str(target)])
            if not target.is_file() or target.stat().st_size == 0:
                raise ValueError('לא הצלחתי להוציא פריים מהסרטון')
            out.append((ms, target.read_bytes()))
    return out


def audio(path):
    """16 kHz mono WAV for local transcription; None when the video has no sound."""
    if not probe(path)['has_audio']:
        return None
    with tempfile.TemporaryDirectory(prefix='video-audio-') as temp:
        target = Path(temp) / 'audio.wav'
        _run([tool('ffmpeg'), '-v', 'error', '-i', str(path), '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', str(target)],
             timeout=120)
        return target.read_bytes() if target.is_file() and target.stat().st_size > 44 else None
