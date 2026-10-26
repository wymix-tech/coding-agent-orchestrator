"""Exact scope-owned verification inputs; other runtime state stays protected."""
import json
from pathlib import Path
from scripts.project_context import fingerprint, load_context, relative, selected_scopes, read
from scripts.state_store import atomic_json, repo_transaction

ROOT = '.orchestrator/scripts/verification'
REGISTRY = '.orchestrator/project/verification-files.json'


def safe_script(repo, name, *, legacy=False):
    name = relative(repo, name)
    path = repo / name
    for index, component in enumerate(Path(name).parts):
        if component.endswith(('.', ' ')) or component.casefold().split('.')[0] in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}:
            raise ValueError('VERIFICATION_SCRIPT_RESERVED_PATH: ' + name)
        parent = repo.joinpath(*Path(name).parts[:index])
        if parent.is_dir() and any(child.name.casefold() == component.casefold() and child.name != component for child in parent.iterdir()):
            raise ValueError('VERIFICATION_SCRIPT_CASE_COLLISION: ' + name)
    root = 'scripts/verification' if legacy else ROOT
    if not (name.startswith(root + '/') or legacy and ('/' + root + '/') in name):
        raise ValueError('VERIFICATION_SCRIPT_PATH_INVALID: ' + name)
    for part in (path, *path.parents):
        if part == repo:
            break
        if part.is_symlink():
            raise ValueError('VERIFICATION_SCRIPT_SYMLINK: ' + name)
    if path.exists() and not path.is_file():
        raise ValueError('VERIFICATION_SCRIPT_NOT_FILE: ' + name)
    return path


def registry(repo):
    return read(repo / REGISTRY) or {'files': {}}


def owned_files(repo, *, include_planned=False):
    allowed = dict(registry(repo)['files'])
    for scope in (load_context(repo) or {}).get('scopes', {}):
        record = read(repo / '.orchestrator/project/setup' / (scope + '.json'))
        if record and (record.get('applied') or include_planned):
            plan = record['plan']
            for entry in plan['files']:
                if entry['purpose'] == 'verification':
                    allowed.setdefault(entry['path'], {'scope': scope, 'context_sha256': plan['context_sha256']})
    return allowed


def prepare(repo, names, scope):
    context = load_context(repo)
    if not context:
        raise ValueError('PROJECT_CONTEXT_REQUIRED')
    selected_scopes(context, [scope])
    from scripts.project_context import context_state
    background = context_state(repo, [scope], check_sources=True)
    if not background['ready']:
        raise ValueError(background['reason'])
    record = registry(repo)
    for name in names:
        name = safe_script(repo, name).relative_to(repo).as_posix()
        old = owned_files(repo, include_planned=True).get(name)
        if old and old['scope'] != scope:
            raise ValueError('VERIFICATION_SCRIPT_SCOPE_CONFLICT: ' + name)
        record['files'][name] = {'scope': scope, 'context_sha256': fingerprint(context, [scope])}
    ensure_trackable(repo)
    for name in names:
        safe_script(repo, name).parent.mkdir(parents=True, exist_ok=True)
    atomic_json(repo / REGISTRY, record)
    return {'files': [relative(repo, name) for name in names], 'scope': scope, 'next': 'Write these exact inputs, configure actual commands, then cao verification check before business Work.'}


def writable(repo, raw):
    from scripts.project_setup import write_targets
    targets = write_targets(raw)
    from scripts.cao_tool_policy import SHELL_TOOLS, _event_key, _collect_values_for_keys, _shell_segments, tool_effect
    if _event_key(raw, 'tool_name', 'toolname', 'tool', 'name').lower() in SHELL_TOOLS:
        try:
            commands = _collect_values_for_keys(raw, {'command', 'cmd'})
            if not commands:
                return False
            for command in commands:
                for tokens in _shell_segments(command):
                    if len(tokens) < 3 or tokens[0] != 'chmod' or tokens[1] not in {'+x', 'u+x', 'a+x', 'ug+x', '700', '750', '755'}:
                        return False
                    if any(token.startswith('-') for token in tokens[2:]):
                        return False
            analysis = tool_effect(raw, repo)
            if analysis['diagnostics']:
                return False
            targets = analysis['targets']
            if not all((repo / value).is_file() for value in targets):
                return False
        except (ValueError, OSError, RuntimeError):
            return False
    context = load_context(repo)
    if not targets or not context:
        return False
    allowed = owned_files(repo)
    for value in targets:
        path = Path(value)
        path = path if path.is_absolute() else repo / path
        try:
            name = path.relative_to(repo).as_posix()
            safe_script(repo, name)
            entry = allowed.get(name)
            if not entry or entry['context_sha256'] != fingerprint(context, [entry['scope']]):
                return False
        except (ValueError, OSError, RuntimeError):
            return False
    return True


def input_hashes(repo, scopes=None):
    """Include sourced helpers, execution bits, missing files and link topology."""
    import os
    from scripts.project_setup import file_hash
    allowed = owned_files(repo)
    names = set(allowed)
    root = repo / ROOT
    if root.exists() or root.is_symlink():
        safe_script(repo, ROOT + '/.guard')
        visited = 0
        for base, dirs, files in os.walk(root, followlinks=False):
            visited += len(dirs) + len(files)
            if visited > 100000 or len(Path(base).relative_to(root).parts) > 100:
                raise ValueError('VERIFICATION_SCRIPT_SCAN_LIMIT')
            for name in dirs + files:
                if (Path(base) / name).is_symlink():
                    raise ValueError('VERIFICATION_SCRIPT_SYMLINK: ' + str(Path(base) / name))
            from scripts.cao_defaults import SNAPSHOT_CACHE_DIRS
            dirs[:] = [name for name in dirs if name not in SNAPSHOT_CACHE_DIRS]
            names.update((Path(base) / name).relative_to(repo).as_posix() for name in files if not name.endswith(('.pyc', '.pyo')))
    hashes = {}
    for name in sorted(names):
        if scopes is not None and name in allowed and allowed[name]['scope'] not in {*scopes, 'root'}:
            continue
        path = safe_script(repo, name, legacy=not name.startswith(ROOT + '/'))
        hashes[name] = {'sha256': file_hash(path), 'executable': path.stat().st_mode & 0o111 if path.is_file() else None}
    return hashes


def migrate(repo):
    """Migrate only setup-owned wrappers. Retain original files for rollback."""
    import re
    import shutil
    from scripts.cao_runtime import config, save_json
    record_registry = registry(repo)
    records, moves = {}, dict(record_registry.get('migration', {}))
    for scope in (load_context(repo) or {}).get('scopes', {}):
        path = repo / '.orchestrator/project/setup' / (scope + '.json')
        record = read(path)
        if not record:
            continue
        if record.get('running'):
            raise ValueError('PROJECT_SETUP_CHECK_RUNNING')
        records[path] = record
        for entry in record['plan']['files']:
            if entry['purpose'] == 'verification' and ('scripts/verification/' in entry['path']):
                old = entry['path']
                new = ROOT + '/' + scope + '/' + old.split('scripts/verification/', 1)[1]
                source, target = safe_script(repo, old, legacy=True), safe_script(repo, new)
                if not source.is_file():
                    raise ValueError('VERIFICATION_MIGRATION_SOURCE_MISSING: ' + old)
                moves[old] = new
    def rewrite(value):
        if isinstance(value, str):
            for old, new in moves.items():
                # Accept ordinary shell/quoted references, including module cwd.
                value = re.sub(r'(?<![\w.-])' + re.escape(str(repo / old)) + r'(?![\w./-])', lambda _: str(repo / new), value)
                value = re.sub(r'(?<![\w./-])' + re.escape(old) + r'(?![\w./-])', lambda _: new, value)
            return value
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        return value
    contents = {}
    for old, new in moves.items():
        source, target = safe_script(repo, old, legacy=True), safe_script(repo, new)
        if not source.is_file():
            raise ValueError('VERIFICATION_MIGRATION_SOURCE_MISSING: ' + old)
        contents[new] = rewrite(source.read_text()).encode()
        if target.exists() and target.read_bytes() != contents[new]:
            raise ValueError('VERIFICATION_MIGRATION_CONFLICT: ' + new)
    ensure_trackable(repo)
    # Journal before mutations so interruption between plan/config updates is retryable.
    record_registry['migration'] = moves
    atomic_json(repo / REGISTRY, record_registry)
    for old, new in moves.items():
        target = repo / new
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with target.open('xb') as stream:
                stream.write(contents[new])
            shutil.copymode(repo / old, target)
    cfg = config(repo)
    cfg['verification']['scripts'] = rewrite(cfg['verification'].get('scripts', []))
    for path, record in records.items():
        import hashlib
        for entry in record['plan']['files']:
            entry['path'] = moves.get(entry['path'], entry['path'])
            if entry['path'] in contents:
                entry['content'] = contents[entry['path']].decode()
                entry['sha256'] = hashlib.sha256(contents[entry['path']]).hexdigest()
        import os
        import shlex
        for check in record['plan']['checks']:
            command = check['command']
            cwd = check['cwd']
            for old, new in moves.items():
                old_relative = os.path.relpath(repo / old, repo / cwd)
                new_relative = os.path.relpath(repo / new, repo / cwd)
                command = re.sub(r'(?<![\w./-])' + re.escape(old_relative) + r'(?![\w./-])', lambda _: new_relative, command)
            check['command'] = command
            configured_name = 'setup-' + record['plan']['scope'] + '-' + check['name']
            for script in cfg['verification']['scripts']:
                if script['name'] == configured_name:
                    script['command'] = command if cwd == '.' else 'cd ' + shlex.quote(cwd) + ' && ' + command
        from scripts.project_context import digest
        plan = record['plan']
        plan.pop('id', None)
        plan['id'] = 'setup-' + digest(plan)
        atomic_json(path, record)
        # Keep prepared declarations consistent; do not reapply stale old paths.
        from scripts.project_setup import input_path
        prepared = input_path(repo, plan['scope'])
        original = read(prepared)
        if original:
            original = rewrite(original)
            atomic_json(prepared, original)
    save_json(repo / '.orchestrator/config.json', cfg)
    if moves:
        from scripts.cao_work_store import load_index, load_work
        from scripts.cao_snapshot import verification_snapshot
        from scripts.cao_provider_workflows import provider_native_skills
        wid = load_index(repo).get('active_work')
        work = load_work(repo, wid) if wid else None
        if work and (work.get('acceptance') or {}).get('config_sha256') != verification_snapshot(cfg, repo):
            if work['sdd'].get('native_route_required') or any(s['name'] == 'bmad-build' for s in provider_native_skills(repo, work['sdd']['provider'])):
                from scripts.cao_native_sync import ensure_verification_review
                ensure_verification_review(repo, work, cfg)
    return {'migrated': moves, 'retained_old_files': list(moves), 'next': 'Review changed baseline, reanchor applicable active Work and rerun checks; originals retained for rollback.'}


def command(args):
    from scripts.cao_runtime import ensure_initialized, repo_root
    from scripts.provider_restart import restart_state
    from scripts.cao_providers import detect_current_host
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    restart = restart_state(repo, detect_current_host())
    if not restart['ready']:
        raise ValueError(restart['reason'])
    with repo_transaction(repo):
        out = prepare(repo, args.path, args.scope) if args.verification_command == 'prepare' else migrate(repo)
    print(json.dumps({'ok': True, **out}, ensure_ascii=False))
    return 0


def register(sub):
    for name in ('prepare', 'migrate'):
        parser = sub.add_parser(name, help='Reserve exact verification inputs' if name == 'prepare' else 'Migrate proven setup-owned wrappers; retain originals')
        parser.add_argument('--repo', default='.')
        if name == 'prepare':
            parser.add_argument('--scope', default='root')
            parser.add_argument('--path', action='append', required=True)
        parser.set_defaults(fn=command)


def ensure_trackable(repo):
    """Keep only the scripts subtree trackable; derived state stays ignored."""
    path = repo / '.gitignore'
    if path.is_symlink():
        raise ValueError('VERIFICATION_GITIGNORE_SYMLINK')
    text = path.read_text() if path.exists() else ''
    marker = '# CAO managed verification inputs'
    if marker not in text:
        retained = [line for line in text.splitlines() if line.startswith('!.orchestrator/') and line != '!.orchestrator/']
        path.write_text(text.rstrip() + '\n\n' + marker + '\n!.orchestrator/\n.orchestrator/*\n!.orchestrator/config.json\n!.orchestrator/scripts/\n.orchestrator/scripts/*\n!.orchestrator/scripts/verification/\n' + '\n'.join(retained) + ('\n' if retained else ''))
