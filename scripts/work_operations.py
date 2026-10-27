"""Durable cross-Work switch journal and write barrier; no product rollback."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
import uuid

_authorized = ContextVar('cao_operation_writer', default=False)


def root(repo):
    path = repo / '.orchestrator/operations'
    for entry in (path, *path.parents):
        if entry == repo:
            break
        if entry.is_symlink():
            raise ValueError('OPERATION_SYMLINK')
    return path


def pending(repo):
    rows = []
    for path in root(repo).glob('*.json'):
        if path.is_symlink():
            raise ValueError('OPERATION_SYMLINK')
        row = json.loads(path.read_text())
        if row.get('status') not in {'committed', 'aborted'}:
            rows.append(row)
    return rows


def barrier(repo):
    if not _authorized.get() and pending(repo):
        raise ValueError('WORK_TRANSITION_PENDING: use cao switch recover --operation <id>')


@contextmanager
def authorized():
    token = _authorized.set(True)
    try:
        yield
    finally:
        _authorized.reset(token)


def semantic(work):
    from scripts.lifecycle_guard import digest
    return digest({k: v for k, v in work.items() if k not in {'state_revision', 'updated_at', 'pending_events'}})


def preview(repo, target_id):
    from scripts.recovery_plan import read_work, plan
    from scripts.cao_defaults import WORK_DIR
    target = read_work(repo, target_id)
    if target['status'] == 'completed':
        raise ValueError('WORK_ITEM_ALREADY_COMPLETED')
    works = [read_work(repo, p.stem) for p in (repo / WORK_DIR).glob('*.json')]
    active = [w for w in works if w['status'] == 'active']
    if len(active) > 1:
        raise ValueError('MULTIPLE_ACTIVE_WORK_ITEMS')
    source = active[0] if active else None
    recovery = plan(repo, target)
    from scripts.project_verification import project_prerequisite
    from scripts.cao_completion import work_sdd_ready
    blockers = []
    if project_prerequisite(repo, target if target.get('project_binding') else None):
        blockers.append('PROJECT_PREPARATION_REQUIRED')
    if not work_sdd_ready(repo, target)[0]:
        blockers.append('SDD_NOT_INSTALLED')
    from scripts.provider_restart import restart_state
    from scripts.cao_providers import detect_current_host
    if not restart_state(repo, detect_current_host())['ready']:
        blockers.append('HOST_RESTART_REQUIRED')
    from scripts.cao_runtime import sha256_file
    if sha256_file(repo / target['requirement']['path']) != target['requirement']['sha256']:
        blockers.append('REQUIREMENT_CHANGED_REANCHOR_REQUIRED')
    if source:
        from scripts.recovery_plan import plan as recovery_plan
        if recovery_plan(repo, source).get('unresolved_runs'):
            blockers.append('SOURCE_RUN_UNRESOLVED')
        # In-flight host effects are observations, not controllable processes.
        from scripts.runtime_queries import events
        starts, finishes = set(), set()
        for event in events(repo, source['id']):
            key = (event.get('host'), event.get('session_id'), event.get('tool_call_id'))
            if key[-1] and event.get('event') == 'tool.started':
                starts.add(key)
            if key[-1] and event.get('event') in {'tool.finished', 'tool.effect.settled'}:
                finishes.add(key)
        if starts - finishes:
            blockers.append('SOURCE_HOST_EFFECT_UNRESOLVED')
    return {'source': source, 'target': target, 'recovery': recovery,
            'blockers': blockers, 'ready': not blockers, 'pending_operations': pending(repo)}


def switch(repo, target_id, reason, session=None, host=None):
    from scripts.state_store import repo_transaction, atomic_json
    from scripts.cao_sessions import create_handoff
    from scripts.cao_work_store import load_work, queue_event
    from scripts.project_context import make_binding
    from scripts.cao_snapshot import anchor_verification
    from scripts.cao_runtime import config
    from scripts.lifecycle_guard import digest
    with repo_transaction(repo):
        barrier(repo)
        # Explicit mutation may seal interrupted authorized Work writes before read-only preflight.
        from scripts.cao_defaults import WORK_DIR
        for path in (repo / WORK_DIR).glob("*.json"):
            load_work(repo, path.stem)
        state = preview(repo, target_id)
        source, target = state['source'], state['target']
        if source and source['id'] == target_id:
            return {'ok': True, 'status': 'unchanged', 'work_id': target_id}
        if not state['ready']:
            return {'ok': False, 'error': state['blockers'][0], 'blockers': state['blockers']}
        if target.get('blockers'):
            return {'ok': False, 'error': 'TARGET_BLOCKERS_UNRESOLVED'}
        handoff = create_handoff(repo, source, session, host, reason) if source else None
        if source:
            source = load_work(repo, source['id'])
        planned_target = deepcopy(target)
        if target['status'] == 'todo':
            planned_target['project_binding'] = make_binding(repo, target.get('deferred_scopes'))
            anchor_verification(repo, planned_target, config(repo))
            from scripts.review_changes import capture_baseline
            capture_baseline(repo, planned_target)
        if session and host:
            planned_target.setdefault('resume', {})['last_agent'] = host
        from scripts.cao_runtime import now
        planned_target['started_at'] = planned_target.get('started_at') or now()
        planned_target['status'] = 'active'
        planned_target['execution_generation'] = int(target.get('execution_generation', 0)) + 1
        changes = []
        if source:
            after = deepcopy(source)
            after.setdefault('resume', {}).setdefault('pending', []).append('Execution re-anchored to another Work; inspect changed inputs before returning.')
            after['status'] = 'suspended'
            after['execution_generation'] = int(source.get('execution_generation', 0)) + 1
            changes.append({'before': source, 'after': after})
        changes.append({'before': target, 'after': planned_target})
        identity = 'switch-' + uuid.uuid4().hex
        operation = {'version': 1, 'id': identity, 'status': 'prepared', 'reason': reason,
                     'target': target_id, 'source': source['id'] if source else None,
                     'changes': changes, 'handoff_id': handoff['id'] if handoff else None,
                     'session': session, 'host': host,
                     'product_snapshot': (handoff or {}).get('continuity', {}).get('product_snapshot'),
                     'config_fingerprint': digest(config(repo))}
        from scripts.lifecycle_guard import digest
        operation['intent_sha256'] = digest({k: v for k, v in operation.items() if k != 'status'})
        atomic_json(root(repo) / (identity + '.json'), operation)
        return recover(repo, identity)


def recover(repo, identity):
    from scripts.runtime_store import identifier
    from scripts.state_store import repo_transaction, atomic_json
    from scripts.cao_work_store import load_work, save_work, queue_event
    from scripts.cao_sessions import register_session
    path = root(repo) / (identifier(identity) + '.json')
    with repo_transaction(repo), authorized():
        operation = json.loads(path.read_text())
        from scripts.lifecycle_guard import digest
        if operation.get('intent_sha256') != digest({k: v for k, v in operation.items() if k not in {'status', 'intent_sha256'}}):
            raise ValueError('OPERATION_INTENT_INVALID')
        if operation['status'] not in {'prepared', 'applying', 'committed'}:
            raise ValueError('OPERATION_STATUS_INVALID')
        if any(change['before']['id'] != change['after']['id'] for change in operation['changes']):
            raise ValueError('OPERATION_WORK_MISMATCH')
        if operation['id'] != identity:
            raise ValueError('OPERATION_ID_MISMATCH')
        if operation['status'] == 'committed':
            return {'ok': True, 'status': 'unchanged', 'operation': identity}
        if operation.get('config_fingerprint'):
            from scripts.cao_runtime import config
            from scripts.lifecycle_guard import digest
            if digest(config(repo)) != operation['config_fingerprint']:
                raise ValueError('SWITCH_RECOVERY_CONFIG_CHANGED')
        if operation.get('product_snapshot'):
            from scripts.cao_snapshot import code_snapshot
            if code_snapshot(repo) != operation['product_snapshot']:
                raise ValueError('SWITCH_RECOVERY_PRODUCT_CHANGED')
        # Validate ALL records before touching any of them.
        for change in operation['changes']:
            actual = load_work(repo, change['before']['id'])
            if semantic(actual) not in {semantic(change['before']), semantic(change['after'])}:
                raise ValueError('SWITCH_RECOVERY_CONFLICT')
        if operation.get('kind') == 'amend':
            import base64
            from scripts.path_guard import repo_file
            target = repo_file(repo, operation['requirement_path'])
            before_bytes = base64.b64decode(operation['before_bytes'])
            after_bytes = base64.b64decode(operation['after_bytes'])
            if target.read_bytes() not in {before_bytes, after_bytes}:
                raise ValueError('AMEND_RECOVERY_SOURCE_CONFLICT')
            import os, tempfile
            fd, name = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(after_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        operation['status'] = 'applying'
        atomic_json(path, operation)
        for change in operation['changes']:
            actual = load_work(repo, change['before']['id'])
            if semantic(actual) == semantic(change['after']):
                continue
            planned = deepcopy(change['after'])
            planned['state_revision'] = actual.get('state_revision', 0)
            queue_event(planned, {'event': 'work.switched', 'work_item_id': planned['id'],
                                 'operation_id': identity, 'idempotency_key': identity + planned['id']})
            save_work(repo, planned)
        if operation.get('session') and operation.get('host'):
            register_session(repo, load_work(repo, operation['target']), operation['session'], operation['host'], event='switch')
        operation['status'] = 'committed'
        atomic_json(path, operation)
        return {'ok': True, 'status': 'switched', 'operation': identity,
                'target': operation['target'], 'handoff_id': operation.get('handoff_id')}


def command(args):
    from scripts.cao_runtime import repo_root
    repo = repo_root(args.repo)
    if args.switch_action == 'recover' and not args.operation:
        raise ValueError('OPERATION_ID_REQUIRED')
    if args.switch_action != 'recover' and not args.to:
        raise ValueError('TARGET_WORK_REQUIRED')
    if args.switch_action == 'recover':
        result = recover(repo, args.operation)
    elif args.check:
        state = preview(repo, args.to)
        result = {'ok': True, 'check_only': True, **state}
    else:
        result = switch(repo, args.to, args.reason, args.session, args.host)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


def register_parser(sub):
    p = sub.add_parser('switch', help='Switch Work through a durable recoverable journal')
    p.add_argument('switch_action', nargs='?', choices=['recover'])
    p.add_argument('--repo', default='.')
    p.add_argument('--to')
    p.add_argument('--operation')
    p.add_argument('--reason', default='explicit task switch')
    p.add_argument('--session')
    p.add_argument('--host')
    p.add_argument('--check', action='store_true')
    p.set_defaults(fn=command)
