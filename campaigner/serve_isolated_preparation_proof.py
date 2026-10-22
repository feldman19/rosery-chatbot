"""Local UI proof fixture. Replays cached images and prior vision; no provider calls."""
import sys,json,hashlib,shutil,socket
from pathlib import Path
from datetime import datetime,timezone
import server,launch_materials
from creative_preparation import CreativePreparation

ROOT=Path(__file__).resolve().parent
PROOF=ROOT.parent/'outputs'/'isolated-ui-preparation-proof-2026-10-20'
PROOF.mkdir(parents=True,exist_ok=True)
DATA=ROOT/'.uit';DATA.mkdir(parents=True,exist_ok=True)
assert DATA.resolve()!=(ROOT/'.local').resolve()
if not (PROOF/'events.jsonl').exists():(PROOF/'events.jsonl').write_text('',encoding='utf-8')
def event(name,**data):
 with (PROOF/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'event':name,**data},ensure_ascii=False)+'\n')
analysis=json.loads((ROOT.parent/'outputs/creative-vision-live-test-2026-10-20.json').read_text(encoding='utf-8'))
ids={x['asset_id'] for x in analysis['assets']}
assets=[r for r in json.loads((ROOT/'.local/drive-assets/manifest.json').read_text(encoding='utf-8'))['assets'] if r['id'] in ids]
assert len(assets)==2
(DATA/'drive-assets').mkdir(exist_ok=True)
for a in assets:
 raw=(ROOT/'.local/drive-assets'/(a['id']+'-'+a['sha256']+'.bin')).read_bytes();assert hashlib.sha256(raw).hexdigest()==a['sha256'];(DATA/'drive-assets'/(a['id']+'-'+a['sha256']+'.bin')).write_bytes(raw)
(DATA/'rosery-pilot.json').write_text('{}',encoding='utf-8')
urls=['https://drive.google.com/file/d/'+a['id']+'/view' for a in assets]
(PROOF/'inputs.json').write_text(json.dumps({'drive_urls':urls,'goal':'הכן הצעת מודעות מכירה מהתמונות המצורפות בלבד, ללא פרסום או הפעלה','simulation':True,'prior_real_vision':'outputs/creative-vision-live-test-2026-10-20.json'},ensure_ascii=False,indent=2),encoding='utf-8')
# A hard guard ensures a missing mock cannot silently reach any external host.
original_connect=socket.socket.connect
def local_only(sock,address):
 if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
  event('external_network_blocked',host=str(address[0]));raise RuntimeError('External network disabled in fixture')
 return original_connect(sock,address)
socket.socket.connect=local_only
launch_materials.drive_status=lambda directory:{'configured':True}
def read_sources(directory,selected):
 assert set(selected)==set(urls);event('mock_drive_read',urls=selected)
 return {'verified':True,'complete':True,'sources':[{'files':[{'id':a['id'],'name':a['name'],'mimeType':a['mime_type']} for a in assets]}]}
def read_assets(directory,metadata):
 event('mock_asset_read',count=2);return {'assets':assets,'complete':True,'content_read_count':2,'files_total':2,'files_processed':2}
RealMaterials=server.LaunchMaterials
server.LaunchMaterials=lambda directory,key:RealMaterials(directory,key,reader=read_sources,asset_reader=read_assets)
def replay_vision(selected):
 assert {(a['id'],a['sha256']) for a in selected}=={(a['id'],a['sha256']) for a in assets};event('mock_claude_replay',prior_real_analysis_at=analysis['checked_at']);return json.loads(json.dumps(analysis))
server.CreativePreparation=lambda directory,**kwargs:CreativePreparation(directory,**kwargs,analyzer=replay_vision)
class LocalChat:
 def __init__(self,directory):pass
 def status(self):return {'authenticated':False,'sharing_approved':False,'turns':[],'remote_available':False}
server.ClaudeChat=LocalChat
http=server.make_server(8782,data_dir=DATA,workspace='manager',start_live=False,start_execution_workers=False)
event('server_ready',port=8782,production_changed=False,external_providers=False)
print('http://127.0.0.1:8782/',flush=True)
try:http.serve_forever()
finally:http.server_close();event('server_closed')
