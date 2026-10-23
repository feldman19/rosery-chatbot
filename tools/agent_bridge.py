"""Two agents on one repo: Claude Code and Codex build in parallel and review each other.

Each agent works on its own branch in its own git worktree, so neither writes
over the other's files. After a commit the other agent reviews it read-only and
the review is saved under .agents/reviews/. Messages between agents are plain
files under .agents/messages/<to>/. Nothing here touches Meta, Shopify or the
live data folder; Codex runs with a restricted sandbox, never full access.

  python tools/agent_bridge.py codex-task  <task> <brief.md>     start Codex on its own worktree (background)
  python tools/agent_bridge.py codex-review <commit>              Codex reviews a commit, read-only
  python tools/agent_bridge.py claude-review <commit>             Claude reviews a commit, read-only
  python tools/agent_bridge.py propose <agent> <task> <brief.md>  one agent's design proposal, read-only
  python tools/agent_bridge.py status                             tasks, running agents, latest reviews
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENTS = REPO / '.agents'
WORKTREES = REPO.parent / (REPO.name + '-worktrees')
RULES = AGENTS / 'README.md'
NAME = re.compile(r'^[a-z0-9][a-z0-9-]{1,40}$')


def codex_exe():
    found = shutil.which('codex')
    if found:
        return found
    base = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI' / 'Codex' / 'bin'
    candidates = sorted(base.glob('*/codex.exe'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        sys.exit('Codex CLI not found')
    return str(candidates[0])


def git(*args, cwd=REPO):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True, encoding='utf-8').stdout.strip()


def stamp():
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S')


def shared_rules():
    return RULES.read_text(encoding='utf-8') if RULES.exists() else ''


def log_event(kind, **fields):
    AGENTS.mkdir(exist_ok=True)
    with (AGENTS / 'events.jsonl').open('a', encoding='utf-8') as out:
        out.write(json.dumps({'at': datetime.datetime.now().isoformat(timespec='seconds'), 'kind': kind, **fields},
                             ensure_ascii=False) + '\n')


def codex_task(task, brief):
    if not NAME.match(task):
        sys.exit('Task name: lowercase letters, digits, dashes')
    branch = 'codex/' + task
    tree = WORKTREES / ('codex-' + task)
    base = git('rev-parse', '--abbrev-ref', 'HEAD')
    if not tree.exists():
        WORKTREES.mkdir(exist_ok=True)
        git('worktree', 'add', '-b', branch, str(tree), base)
    prompt = (shared_rules() + '\n\n# Your task: ' + task + '\n\n' + Path(brief).read_text(encoding='utf-8') +
              f'\n\nWork only inside {tree}. Commit to branch {branch} when tests pass. Do not push. '
              'Finish with a short report: what you changed, which tests you ran and their result, open questions.')
    result = AGENTS / 'messages' / 'claude' / f'{stamp()}-codex-{task}-report.md'
    result.parent.mkdir(parents=True, exist_ok=True)
    log = AGENTS / 'logs' / f'codex-{task}-{stamp()}.jsonl'
    log.parent.mkdir(parents=True, exist_ok=True)
    # A worktree commits into the main repository's .git; without it the sandbox cannot commit.
    command = [codex_exe(), 'exec', '-s', 'workspace-write', '-C', str(tree), '--add-dir', str(REPO / '.git'),
               '--json', '-o', str(result), '-']
    with log.open('w', encoding='utf-8') as out:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8')
        process.stdin.write(prompt)
        process.stdin.close()
    log_event('codex_task_started', task=task, branch=branch, worktree=str(tree), pid=process.pid,
              report=str(result.relative_to(REPO)), log=str(log.relative_to(REPO)))
    print(json.dumps({'task': task, 'branch': branch, 'worktree': str(tree), 'pid': process.pid,
                      'report': str(result)}, ensure_ascii=False))


def review_prompt(commit, reviewer):
    diff = git('show', '--stat', '--patch', '--format=%H%n%s%n%n%b', commit)
    if len(diff) > 150_000:
        diff = diff[:150_000] + '\n[diff truncated]'
    return (shared_rules() + f'\n\n# Review request for {reviewer}\n'
            'Review this commit for correctness bugs, missing tests, and violations of the project rules above '
            '(missing data shown as zero, causal claims, any write to Meta or Shopify, secrets). '
            'List findings most severe first, each with file:line, the concrete failure scenario, and a suggested fix. '
            'Say plainly if you found nothing serious. Do not modify any file.\n\n' + diff)


def codex_review(commit):
    sha = git('rev-parse', '--short', commit)
    out = AGENTS / 'reviews' / f'{sha}-by-codex.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([codex_exe(), 'exec', '-s', 'read-only', '-C', str(REPO), '-o', str(out), '-'],
                   input=review_prompt(commit, 'Codex'), text=True, encoding='utf-8', check=True,
                   capture_output=True, timeout=1800)
    log_event('review', reviewer='codex', commit=sha, file=str(out.relative_to(REPO)))
    print(out)


def claude_review(commit):
    sha = git('rev-parse', '--short', commit)
    out = AGENTS / 'reviews' / f'{sha}-by-claude.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    exe = shutil.which('claude') or sys.exit('Claude Code CLI not found')
    done = subprocess.run([exe, '-p', '--tools', '', '--no-session-persistence', '--output-format', 'text'],
                          input=review_prompt(commit, 'Claude'), text=True, encoding='utf-8', check=True,
                          capture_output=True, timeout=1800, cwd=REPO)
    out.write_text(done.stdout, encoding='utf-8')
    log_event('review', reviewer='claude', commit=sha, file=str(out.relative_to(REPO)))
    print(out)


def propose(agent, task, brief):
    """Brainstorm: each agent writes its own design for the same brief, without seeing the other's."""
    out = AGENTS / 'design' / task / f'{agent}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = (shared_rules() + f'\n\n# Design proposal for task {task}\n' + Path(brief).read_text(encoding='utf-8') +
              '\n\nRead the code you need, then write a design: approach, files touched, data flow, risks, '
              'tests, and what you would ask the owner. Do not modify or create any file: reply with the full '
              'design as your final message; the bridge saves it.')
    if agent == 'codex':
        subprocess.run([codex_exe(), 'exec', '-s', 'read-only', '-C', str(REPO), '-o', str(out), '-'],
                       input=prompt, text=True, encoding='utf-8', check=True, capture_output=True, timeout=1800)
    else:
        exe = shutil.which('claude') or sys.exit('Claude Code CLI not found')
        done = subprocess.run([exe, '-p', '--output-format', 'text', '--allowedTools', 'Read,Grep,Glob'],
                              input=prompt, text=True, encoding='utf-8', check=True, capture_output=True,
                              timeout=1800, cwd=REPO)
        out.write_text(done.stdout, encoding='utf-8')
    log_event('proposal', agent=agent, task=task, file=str(out.relative_to(REPO)))
    print(out)


def status():
    tasks = AGENTS / 'tasks.md'
    print(tasks.read_text(encoding='utf-8') if tasks.exists() else 'no tasks.md')
    print('\nworktrees:\n' + git('worktree', 'list'))
    events = AGENTS / 'events.jsonl'
    if events.exists():
        print('\nlast events:')
        for line in events.read_text(encoding='utf-8').splitlines()[-10:]:
            print(' ', line)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('codex-task'); p.add_argument('task'); p.add_argument('brief')
    p = sub.add_parser('codex-review'); p.add_argument('commit')
    p = sub.add_parser('claude-review'); p.add_argument('commit')
    p = sub.add_parser('propose'); p.add_argument('agent', choices=('codex', 'claude')); p.add_argument('task'); p.add_argument('brief')
    sub.add_parser('status')
    args = parser.parse_args()
    if args.command == 'codex-task':
        codex_task(args.task, args.brief)
    elif args.command == 'codex-review':
        codex_review(args.commit)
    elif args.command == 'claude-review':
        claude_review(args.commit)
    elif args.command == 'propose':
        propose(args.agent, args.task, args.brief)
    else:
        status()


if __name__ == '__main__':
    main()
