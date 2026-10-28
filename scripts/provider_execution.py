"""Bounded provider commands; transition journals prevent unknown-effect replay."""
import json
import shlex
import uuid
from contextlib import contextmanager


def run(repo, argv, *, kind, timeout, env=None, wid='_unassigned'):
    from scripts.completion_policy import normalize_timeout
    from scripts.runtime_store import start_run
    from scripts.runtime_execution import owner, execute, finish_execution
    timeout = normalize_timeout(timeout)
    rid = 'provider-' + uuid.uuid4().hex
    start_run(repo, wid, rid, kind, {'argv': argv})
    with owner(repo, wid, rid):
        result = execute(repo, wid, rid, 'command-0001', shlex.join(argv), timeout, env=env)
        finish_execution(repo, wid, rid, [result])
    return {'command': shlex.join(argv), 'exit_code': result['exit_code'],
            'stdout': result.get('stdout_tail', ''), 'stderr': result.get('stderr_tail', ''),
            'process_status': result.get('process_status'), 'run_id': rid}


@contextmanager
def transition_owner(repo, wid):
    import os
    from scripts.runtime_store import safe_path
    from scripts.state_store import _lock, _unlock
    path = safe_path(repo, wid, 'provider-transition.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b'0')
        try:
            _lock(fd)
        except OSError:
            raise ValueError('PROVIDER_TRANSITION_EXECUTOR_ACTIVE') from None
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def transition(repo, target, wid):
    with transition_owner(repo, wid):
        return _transition(repo, target, wid)


def _transition(repo, target, wid):
    from scripts.cao_runtime import config
    from scripts.cao_work_store import load_work, save_work
    from scripts.state_store import repo_transaction
    from scripts.cao_completion import verification_inputs
    from scripts.lifecycle_guard import digest
    from scripts.cao_providers import run_sdd_command
    command = str(((config(repo).get('sdd') or {}).get('command') or {}).get('transition') or '').strip()
    if not command:
        return None
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if not work or work.get('status') != 'active':
            raise ValueError('TRANSITION_ACTIVE_WORK_REQUIRED')
        identity = {'command': command, 'target': target, 'generation': work.get('execution_generation', 0),
                    'inputs': verification_inputs(repo, work, config(repo))}
        rows = work.setdefault('sdd', {}).setdefault('transition_runs', [])
        for row in reversed(rows):
            if all(row['identity'].get(key) == identity[key] for key in ('command', 'target', 'generation')):
                if row['status'] == 'passed':
                    return row['result']
                if row['status'] != 'not_applied':
                    raise ValueError('PROVIDER_TRANSITION_UNRESOLVED: inspect the operation and use cao sdd transition resolve --work ' + wid + ' --operation ' + row['id'] + ' --outcome applied|not-applied --evidence <inspection-file> --reason <diagnosis>')
                break
        if any(row['status'] in {'running', 'unknown'} for row in rows):
            raise ValueError('PROVIDER_TRANSITION_UNRESOLVED: resolve the existing transition before new inputs')
        row = {'id': 'transition-' + uuid.uuid4().hex, 'identity': identity, 'status': 'running'}
        rows.append(row)
        save_work(repo, work)
    result = run_sdd_command(repo, 'transition', [target])
    with repo_transaction(repo):
        work = load_work(repo, wid)
        saved = next(r for r in work['sdd']['transition_runs'] if r['id'] == row['id'])
        saved['result'] = result
        # Even a nonzero external command may have applied partial effects.
        saved['status'] = 'passed' if result and result['exit_code'] == 0 else 'unknown'
        saved['observed_inputs'] = digest(verification_inputs(repo, work, config(repo)))
        save_work(repo, work)
    return result


def resolve(args):
    from scripts.cao_runtime import repo_root, sha256_file
    repo = repo_root(args.repo)
    with transition_owner(repo, args.work):
        return _resolve(args, repo)


def _resolve(args, repo):
    from scripts.cao_runtime import sha256_file
    from scripts.cao_work_store import resolve_work, save_work, queue_event
    from scripts.state_store import repo_transaction
    from scripts.path_guard import repo_file
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        row = next((r for r in (work or {}).get('sdd', {}).get('transition_runs', []) if r['id'] == args.operation), None)
        if not row or row['status'] not in {'running', 'unknown'}:
            raise ValueError('UNRESOLVED_TRANSITION_REQUIRED')
        # A transition may be live even before its runtime ID is committed.
        from scripts.runtime_execution import owner_status
        from scripts.runtime_store import run_records, read_record, run_path
        for folder in run_records(repo, '_unassigned'):
            start = read_record(run_path(repo, '_unassigned', folder.name, 'start.json')) or {}
            if start.get('kind') == 'provider_transition' and owner_status(repo, '_unassigned', folder.name) == 'running':
                raise ValueError('PROVIDER_TRANSITION_EXECUTOR_ACTIVE')
        path = repo_file(repo, args.evidence)
        if not path.is_file() or not path.read_bytes().strip() or not args.reason.strip():
            raise ValueError('TRANSITION_INSPECTION_REQUIRED')
        row['resolution'] = {'path': path.relative_to(repo).as_posix(), 'sha256': sha256_file(path), 'reason': args.reason}
        row['status'] = 'passed' if args.outcome == 'applied' else 'not_applied'
        if args.outcome == 'applied':
            row['result'] = {'exit_code': 0, 'source': 'inspection', 'operation': row['id']}
        queue_event(work, {'event': 'provider.transition.resolved', 'work_item_id': work['id'], 'operation': row['id'], 'outcome': args.outcome})
        save_work(repo, work)
    print(json.dumps({'ok': True, 'operation': row}))
    return 0
