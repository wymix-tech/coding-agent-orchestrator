"""Materialize project-owned verification scripts as a separate pre-Work step."""
import os
import shlex
from scripts.cao_runtime import config, save_json
from scripts.project_context import load_context, digest
from scripts.completion_policy import normalize_scripts
from scripts.managed_verification import owned_files, prepare, safe_script


def script_paths(command, repo):
    from scripts.cao_tool_policy import _shell_segments
    try:
        directory = repo
        paths = []
        for tokens in _shell_segments(command):
            if tokens[0] == 'cd' and len(tokens) == 2:
                directory = (directory / tokens[1]).resolve()
                continue
            for operand in tokens:
                if '.orchestrator/scripts/verification/' not in operand:
                    continue
                target = (directory / operand).resolve()
                if target.is_relative_to(repo):
                    name = target.relative_to(repo).as_posix()
                    if name.startswith('.orchestrator/scripts/verification/'):
                        # Validate the original link topology before resolving it.
                        safe_script(repo, name)
                        paths.append(name)
        return paths
    except (ValueError, OSError):
        return []


def readiness(repo, scripts, scopes):
    owned = owned_files(repo)
    for entry in scripts:
        if not entry['required']:
            continue
        paths = script_paths(entry['command'], repo)
        if not paths or not all(p in owned and owned[p]['scope'] in {*scopes, 'root'}
                                and safe_script(repo, p).is_file() and safe_script(repo, p).stat().st_size
                                for p in paths):
            return {'ready': False, 'reason': 'PROJECT_VERIFICATION_SCRIPTS_REQUIRED',
                    'next': 'After scaffold checks, run `cao verification materialize` to persist project commands under .orchestrator/scripts/verification/, or prepare/write/configure exact project wrappers; then run `cao verification check --project`. Do not create Work until this separate baseline passes.'}
    return {'ready': True}


def materialize(repo, scope='root', *, update_review=True):
    from scripts.project_context import context_state
    from scripts.cao_work_store import load_index, load_work
    wid = load_index(repo).get('active_work')
    work = load_work(repo, wid) if wid else None
    state = context_state(repo, [scope], work=work, check_sources=True)
    if not state['ready']:
        raise ValueError(state['reason'])
    cfg = config(repo)
    scripts = normalize_scripts(cfg.get('verification', {}).get('scripts', []))
    from scripts.verification_probe import scoped_config
    selected = {s['name'] for s in scoped_config(repo, cfg, [scope])['verification']['scripts']}
    if not any(s['required'] for s in scripts):
        raise ValueError('VERIFICATION_BASELINE_REQUIRED: configure actual project checks first')
    from scripts.project_verification import validate_entries, verification_binding
    validate_entries(repo, [s for s in scripts if s['name'] in selected], [scope])
    files, changes = [], []
    for entry in scripts:
        if entry['name'] not in selected:
            continue
        paths = script_paths(entry['command'], repo)
        if paths:
            if not readiness(repo, [entry], [scope])['ready']:
                raise ValueError('PROJECT_VERIFICATION_SCRIPT_INVALID')
            continue
        # Module setup commands already contain their scope cwd; preserve them.
        name = '.orchestrator/scripts/verification/' + scope + '/check-' + digest(entry)[:20] + '.py'
        content = ('"""Project baseline command. Generated from accepted project verification entries."""\n'
                   'from pathlib import Path\nimport os\nimport shlex\nimport subprocess\nimport sys\n'
                   'os.chdir(Path(__file__).resolve().parents[4])\n'
                   'command = ' + repr(entry['command']) + '\n'
                   'if sys.argv[1:]:\n    command += " " + shlex.join(sys.argv[1:])\n'
                   'raise SystemExit(subprocess.call(command, shell=True))\n')
        target = safe_script(repo, name)
        if target.exists() and target.read_text() != content:
            raise ValueError('VERIFICATION_SCRIPT_FILE_CONFLICT: ' + name)
        files.append((name, content))
        changes.append((entry, shlex.join(['python' if os.name == 'nt' else 'python3', name])))
    # Validate every collision before writing any script or replacing declarations.
    if files:
        prepare(repo, [name for name, _ in files], scope)
        for name, content in files:
            import tempfile
            target = safe_script(repo, name)
            fd, temporary = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    for entry, command in changes:
        cfg['verification'].setdefault('materialized_commands', {})[script_paths(command, repo)[0]] = entry['command']
        entry['command'] = command
    cfg['verification']['scripts'] = scripts
    cfg['verification']['context_binding'] = verification_binding(repo, list(load_context(repo)['scopes']))
    save_json(repo / '.orchestrator/config.json', cfg)
    if changes and update_review:
        if work and work.get('status') == 'active' and (work.get('verification_review') or work.get('sdd', {}).get('native_route_required')):
            from scripts.cao_native_sync import ensure_verification_review
            ensure_verification_review(repo, work, cfg)
    return {'status': 'materialized' if changes else 'unchanged', 'files': [n for n, _ in files],
            'next': 'Inspect the project scripts, then run `cao verification check --project --scope ' + scope + '` before Work. Initialization alone cannot certify this baseline.'}
