"""Preparation checks may fail before baseline acceptance or native review.

They never update Work verification, gates, or final acceptance results.
"""
import json
import uuid

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
        work = resolve_work(repo, args.work)
        if not work or work.get('status') != 'active':
            raise ValueError('VERIFICATION_PROBE_ACTIVE_WORK_REQUIRED')
        cfg = config(repo)
        checks = normalize_scripts(cfg.get('verification', {}).get('scripts', []))
        if not checks:
            raise ValueError('VERIFICATION_PROBE_ENTRIES_REQUIRED')
        before = {'code': code_snapshot(repo), 'verification': verification_snapshot(cfg, repo)}
        run_id = 'probe-' + uuid.uuid4().hex
        path = safe_path(repo, work['id'], 'runs', run_id + '.json')
        record = {'id': run_id, 'purpose': 'baseline_preparation', 'work_item_id': work['id'],
                  'inputs': before, 'status': 'running'}
        atomic_json(path, record)
    results = []
    for check in checks:
        result = run_script(check['command'], repo, cfg['verification'].get('timeout_seconds', 300))
        from scripts.preparation_result import status as preparation_status
        state = preparation_status(result)
        discovered = result.get('test_discovery') or {}
        if 'tests' in check.get('gates', []) and (not discovered.get('observed') or discovered.get('maximum_count', 0) == 0):
            state = 'blocked'
            result['reason'] = 'VERIFICATION_PROBE_NONEMPTY_TESTS_REQUIRED'
        results.append({**result, 'name': check['name'], 'status': state})
    with repo_transaction(repo):
        try:
            after = {'code': code_snapshot(repo), 'verification': verification_snapshot(config(repo), repo)}
        except (ValueError, OSError, RuntimeError) as exc:
            after = None
            results.append({'status': 'stale', 'reason': str(exc)})
        status = ('stale' if after != before else 'error' if any(x['status'] == 'error' for x in results)
                  else 'failed' if any(x['status'] == 'failed' for x in results)
                  else 'blocked' if any(x['status'] == 'blocked' for x in results) else 'passed')
        record.update(status=status, results=results)
        atomic_json(path, record)
    print(json.dumps({'ok': status == 'passed', **record,
          'next': 'Review the actual preparation results; accept the applicable baseline, then continue the native workflow. This probe does not qualify final acceptance.'}, ensure_ascii=False, indent=2))
    return {'passed': 0, 'failed': 1, 'blocked': 2, 'stale': 2, 'error': 3}[status]
