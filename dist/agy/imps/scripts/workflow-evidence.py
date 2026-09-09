#!/usr/bin/env python3
"""Revision snapshots and atomic run state. No model judgement or implicit approvals."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import math
import subprocess
import sys
import tempfile
import time
import uuid


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()


PROCESS_CRITERION = re.compile(r'^(Gates green|Persona panel reviewed|No merge conflicts|CI green on the PR|Outcome comment posted to the source Discussion)\b', re.I)


def contract(text):
    """Only acceptance and constraints are immutable; progress prose is not a spec."""
    section = ''
    criteria, constraints = [], []
    for line in text.splitlines():
        heading = re.match(r'^##\s+(.+)', line)
        if heading:
            section = heading.group(1).strip().lower()
        elif section.startswith('definition of done'):
            item = re.match(r'^\s*- \[[ xX]\]\s+(.+)', line)
            if not item or PROCESS_CRITERION.match(item.group(1)):
                continue
            value = item.group(1).strip()
            explicit = re.match(r'^\[([A-Z][A-Z0-9_-]+)\]\s+(.+)', value)
            identifier = explicit.group(1) if explicit else 'REQ-' + digest(value.encode())[:12]
            wording = explicit.group(2) if explicit else value
            method = re.search(r'\s+\[verify:(inspection|command|runtime|manual)\]$', wording)
            criteria.append({'id': identifier, 'text': wording,
                             **({'method': method.group(1)} if method else {})})
        elif section.startswith('global constraints'):
            constraints.append(line.rstrip())
    if len({item['id'] for item in criteria}) != len(criteria):
        raise ValueError('duplicate requirement ID')
    if not criteria:
        raise ValueError('no_functional_criteria: add observable delivery criteria to GOAL.md; process boxes are insufficient. For research/non-code work use inspection evidence of the produced artifact, not a code diff.')
    value = {'requirements': criteria, 'constraints': '\n'.join(constraints).strip()}
    return value, digest(json.dumps(value, sort_keys=True).encode())


def snapshot(repo, base, goal, refresh=False):
    repo = Path(git(repo, 'rev-parse', '--show-toplevel')).resolve()
    if refresh:
        if not base.startswith('origin/'):
            raise ValueError('refresh requires origin/<branch>')
        branch = base[len('origin/'):]
        git(repo, 'check-ref-format', 'refs/heads/' + branch)
        command = ['git', '-C', str(repo), 'fetch', '--quiet', '--no-tags', 'origin',
                   'refs/heads/' + branch + ':refs/remotes/origin/' + branch]
        subprocess.run([sys.executable, str(Path(__file__).with_name('run-bounded.py')), '30', *command],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    goal = Path(goal).resolve()
    value, spec_hash = contract(goal.read_text())
    head = git(repo, 'rev-parse', 'HEAD^{commit}')
    base_sha = git(repo, 'rev-parse', '--verify', base + '^{commit}')
    # Changes to progress boxes in GOAL.md are permitted. The semantic hash above
    # still invalidates evidence when the agreed criteria or constraints change.
    exclusions = []
    if goal.is_relative_to(repo):
        exclusions = [':(exclude)' + str(goal.relative_to(repo))]
    dirty = git(repo, 'status', '--porcelain=v1', '--', '.', *exclusions)
    return {'schema': 1, 'repo': str(repo), 'head': head, 'base': base_sha,
            'merge_base': git(repo, 'merge-base', base_sha, head), 'spec_hash': spec_hash,
            'policy_hash': digest(b''.join(path.read_bytes() for path in sorted(Path(__file__).parent.iterdir())
                                          if path.name in ('workflow-evidence.py', 'imps-run.workflow.js', 'run-code-review.sh',
                                                           'run-codex-review.sh', 'run-ocr.sh', 'run-bounded.py', 'review-context.py')) +
                (Path(os.environ.get('IMPS_OCR_RULE', str(Path(__file__).parent.parent / 'references/ocr-review-rule.json'))).read_bytes()
                 if Path(os.environ.get('IMPS_OCR_RULE', str(Path(__file__).parent.parent / 'references/ocr-review-rule.json'))).is_file() else b'') + json.dumps({
                name: os.environ.get(name) for name in ('IMPS_CODEX_MODEL', 'IMPS_CODEX_TIMEOUT',
                'IMPS_CODEX_MAX_DIFF_BYTES', 'IMPS_OCR_MODEL', 'IMPS_OCR_TIMEOUT', 'IMPS_OCR_VERSION')
            }, sort_keys=True).encode()),
            'requirements': value['requirements'], 'clean': not dirty}


def artifact(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('evidence artifact is not a file')
    return {'path': str(path), 'sha256': digest(path.read_bytes())}


def gate_plan(name, command, cwd, seconds, argv=None, source=None):
    root = Path(git('.', 'rev-parse', '--show-toplevel')).resolve()
    cwd = (root / cwd).resolve(strict=True)
    if not cwd.is_relative_to(root) or not cwd.is_dir() or not 0 < seconds <= 3600:
        raise ValueError('gate cwd must be inside checkout and timeout in 1..3600 seconds')
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
        raise ValueError('gate needs an explicit nonempty argv array')
    if shlex.split(command) != argv:
        raise ValueError('display command must match argv exactly')
    binary = Path(argv[0]).name
    if binary in ('sh', 'bash', 'zsh', 'dash', 'fish', 'python', 'python3', 'node', 'perl', 'ruby') and any(arg in ('-c', '-e', '--eval', '-p') for arg in argv[1:]):
        raise ValueError('inline interpreter commands are not supported gates; use a declared repository script')
    if binary in ('env', 'eval', 'exec'):
        raise ValueError('indirect command execution is not a supported gate')
    declared = (root / (source or '')).resolve(strict=True)
    if not declared.is_relative_to(root) or not declared.is_file():
        raise ValueError('gate source must be a repository file')
    contents = declared.read_text()
    supported = False
    check_name = re.compile(r'^(test|lint|build|typecheck|type-check|check|verify|validate|fmt|format)(?:$|[:_.-])', re.I)
    if declared.name == 'package.json' and binary in ('npm', 'pnpm', 'yarn', 'bun'):
        scripts = json.loads(contents).get('scripts', {})
        # No model-added filters, directory switches, snapshot updates or tolerance flags.
        script = argv[2] if len(argv) == 3 and argv[1] == 'run' else argv[1] if binary == 'npm' and argv == ['npm', 'test'] else ''
        supported = declared.parent == cwd and script in scripts and bool(check_name.match(script))
    elif declared.name in ('Makefile', 'makefile', 'GNUmakefile') and binary == 'make':
        targets = set(re.findall(r'^([A-Za-z0-9_.-]+)\s*:(?!=)', contents, re.M))
        supported = declared.parent == cwd and len(argv) == 2 and argv[1] in targets and bool(check_name.match(argv[1]))
    elif declared.suffix in ('.sh', '.py', '.js', '.mjs'):
        # The script itself is the executable declaration. CI YAML is never treated
        # as a bag of commands: use its checked-in test script or a remote obligation.
        relative = declared.relative_to(root).as_posix()
        invocation = (cwd / argv[-1]).resolve() if argv else None
        supported = invocation == declared and (relative.startswith('tests/') or bool(check_name.match(declared.stem))) and (
            (len(argv) == 1 and os.access(declared, os.X_OK)) or
            (len(argv) == 2 and binary in ('bash', 'sh', 'python3', 'node')))
    if not supported:
        raise ValueError('unsupported gate declaration: use an exact check package script, Make target, or checked-in check script; CI-only jobs remain remote obligations')
    return {'gate': name, 'cmd': command, 'argv': argv, 'cwd': str(cwd), 'timeout_seconds': seconds,
            'source': str(declared), 'source_sha256': digest(declared.read_bytes()), 'execution': 'native_host_tool_required'}


def gate_record(name, command, status, log, duration_ms, unavailable_reason=None):
    """Record a native tool result. This helper never executes a gate command."""
    if status == 0 and unavailable_reason:
        raise ValueError('a successful exit cannot be recorded as unavailable')
    if duration_ms is not None and duration_ms < 0:
        raise ValueError('duration must not be negative')
    proof = artifact(log)
    return {'gate': name, 'cmd': command, 'pass': status == 0, 'exit_code': status,
            'status': 'passed' if status == 0 else 'unavailable' if unavailable_reason or status in (124, 126, 127) else 'failed',
            'duration_ms': duration_ms, 'artifact': proof, 'unavailable_reason': unavailable_reason, 'environment_policy': 'native_host',
            'tail': '\n'.join(Path(log).read_text(errors='replace').splitlines()[-20:])}


def state_operation(path, action, token=None, patch=None, confirmed_dead=False):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    claim = path.with_name(path.name + '.owner')
    # Lock one stable inode; do not unlink it after releasing flock.
    with open(str(path) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        owner = json.loads(claim.read_text()) if claim.exists() else None
        if action == 'claim':
            if owner:
                raise ValueError('run already owned; verify the prior invocation stopped before explicit recovery')
            state = json.loads(path.read_text())
            if not isinstance(state, dict) or not isinstance(state.get('tasks'), list) or not state.get('branch'):
                raise ValueError('invalid run state')
            seconds = state.get('budget_seconds', 14400)
            if not isinstance(seconds, (int, float)) or not 0 < seconds <= 86400:
                raise ValueError('budget_seconds must be in 1..86400')
            spent = state.get('budget_spent_seconds', 0)
            if not isinstance(spent, (int, float)) or not math.isfinite(spent) or spent < 0:
                raise ValueError('invalid active budget expenditure')
            now = time.time()
            owner = {'token': uuid.uuid4().hex, 'claimed_at': now, 'deadline': now + max(0, seconds - spent),
                     'spent_before': spent, 'budget_seconds': seconds}
            atomic_json(claim, owner)
            return owner
        if owner and (not token or owner.get('token') != token):
            raise ValueError('run ownership mismatch')
        if action == 'budget':
            if not owner:
                raise ValueError('no ownership record')
            return {'ok': time.time() < owner['deadline'], 'remaining_seconds': max(0, owner['deadline'] - time.time())}
        if action in ('release', 'recover'):
            if not owner:
                raise ValueError('no ownership record')
            if action == 'recover' and not confirmed_dead:
                raise ValueError('recovery requires confirmed dead invocation, never heartbeat age alone')
            if path.exists():
                state = json.loads(path.read_text())
                # Absolute charge from this claim's starting expenditure makes a crash
                # between state replacement and owner removal safe to reconcile.
                state['budget_spent_seconds'] = owner.get('spent_before', 0) + max(0, min(time.time(), owner['deadline']) - owner['claimed_at'])
                atomic_json(path, state)
            claim.unlink()
            return {'released': True}
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not isinstance(patch, dict):
            raise ValueError('state and patch must be objects')
        if not isinstance(value.get('tasks'), list) or not value.get('branch'):
            raise ValueError('invalid run state')
        value.update(patch)
        if not isinstance(value.get('tasks'), list) or not value.get('branch'):
            raise ValueError('patch invalidates run state')
        atomic_json(path, value)
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    snap = commands.add_parser('snapshot')
    snap.add_argument('--repo', default='.')
    snap.add_argument('--base', required=True)
    snap.add_argument('--goal', required=True)
    snap.add_argument('--refresh', action='store_true')
    contract_cmd = commands.add_parser('contract')
    contract_cmd.add_argument('--goal', required=True)
    evidence = commands.add_parser('artifact')
    evidence.add_argument('path')
    check = commands.add_parser('gate-plan')
    check.add_argument('--name', required=True)
    check.add_argument('--cmd', required=True)
    check.add_argument('--argv-json', required=True)
    check.add_argument('--source', required=True)
    check.add_argument('--cwd', default='.')
    check.add_argument('--timeout', required=True, type=int)
    receipt = commands.add_parser('gate-record')
    receipt.add_argument('--name', required=True)
    receipt.add_argument('--cmd', required=True)
    receipt.add_argument('--exit-code', required=True, type=int)
    receipt.add_argument('--log', required=True)
    receipt.add_argument('--duration-ms', type=int)
    receipt.add_argument('--unavailable-reason')
    for action in ('claim', 'patch', 'release', 'recover', 'budget'):
        sub = commands.add_parser(action)
        sub.add_argument('--state', required=True)
        sub.add_argument('--token')
        if action == 'patch':
            sub.add_argument('--patch', required=True, help='JSON object; shell-quote as one argument')
        if action == 'recover':
            sub.add_argument('--confirmed-dead', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'snapshot':
            result = snapshot(args.repo, args.base, args.goal, args.refresh)
        elif args.action == 'contract':
            value, spec_hash = contract(Path(args.goal).read_text())
            result = {**value, 'spec_hash': spec_hash}
        elif args.action == 'artifact':
            result = artifact(args.path)
        elif args.action == 'gate-plan':
            result = gate_plan(args.name, args.cmd, args.cwd, args.timeout, json.loads(args.argv_json), args.source)
        elif args.action == 'gate-record':
            result = gate_record(args.name, args.cmd, args.exit_code, args.log, args.duration_ms, args.unavailable_reason)
        else:
            result = state_operation(args.state, args.action, args.token,
                                     json.loads(args.patch) if args.action == 'patch' else None,
                                     getattr(args, 'confirmed_dead', False))
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
