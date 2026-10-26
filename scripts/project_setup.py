"""Scope-owned plan/apply/probe lifecycle. No synthetic business Work required.

The existing transaction, snapshot and process runner are reused. A recorded
command is trusted project code, not an OS sandbox. No blanket src/ exemption.
"""
import argparse
import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from pathlib import Path

from scripts.cao_runtime import config, ensure_initialized, repo_root, save_json
from scripts.project_context import ROOT, context_state, digest, fingerprint, load_context, read, relative, selected_scopes
from scripts.project_sources import user_evidence
from scripts.state_store import atomic_json, repo_transaction
from scripts.verification_runner import run_script

PURPOSES = {'manifest', 'entry', 'test', 'resource', 'verification'}
CAPABILITIES = {'build', 'syntax', 'type', 'load', 'startup', 'test', 'security'}


def scope_path(repo, scope):
    if not isinstance(scope, str) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', scope) or scope in {'.', '..'}:
        raise ValueError('PROJECT_SCOPE_INVALID')
    path = repo / ROOT / 'setup' / (scope + '.json')
    for part in (path, *path.parents):
        if part == repo:
            break
        if part.is_symlink():
            raise ValueError('PROJECT_SETUP_SYMLINK')
    return path


def input_path(repo, scope):
    scope_path(repo, scope)
    return repo / '.orchestrator/inputs' / ('project-' + scope) / 'setup.json'


def safe_product(repo, name, bases):
    if not isinstance(name, str) or not name or name != name.strip():
        raise ValueError('PROJECT_SCOPE_INVALID')
    name = relative(repo, name)
    path = repo / name
    parent = repo
    for component in Path(name).parts:
        if parent.is_dir() and any(child.name.casefold() == component.casefold() and child.name != component
                                   for child in parent.iterdir()):
            raise ValueError('PROJECT_SETUP_CASE_COLLISION: ' + name)
        parent = parent / component
    if any(part in {'.orchestrator', '.git', '.agents', '.pi', '_bmad', '_bmad-output', 'openspec', '.specify', 'specs'} for part in Path(name).parts):
        raise ValueError('PROJECT_SETUP_PROTECTED_PATH: ' + name)
    if not any(path.is_relative_to(repo / base) for base in bases):
        raise ValueError('PROJECT_SETUP_SCOPE_ESCAPE: ' + name)
    for part in (path, *path.parents):
        if part == repo:
            break
        if part.is_symlink():
            raise ValueError('PROJECT_SETUP_SYMLINK: ' + name)
    if any(part.endswith(('.', ' ')) for part in Path(name).parts):
        raise ValueError('PROJECT_SETUP_RESERVED_PATH: ' + name)
    if any(part.casefold().split('.')[0] in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))} for part in Path(name).parts):
        raise ValueError('PROJECT_SETUP_RESERVED_PATH: ' + name)
    return path


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def normalize_plan(repo, scope, raw):
    context = load_context(repo)
    state = context_state(repo, [scope])
    if not state['ready']:
        raise ValueError(state['reason'])
    if not isinstance(raw, dict) or type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise ValueError('PROJECT_SETUP_PLAN_INVALID')
    identity = raw.get('identity')
    if not isinstance(identity, dict) or not identity or any(not isinstance(v, str) or not v.strip() for v in identity.values()):
        raise ValueError('PROJECT_IDENTITY_REQUIRED')
    source = raw.get('source', 'user-specified')
    if source not in ('user-specified', 'user-delegated'):
        raise ValueError('PROJECT_SETUP_SOURCE_REQUIRED')
    from scripts.setup_identity import resolve
    evidence_records, identity_sources = resolve(repo, scope, identity, raw, context)
    evidence = evidence_records[0]
    rule = raw.get('rule') or {'id': 'explicit', 'version': 1}
    if not isinstance(rule, dict) or type(rule.get('version')) is not int or not isinstance(rule.get('id'), str):
        raise ValueError('PROJECT_SETUP_RULE_INVALID')
    if rule.get('id') != 'explicit':
        from scripts.setup_rules import validate_identity
        spec = validate_identity(rule['id'], identity)
        if rule['version'] != spec['version']:
            raise ValueError('PROJECT_SETUP_RULE_INVALID')
        from scripts.project_sources import compact
        actual = context['scopes'][scope]['technology']['language']['value']
        if compact(actual) != compact(spec['language']):
            raise ValueError('PROJECT_SETUP_RULE_CONTEXT_MISMATCH')
        technology = context['scopes'][scope]['technology']
        for field, allowed in spec.get('technology', {}).items():
            accepted = technology.get(field, {})
            if accepted.get('status') == 'accepted' and compact(accepted['value']) not in {compact(v) for v in allowed}:
                raise ValueError('PROJECT_SETUP_RULE_CONTEXT_MISMATCH: ' + field + '; adapt an explicit plan to the confirmed runner')
    elif not isinstance(raw.get('basis'), str) or not raw['basis'].strip():
        raise ValueError('PROJECT_SETUP_EXPLICIT_BASIS_REQUIRED')
    elif rule['version'] != 1:
        raise ValueError('PROJECT_SETUP_RULE_INVALID')
    framework = context['scopes'][scope]['technology'].get('framework', {})
    if framework.get('status') == 'accepted' and raw.get('framework_support') != framework.get('value'):
        raise ValueError('PROJECT_SETUP_FRAMEWORK_PLAN_REQUIRED: extend the draft with the confirmed framework entry and real startup/load check; record framework_support, or use an explicit plan')
    delivery = context['scopes'][scope]['technology'].get('delivery', {})
    if delivery.get('status') == 'accepted' and raw.get('delivery_support') != delivery.get('value'):
        raise ValueError('PROJECT_SETUP_DELIVERY_PLAN_REQUIRED: adapt the scaffold/checks to the accepted delivery shape and record delivery_support')
    bases = context['scopes'][scope]['paths']
    files, seen, spellings = [], set(), {}
    if not isinstance(raw.get('files'), list) or not raw['files']:
        raise ValueError('PROJECT_SETUP_FILES_REQUIRED')
    for entry in raw['files']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('purpose'), str)
                or entry['purpose'] not in PURPOSES or not isinstance(entry.get('content'), str)):
            raise ValueError('PROJECT_SETUP_FILE_INVALID')
        path = safe_product(repo, entry.get('path'), bases)
        name = path.relative_to(repo).as_posix()
        key = name.casefold()
        for parent in [*Path(name).parents, Path(name)]:
            spelling = parent.as_posix()
            if spelling.casefold() in spellings and spellings[spelling.casefold()] != spelling:
                raise ValueError('PROJECT_SETUP_FILE_COLLISION: ' + name)
            spellings[spelling.casefold()] = spelling
        if key in seen or len(entry['content'].encode()) > 1024 * 1024:
            raise ValueError('PROJECT_SETUP_FILE_COLLISION: ' + name)
        seen.add(key)
        if path.parent.exists() and any(p.name.casefold() == path.name.casefold() and p.name != path.name for p in path.parent.iterdir()):
            raise ValueError('PROJECT_SETUP_FILE_COLLISION: ' + name)
        expected = hashlib.sha256(entry['content'].encode()).hexdigest()
        if path.exists() and file_hash(path) != expected:
            raise ValueError('PROJECT_SETUP_FILE_CONFLICT: ' + name)
        files.append({**entry, 'path': name, 'sha256': expected})
    if not isinstance(raw.get('checks'), list) or not isinstance(raw.get('required_capabilities'), list):
        raise ValueError('PROJECT_SETUP_CHECK_INVALID')
    checks, names = [], set()
    for check in raw.get('checks', []):
        if (not isinstance(check, dict) or not isinstance(check.get('capability'), str)
                or check['capability'] not in CAPABILITIES):
            raise ValueError('PROJECT_SETUP_CHECK_INVALID')
        if (not isinstance(check.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', check['name']) or check['name'] in names):
            raise ValueError('PROJECT_SETUP_CHECK_INVALID')
        names.add(check['name'])
        if not isinstance(check.get('command'), str) or not check['command'].strip():
            raise ValueError('PROJECT_SETUP_CHECK_INVALID')
        from scripts.completion_policy import normalize_timeout
        cwd = relative(repo, check.get('cwd', bases[0]))
        if cwd not in bases:
            raise ValueError('PROJECT_SETUP_CHECK_SCOPE_ESCAPE')
        checks.append({**check, 'cwd': cwd, 'timeout_seconds': normalize_timeout(check.get('timeout_seconds', 300))})
    if any(not isinstance(c, str) or c not in CAPABILITIES for c in raw['required_capabilities']):
        raise ValueError('PROJECT_SETUP_CAPABILITIES_INVALID')
    capabilities = set(raw['required_capabilities'])
    if not checks or not capabilities or not capabilities <= {c['capability'] for c in checks}:
        raise ValueError('PROJECT_SETUP_CAPABILITIES_INCOMPLETE')
    if rule['id'] != 'explicit' and not set(spec['capabilities']) <= capabilities:
        raise ValueError('PROJECT_SETUP_CAPABILITIES_INCOMPLETE: the selected rule requires ' + ', '.join(spec['capabilities']))
    if 'test' not in capabilities:
        # Non-runtime artifacts may explicitly declare a different applicable contract.
        if raw.get('delivery_kind') != 'non-runtime' or not raw.get('no_tests_reason'):
            raise ValueError('PROJECT_SETUP_REAL_TEST_REQUIRED')
        if context['scopes'][scope]['technology'].get('test_framework', {}).get('status') != 'not_applicable':
            raise ValueError('PROJECT_SETUP_TEST_APPLICABILITY_CONFLICT: preserve the confirmed runtime test contract')
        user_evidence(repo, raw.get('evidence'), {'delivery': 'non-runtime'}, source == 'user-delegated')
    from scripts.snapshot_scope import paths
    outputs = paths(raw.get('outputs', []), 'setup.outputs')
    for name in outputs:
        safe_product(repo, name.rstrip('/'), bases)
        if any(f['path'] == name.rstrip('/') or f['path'].startswith(name.rstrip('/') + '/') for f in files):
            raise ValueError('PROJECT_SETUP_OUTPUT_OVERLAPS_INPUT')
    generated = raw.get('generated_output_suffixes', {})
    from scripts.snapshot_scope import scope_config, validate_scope
    scope_config({'verification': {'outputs': outputs, 'generated_output_suffixes': generated}})
    initial = deepcopy(config(repo))
    initial.setdefault('verification', {})['outputs'] = sorted(set(initial.get('verification', {}).get('outputs', []) + outputs))
    validate_scope(repo, initial)
    plan = {'schema_version': 1, 'scope': scope, 'identity': identity, 'evidence': evidence,
            'context_sha256': fingerprint(context, [scope]), 'bases': bases, 'rule': rule,
            'basis': raw.get('basis'), 'files': files, 'checks': checks,
            'required_capabilities': sorted(capabilities), 'outputs': outputs,
            'identity_sources': identity_sources, 'identity_evidence': evidence_records,
            **({'generated_output_suffixes': generated} if generated else {})}
    plan['id'] = 'setup-' + digest(plan)
    return plan


def baseline_snapshot(repo, plan):
    from scripts.cao_snapshot import verification_snapshot
    cfg = deepcopy(config(repo))
    verification = cfg.get('verification', {})
    prefix = 'setup-' + plan['scope'] + '-'
    verification['scripts'] = [c for c in verification.get('scripts', []) if c['name'].startswith(prefix) or not c['name'].startswith('setup-')]
    def selected(path):
        return any(base == '.' or path.rstrip('/') == base or path.startswith(base + '/') for base in plan['bases'])
    verification['outputs'] = [p for p in verification.get('outputs', []) if selected(p)]
    if 'generated_output_suffixes' in verification:
        verification['generated_output_suffixes'] = {p: s for p, s in verification['generated_output_suffixes'].items() if selected(p)}
    cfg.setdefault('snapshot', {})['exclude_paths'] = [p for p in cfg.get('snapshot', {}).get('exclude_paths', []) if selected(p)]
    return verification_snapshot(cfg, repo)


def current_inputs(repo, plan):
    from scripts.cao_snapshot import code_snapshot
    return {'verification_sha256': baseline_snapshot(repo, plan),
            'context_sha256': fingerprint(load_context(repo), [plan['scope']]),
            'plan_id': plan['id'], 'code_snapshot': code_snapshot(repo, include_roots=plan['bases']),
            'files': {f['path']: file_hash(safe_product(repo, f['path'], plan['bases'])) for f in plan['files']}}


def state(repo, scope='root'):
    background = context_state(repo, [scope])
    if not background['ready']:
        return {**background, 'stage': 'context_required'}
    record = read(scope_path(repo, scope))
    if not record:
        return {'ready': False, 'stage': 'identity_required', 'reason': 'PROJECT_SETUP_REQUIRED',
                'next': 'Run `cao project setup rules`; confirm applicable identity/layout, then draft/plan/apply/check.'}
    plan = record['plan']
    if plan['rule']['id'] != 'explicit':
        from scripts.setup_rules import describe
        try:
            current_rule = describe(plan['rule']['id'])
        except ValueError:
            return {'ready': False, 'stage': 'identity_required', 'reason': 'PROJECT_SETUP_RULE_UNAVAILABLE',
                    'next': 'Restore the recorded adapter or review an explicit replacement plan; do not reuse its old result silently.'}
        if current_rule['version'] != plan['rule']['version']:
            return {'ready': False, 'stage': 'identity_required', 'reason': 'PROJECT_SETUP_RULE_STALE',
                    'next': 'Review the current adapter rule/version and create an updated plan; preserve existing files.'}
    if fingerprint(load_context(repo), [scope]) != plan['context_sha256']:
        return {'ready': False, 'stage': 'context_required', 'reason': 'PROJECT_SETUP_CONTEXT_STALE',
                'next': 'Review changed project context and create an updated setup plan; keep current files.'}
    applied = all(file_hash(safe_product(repo, f['path'], plan['bases'])) == f['sha256'] for f in plan['files'])
    # After successful setup, later business implementation may extend entry files.
    # Toolchain/context/baseline changes, not ordinary product changes, invalidate setup readiness.
    result = record.get('result') or {}
    if result.get('status') == 'passed':
        from scripts.cao_snapshot import verification_snapshot
        if result.get('verification_sha256') == baseline_snapshot(repo, plan):
            return {'ready': True, 'stage': 'ready', 'plan_id': plan['id'], 'result': result}
        return {'ready': False, 'stage': 'check_pending', 'reason': 'PROJECT_SETUP_BASELINE_STALE',
                'next': 'Review changed baseline and run `cao project setup check --scope ' + scope + '`.'}
    stage = 'check_pending' if record.get('applied') or applied else 'scaffold_pending'
    return {'ready': False, 'stage': stage, 'reason': 'PROJECT_SETUP_REQUIRED', 'plan_id': plan['id'],
            'result': result, 'next': 'Run `cao project setup ' + ('check' if applied else 'apply') + ' --scope ' + scope + '`.'}


def prerequisite(repo, work=None, scopes=None):
    # Existing active/historical Work never gets rolled back into empty-project setup.
    if work:
        return None
    context = load_context(repo)
    if not context:
        return None
    from scripts.project_inspection import inspect
    for scope in selected_scopes(context, scopes):
        record = read(scope_path(repo, scope))
        observed = inspect(repo, {scope: context['scopes'][scope]})
        existing_nonruntime = context['scopes'][scope]['technology'].get('language', {}).get('status') == 'not_applicable'
        if not record and (observed['observations'] or observed['product_paths'] or existing_nonruntime):
            continue
        result = state(repo, scope)
        if not result['ready']:
            return {'type': 'agent', 'stage': 'project_setup', 'reason': result['reason'],
                    'instruction': result['next'], 'project_state': result, 'scope': scope}
    return None


def apply(repo, scope):
    with repo_transaction(repo):
        path = scope_path(repo, scope)
        record = read(path)
        if not record:
            raise ValueError('PROJECT_SETUP_PLAN_REQUIRED')
        if record.get('running'):
            raise ValueError('PROJECT_SETUP_CHECK_RUNNING')
        plan = record['plan']
        if fingerprint(load_context(repo), [scope]) != plan['context_sha256']:
            raise ValueError('PROJECT_SETUP_CONTEXT_STALE')
        from scripts.snapshot_scope import validate_scope
        initial = deepcopy(config(repo))
        initial.setdefault('verification', {})['outputs'] = sorted(set(initial.get('verification', {}).get('outputs', []) + plan['outputs']))
        validate_scope(repo, initial)
        for f in plan['files']:
            target = safe_product(repo, f['path'], plan['bases'])
            if target.exists() and file_hash(target) != f['sha256']:
                raise ValueError('PROJECT_SETUP_FILE_CONFLICT: ' + f['path'])
        for f in plan['files']:
            target = safe_product(repo, f['path'], plan['bases'])
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open('x', encoding='utf-8', newline='') as output:
                    output.write(f['content'])
            except FileExistsError:
                if file_hash(target) != f['sha256']:
                    raise ValueError('PROJECT_SETUP_FILE_CONFLICT: ' + f['path'])
        cfg = config(repo)
        from scripts.project_verification import verification_binding
        from scripts.completion_policy import normalize_scripts
        verification = cfg.setdefault('verification', {})
        prefix = 'setup-' + scope + '-'
        scripts = [s for s in verification.get('scripts', []) if not s['name'].startswith(prefix)]
        for c in plan['checks']:
            import shlex
            command = c['command'] if c['cwd'] == '.' else 'cd ' + shlex.quote(c['cwd']) + ' && ' + c['command']
            scripts.append({'name': prefix + c['name'], 'command': command, 'required': True,
                            'gates': ['tests'] if c['capability'] == 'test' else ['security_verification'] if c['capability'] == 'security' else []})
        from scripts.project_verification import validate_entries
        validate_entries(repo, scripts[-len(plan['checks']):], [scope])
        verification['scripts'] = normalize_scripts(scripts)
        verification['context_binding'] = verification_binding(repo, list(load_context(repo)['scopes']))
        verification['generated_output_suffixes'] = {**verification.get('generated_output_suffixes', {}), **plan.get('generated_output_suffixes', {})}
        verification['outputs'] = sorted(set(verification.get('outputs', []) + plan['outputs']))
        verification.pop('plan', None)
        from scripts.snapshot_scope import validate_scope
        validate_scope(repo, cfg)
        save_json(repo / '.orchestrator/config.json', cfg)
        record['applied'] = True
        atomic_json(path, record)
        return {'status': 'applied', 'plan_id': plan['id'], 'next': 'cao project setup check --scope ' + scope}


def check(repo, scope, recover_run=None):
    with repo_transaction(repo):
        path = scope_path(repo, scope)
        record = read(path)
        if not record or not record.get('applied'):
            raise ValueError('PROJECT_SETUP_APPLY_REQUIRED')
        running = record.get('running') or {}
        if running.get('id'):
            if recover_run != running['id']:
                raise ValueError('PROJECT_SETUP_CHECK_RUNNING: use check --recover-run RUN_ID only after the owner exits')
            owner = running.get('owner_pid')
            if not isinstance(owner, int) or owner <= 0:
                raise ValueError('PROJECT_SETUP_OWNER_UNKNOWN')
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                record.setdefault('history', []).append({**running, 'status': 'abandoned'})
            else:
                raise ValueError('PROJECT_SETUP_OWNER_STILL_RUNNING')
        plan = record['plan']
        before = current_inputs(repo, plan)
        if before['context_sha256'] != plan['context_sha256']:
            raise ValueError('PROJECT_SETUP_CONTEXT_STALE')
        run_id = 'setup-check-' + uuid.uuid4().hex
        record['running'] = {'id': run_id, 'inputs': before, 'owner_pid': os.getpid()}
        atomic_json(path, record)
    results = []
    try:
        for c in plan['checks']:
            result = run_script(c['command'], repo / c['cwd'], c['timeout_seconds'])
            discovery = result.get('test_discovery') or {}
            from scripts.preparation_result import status as preparation_status
            status = preparation_status(result)
            if c['capability'] == 'test' and (not discovery.get('observed') or discovery.get('maximum_count', 0) < 1):
                status = 'blocked'
                result['reason'] = 'PROJECT_SETUP_NONEMPTY_TEST_EVIDENCE_REQUIRED'
            results.append({**result, 'name': c['name'], 'status': status, 'capability': c['capability']})
    except Exception as exc:
        results.append({'status': 'error', 'reason': str(exc)})
    with repo_transaction(repo):
        latest = read(path)
        if not latest or (latest.get('running') or {}).get('id') != run_id:
            raise ValueError('PROJECT_SETUP_RUN_CHANGED')
        try:
            after = current_inputs(repo, plan)
        except (ValueError, OSError, RuntimeError) as exc:
            after = None
            results.append({'status': 'stale', 'reason': str(exc)})
        status = ('stale' if before != after else 'error' if any(r['status'] == 'error' for r in results)
                  else 'failed' if any(r['status'] == 'failed' for r in results)
                  else 'blocked' if any(r['status'] == 'blocked' for r in results) else 'passed')
        from scripts.cao_snapshot import verification_snapshot
        result = {'id': run_id, 'owner': {'kind': 'project', 'scope': scope}, 'purpose': 'initialization',
                  'status': status, 'inputs': before, 'results': results,
                  'verification_sha256': before['verification_sha256']}
        latest.setdefault('history', []).append(result)
        latest['history'] = latest['history'][-20:]
        latest['result'] = result
        latest.pop('running', None)
        atomic_json(path, latest)
        return result


def dispatch(args):
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    action = args.setup_action
    if action not in {'rules', 'status'}:
        from scripts.provider_restart import restart_state
        from scripts.cao_providers import detect_current_host
        restart = restart_state(repo, detect_current_host())
        if not restart['ready']:
            raise ValueError(restart['reason'])
    if action == 'rules':
        from scripts.setup_rules import describe
        context = load_context(repo) or {}
        scope = context.get('scopes', {}).get(args.scope, {})
        values = (scope.get('identity') or {}).get('values', {})
        out = {'rules': {key: {**rule, 'missing_identity_fields': [field for field in rule['fields'] if not values.get(field)]} for key, rule in describe().items()},
               'confirmed_identity': values, 'explicit_plan': 'project setup draft --scope S, edit exact files/checks/basis, then plan/apply/check'}
    elif action == 'status':
        out = state(repo, args.scope)
    elif action == 'draft':
        with repo_transaction(repo):
            path = input_path(repo, args.scope)
            if not path.exists():
                if args.rule:
                    context = load_context(repo)
                    if not context or args.scope not in context['scopes']:
                        raise ValueError('PROJECT_CONTEXT_REQUIRED')
                    from scripts.setup_rules import scaffold, describe
                    fields = describe(args.rule)['fields']
                    inherited = context['scopes'][args.scope].get('identity', {}).get('values', {})
                    values = {key: value for key, value in inherited.items() if key in fields}
                    values.update(json.loads(args.identity or '{}'))
                    raw = scaffold(args.rule, values)
                    base = context['scopes'][args.scope]['paths'][0]
                    if base != '.':
                        for entry in raw['files']:
                            entry['path'] = base + '/' + entry['path']
                        raw['outputs'] = [base + '/' + value for value in raw['outputs']]
                        if raw.get('generated_output_suffixes'):
                            raw['generated_output_suffixes'] = {base + '/' + p: s for p, s in raw['generated_output_suffixes'].items()}
                        for entry in raw['checks']:
                            entry['cwd'] = base
                else:
                    raw = {'schema_version': 1, 'rule': {'id': 'explicit', 'version': 1},
                           'basis': '', 'identity': {}, 'files': [], 'checks': [],
                           'required_capabilities': [], 'outputs': []}
                raw.update(source=args.source, evidence=args.evidence)
                atomic_json(path, raw)
            out = {'input': path.relative_to(repo).as_posix(), 'plan': read(path),
                   'next': 'Review identity, layout, complete scaffold and actual checks; project setup plan, apply, check.'}
    elif action == 'plan':
        with repo_transaction(repo):
            source = Path(args.declaration) if args.declaration else input_path(repo, args.scope)
            if not source.is_absolute():
                source = repo / source
            if not source.resolve().is_relative_to(repo):
                raise ValueError('PROJECT_SETUP_INPUT_OUTSIDE_REPOSITORY')
            plan = normalize_plan(repo, args.scope, read(source))
            path = scope_path(repo, args.scope)
            previous = read(path)
            if previous and previous.get('running'):
                raise ValueError('PROJECT_SETUP_CHECK_RUNNING')
            context = load_context(repo)
            identity = {'values': plan['identity'], 'rule': plan['rule'], 'sources': plan['identity_sources']}
            if context['scopes'][args.scope].get('identity') != identity:
                from scripts.project_context import commit_context
                candidate = deepcopy(context)
                candidate['scopes'][args.scope]['identity'] = identity
                for evidence in plan['identity_evidence']:
                    if evidence not in candidate['evidence']:
                        candidate['evidence'].append(evidence)
                candidate['previous_revision'] = context['revision']
                context = commit_context(repo, candidate, context['revision'])
                plan['context_sha256'] = fingerprint(context, [args.scope])
                plan.pop('id', None)
                plan['id'] = 'setup-' + digest(plan)
            if previous and previous['plan']['id'] == plan['id']:
                out = {'status': 'unchanged', 'plan': previous['plan']}
            else:
                atomic_json(path, {'plan': plan, 'previous_plan_id': (previous or {}).get('plan', {}).get('id')})
                out = {'status': 'planned', 'plan': plan}
    elif action == 'apply':
        out = apply(repo, args.scope)
    else:
        out = check(repo, args.scope, getattr(args, 'recover_run', None))
    print(json.dumps({'ok': out.get('status') not in {'failed', 'blocked', 'error', 'stale'}, **out}, ensure_ascii=False, indent=2))
    return {'failed': 1, 'blocked': 2, 'error': 3, 'stale': 2}.get(out.get('status'), 0)


def register(actions):
    setup = actions.add_parser('setup', help='Project-owned identity, scaffold and real baseline checks; no business Work').add_subparsers(dest='setup_action', required=True)
    for name in ('rules', 'status', 'draft', 'plan', 'apply', 'check'):
        parser = setup.add_parser(name)
        parser.add_argument('--repo', default='.')
        parser.add_argument('--scope', default='root')
        if name == 'draft':
            parser.add_argument('--rule')
            parser.add_argument('--identity', help='JSON object; inspect rules for applicable fields')
            parser.add_argument('--evidence')
            parser.add_argument('--source', choices=['user-specified', 'user-delegated'], default='user-specified')
        if name == 'plan':
            parser.add_argument('--declaration', help='Explicit repository JSON plan; default is the prepared scope input')
        if name == 'check':
            parser.add_argument('--recover-run', help='Recover this exact abandoned run only after its owner exits')
        parser.set_defaults(fn=dispatch)


def write_targets(raw):
    from scripts.cao_tool_policy import _collect_path_strings, SHELL_TOOLS, _event_key
    if _event_key(raw, 'tool_name', 'toolName', 'name').lower() not in {'write', 'edit', 'apply_patch'}:
        return []
    targets = _collect_path_strings(raw.get('tool_input') or raw.get('input') or raw.get('arguments') or {})
    if _event_key(raw, 'tool_name', 'toolName', 'name').lower() == 'apply_patch':
        payload = raw.get('tool_input') or raw.get('input') or raw.get('arguments') or {}
        patch = payload if isinstance(payload, str) else payload.get('patch') or payload.get('input') or ''
        if isinstance(patch, str):
            targets += re.findall(r'(?m)^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$', patch)
    return targets


def writable_scaffold(repo, raw):
    targets = write_targets(raw)
    if not targets:
        return False
    context = load_context(repo)
    if not context:
        return False
    allowed = set()
    for scope in context['scopes']:
        record = read(scope_path(repo, scope))
        if not record or not record.get('applied') or record.get('running'):
            continue
        plan = record['plan']
        if fingerprint(context, [scope]) != plan['context_sha256'] or state(repo, scope)['ready']:
            continue
        allowed.update(safe_product(repo, f['path'], plan['bases']) for f in plan['files'])
    for value in targets:
        path = Path(value)
        path = path if path.is_absolute() else repo / path
        if path.absolute() not in allowed or path.resolve() != path.absolute():
            return False
    return True


def writable_input(repo, raw):
    targets = write_targets(raw)
    if not targets:
        return False
    for value in targets:
        path = Path(value)
        path = path if path.is_absolute() else repo / path
        allowed = False
        for entry in (repo / '.orchestrator/inputs').glob('project-*/setup.json'):
            if path.absolute() == entry.absolute() and entry.is_file() and not entry.is_symlink():
                allowed = True
        if not allowed or path.resolve() != path.absolute():
            return False
    return True
