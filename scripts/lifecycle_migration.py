"""Read-only migration inventory, durable checkpoints and guarded rollback."""
from copy import deepcopy
import json
import uuid

from scripts.lifecycle_guard import digest, read_guard, verify, protect, recover
from scripts.state_store import atomic_json, repo_transaction


def semantic_work(work):
    return {key: value for key, value in work.items() if key not in {'state_revision', 'updated_at', 'pending_events'}}


def source_inputs(repo, work):
    from scripts.cao_snapshot import code_snapshot, verification_snapshot
    from scripts.cao_runtime import config
    return {'repository': code_snapshot(repo), 'baseline': verification_snapshot(config(repo), repo),
            'requirement': work.get('requirement'), 'project_binding': work.get('project_binding')}


def journal_root(repo, wid):
    from scripts.lifecycle_guard import guard_path
    guard_path(repo, wid)  # Validate identity before using it as a directory.
    root = repo / '.orchestrator/lifecycle/migrations' / wid
    for path in (root, *root.parents):
        if path == repo:
            break
        if path.is_symlink():
            raise ValueError('LIFECYCLE_MIGRATION_SYMLINK')
    return root


def latest(repo, wid):
    rows = [(path, json.loads(path.read_text())) for path in journal_root(repo, wid).glob('*.json')]
    return max(rows, key=lambda row: (row[1]['created_at'], row[0].name)) if rows else (None, None)


def preflight(repo, work):
    errors = []
    version = work.get('lifecycle_contract', 1)
    if type(version) is not int or version not in {1, 2}:
        errors.append('LIFECYCLE_CONTRACT_UNSUPPORTED')
    if not isinstance(work.get('status'), str) or work.get('status') not in {'active', 'suspended'}:
        errors.append('ACTIVE_OR_SUSPENDED_WORK_REQUIRED')
    if not isinstance(work.get('sdd'), dict) or not isinstance(work.get('requirement'), dict):
        return {'ok': False, 'eligible': False, 'work_id': work.get('id'),
                'errors': [*errors, 'LIFECYCLE_WORK_SHAPE_INVALID']}
    for value in (work.get('verification'), work['sdd'].get('acceptance')):
        if value is not None and not isinstance(value, dict):
            return {'ok': False, 'eligible': False, 'work_id': work.get('id'),
                    'errors': [*errors, 'LIFECYCLE_WORK_SHAPE_INVALID']}

    from scripts.cao_defaults import SDD_CATALOG
    if (work.get('sdd') or {}).get('provider') not in SDD_CATALOG:
        errors.append('LIFECYCLE_PROVIDER_UNSUPPORTED')
    requirement = work.get('requirement') or {}
    if not isinstance(requirement.get('path'), str) or not isinstance(requirement.get('sha256'), str):
        errors.append('LIFECYCLE_REQUIREMENT_SHAPE_INVALID')
    old_run = ((work['sdd'].get('acceptance') or {}).get('run'))
    if old_run is not None and not isinstance(old_run, dict):
        return {'ok': False, 'eligible': False, 'work_id': work.get('id'), 'errors': [*errors, 'LIFECYCLE_WORK_SHAPE_INVALID']}
    running = ((work.get('verification') or {}).get('status') == 'running'
               or (((work.get('sdd') or {}).get('acceptance') or {}).get('run') or {}).get('status') == 'running')
    if running:
        errors.append('LIFECYCLE_RUN_ACTIVE')
    try:
        from scripts.path_guard import repo_file
        if isinstance(requirement.get('path'), str):
            repo_file(repo, requirement['path'])
        sources = source_inputs(repo, work)
    except (ValueError, OSError, RuntimeError) as exc:
        sources = None
        errors.append('LIFECYCLE_SOURCE_UNAVAILABLE: ' + str(exc))
    from scripts.lifecycle_contract import prerequisites
    try:
        inventory = prerequisites(repo, work)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        inventory = {'ready': False, 'reason': str(exc)}
    return {'ok': not errors, 'eligible': not errors, 'work_id': work['id'],
            'from_contract': version, 'to_contract': 2, 'errors': errors,
            'source_inputs': sources, 'native_inventory': inventory,
            'preserved': ['requirement', 'project_binding', 'sdd', 'gate_status', 'verification', 'decisions', 'resume'],
            'note': 'Missing native obligations are inventoried, not waived or marked passed by migration.'}


def seal(path, journal):
    journal['status'] = 'committed'
    atomic_json(path, journal)


def migrate(repo, work):
    from scripts.cao_runtime import now
    from scripts.cao_work_store import save_work, queue_event
    from scripts.lifecycle_contract import next_action
    path, journal = latest(repo, work['id'])
    if work.get('status') not in {'active', 'suspended'}:
        raise ValueError('ACTIVE_OR_SUSPENDED_WORK_REQUIRED')
    if work.get('lifecycle_contract') == 2:
        if journal and journal['status'] == 'prepared':
            if digest(semantic_work(work)) != journal['expected_after']:
                raise ValueError('LIFECYCLE_MIGRATION_RECOVERY_CONFLICT')
            seal(path, journal)
        return {'ok': True, 'status': 'unchanged', 'work_id': work['id']}
    report = preflight(repo, work)
    if not report['eligible']:
        return {**report, 'error': report['errors'][0]}
    if journal and journal['status'] == 'prepared':
        if (digest(semantic_work(work)) != journal['before_sha256']
                or report['source_inputs'] != journal['source_inputs']):
            raise ValueError('LIFECYCLE_MIGRATION_RECOVERY_CONFLICT')
        planned = deepcopy(journal['planned_work'])
        planned['state_revision'] = work.get('state_revision', 0)
    else:
        path = journal_root(repo, work['id']) / (uuid.uuid4().hex + '.json')
        planned = deepcopy(work)
        planned['lifecycle_contract'] = 2
        planned['integrity_version'] = 2
        planned['execution_generation'] = int(planned.get('execution_generation', 0)) + 1
        planned.setdefault('lifecycle_migrations', []).append({'from': 1, 'to': 2, 'at': now(),
             'journal': path.relative_to(repo).as_posix()})
        action = next_action(repo, planned)
        execution = planned.setdefault('execution', {})
        if execution.get('step') in {'review', 'verification'} and action.get('stage') == 'execution':
            execution.update(step='implementation', phase='implementation')
        execution['next_action'] = action
        queue_event(planned, {'event': 'cao.lifecycle.migrated', 'work_item_id': work['id'],
                              'from_contract': 1, 'to_contract': 2, 'journal': path.relative_to(repo).as_posix()})
        journal = {'version': 1, 'status': 'prepared', 'work_id': work['id'], 'created_at': now(),
                   'previous_work': deepcopy(work), 'before_sha256': digest(semantic_work(work)),
                   'planned_work': deepcopy(planned), 'expected_after': digest(semantic_work(planned)),
                   'source_inputs': report['source_inputs']}
        atomic_json(path, journal)  # The full checkpoint is durable BEFORE Work changes.
    save_work(repo, planned)
    if digest(semantic_work(planned)) != journal['expected_after']:
        raise ValueError('LIFECYCLE_MIGRATION_CHECKPOINT_MISMATCH')
    seal(path, journal)
    return {'ok': True, 'status': 'migrated', 'work_id': work['id'],
            'journal': path.relative_to(repo).as_posix(), 'next': next_action(repo, planned)}


def rollback(repo, work, check=False):
    from scripts.cao_work_store import save_work, queue_event
    path, journal = latest(repo, work['id'])
    if not journal:
        raise ValueError('LIFECYCLE_MIGRATION_CHECKPOINT_REQUIRED')
    if journal['status'] == 'rolled_back':
        return {'ok': True, 'status': 'unchanged', 'check_only': check}
    if journal['status'] == 'rollback_prepared' and digest(semantic_work(work)) == journal['rollback_sha256']:
        if not check:
            journal['status'] = 'rolled_back'
            atomic_json(path, journal)
        return {'ok': True, 'status': 'unchanged', 'check_only': check}
    errors = []
    if work.get('status') == 'completed':
        errors.append('LIFECYCLE_ROLLBACK_COMPLETED')
    if digest(semantic_work(work)) != journal['expected_after']:
        errors.append('LIFECYCLE_ROLLBACK_PROGRESS_CHANGED')
    if source_inputs(repo, work) != journal['source_inputs']:
        errors.append('LIFECYCLE_ROLLBACK_SOURCES_CHANGED')
    if digest(semantic_work(journal['previous_work'])) != journal['before_sha256']:
        errors.append('LIFECYCLE_ROLLBACK_CHECKPOINT_INVALID')
    result = {'ok': not errors, 'eligible': not errors, 'errors': errors,
              'work_id': work['id'], 'check_only': check, 'journal': path.relative_to(repo).as_posix()}
    if errors or check:
        return {**result, **({'error': errors[0]} if errors else {})}
    restored = deepcopy(journal['previous_work'])
    restored['state_revision'] = work.get('state_revision', 0)
    restored['execution_generation'] = int(work.get('execution_generation', 0)) + 1
    restored.setdefault('lifecycle_rollback_history', []).append(path.relative_to(repo).as_posix())
    queue_event(restored, {'event': 'cao.lifecycle.rollback', 'work_item_id': work['id'],
                           'journal': path.relative_to(repo).as_posix()})
    journal.update(status='rollback_prepared', rollback_sha256=digest(semantic_work(restored)))
    atomic_json(path, journal)
    save_work(repo, restored)
    journal['status'] = 'rolled_back'
    atomic_json(path, journal)
    return {**result, 'status': 'rolled_back'}


def command(args):
    from scripts.cao_runtime import repo_root, ensure_initialized, load_json
    from scripts.cao_work_store import work_path
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    from contextlib import nullcontext
    preview = args.lifecycle_command == 'recover' and args.check
    with nullcontext() if preview else repo_transaction(repo):
        # Recovery preview is advisory and lock-independent; mutation revalidates.
        # Avoid load_index/reconcile: --check/preflight must be read-only.
        wid = args.work or (load_json(repo / '.orchestrator/index.json', {}) or {}).get('active_work')
        if not wid:
            raise ValueError('NO_WORK_ITEM')
        if args.lifecycle_command == 'recover':
            result = recover(repo, wid, check=args.check)
            if preview:
                result.update(inspection_only=True, completion_certified=False)
        else:
            guard = read_guard(repo, wid)
            work = load_json(work_path(repo, wid), None)
            if not isinstance(work, dict) or work.get('id') != wid:
                raise ValueError('LIFECYCLE_WORK_SHAPE_INVALID')
            check = getattr(args, 'check', False) or args.lifecycle_command == 'preflight'
            if check:
                if guard and guard.get('status') != 'committed':
                    raise ValueError('LIFECYCLE_GUARD_RECOVERY_REQUIRED: complete the interrupted guarded write before read-only migration preflight')
                if guard and guard.get('protected') and (guard.get('status') != 'committed' or guard.get('sha256') != digest(work)):
                    raise ValueError('LIFECYCLE_UNSUPPORTED_WRITER: run cao lifecycle recover')
            else:
                verify(repo, wid, work)
            if args.lifecycle_command in {'preflight', 'migrate'}:
                result = preflight(repo, work) if check else migrate(repo, work)
            elif args.lifecycle_command == 'rollback':
                result = rollback(repo, work, check=check)
            elif args.lifecycle_command == 'protect':
                result = {'ok': True, 'status': protect(repo, work), 'work_id': wid}
            else:
                raise ValueError('LIFECYCLE_ACTION_UNSUPPORTED')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 2
