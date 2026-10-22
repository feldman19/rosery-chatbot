"""Host-reviewed draft artifacts. No ad creation, activation or approval capability."""
from batch_limits import MAX_ADSETS, MAX_ADS, MAX_CREATIVE_IMAGES, MAX_DRIVE_SOURCES

import hashlib,hmac,json,re
from pathlib import Path
from datetime import datetime,timezone
from image_validation import decode_image
from artifact_lock import artifact_lock


class LaunchProposal:
    def __init__(self,directory,key):
        self.directory=Path(directory);self.path=self.directory/'launch-proposal.json';self.key=key

    def save(self,proposal,request,expected_hash=None):
        with artifact_lock(self.directory/'launch-proposal.lock'):
            if expected_hash is not None:
                current=self.report(request)
                raw=json.dumps(current.get('proposal'),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
                if not current.get('available') or hashlib.sha256(raw.encode()).hexdigest()!=expected_hash:raise ValueError('Proposal changed before repair')
            return self._save_locked(proposal,request)

    def _save_locked(self,proposal,request):
        value=json.loads(json.dumps(proposal,ensure_ascii=False))
        value.update(request_id=request['id'],request_revision=request['revision'],prepared_at=datetime.now(timezone.utc).isoformat(),activation_available=False,state='draft_for_review',prepared_by='agent_review')
        if not isinstance(value.get('ads'),list) or not 1<=len(value['ads'])<=MAX_ADS:raise ValueError('Bounded reviewed ads required')
        for ad in value['ads']:
            for row in [ad]+([ad['story']] if isinstance(ad.get('story'),dict) else []):
                self._membership(row,request)
                self._file(row)
                if (row.get('review') or {}).get('visual_checked') is not True:raise ValueError('Actual visual review required')
                row['media_url']='/api/launch/proposal/media/'+row['asset_id']
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
        if len(raw)>200000:raise ValueError('Proposal bound')
        envelope={'payload':raw,'signature':hmac.new(self.key,raw.encode(),hashlib.sha256).hexdigest()}
        pending=self.path.with_suffix('.pending');pending.write_text(json.dumps(envelope,ensure_ascii=False),encoding='utf-8');pending.replace(self.path)
        return value

    def _membership(self,row,request):
        from launch_workflow import LaunchWorkflow
        current=LaunchWorkflow(self.directory).report({})['request']
        if not current or current['brief']!=request['goal'] or current['drive_urls']!=request['drive_urls'] or current.get('local_sources',[])!=request.get('local_sources',[]):raise ValueError('Current source request mismatch')
        envelope=json.loads((self.directory/'launch-materials.json').read_text(encoding='utf-8'));raw=envelope['payload']
        if not hmac.compare_digest(envelope['signature'],hmac.new(self.key,raw.encode(),hashlib.sha256).hexdigest()):raise ValueError('Material signature invalid')
        evidence=json.loads(raw)
        if (evidence['request_id'],evidence['request_revision'],evidence['drive_urls'])!=(current['id'],current['revision'],current['drive_urls']):raise ValueError('Material request mismatch')
        if evidence.get('local_sources',[])!=current.get('local_sources',[]):raise ValueError('Local material request mismatch')
        rows=(evidence.get('assets') or {}).get('assets',[])
        if not any(x.get('id')==row['asset_id'] and x.get('sha256')==row['sha256'] and x.get('content_read') is True for x in rows):raise ValueError('Image absent from bound source inventory')

    def _file(self,row):
        identity=row.get('asset_id');digest=row.get('sha256')
        if not isinstance(identity,str) or not re.fullmatch('[A-Za-z0-9_-]{10,150}',identity) or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid source binding')
        path=self.directory/'drive-assets'/(identity+'-'+digest+'.bin')
        if path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024*1024:raise ValueError('Bounded source missing')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Source changed')
        decode_image(str(path.resolve()),digest)
        if raw.startswith(b'\xff\xd8\xff'):return raw,'image/jpeg'
        if raw.startswith(b'\x89PNG\r\n\x1a\n'):return raw,'image/png'
        raise ValueError('Only reviewed JPEG/PNG previews supported')

    def report(self,request):
        try:
            envelope=json.loads(self.path.read_text(encoding='utf-8'));raw=envelope['payload']
            if not hmac.compare_digest(envelope['signature'],hmac.new(self.key,raw.encode(),hashlib.sha256).hexdigest()):raise ValueError()
            value=json.loads(raw)
            if not request or (value['request_id'],value['request_revision'])!=(request['id'],request['revision']):return {'available':False,'reason':'request_changed'}
            for ad in value['ads']:
                for row in [ad]+([ad['story']] if isinstance(ad.get('story'),dict) else []):self._membership(row,request);self._file(row)
            return {'available':True,'proposal':value,'source_binding':{'request_id':value['request_id'],'request_revision':value['request_revision'],'proposal_hash':hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()},'live_action_allowed':False}
        except (OSError,ValueError,KeyError,TypeError):return {'available':False,'reason':'not_prepared_or_source_changed','live_action_allowed':False}

    def media(self,identity,request):
        report=self.report(request)
        if report.get('available') is not True:raise ValueError('No bound proposal')
        for ad in report['proposal']['ads']:
            for row in [ad]+([ad['story']] if isinstance(ad.get('story'),dict) else []):
                if row['asset_id']==identity:return self._file(row)
        raise ValueError('Source not in active proposal')
