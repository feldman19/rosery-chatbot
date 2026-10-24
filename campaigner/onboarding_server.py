"""Local onboarding screen for a new business: python -B onboarding_server.py --data-dir <new empty folder>.

Separate from server.py on purpose: it serves only onboarding.html and the onboarding steps, on 127.0.0.1, with
the same session-header and same-origin checks as the main server. It never calls Meta or Shopify itself; the
connectors it is given do (LocalOnlyConnectors by default, which connect nothing).
"""
import argparse
import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from onboarding import NotConnected, Onboarding

ROOT = Path(__file__).resolve().parent
STEP_ROUTES = {'/api/onboarding/business': 'business', '/api/onboarding/meta-connect': 'meta_connect',
               '/api/onboarding/meta-account': 'meta_account', '/api/onboarding/meta-campaign': 'meta_campaign',
               '/api/onboarding/shopify': 'shopify', '/api/onboarding/questionnaire': 'questionnaire'}
MAX_BODY = 16 * 1024


def make_onboarding_server(port, data_dir, connectors=None):
    flow = Onboarding(data_dir, connectors)
    session = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value, kind='application/json; charset=utf-8'):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def session_ok(self):
            return hmac.compare_digest(self.headers.get('X-Local-Session', ''), session)

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {'error': 'Invalid host'})
            if self.path.split('?', 1)[0] == '/':
                page = (ROOT / 'onboarding.html').read_text(encoding='utf-8').replace('__SESSION__', session)
                return self.reply(200, page.encode('utf-8'), 'text/html; charset=utf-8')
            if self.path == '/api/onboarding':
                if not self.session_ok():
                    return self.reply(403, {'error': 'Invalid local session'})
                return self.reply(200, flow.status())
            return self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.host_ok() or self.headers.get('Origin') != origin or not self.session_ok():
                return self.reply(403, {'error': 'Local session and same-origin request required'})
            if self.headers.get('Content-Type') != 'application/json':
                return self.reply(415, {'error': 'JSON required'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                size = -1
            if not 0 <= size <= MAX_BODY:
                return self.reply(413, {'error': 'Body too large'})
            try:
                body = json.loads(self.rfile.read(size) or b'{}')
                if self.path in STEP_ROUTES:
                    return self.reply(200, flow.submit(STEP_ROUTES[self.path], body))
                if self.path == '/api/onboarding/review':
                    return self.reply(200, flow.review((body or {}).get('overrides')))
                if self.path == '/api/onboarding/approve':
                    return self.reply(200, flow.approve((body or {}).get('overrides')))
                return self.reply(404, {'error': 'Not found'})
            except NotConnected as error:
                return self.reply(409, {'error': str(error)})
            except (ValueError, KeyError, TypeError) as error:
                return self.reply(400, {'error': str(error) if isinstance(error, ValueError) else 'תשובה לא תקינה'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Onboard a new business (local only)')
    parser.add_argument('--data-dir', required=True, help='a NEW, empty folder for the new business')
    parser.add_argument('--port', type=int, default=8790)
    args = parser.parse_args()
    server = make_onboarding_server(args.port, args.data_dir)
    print(f'הקמת עסק חדש: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
