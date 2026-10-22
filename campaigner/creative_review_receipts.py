"""Immutable local advisory review receipts. Never authorization or auto-reuse."""
import hashlib,inspect,json,re
from datetime import datetime
from pathlib import Path
from creative_vision import CreativeVision,vision_output_schema,MAX_OUTPUT
from batch_limits import MAX_CREATIVE_IMAGES


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def save_review_receipt(directory,request,assets,analysis):
    identity=request.get('id');revision=request.get('revision')
    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',identity) or type(revision) is not int or revision<1:raise ValueError('Invalid review request binding')
    if not isinstance(assets,list) or not 1<=len(assets)<=MAX_CREATIVE_IMAGES:raise ValueError('Bounded review sources required')
    if not isinstance(analysis,dict) or analysis.get('creative_reviewed') is not True or analysis.get('review_kind')!='model_advisory':raise ValueError('Validated advisory review required')
    checked=analysis.get('checked_at')
    if not isinstance(checked,str) or len(checked)>80:raise ValueError('Dated review required')
    try:
        if datetime.fromisoformat(checked.replace('Z','+00:00')).tzinfo is None:raise ValueError()
    except ValueError:raise ValueError('Aware review timestamp required') from None
    sources=[];seen=set()
    for asset in assets:
        aid=asset.get('id');sha=asset.get('sha256');dims=asset.get('dimensions') or {}
        w,h=dims.get('width'),dims.get('height')
        if not isinstance(aid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}',aid) or aid in seen or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('Exact review source binding required')
        if type(w) is not int or type(h) is not int or min(w,h)<=0:raise ValueError('Review source dimensions required')
        ratio=w/h;fmt='story' if abs(ratio-9/16)<0.015 else 'feed' if .79<=ratio<=1.92 else 'other'
        sources.append({'asset_id':aid,'sha256':sha,'format':fmt});seen.add(aid)
    # Recheck source-bound shape before preserving any advisory text.
    review={key:analysis[key] for key in ('assets','proposed_adsets') if key in analysis}
    CreativeVision._validate(review,sources)
    # Explicit allowlist: no images, paths, raw events, credentials, or request text.
    payload={'receipt_version':1,'request_id':identity,'request_revision':revision,
        'checked_at':checked,'stage':'analysis_received','review_kind':'model_advisory',
        'execution_authority':False,'automatic_reuse':False,'sources':sources,'review':review,
        'prompt_version':'analyze-sha256:'+hashlib.sha256(inspect.getsource(CreativeVision.analyze).encode()).hexdigest(),
        'schema_version':'json-schema-sha256:'+hashlib.sha256(canonical(vision_output_schema()).encode()).hexdigest()}
    encoded=canonical(payload).encode('utf-8')
    if len(encoded)>MAX_OUTPUT:raise ValueError('Review receipt exceeds bound')
    receipt_id=hashlib.sha256(encoded).hexdigest()
    root=Path(directory)/'creative-review-receipts'
    if root.is_symlink():raise ValueError('Unsafe review receipt directory')
    root.mkdir(exist_ok=True)
    path=root/(receipt_id+'.json')
    if path.is_symlink():raise ValueError('Unsafe review receipt file')
    try:
        with path.open('xb') as stream:stream.write(encoded)
    except FileExistsError:
        if path.stat().st_size>MAX_OUTPUT or path.read_bytes()!=encoded:raise ValueError('Review receipt collision or corruption') from None
    return receipt_id
