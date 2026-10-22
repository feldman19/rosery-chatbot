"""Local, bounded quality gate. Never starts production workers or ad writes.

Runs only an explicit offline-test allowlist. Every round gets its own source
hashes, logs and receipt. Claude is optional and failure never becomes approval.
Repair is coordinator-assisted; rerun after a repair creates a separate round.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from claude_quality_review import subscription_environment, subscription_auth

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT.parent / 'outputs'
TESTS = (
    ('python', ('-B', '-m', 'unittest', 'test_integrated_launch_path',
        'test_placement_launch_path', 'test_launch_blueprint', 'test_business_context',
        'test_economics_limits', 'test_meta_batch', 'test_quality_review')),
    ('node', ('test_owner_operations_ui.js',)),
)


def snapshot():
    # Direct code files only, never runtime data, fixtures or nested directories.
    paths = sorted(p for p in ROOT.iterdir() if p.suffix in ('.py', '.js', '.html') and p.is_file())
    if any(p.is_symlink() for p in paths):
        raise ValueError('Symlink source files are not permitted')
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def classify(checks, stale, claude):
    if stale:
        return 'stale_source'
    if not checks or any(c.get('state') != 'passed' for c in checks):
        return 'tests_failed'
    # Even a completed model review needs coordinator assessment of findings.
    return 'review_assessment_pending' if claude.get('state') == 'completed' else 'tests_passed_external_review_pending'


def exit_code(state):
    return 1 if state in ('tests_failed', 'stale_source') else 0


def save(run, report):
    report['updated_at'] = datetime.now(timezone.utc).isoformat()
    temp = run / 'status.tmp'
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(run / 'status.json')
    rows = ''.join('<li>' + html.escape(c['name'] + ': ' + c['state']) + '</li>' for c in report['checks'])
    page = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ROSERY Quality</title><main style="max-width:760px;margin:2rem auto;padding:1rem;font:18px system-ui"><h1>ROSERY — Quality review</h1><p>' + html.escape(report['state']) + '</p><ul>' + rows + '</ul><p>Claude: ' + html.escape(report['claude']['state']) + '</p><p>Local tests only. Production readiness, rendered QA and campaign approval remain unverified.</p><p>' + html.escape(report['updated_at']) + '</p></main>'
    (run / 'index.html').write_text(page, encoding='utf-8')


def run_check(name, binary, args, run, timeout=120):
    started = time.monotonic()
    result = {'name': name, 'state': 'failed', 'exit_code': None}
    with (run / (name + '.stdout.txt')).open('w', encoding='utf-8') as out, (run / (name + '.stderr.txt')).open('w', encoding='utf-8') as err:
        try:
            completed = subprocess.run([binary, *args], cwd=ROOT, stdout=out, stderr=err,
                                       timeout=timeout, check=False)
            result.update(exit_code=completed.returncode, state='passed' if completed.returncode == 0 else 'failed')
        except subprocess.TimeoutExpired:
            result['state'] = 'timeout'
        except OSError:
            result['state'] = 'unavailable'
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--claude', action='store_true', help='Request one read-only Claude subscription review if authenticated')
    args = parser.parse_args()
    run = OUTPUTS / ('quality-round-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    run.mkdir()
    before = snapshot()
    report = {'schema_version': 1, 'state': 'running', 'checks': [],
              'claude': {'state': 'not_requested'}, 'source_manifest': before,
              'source_stale': False, 'production_ready': False,
              'campaign_approval': False, 'live_writes': False,
              'repair_mode': 'coordinator_assisted_no_automatic_edits'}
    save(run, report)
    for name, command in TESTS:
        binary = sys.executable if name == 'python' else shutil.which('node')
        report['checks'].append(run_check(name, binary, command, run) if binary else {'name': name, 'state': 'unavailable'})
        save(run, report)
    if args.claude:
        executable = shutil.which('claude')
        try:
            if not executable:
                raise OSError('Claude unavailable')
            environment = subscription_environment()
            logged_in = subscription_auth(executable, environment)
            report['claude'] = {'state': 'authentication_required' if not logged_in else 'running'}
            save(run, report)
            if logged_in:
                target = run.with_name(run.name + '-claude')
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'claude_quality_review.py'),
                                         '--run', str(target)], cwd=ROOT, capture_output=True, timeout=320, check=False, env=environment)
                report['claude'] = {'state': 'completed' if result.returncode == 0 else 'failed', 'evidence_directory': target.name}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            report['claude'] = {'state': 'unavailable'}
    report['source_stale'] = snapshot() != before
    report['state'] = classify(report['checks'], report['source_stale'], report['claude'])
    save(run, report)
    print(json.dumps({'run': str(run), 'state': report['state'], 'claude': report['claude']['state']}))
    return exit_code(report['state'])


if __name__ == '__main__':
    sys.exit(main())
