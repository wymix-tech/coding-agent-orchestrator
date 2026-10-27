"""Strictly read-only continuity inspection: no index, guard or outbox repair."""
from copy import deepcopy
import json


def read_work(repo, wid=None):
    from scripts.cao_defaults import WORK_DIR
    from scripts.lifecycle_guard import guard_path, read_guard, digest
    works = []
    if wid:
        from scripts.runtime_store import identifier
        identifier(wid)
    paths = [repo / WORK_DIR / (wid + '.json')] if wid else sorted((repo / WORK_DIR).glob('*.json'))
    for path in paths:
        guard_path(repo, path.stem)
        for entry in (path, *path.parents):
            if entry == repo:
                break
            if entry.is_symlink():
                raise ValueError("WORK_SYMLINK")
        value = json.loads(path.read_text())
        guard = read_guard(repo, path.stem)
        if guard:
            if guard['status'] == 'prepared':
                raise ValueError('LIFECYCLE_TRANSITION_PENDING: recover authorized state before execution')
            if guard.get('protected') and digest(value) != guard['sha256']:
                raise ValueError('LIFECYCLE_UNSUPPORTED_WRITER: use lifecycle recover')
        if value.get('id') != path.stem:
            raise ValueError('WORK_ID_MISMATCH')
        works.append(value)
    if wid:
        return works[0]
    active = [w for w in works if w.get('status') == 'active']
    candidates = active or [w for w in works if w.get('status') in {'todo', 'suspended'}]
    if len(candidates) != 1:
        raise ValueError('WORK_ITEM_SELECTION_REQUIRED')
    return candidates[0]


def plan(repo, work):
    from scripts.cao_sessions import latest_handoff
    from scripts.continuity_view import snapshot, view
    from scripts.runtime_queries import runs
    from scripts.project_verification import project_prerequisite
    handoff = latest_handoff(repo, work['id'])
    current = snapshot(repo, work)
    if old_work := (handoff or {}).get('work_item'):
        if old_work != work['id']:
            raise ValueError('HANDOFF_WORK_MISMATCH')
    old = (handoff or {}).get('continuity') or {}
    changed = [key for key in current if key != 'capture_limits' and old and old.get(key) != current[key]]
    unresolved = []
    for row in runs(repo, work):
        if row['recovery_status'] not in {'complete', 'projection_missing', 'superseded'}:
            unresolved.append(row)
    native = {}
    try:
        from scripts.cao_plans import provider_verification_plan_state
        native = provider_verification_plan_state(repo, work)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        native = {'ready': False, 'reason': str(exc)}
    prerequisite = project_prerequisite(repo, work)
    if prerequisite:
        return {'work_id': work['id'], 'status': work['status'], 'ready': False,
                'next': prerequisite, 'check_only': True, 'pending_work': {'id': work['id'], 'status': work['status']}}
    from scripts.evidence_domains import changed as domain_changes
    diagnostics = domain_changes(old.get('evidence_domains') or {}, current.get('evidence_domains') or {}) if old else {}
    next_action = ('Inspect unresolved runs; never replay unknown side effects.' if unresolved else
                   'Review changed inputs and refresh affected native evidence.' if changed else
                   'Continue original native workflow from the recorded unit; preserve current Work.')
    return {'work_id': work['id'], 'status': work['status'], 'check_only': True,
            'ready': work['status'] in {'active', 'suspended', 'todo'} and not unresolved and not changed,
            'continuity': view(work, handoff), 'current': current, 'changed_domains': changed,
            'native_progress': native, 'unresolved_runs': unresolved, 'evidence_changes': diagnostics,
            'next': next_action, 'historical': work['status'] == 'completed'}


def command(args):
    from scripts.cao_runtime import repo_root
    repo = repo_root(args.repo)
    work = read_work(repo, args.work)
    print(json.dumps({'ok': True, 'recovery': plan(repo, work)}, ensure_ascii=False))
    return 0


def accept(repo, work, handoff_id, host, session_id):
    from scripts.cao_sessions import find_handoff, load_session
    from scripts.runtime_store import safe_path, write_record
    from scripts.lifecycle_guard import digest
    handoff = find_handoff(repo, handoff_id)
    if not handoff or handoff['work_item'] != work['id']:
        raise ValueError('HANDOFF_WORK_MISMATCH')
    session = load_session(repo, session_id, host)
    if not session or session.get('work_item_id') != work['id']:
        raise ValueError('HANDOFF_RECEIVER_NOT_BOUND')
    report = plan(repo, work)
    record = {'schema_version': 1, 'handoff_id': handoff_id, 'work_id': work['id'],
              'session_id': session['id'], 'state_revision': work.get('state_revision'),
              'generation': work.get('execution_generation'), 'recovery': report}
    identity = 'accept-' + digest(record)[:24]
    write_record(safe_path(repo, work['id'], 'handoff-acceptance', identity + '.json'), record)
    return {'id': identity, 'record': record, 'note': 'Reception observation only; no completion authority.'}


def summary(report):
    """Bound model context; full inspection remains available through --check."""
    from scripts.continuity_view import page
    result = deepcopy(report)
    if result.get('current'):
        current = result['current']
        result['current'] = {k: current.get(k) for k in ('product_snapshot', 'workspace', 'native_unit_error')}
        workspace = deepcopy(result['current'].get('workspace') or {})
        if workspace.get('dirty_paths') is not None:
            workspace['dirty_paths'] = page(workspace.get('dirty_paths'))
        result['current']['workspace'] = workspace
        result['current']['product_files'] = page(current.get('product_files'))
        result['current']['native_units'] = page(current.get('native_units'))
    if result.get('native_progress'):
        native = result['native_progress']
        result['native_progress'] = {k: native.get(k) for k in ('ready', 'state', 'reason', 'next')}
        result['native_progress']['pending'] = page(native.get('pending'))
    return result
