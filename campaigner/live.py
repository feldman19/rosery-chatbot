"""Process-local read-only refresh loop, durable last-good cache, no live writes."""
import copy
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from meta_direct import read_reports as meta_report
from shopify_direct import read_reports as shopify_report
from decision_history import save_observation


def now():return datetime.now(timezone.utc).isoformat()


class LiveReports:
    def __init__(self,data_dir,interval=300,readers=None):
        self.directory=Path(data_dir);self.interval=interval
        self.readers=readers or {'meta':meta_report,'shopify':shopify_report}
        self.lock=threading.RLock();self.stop_event=threading.Event()
        self.running=False;self.last_started=0;self.generation=0
        self.collector=None;self.last_cycle_at=None
        self.on_cycle=None;self.analysis_error=False
        self.decision_history_status='not_recorded_this_process'
        self.sources={k:{'last_success_at':None,'last_attempt_at':None,'error':None,'data':None} for k in self.readers}
        self.path=self.directory/'live-reports.json'
        try:
            stored=json.loads(self.path.read_text(encoding='utf-8'))
            for name in self.sources:
                source=stored['sources'][name]
                # A restored snapshot stays visibly historical until a new read.
                if isinstance(source,dict):self.sources[name].update({k:source.get(k) for k in self.sources[name]})
        except (ValueError,KeyError,TypeError,OSError):pass
        self.restored={key:bool(value.get('data')) for key,value in self.sources.items()}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({'checked_at':now(),'refreshing':self.running,
                'refresh_interval_seconds':self.interval,'generation':self.generation,
                'collection_active':bool(self.collector and self.collector.is_alive() and not self.stop_event.is_set()),
                'cache_restored':dict(self.restored),'last_cycle_at':self.last_cycle_at,
                'analysis_error':self.analysis_error,
                'decision_history_status':self.decision_history_status,
                'live_action_allowed':False,'sources':self.sources})

    def _source(self,name,reader):
        attempted=now()
        try:
            result=reader(self.directory)
            if result.get('verified') is not True:raise ValueError('Unverified report')
            if result.get('complete') is False:raise ValueError('Incomplete report')
            return name,{'last_attempt_at':attempted,'last_success_at':now(),'error':None,'data':result}
        except Exception:
            # No exception strings: provider errors may contain credentials/URLs.
            return name,{'last_attempt_at':attempted,'error':'הקריאה נכשלה — הנתונים האחרונים נשמרו. נדרשת בדיקת חיבור או הרשאה'}

    def refresh(self,force=False):
        with self.lock:
            if self.running or (not force and time.monotonic()-self.last_started<30):return False
            self.running=True;self.last_started=time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(self._source,name,reader) for name,reader in self.readers.items()]
                for job in as_completed(jobs):
                    name,update=job.result()
                    with self.lock:
                        self.sources[name].update(update)
                        if update.get('data'):self.restored[name]=False
                        self.generation+=1
            with self.lock:
                self.generation+=1
                self.last_cycle_at=now()
                temp=self.path.with_suffix('.pending')
                temp.write_text(json.dumps({'sources':self.sources},ensure_ascii=False),encoding='utf-8')
                temp.replace(self.path)
                try:self.decision_history_status=save_observation(self.directory,self.sources.get('meta') or {})
                except OSError:self.decision_history_status='write_failed'
        finally:
            with self.lock:self.running=False
        if self.on_cycle:
            try:self.on_cycle(self.snapshot());self.analysis_error=False
            except Exception:self.analysis_error=True
        return True

    def request_refresh(self):
        with self.lock:
            if self.running or time.monotonic()-self.last_started<30:return False
            # Reserve before starting to ensure concurrent UI requests coalesce.
            self.running=True
        def work():
            with self.lock:self.running=False
            self.refresh()
        threading.Thread(target=work,daemon=True).start()
        return True

    def start(self):
        if self.collector and self.collector.is_alive():return
        def loop():
            while not self.stop_event.is_set():
                try:self.refresh()
                except OSError:pass
                self.stop_event.wait(self.interval)
        self.collector=threading.Thread(target=loop,daemon=True,name='rosery-readonly-reports');self.collector.start()

    def stop(self):self.stop_event.set()
