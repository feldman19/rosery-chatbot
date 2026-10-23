"""Runs ROSERY on the owner's computer and keeps it up to date (owner, 2026-10-23: "just refresh and see the new one").

Starts the local server and, when configured, the phone gateway. Every few minutes, and when the owner taps
"בדוק עדכון עכשיו" on the screen, it fetches the branch from GitHub. A new version is taken only when:
- no file changed on this computer is also changed by the update (it never overwrites local edits; files that
  other tools keep writing, like .agents/events.jsonl, do not stop it as long as the update leaves them alone);
- the new version continues the one running now (fast-forward only, no rewritten history);
- a smoke check of the new code passes: it imports, a server starts on a spare port with an empty temporary
  folder, and the simple screen loads; otherwise the previous version is restored;
- the running server says nothing is half done (an approved Meta change, an upload to Meta, an analysis of
  new content, a chat answer or a file still uploading).
Otherwise it keeps the current version and says why on the screen. It never touches Meta, credentials or rules.

First run, from the campaigner folder (the phone flags only if you use the phone):
    python -B run_owner.py --data-dir <your data folder> --phone-bind-ip 100.x.x.x --phone-ip 100.y.y.y
The settings are saved in campaigner/.local; later runs (START-ROSERY.bat) need no flags.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS = 'run-owner.json'
STATUS = 'update-status.json'
REQUEST = 'update-request.json'
SMOKE = r'''
import json, sys, tempfile, threading, urllib.request
sys.path.insert(0, sys.argv[1])
import phone_gateway  # noqa: F401  (must import)
from server import make_server
with tempfile.TemporaryDirectory() as folder:
    server = make_server(0, folder, workspace='manager', start_live=False, start_execution_workers=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page = urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/', timeout=30).read().decode()
    server.shutdown(); server.server_close()
    assert 'id="version"' in page and '__SESSION__' not in page, 'screen did not load'
print('smoke ok')
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def git(*args, timeout=120):
    done = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip()[:300] or 'git failed')
    return done.stdout.strip()


class Runner:
    def __init__(self, settings, *, git_run=git, smoke=None, busy=None, spawn=None, clock=time.monotonic):
        self.settings = settings
        self.data = Path(settings['data_dir']) if settings.get('data_dir') else ROOT / '.local'
        self.data.mkdir(parents=True, exist_ok=True)
        self.git = git_run
        self.smoke = smoke or self._smoke
        self.busy = busy or self._busy
        self.spawn = spawn or self._spawn
        self.clock = clock
        self.token = secrets.token_urlsafe(32)
        self.children = {}
        self.last_check = None
        self.status = {}

    # ---- status the screen reads ----
    def write_status(self, state, message, **extra):
        self.status = {'state': state, 'message': message, 'checked_at': now(), 'branch': self.settings['branch'], **extra}
        pending = self.data / (STATUS + '.pending')
        pending.write_text(json.dumps(self.status, ensure_ascii=False), encoding='utf-8')
        os.replace(pending, self.data / STATUS)

    # ---- processes ----
    def commands(self):
        server = [sys.executable, '-B', str(ROOT / 'server.py'), '--workspace', 'manager', '--port', str(self.settings['port']),
                  '--no-startup-execution-workers']
        if self.settings.get('data_dir'):
            server += ['--data-dir', self.settings['data_dir']]
        result = {'server': server}
        if self.settings.get('phone_bind_ip') and self.settings.get('phone_ip'):
            result['gateway'] = [sys.executable, '-B', str(ROOT / 'phone_gateway.py'), '--bind-ip', self.settings['phone_bind_ip'],
                                 '--phone-ip', self.settings['phone_ip'], '--data-dir', str(self.data)]
        return result

    def _spawn(self, command):
        return subprocess.Popen(command, cwd=ROOT, env={**os.environ, 'ROSERY_SUPERVISOR_TOKEN': self.token})

    def start(self):
        for name, command in self.commands().items():
            self.children[name] = self.spawn(command)

    def stop(self):
        for child in self.children.values():
            if child.poll() is None:
                child.terminate()
        for child in self.children.values():
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
        self.children = {}

    def keep_alive(self):
        """A process that exited on its own is started again (after a short pause, by the main loop)."""
        for name, command in self.commands().items():
            child = self.children.get(name)
            if child is None or child.poll() is not None:
                self.children[name] = self.spawn(command)

    # ---- checks ----
    def _busy(self):
        """What the running server is in the middle of; [] when idle or unreachable (nothing to protect then)."""
        request = urllib.request.Request(f'http://127.0.0.1:{self.settings["port"]}/api/supervisor/busy',
                                         headers={'X-Supervisor-Token': self.token})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response).get('busy') or []
        except OSError:
            return []

    def _smoke(self):
        done = subprocess.run([sys.executable, '-B', '-c', SMOKE, str(ROOT)], cwd=tempfile.gettempdir(),
                              capture_output=True, text=True, timeout=120)
        if done.returncode or 'smoke ok' not in done.stdout:
            raise RuntimeError((done.stderr or done.stdout).strip().splitlines()[-1][:300] if (done.stderr or done.stdout).strip() else 'smoke failed')

    def requested(self):
        path = self.data / REQUEST
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
            return True
        return False

    def due(self, force=False):
        return force or self.last_check is None or self.clock() - self.last_check >= self.settings['every_minutes'] * 60

    def check(self):
        """One update round. Returns True when it switched to a new version and restarted."""
        self.last_check = self.clock()
        branch = self.settings['branch']
        try:
            current = self.git('rev-parse', 'HEAD')
            self.git('fetch', 'origin', branch)
            available = self.git('rev-parse', 'origin/' + branch)
        except Exception as error:
            self.write_status('error', 'לא הצלחתי לבדוק עדכון ב-GitHub. המערכת ממשיכה לעבוד בגרסה הנוכחית.', detail=str(error)[:300])
            return False
        short = lambda c: c[:7]
        if available == current:
            self.write_status('up_to_date', 'המערכת מעודכנת.', running=short(current))
            return False
        local = set(self.git('diff', '--name-only', 'HEAD').splitlines())
        both = sorted(local & set(self.git('diff', '--name-only', current, available).splitlines()))
        if both:
            self.write_status('blocked', 'יש במחשב שינויים מקומיים בקבצים שהעדכון משנה (' + ', '.join(both[:5]) +
                              '), ולכן לא עדכנתי כדי לא לדרוס אותם.', running=short(current), available=short(available))
            return False
        try:
            self.git('merge-base', '--is-ancestor', current, available)
        except Exception:
            self.write_status('blocked', 'הגרסה ב-GitHub לא ממשיכה את מה שרץ במחשב, ולכן לא עדכנתי אוטומטית.',
                              running=short(current), available=short(available))
            return False
        busy = self.busy()
        if busy:
            self.write_status('waiting', 'יש גרסה חדשה. מחכה שיסתיים: ' + ', '.join(busy) + '.', running=short(current),
                              available=short(available))
            self.last_check = None  # look again on the next loop, not in N minutes
            return False
        self.write_status('updating', 'מעדכן לגרסה חדשה…', running=short(current), available=short(available))
        try:
            self.git('merge', '--ff-only', available)
            self.smoke()
        except Exception as error:
            try:
                self.git('reset', '--keep', current)  # back to the version that ran; local edits elsewhere stay
            except Exception:
                pass
            self.write_status('error', 'הגרסה החדשה לא עברה בדיקה, ולכן נשארתי בגרסה הקודמת.', running=short(current),
                              available=short(available), detail=str(error)[:300])
            return False
        self.stop()
        self.start()
        self.write_status('updated', 'עודכן לגרסה חדשה. רענן את המסך כדי לראות אותה.', running=short(available), updated_at=now())
        return True

    def run(self):
        self.start()
        try:
            while True:
                if self.settings['every_minutes'] > 0 and self.due(self.requested()):
                    self.check()
                time.sleep(5)
                self.keep_alive()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def load_settings(argv=None):
    parser = argparse.ArgumentParser(description='Run ROSERY and keep it up to date')
    parser.add_argument('--data-dir')
    parser.add_argument('--port', type=int)
    parser.add_argument('--phone-bind-ip', help="this computer's Tailscale address (100.x)")
    parser.add_argument('--phone-ip', help="the owner's phone Tailscale address (100.x)")
    parser.add_argument('--branch')
    parser.add_argument('--every-minutes', type=float, help='how often to look for a new version; 0 turns updates off')
    args = parser.parse_args(argv)
    saved_path = ROOT / '.local' / SETTINGS  # one fixed place (ignored by git), whatever data folder is used
    try:
        saved = json.loads(saved_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    settings = {'data_dir': args.data_dir or saved.get('data_dir'), 'port': args.port or saved.get('port') or 8767,
                'phone_bind_ip': args.phone_bind_ip or saved.get('phone_bind_ip'), 'phone_ip': args.phone_ip or saved.get('phone_ip'),
                'branch': args.branch or saved.get('branch') or git('rev-parse', '--abbrev-ref', 'HEAD'),
                'every_minutes': args.every_minutes if args.every_minutes is not None else saved.get('every_minutes', 5)}
    if settings['branch'] == 'HEAD':
        raise SystemExit('המחשב לא נמצא על ענף (detached HEAD). הרץ: git checkout <שם הענף>')
    saved_path.parent.mkdir(parents=True, exist_ok=True)
    saved_path.write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding='utf-8')
    return settings


if __name__ == '__main__':
    config = load_settings()
    print(f"ROSERY: http://127.0.0.1:{config['port']} · עדכון אוטומטי מהענף {config['branch']} כל {config['every_minutes']} דקות", flush=True)
    Runner(config).run()
