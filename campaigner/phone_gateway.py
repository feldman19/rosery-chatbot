"""Owner's phone → the simple ROSERY screen, over Tailscale only (owner decision 2026-10-23).

Binds only to this computer's Tailscale address and answers only the one pinned phone address, so
nothing is reachable from the internet. On top of the private network the phone signs in once with
a 6-digit code; after that a 30-day cookie. Five wrong codes lock sign-in for 15 minutes.

Requests are relayed to the local server on 127.0.0.1, which still applies its own session, origin
and approval checks: a Meta change still needs the owner's "אשר ובצע" with a fresh single-use token.
Only the routes the simple screen uses are relayed; the advanced screen stays on the computer.
"""
import argparse
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TAILNET = ipaddress.ip_network('100.64.0.0/10')
COOKIE = 'rosery_phone'
SESSION_SECONDS = 30 * 86400
GET_ROUTES = {'/api/experiments', '/api/experiments/candidates', '/', '/api/management-report', '/api/outcomes', '/api/profit', '/api/creative-launch', '/api/automation-shadow',
              '/api/approve-execute', '/api/claude-chat', '/api/daily-brief', '/api/campaign-operations', '/api/learning',
              '/api/update-status'}
POST_ROUTES = {'/api/experiments/idea', '/api/experiments/dismiss', '/api/experiments/stop', '/api/claude-chat/threads', '/api/claude-chat/thread', '/api/claude-chat/create', '/api/claude-chat/update', '/api/approve-execute/preview', '/api/approve-execute/confirm', '/api/outcomes/mark', '/api/outcomes/answer',
               '/api/creative-drift/keep', '/api/claude-chat', '/api/profit/settings', '/api/live/refresh',
               '/api/creative-launch/start', '/api/creative-launch/finalize', '/api/learning/outside-check', '/api/campaign-operations/preview',
               '/api/campaign-operations/approve', '/api/campaign-operations/execute', '/api/local-uploads', '/api/automation-shadow/policy', '/api/automation-shadow/stop',
               '/api/update/request'}
MAX_BODY = 64 * 1024
MAX_UPLOAD = 300 * 1024 * 1024
LOGIN_PAGE = '''<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ROSERY</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0e0c;color:#ecf1ee;font:16px -apple-system,"Segoe UI",sans-serif}
form{display:grid;gap:14px;width:min(320px,90vw);text-align:center}h1{letter-spacing:.28em;font-size:16px;margin:0 0 8px}
input{font-size:28px;letter-spacing:.4em;text-align:center;padding:14px;border-radius:14px;border:1px solid #232c27;background:#131916;color:inherit}
button{font:inherit;font-weight:700;padding:14px;border-radius:14px;border:0;background:#2c7a5c;color:#fff}p{color:#f2c46b;margin:0}</style>
<form method="post" action="/__login"><h1>ROSERY</h1><input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required autofocus placeholder="••••••">
<button>כניסה</button>__ERROR__</form></html>'''


def _hash(value, salt):
    return hashlib.sha256((salt + value).encode()).hexdigest()


class PhoneAccess:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.failures = []

    def _save(self):
        pending = self.path.with_suffix('.pending')
        pending.write_text(json.dumps(self.state), encoding='utf-8')
        pending.replace(self.path)

    def new_code(self):
        """Host step only: a new code ends every phone session."""
        code = f'{secrets.randbelow(10 ** 6):06d}'
        salt = secrets.token_hex(16)
        with self.lock:
            self.state = {'salt': salt, 'code': _hash(code, salt), 'sessions': {}}
            self._save()
        return code

    def login(self, code):
        now = self.clock()
        with self.lock:
            self.failures = [t for t in self.failures if now - t < 900]
            if len(self.failures) >= 5 or 'code' not in self.state:
                return None
            if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code) or not hmac.compare_digest(_hash(code, self.state['salt']), self.state['code']):
                self.failures.append(now)
                return None
            token = secrets.token_urlsafe(32)
            sessions = {k: v for k, v in self.state.get('sessions', {}).items() if v > now}
            sessions[_hash(token, self.state['salt'])] = now + SESSION_SECONDS
            self.state['sessions'] = sessions
            self._save()
            return token

    def valid(self, token):
        if not token or 'salt' not in self.state:
            return False
        with self.lock:
            expires = self.state.get('sessions', {}).get(_hash(token, self.state['salt']))
        return bool(expires and expires > self.clock())


def permitted(method, path, peer, phone_ip):
    if peer != phone_ip:
        return False
    route = path.split('?', 1)[0]
    if method == 'GET':
        return route in GET_ROUTES or re.fullmatch(r'/api/creative-launch/(media|video)/local_[a-f0-9]{32}~[a-f0-9]{64}', route) is not None
    return method == 'POST' and route in POST_ROUTES


def make_gateway(bind_ip, phone_ip, access, port=8768, backend_port=8767):
    for value in (bind_ip, phone_ip):
        if ipaddress.ip_address(value) not in TAILNET:
            raise ValueError('Tailscale (100.64.0.0/10) addresses required')
    if bind_ip == phone_ip:
        raise ValueError('Computer and phone addresses must differ')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_bytes(self, status, body, kind='application/json', cookies=(), location=None):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            if location:
                self.send_header('Location', location)
            for cookie in cookies:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(body)

        def _stream(self, response):
            # A video can be up to 300 MiB: pass it through in pieces instead of holding it in memory.
            try:
                size = int(response.headers.get('Content-Length', ''))
            except ValueError:
                return self.send_bytes(502, b'{"error":"Unknown video size"}')
            if not 0 <= size <= MAX_UPLOAD:
                return self.send_bytes(502, b'{"error":"Response too large"}')
            self.send_response(response.code)
            for name, value in (('Content-Type', response.headers.get('Content-Type', 'application/octet-stream')), ('Content-Length', str(size)),
                                ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                                ('Accept-Ranges', 'bytes'), ('Content-Range', response.headers.get('Content-Range'))):
                if value:
                    self.send_header(name, value)
            self.end_headers()
            while size > 0:
                chunk = response.read(min(1024 * 1024, size))
                if not chunk:
                    self.close_connection = True  # cut short: the phone sees an incomplete file, never a different one
                    break
                self.wfile.write(chunk)
                size -= len(chunk)

        def _phone_cookie(self):
            for part in (self.headers.get('Cookie') or '').split(';'):
                name, _, value = part.strip().partition('=')
                if name == COOKIE:
                    return value
            return None

        def _login_page(self, error=''):
            page = LOGIN_PAGE.replace('__ERROR__', f'<p>{error}</p>' if error else '')
            self.send_bytes(401, page.encode(), 'text/html; charset=utf-8')

        def handle_one(self):
            if self.client_address[0] != phone_ip:
                return self.send_bytes(403, b'{"error":"Owner phone only"}')
            if self.command == 'POST' and self.path == '/__login':
                size = int(self.headers.get('Content-Length') or 0)
                if not 1 <= size <= 200:
                    return self._login_page('קוד לא תקין')
                form = dict(p.split('=', 1) for p in self.rfile.read(size).decode('ascii', 'replace').split('&') if '=' in p)
                token = access.login(form.get('code'))
                if not token:
                    return self._login_page('קוד שגוי, או יותר מדי ניסיונות. נסה שוב מאוחר יותר.')
                cookie = f'{COOKIE}={token}; Max-Age={SESSION_SECONDS}; Path=/; HttpOnly; SameSite=Strict'
                return self.send_bytes(303, b'', 'text/plain', [cookie], location='/')
            if not access.valid(self._phone_cookie()):
                return self._login_page() if self.command == 'GET' and self.path.split('?')[0] == '/' else self.send_bytes(401, '{"error":"צריך להיכנס עם הקוד"}'.encode())
            if not permitted(self.command, self.path, self.client_address[0], phone_ip):
                return self.send_bytes(404, '{"error":"לא זמין מהטלפון"}'.encode())
            body = None
            if self.command == 'POST':
                if self.headers.get('Transfer-Encoding'):
                    return self.send_bytes(400, b'{"error":"Bounded body required"}')
                limit = MAX_UPLOAD if self.path == '/api/local-uploads' else MAX_BODY
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    size = -1
                if not 0 <= size <= limit:
                    return self.send_bytes(413, b'{"error":"Body too large"}')
                body = self.rfile.read(size)
            # The phone cookie stays here; only the local server's own headers go through.
            cookies = '; '.join(p.strip() for p in (self.headers.get('Cookie') or '').split(';') if p.strip() and not p.strip().startswith(COOKIE + '='))
            headers = {k: self.headers[k] for k in ('X-Local-Session', 'Content-Type', 'X-File-Name', 'X-Upload-Id', 'Range') if k in self.headers}
            if cookies:
                headers['Cookie'] = cookies
            if body is not None:
                headers['Origin'] = f'http://127.0.0.1:{backend_port}'
                headers['Sec-Fetch-Site'] = 'same-origin'
            request = urllib.request.Request(f'http://127.0.0.1:{backend_port}{self.path}', data=body, headers=headers, method=self.command)
            try:
                try:
                    response = urllib.request.urlopen(request, timeout=120)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    if self.path.startswith('/api/creative-launch/video/') and response.code in (200, 206):
                        return self._stream(response)
                    content = response.read(64 * 1024 * 1024 + 1)
                    if len(content) > 64 * 1024 * 1024:
                        return self.send_bytes(502, b'{"error":"Response too large"}')
                    self.send_bytes(response.code, content, response.headers.get('Content-Type', 'application/json'),
                                    response.headers.get_all('Set-Cookie', []))
            except (OSError, ValueError):
                self.send_bytes(503, '{"error":"המערכת במחשב לא זמינה כרגע"}'.encode())

        do_GET = handle_one
        do_POST = handle_one

    return ThreadingHTTPServer((bind_ip, port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ROSERY phone access over Tailscale')
    parser.add_argument('--bind-ip', required=True, help="this computer's Tailscale address (100.x)")
    parser.add_argument('--phone-ip', required=True, help="the owner's phone Tailscale address (100.x)")
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--new-code', action='store_true', help='make a new 6-digit code (signs out the phone)')
    args = parser.parse_args()
    access = PhoneAccess(Path(args.data_dir) / 'phone-gateway.json')
    if args.new_code or 'code' not in access.state:
        print('Phone sign-in code: ' + access.new_code(), flush=True)
    server = make_gateway(args.bind_ip, args.phone_ip, access, port=args.port)
    print(f'ROSERY on the phone: http://{args.bind_ip}:{args.port}', flush=True)
    server.serve_forever()
