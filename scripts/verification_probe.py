"""Preparation checks may fail before baseline acceptance or native review.

They never update Work verification, gates, or final acceptance results.
"""
import json
import uuid
import os

from scripts.cao_runtime import repo_root, config, ensure_initialized
from scripts.cao_work_store import resolve_work
from scripts.cao_snapshot import code_snapshot, verification_snapshot
from scripts.completion_policy import normalize_scripts
from scripts.provider_restart import restart_state
from scripts.cao_providers import detect_current_host
from scripts.state_store import atomic_json, repo_transaction
from scripts.runtime_store import safe_path
from scripts.verification_runner import run_script


def command(args):
    repo = repo_root(args.repo)
    with repo_transaction(repo):
        ensure_initialized(repo)
        restart = restart_state(repo, detect_current_host())
        if not restart['ready']:
            raise ValueError(restart['reason'])
        if getattr(args, 'project', False) and args.work:
            raise ValueError('VERIFICATION_PROBE_SCOPE_CONFLICT')
        work = None if getattr(args, 'project', False) else resolve_work(repo, args.work)
        if work and work.get('status') != 'active':
            raise ValueError('VERIFICATION_PROBE_ACTIVE_WORK_REQUIRED')
        cfg = config(repo)
        from scripts.project_context import load_context, context_state, selected_scopes
        if not work and not load_context(repo):
            raise ValueError('PROJECT_CONTEXT_REQUIRED')
        scopes = selected_scopes(load_context(repo), getattr(args, 'scope', None)) if not work else None
        if not work:
            background = context_state(repo, scopes, check_sources=True)
            if not background['ready']:
                raise ValueError(background['reason'])
            cfg = scoped_config(repo, cfg, scopes)
        checks = [c for c in normalize_scripts(cfg.get('verification', {}).get('scripts', [])) if c['required']]
        if not work:
            from scripts.managed_verification import input_hashes
            if any(entry['sha256'] is None for entry in input_hashes(repo, scopes).values()):
                raise ValueError('VERIFICATION_MANAGED_INPUT_MISSING')
            context = load_context(repo)
            needs_tests = any(context['scopes'][scope]['technology'].get('test_framework', {}).get('status') != 'not_applicable' for scope in scopes)
            if needs_tests and not any('tests' in c.get('gates', []) for c in checks):
                raise ValueError('PROJECT_VERIFICATION_TEST_ENTRY_REQUIRED')
        if not checks:
            raise ValueError('VERIFICATION_PROBE_ENTRIES_REQUIRED')
        before = probe_inputs(repo, cfg, scopes) if not work else {'code': code_snapshot(repo), 'verification': verification_snapshot(cfg, repo)}
        run_id = 'probe-' + uuid.uuid4().hex
        path = safe_path(repo, work['id'], 'runs', run_id + '.json') if work else repo / '.orchestrator/project/verification-runs' / (run_id + '.json')
        if not work:
            for scope in scopes:
                current = read_project_result(repo, scope)
                running = current.get('running') or {}
                if running:
                    try:
                        os.kill(running['owner_pid'], 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise ValueError('PROJECT_VERIFICATION_CHECK_RUNNING')
        record = {'id': run_id, 'purpose': 'baseline_preparation', 'work_item_id': work['id'] if work else None,
                  'inputs': before, 'status': 'running'}
        if not work:
            record['owner'] = {'kind': 'project', 'scopes': scopes}
            record['purpose'] = 'initialization'
            for scope in scopes:
                atomic_json(project_result_path(repo, scope), {'running': {'id': run_id, 'owner_pid': os.getpid()}})
        atomic_json(path, record)
    results = []
    for check in checks:
        try:
            result = run_script(check['command'], repo, cfg['verification'].get('timeout_seconds', 300))
        except Exception as exc:
            results.append({'name': check['name'], 'status': 'error', 'reason': str(exc)})
            continue
        from scripts.preparation_result import status as preparation_status
        state = preparation_status(result)
        discovered = result.get('test_discovery') or {}
        if 'tests' in check.get('gates', []) and (not discovered.get('observed') or discovered.get('maximum_count', 0) == 0):
            state = 'blocked'
            result['reason'] = 'VERIFICATION_PROBE_NONEMPTY_TESTS_REQUIRED'
        results.append({**result, 'name': check['name'], 'status': state})
    with repo_transaction(repo):
        try:
            after = probe_inputs(repo, scoped_config(repo, config(repo), scopes), scopes) if not work else {'code': code_snapshot(repo), 'verification': verification_snapshot(config(repo), repo)}
        except (ValueError, OSError, RuntimeError) as exc:
            after = None
            results.append({'status': 'stale', 'reason': str(exc)})
        status = ('stale' if after != before else 'error' if any(x['status'] == 'error' for x in results)
                  else 'failed' if any(x['status'] == 'failed' for x in results)
                  else 'blocked' if any(x['status'] == 'blocked' for x in results) else 'passed')
        record.update(status=status, results=results)
        atomic_json(path, record)
        if not work:
            for scope in scopes:
                atomic_json(project_result_path(repo, scope), {'status': status, 'run_id': run_id,
                    'standard': project_standard(repo, scope) if after is not None else None})
    print(json.dumps({'ok': status == 'passed', **record,
          'next': 'Review the actual preparation results; accept the applicable baseline, then continue the native workflow. This probe does not qualify final acceptance.'}, ensure_ascii=False, indent=2))
    return {'passed': 0, 'failed': 1, 'blocked': 2, 'stale': 2, 'error': 3}[status]


def project_result_path(repo, scope):
    from scripts.project_setup import scope_path
    scope_path(repo, scope)  # shared validated scope grammar and link protection
    return repo / '.orchestrator/project/verification-checks' / (scope + '.json')


def read_project_result(repo, scope):
    from scripts.project_context import read
    return read(project_result_path(repo, scope)) or {}


def scoped_config(repo, cfg, scopes):
    from copy import deepcopy
    from scripts.managed_verification import owned_files
    selected = deepcopy(cfg)
    owned = owned_files(repo)
    def applicable(check):
        name = check['name']
        if name.startswith('setup-'):
            return any(name.startswith('setup-' + scope + '-') for scope in {*scopes, 'root'})
        bound = {entry['scope'] for path, entry in owned.items() if path in check['command']}
        return not bound or bool(bound & {*scopes, 'root'})
    selected['verification']['scripts'] = [c for c in normalize_scripts(cfg.get('verification', {}).get('scripts', [])) if applicable(c)]
    return selected


def project_standard(repo, scope):
    from scripts.project_context import fingerprint, load_context
    return {'context': fingerprint(load_context(repo), [scope]),
            'verification': verification_snapshot(scoped_config(repo, config(repo), [scope]), repo, managed_scopes=[scope])}


def probe_inputs(repo, cfg, scopes):
    from scripts.project_context import load_context
    roots = [base for scope in scopes for base in load_context(repo)['scopes'][scope]['paths']]
    return {'code': code_snapshot(repo, include_roots=roots),
            'standards': {scope: project_standard(repo, scope) for scope in scopes}}


def project_state(repo, scopes=None):
    from scripts.project_context import load_context, selected_scopes
    from scripts.project_setup import state as setup_state
    for scope in selected_scopes(load_context(repo), scopes):
        if setup_state(repo, scope).get('ready'):
            continue
        record = read_project_result(repo, scope)
        if record.get('status') != 'passed' or record.get('standard') != project_standard(repo, scope):
            return {'ready': False, 'reason': 'PROJECT_VERIFICATION_CHECK_REQUIRED',
                    'next': 'Run `cao verification check --scope ' + scope + '`; inspect failures and repair project verification before creating Work.',
                    'scope': scope, 'last_status': record.get('status')}
    return {'ready': True}
