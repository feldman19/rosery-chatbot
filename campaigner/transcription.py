"""Local speech-to-text for ad videos (faster-whisper on this machine). Audio never leaves it.

The model downloads once on first use. A failure or an empty result is reported as
"not checked" or "no speech", never as an invented transcript.
"""
import io
import threading
import wave

MODEL = 'small'  # multilingual, handles Hebrew; about 500 MB on first use
_model = None
_lock = threading.Lock()


def _load():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL, device='cpu', compute_type='int8')
        return _model


def _samples(wav_bytes):
    import numpy
    with wave.open(io.BytesIO(wav_bytes)) as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != 16000:
            raise ValueError('Expected 16 kHz mono 16-bit audio')
        raw = clip.readframes(clip.getnframes())
    return numpy.frombuffer(raw, dtype=numpy.int16).astype(numpy.float32) / 32768.0


def transcribe(wav_bytes, model_loader=None):
    """Returns {'status': 'transcribed'|'no_speech'|'not_checked', 'text', 'language', 'segments'}."""
    if not wav_bytes:
        return {'status': 'no_speech', 'text': '', 'language': None, 'segments': []}
    try:
        model = (model_loader or _load)()
        # ffmpeg already produced 16 kHz mono PCM; hand the samples over directly instead of
        # faster-whisper's own decoder, which breaks with some installed PyAV versions.
        samples = _samples(wav_bytes)
        segments, info = model.transcribe(samples, vad_filter=True, beam_size=1)
        rows = [{'start_ms': int(s.start * 1000), 'end_ms': int(s.end * 1000), 'text': s.text.strip()}
                for s in segments if s.text.strip()]
    except Exception:
        return {'status': 'not_checked', 'text': '', 'language': None, 'segments': [],
                'reason': 'התמלול המקומי נכשל; הקול לא נבדק'}
    text = ' '.join(r['text'] for r in rows)[:4000]
    return {'status': 'transcribed' if text else 'no_speech', 'text': text,
            'language': getattr(info, 'language', None), 'segments': rows[:200]}
