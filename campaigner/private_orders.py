"""Encrypted backend-only order cache. API summaries contain no customer IDs."""
from datetime import datetime,timezone
import json
from pathlib import Path
import secrets
import threading
from shopify_direct import crypt
from shopify_orders import read_orders


class PrivateOrders:
    def __init__(self,directory,reader=None,interval_seconds=1800):
        self.directory=Path(directory);self.path=self.directory/'cohort-orders.dpapi'
        self.key_path=self.directory/'cohort-identity.dpapi';self.reader=reader or (lambda directory,secret: read_orders(directory,secret,window_days=28))
        self.interval=interval_seconds;self.lock=threading.RLock();self.running=False
        self.last_attempt=None;self.last_success=None;self.error=None;self.data=None
        try:
            saved=json.loads(crypt(self.path.read_bytes(),True))
            self.data=saved['data'];self.last_success=saved['at']
        except (OSError,ValueError,KeyError,TypeError):pass

    def identity_secret(self):
        if not self.key_path.exists():
            try:
                with self.key_path.open('xb') as out:out.write(crypt(secrets.token_bytes(32)))
            except FileExistsError:pass
        secret=crypt(self.key_path.read_bytes(),True)
        if len(secret)!=32:raise ValueError('Invalid private identity key')
        return secret

    def refresh(self,force=False):
        now=datetime.now(timezone.utc)
        with self.lock:
            if self.running:return False
            if not force and self.last_attempt and (now-datetime.fromisoformat(self.last_attempt)).total_seconds()<self.interval:return False
            self.running=True;self.last_attempt=now.isoformat()
        try:
            result=self.reader(self.directory,self.identity_secret())
            if result.get('coverage',{}).get('complete') is not True or result.get('coverage',{}).get('error'):
                raise ValueError('Incomplete order coverage')
            at=datetime.now(timezone.utc).isoformat()
            raw=json.dumps({'at':at,'data':result},ensure_ascii=False).encode()
            pending=self.path.with_suffix('.pending');pending.write_bytes(crypt(raw));pending.replace(self.path)
            with self.lock:self.data=result;self.last_success=at;self.error=None
        except Exception:
            with self.lock:self.error='קריאת ההזמנות לא הושלמה; נתונים חסרים אינם אפס רכישות'
        finally:
            with self.lock:self.running=False
        return True

    def summary(self):
        with self.lock:
            data=self.data or {};coverage=data.get('coverage') or {};binding=data.get('binding') or {}
            return {'configured':self.key_path.exists(),'refreshing':self.running,'last_success_at':self.last_success,
                    'last_attempt_at':self.last_attempt,'error':self.error,'interval_seconds':self.interval,
                    'source_verified':binding.get('source_verified') is True,
                    'complete':coverage.get('complete') is True,
                    'matchable_orders':len(data['orders']) if self.data else None,
                    'excluded_orders':len(data['excluded']) if self.data else None,
                    'since':coverage.get('since'),'until':coverage.get('until'),
                    'storage':'Windows current-user DPAPI; backend only','live_action_allowed':False}
