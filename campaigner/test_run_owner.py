"""The owner's runner: takes a new version only when it is safe, otherwise keeps running what works."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_owner
from run_owner import Runner


class FakeChild:
    def __init__(self, command):
        self.command, self.stopped = command, False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.stopped = True


class FakeGit:
    def __init__(self, current='a' * 40, available='b' * 40, local=(), incoming=('campaigner/server.py',), ancestor=True, fetch_error=False):
        self.current, self.available, self.ancestor, self.fetch_error = current, available, ancestor, fetch_error
        self.local, self.incoming = local, incoming
        self.calls = []

    def __call__(self, *args, timeout=120):
        self.calls.append(args)
        if args[0] == 'rev-parse':
            return self.current if args[1] == 'HEAD' else self.available
        if args[0] == 'fetch' and self.fetch_error:
            raise RuntimeError('network')
        if args[0] == 'diff':
            return '\n'.join(self.local if args[2] == 'HEAD' else self.incoming)
        if args[0] == 'merge-base' and not self.ancestor:
            raise RuntimeError('not ancestor')
        if args[0] == 'merge':
            self.current = self.available
        if args[0] == 'reset':
            self.current = args[2]
        return ''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = {'data_dir': self.temp.name, 'port': 8767, 'phone_bind_ip': '100.1.1.1', 'phone_ip': '100.1.1.2',
                         'branch': 'claude/x', 'every_minutes': 5}
        self.spawned = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, git, busy=(), smoke_error=None):
        def smoke():
            if smoke_error:
                raise RuntimeError(smoke_error)
        def spawn(command):
            child = FakeChild(command)
            self.spawned.append(child)
            return child
        runner = Runner(self.settings, git_run=git, smoke=smoke, busy=lambda: list(busy), spawn=spawn)
        runner.start()
        return runner

    def status(self):
        return json.loads((Path(self.temp.name) / 'update-status.json').read_text(encoding='utf-8'))

    def test_starts_server_and_phone_gateway_with_the_saved_settings(self):
        self.runner(FakeGit(available='a' * 40))
        server, gateway = (c.command for c in self.spawned)
        self.assertEqual(server[2:], [str(run_owner.ROOT / 'server.py'), '--workspace', 'manager', '--port', '8767',
                                      '--no-startup-execution-workers', '--data-dir', self.temp.name])
        self.assertIn('--phone-ip', gateway)

    def test_up_to_date_changes_nothing(self):
        git = FakeGit(available='a' * 40)
        runner = self.runner(git)
        self.assertFalse(runner.check())
        self.assertEqual((self.status()['state'], len(self.spawned)), ('up_to_date', 2))
        self.assertNotIn(('merge', '--ff-only', 'a' * 40), git.calls)

    def test_new_version_is_checked_then_both_processes_restart(self):
        git = FakeGit()
        runner = self.runner(git)
        self.assertTrue(runner.check())
        self.assertIn(('merge', '--ff-only', 'b' * 40), git.calls)
        self.assertEqual(len(self.spawned), 4)
        self.assertTrue(all(c.stopped for c in self.spawned[:2]))
        self.assertEqual((self.status()['state'], self.status()['running']), ('updated', 'bbbbbbb'))

    def test_local_edits_are_never_overwritten(self):
        git = FakeGit(local=('campaigner/server.py',))
        self.assertFalse(self.runner(git).check())
        self.assertEqual(self.status()['state'], 'blocked')
        self.assertIn('campaigner/server.py', self.status()['message'])
        self.assertFalse(any(c[0] in ('merge', 'reset') for c in git.calls))

    def test_a_file_other_tools_keep_writing_does_not_stop_updates(self):
        git = FakeGit(local=('.agents/events.jsonl',))
        self.assertTrue(self.runner(git).check())

    def test_rewritten_history_is_not_taken(self):
        git = FakeGit(ancestor=False)
        self.assertFalse(self.runner(git).check())
        self.assertEqual(self.status()['state'], 'blocked')
        self.assertFalse(any(c[0] == 'merge' for c in git.calls))

    def test_waits_while_something_is_half_done(self):
        git = FakeGit()
        runner = self.runner(git, busy=['העלאה למטא מתבצעת עכשיו'])
        self.assertFalse(runner.check())
        self.assertEqual(self.status()['state'], 'waiting')
        self.assertIn('העלאה למטא', self.status()['message'])
        self.assertFalse(any(c[0] == 'merge' for c in git.calls))
        self.assertTrue(runner.due())  # looks again on the next loop
        self.assertEqual(len(self.spawned), 2)

    def test_a_broken_version_is_rolled_back_and_the_old_one_keeps_running(self):
        git = FakeGit()
        runner = self.runner(git, smoke_error='ImportError: boom')
        self.assertFalse(runner.check())
        self.assertIn(('reset', '--keep', 'a' * 40), git.calls)
        self.assertEqual((self.status()['state'], git.current, len(self.spawned)), ('error', 'a' * 40, 2))

    def test_github_unreachable_keeps_running(self):
        self.assertFalse(self.runner(FakeGit(fetch_error=True)).check())
        self.assertEqual((self.status()['state'], len(self.spawned)), ('error', 2))

    def test_the_screen_button_asks_for_a_check_now(self):
        runner = self.runner(FakeGit(available='a' * 40))
        runner.check()
        self.assertFalse(runner.due())
        (Path(self.temp.name) / 'update-request.json').write_text('{}', encoding='utf-8')
        self.assertTrue(runner.due(runner.requested()))
        self.assertFalse((Path(self.temp.name) / 'update-request.json').exists())

    def test_a_crashed_process_is_started_again(self):
        runner = self.runner(FakeGit(available='a' * 40))
        self.spawned[0].stopped = True
        runner.keep_alive()
        self.assertEqual(len(self.spawned), 3)

    def test_settings_are_saved_once_and_reused(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(run_owner, 'ROOT', Path(root)):
            first = run_owner.load_settings(['--data-dir', 'D:/rosery', '--phone-bind-ip', '100.1.1.1', '--phone-ip', '100.1.1.2',
                                             '--branch', 'claude/x'])
            again = run_owner.load_settings([])
            self.assertEqual(again, first)
            self.assertEqual((again['port'], again['every_minutes']), (8767, 5))


class RealGitTests(unittest.TestCase):
    """The same rounds against a real repository and remote, as on the owner's computer."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.remote, self.work, self.other = base / 'remote.git', base / 'work', base / 'other'
        run = lambda *a, cwd=base: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)
        self.run = run
        run('git', 'init', '-q', '--bare', '-b', 'main', str(self.remote))
        for folder in (self.work, self.other):
            run('git', 'clone', '-q', str(self.remote), str(folder))
            run('git', 'config', 'user.email', 't@t', cwd=folder)
            run('git', 'config', 'user.name', 't', cwd=folder)
        (self.other / 'app.txt').write_text('v1', encoding='utf-8')
        run('git', 'add', '.', cwd=self.other)
        run('git', 'commit', '-q', '-m', 'v1', cwd=self.other)
        run('git', 'push', '-q', 'origin', 'HEAD:main', cwd=self.other)
        run('git', 'pull', '-q', 'origin', 'main', cwd=self.work)
        self.data = base / 'data'

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args, timeout=120):
        done = subprocess.run(['git', *args], cwd=self.work, capture_output=True, text=True, timeout=timeout)
        if done.returncode:
            raise RuntimeError(done.stderr)
        return done.stdout.strip()

    def push(self, text):
        (self.other / 'app.txt').write_text(text, encoding='utf-8')
        self.run('git', 'commit', '-q', '-am', text, cwd=self.other)
        self.run('git', 'push', '-q', 'origin', 'HEAD:main', cwd=self.other)

    def runner(self, smoke_error=None):
        def smoke():
            if smoke_error:
                raise RuntimeError(smoke_error)
        runner = Runner({'data_dir': str(self.data), 'port': 1, 'branch': 'main', 'every_minutes': 5}, git_run=self.git,
                        smoke=smoke, busy=lambda: [], spawn=FakeChild)
        runner.start()
        return runner

    def test_fast_forward_update_and_rollback_on_a_failed_check(self):
        self.push('v2')
        self.assertTrue(self.runner().check())
        self.assertEqual((self.work / 'app.txt').read_text(encoding='utf-8'), 'v2')
        before = self.git('rev-parse', 'HEAD')
        self.push('v3-broken')
        self.assertFalse(self.runner(smoke_error='boom').check())
        self.assertEqual((self.git('rev-parse', 'HEAD'), (self.work / 'app.txt').read_text(encoding='utf-8')), (before, 'v2'))

    def test_an_unrelated_local_edit_survives_update_and_rollback(self):
        (self.other / 'events.jsonl').write_text('x', encoding='utf-8')
        self.run('git', 'add', '.', cwd=self.other)
        self.run('git', 'commit', '-q', '-m', 'events', cwd=self.other)
        self.run('git', 'push', '-q', 'origin', 'HEAD:main', cwd=self.other)
        self.run('git', 'pull', '-q', 'origin', 'main', cwd=self.work)
        (self.work / 'events.jsonl').write_text('written by another tool', encoding='utf-8')
        self.push('v2')
        self.assertTrue(self.runner().check())
        self.assertEqual((self.work / 'app.txt').read_text(encoding='utf-8'), 'v2')
        self.push('v3-broken')
        self.assertFalse(self.runner(smoke_error='boom').check())
        self.assertEqual((self.work / 'app.txt').read_text(encoding='utf-8'), 'v2')
        self.assertEqual((self.work / 'events.jsonl').read_text(encoding='utf-8'), 'written by another tool')

    def test_owner_edits_stay(self):
        self.push('v2')
        (self.work / 'app.txt').write_text('my edit', encoding='utf-8')
        self.assertFalse(self.runner().check())
        self.assertEqual((self.work / 'app.txt').read_text(encoding='utf-8'), 'my edit')


if __name__ == '__main__':
    unittest.main()
