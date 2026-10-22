"""Current-user startup supervisor for the pinned ROSERY mobile endpoint.
No campaign writes. Secrets are decrypted only into the child environment.
"""
import json,os,socket,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
LOCAL=ROOT/'.local'
ORIGIN='https://frequency-rasping-eaten.ngrok-free.dev'

def listening(port):
 with socket.socket() as s:
  s.settimeout(1)
  return s.connect_ex(('127.0.0.1',port))==0

def main():
 lock=socket.socket()
 try:lock.bind(('127.0.0.1',8770))
 except OSError:return
 from shopify_direct import crypt
 token=crypt((LOCAL/'ngrok-token.dpapi').read_bytes(),True).decode()
 children={}
 while True:
  try:
   for name,port,args in (
    ('manager',8767,[sys.executable,str(ROOT/'server.py'),'--workspace','manager','--port','8767']),
    ('mobile',8768,[sys.executable,str(ROOT/'mobile_web.py')]),
    ('tunnel',None,[str(LOCAL/'tools/ngrok/ngrok.exe'),'http','http://127.0.0.1:8768','--url',ORIGIN,'--inspect=false','--log=false'])):
    child=children.get(name)
    if child is not None and child.poll() is None:continue
    if port and listening(port):continue
    env=os.environ.copy()
    if name=='tunnel':env['NGROK_AUTHTOKEN']=token
    with (LOCAL/('mobile-'+name+'.log')).open('ab') as log:
     children[name]=subprocess.Popen(args,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
   (LOCAL/'mobile-supervisor-status.json').write_text(json.dumps({'checked_at':time.time(),'origin':ORIGIN,'manager_up':listening(8767),'gateway_up':listening(8768),'tunnel_running':children.get('tunnel') is not None and children['tunnel'].poll() is None}))
  except Exception:
   # Never write credential-bearing exception values to logs.
   (LOCAL/'mobile-supervisor-error.txt').write_text('Local service could not start; inspect service status.')
  time.sleep(15)

if __name__=='__main__':main()
