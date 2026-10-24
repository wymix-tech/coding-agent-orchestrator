"""Run a configured review experiment in a disposable working copy.

This separates generated build files from product changes. It is not an OS
sandbox: configured commands retain their ordinary process permissions.
"""
import os
import shlex
import shutil
import tempfile
from pathlib import Path

from scripts.cao_runtime import config, verification_scripts
from scripts.cao_snapshot import code_snapshot, verification_timeout
from scripts.native_obligations import review_inputs
from scripts.review_diagnostics import invalid


def run_check(repo, work, layer, script_name, identity, reviewer_session=None):
    run = ((work.get('sdd') or {}).get('native_reviews') or {}).get(layer) or {}
    if not run.get('execution_binding'):
        invalid('NATIVE_REVIEW_EXECUTION_UNBOUND', 'reviewer', 'attach the observed reviewer before running a check')
    if reviewer_session:
        from scripts.review_binding import delegated_reviewer
        _, child = delegated_reviewer(repo, run, review_inputs(repo, work, code_snapshot), identity,
                                      reviewer_session, run.get('model'), run.get('id'), run.get('input_sha256'))
        identity = (child['host'], child['host_session_id'])
    binding = run.get('execution_binding') or {}
    reviewer = binding.get('reviewer') or {}
    from scripts.cao_sessions import load_session
    session = load_session(repo, identity[1], host=identity[0]) if identity else None
    if (not identity or identity[0] != binding.get('host') or identity[1] != reviewer.get('session_id')
            or not session or session.get('status') != 'active' or session.get('work_item_id') != work['id']
            or run.get('status') != 'running'):
        invalid('NATIVE_REVIEW_CHECK_SESSION_REQUIRED', 'session', 'the attached active reviewer')
    inputs = review_inputs(repo, work, code_snapshot)
    if run.get('inputs') != inputs:
        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH')
    cfg = config(repo)
    from scripts.cao_tool_policy import verification_baseline_state
    baseline = verification_baseline_state(repo, work)
    if not baseline['ready']:
        raise ValueError(baseline['reason'])
    scripts = [item for item in verification_scripts(cfg) if item['name'] == script_name]
    if len(scripts) != 1:
        invalid('NATIVE_REVIEW_CHECK_UNKNOWN', 'script', [item['name'] for item in verification_scripts(cfg)], script_name)
    tokens = shlex.split(scripts[0]['command'])
    if not tokens or any(any(char in token for char in '$`\n') or token in {';', '&&', '||', '|', '>', '>>', '<'} for token in tokens):
        raise ValueError('NATIVE_REVIEW_CHECK_ARGV_REQUIRED: configure an argv-compatible wrapper')
    with tempfile.TemporaryDirectory(prefix='cao-review-check-') as temp:
        root = Path(temp) / 'project'
        from scripts.snapshot_scope import excluded_input, git_inputs, path_key, validate_scope
        tracked = git_inputs(repo)
        scope = validate_scope(repo, cfg, tracked)
        tracked_keys = {path_key(name) for name in tracked}
        tracked_dirs = {path_key(parent.as_posix()) for name in tracked for parent in Path(name).parents}
        def ignore(directory, names):
            selected = []
            for name in names:
                candidate = Path(directory) / name
                relative = candidate.relative_to(repo).as_posix()
                if excluded_input(relative, scope, tracked_keys, tracked_dirs if candidate.is_dir() else ()):
                    selected.append(name)
                elif candidate.is_symlink():
                    raise ValueError('NATIVE_REVIEW_CHECK_SYMLINK: disposable checks require regular files')
            return selected
        shutil.copytree(repo, root, ignore=ignore)
        # Rebase exact repository-local paths, including absolute wrapper names.
        argv = []
        for token in tokens:
            path = Path(token)
            if path.is_absolute() and path.is_relative_to(repo):
                token = str(root / path.relative_to(repo))
            argv.append(token)
        environment = {key: value for key, value in os.environ.items() if not key.startswith(('CAO_', 'PI_'))}
        from scripts.verification_runner import run_script
        result = run_script(shlex.join(argv), root, timeout=verification_timeout(cfg),
                            max_bytes=24000, env=environment)
        out = {'ok': result['exit_code'] == 0, 'exit_code': result['exit_code'],
               'stdout': result['stdout_tail'], 'stderr': result['stderr_tail'],
               'timed_out': result['timed_out']}
    if inputs != review_inputs(repo, work, code_snapshot):
        raise ValueError('NATIVE_REVIEW_CHECK_SOURCE_CHANGED')
    return {**out, 'script': script_name, 'input_sha256': run['input_sha256'],
            'disposable_copy': True, 'gate_evidence': False}
