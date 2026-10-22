"""Installed desktop OAuth preparation; no network until complete is invoked.

Host must expose the callback only on its exact loopback Host/port/path. The
callback does not require the app session header: single-use state binds it to
the locally prepared PKCE flow. Never accept client configuration from HTTP.
Official protocol: developers.google.com/identity/protocols/oauth2/native-app.
"""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlencode
import urllib.request

from connections import NoRedirect
from shopify_direct import crypt
import drive_direct

CLIENT_ID = '163075886816-kbpjqhk2gjaduimqlie6qpv2mh2r46sq.apps.googleusercontent.com'
AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
TTL = 600


def _exchange(form):
    request = urllib.request.Request(TOKEN, data=urlencode(form).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('error'):
            raise ValueError()
        return value
    except Exception:
        raise ValueError('Drive authorization exchange failed') from None


class DriveOAuth:
    def __init__(self, directory, port, client_loader=None, *, clock=None,
                 exchange=None, configure=None):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('Invalid loopback port')
        self.directory = Path(directory)
        self.redirect_uri = f'http://127.0.0.1:{port}/api/drive/oauth/callback'
        self._loader = client_loader or self._load
        self._clock = clock or time.monotonic
        self._exchange = exchange or _exchange
        self._configure = configure or drive_direct.configure
        self._lock = threading.RLock()
        self._pending = None

    def _load(self):
        return json.loads(crypt((self.directory / 'drive-oauth-client.dpapi').read_bytes(), decrypt=True))

    def _client(self):
        try:
            value = self._loader()
            if not isinstance(value, dict) or set(value) != {'client_id', 'client_secret'} or value['client_id'] != CLIENT_ID:
                raise ValueError()
            secret = value['client_secret']
            if not isinstance(secret, str) or not 10 <= len(secret) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in secret):
                raise ValueError()
            return dict(value)
        except Exception:
            raise ValueError('Drive OAuth client unavailable') from None

    def status(self):
        try:
            self._client()
            configured = True
        except ValueError:
            configured = False
        with self._lock:
            pending = bool(self._pending and 0 <= self._clock() - self._pending['created'] < TTL)
        return {'client_configured': configured, 'authorization_pending': pending,
                'scopes': [drive_direct.SCOPE], 'email': drive_direct.EMAIL}

    def begin(self):
        client = self._client()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with self._lock:
            self._pending = {'state': state, 'verifier': verifier, 'client': client, 'created': self._clock()}
        params = {'client_id': CLIENT_ID, 'redirect_uri': self.redirect_uri,
                  'response_type': 'code', 'scope': drive_direct.SCOPE,
                  'access_type': 'offline', 'prompt': 'consent',
                  'login_hint': drive_direct.EMAIL, 'include_granted_scopes': 'false',
                  'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'}
        return {'authorization_url': AUTH + '?' + urlencode(params), 'expires_in': TTL,
                'scopes': [drive_direct.SCOPE], 'authorization_granted': False}

    def complete(self, code, state):
        if not isinstance(state, str) or not 20 <= len(state) <= 256:
            raise ValueError('Invalid or expired Drive authorization state')
        with self._lock:
            pending = self._pending
            if not pending or not hmac.compare_digest(state, pending['state']):
                raise ValueError('Invalid or expired Drive authorization state')
            self._pending = None  # Consume before any I/O, including failed exchanges.
            if not 0 <= self._clock() - pending['created'] < TTL:
                raise ValueError('Invalid or expired Drive authorization state')
        if not isinstance(code, str) or not 1 <= len(code) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in code):
            raise ValueError('Invalid Drive authorization code')
        try:
            result = self._exchange({'grant_type': 'authorization_code', 'code': code,
                **pending['client'], 'redirect_uri': self.redirect_uri,
                'code_verifier': pending['verifier']})
            if not isinstance(result, dict) or result.get('error'):
                raise ValueError()
            if 'scope' in result and set(str(result['scope']).split()) != {drive_direct.SCOPE}:
                raise ValueError()
            refresh = result.get('refresh_token')
            if not isinstance(refresh, str) or not 10 <= len(refresh) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in refresh):
                raise ValueError()
            verified = self._configure(self.directory, {'type': 'authorized_user',
                **pending['client'], 'refresh_token': refresh})
            if not isinstance(verified, dict) or verified.get('saved') is not True or verified.get('verified') is not True:
                raise ValueError()
            return {'saved': True, 'verified': True, 'email': drive_direct.EMAIL,
                    'scopes': [drive_direct.SCOPE], 'content_read': False}
        except Exception:
            raise ValueError('Drive authorization could not be verified') from None
