"""Optional NetBird-device gateway. Never bind publicly; only the pinned phone.

Owner authorizes enrollment separately. Remote view and owner notes only; no
campaign, approval, credential, OAuth, configuration or work-goal writes.
The transport is HTTP inside the encrypted private NetBird network.
"""
import argparse,ipaddress,urllib.request,urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

PRIVATE=ipaddress.ip_network('100.64.0.0/10')
READ={'/','/api/pilot','/api/registration','/api/workspace','/api/reviews',
 '/api/live','/api/decisions','/api/action-desk','/api/decision-loop',
 '/api/automation','/api/operator','/api/experiment','/api/meta-execution/status',
 '/api/launch','/api/drive/oauth/status','/api/strategy','/api/work/status','/api/launch/proposal','/api/management-report'}

def permitted(method,path,peer,host,origin,bind_ip,phone_ip,port):
    if peer!=phone_ip or host!=f'{bind_ip}:{port}':return False
    if method=='GET':
        if path in READ:return True
        import re
        return bool(re.fullmatch(r'/api/launch/proposal/media/[A-Za-z0-9_-]{5,160}',path))
    return method=='POST' and path=='/api/help/escalate' and origin==f'http://{bind_ip}:{port}'

def make_gateway(bind_ip,phone_ip,port=8768,backend_port=8767):
    addresses=[ipaddress.ip_address(v) for v in (bind_ip,phone_ip)]
    if any(ip not in PRIVATE for ip in addresses) or bind_ip==phone_ip:raise ValueError('Two distinct private NetBird IPv4 addresses required')
    if not all(type(v) is int and 1024<=v<=65535 for v in (port,backend_port)):raise ValueError('Invalid port')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send_bytes(self,status,body,kind='application/json',cookies=()):
            self.send_response(status);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY')
            self.send_header('Referrer-Policy','no-referrer')
            for cookie in cookies:self.send_header('Set-Cookie',cookie)
            self.end_headers();self.wfile.write(body)
        def relay(self):
            if not permitted(self.command,self.path,self.client_address[0],self.headers.get('Host'),self.headers.get('Origin'),bind_ip,phone_ip,self.server.server_port):
                return self.send_bytes(403,b'{"error":"Private owner device or route required"}')
            body=None
            if self.command=='POST':
                try:size=int(self.headers.get('Content-Length','0'))
                except ValueError:size=0
                if not 1<=size<=8192 or self.headers.get('Transfer-Encoding'):return self.send_bytes(400,b'{"error":"Bounded note required"}')
                body=self.rfile.read(size)
            headers={key:self.headers[key] for key in ('X-Local-Session','Cookie','Content-Type','Sec-Fetch-Site') if key in self.headers}
            if body is not None:headers['Origin']=f'http://127.0.0.1:{backend_port}'
            request=urllib.request.Request(f'http://127.0.0.1:{backend_port}'+self.path,data=body,headers=headers,method=self.command)
            try:
                try:response=urllib.request.urlopen(request,timeout=25)
                except urllib.error.HTTPError as error:response=error
                with response:
                    content=response.read(8*1024*1024+1)
                    if len(content)>8*1024*1024:return self.send_bytes(502,b'{"error":"Response bound exceeded"}')
                    self.send_bytes(response.code,content,response.headers.get('Content-Type','application/json'),response.headers.get_all('Set-Cookie',[]))
            except (OSError,ValueError):self.send_bytes(503,b'{"error":"Local manager unavailable"}')
        do_GET=relay
        do_POST=relay
    return ThreadingHTTPServer((bind_ip,port),Handler)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--bind-ip',required=True);parser.add_argument('--phone-ip',required=True)
    args=parser.parse_args();server=make_gateway(args.bind_ip,args.phone_ip)
    print('Private mobile gateway started for the pinned phone only',flush=True);server.serve_forever()
