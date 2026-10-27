"""Concrete consumer recovery guidance, never a request to reverse-engineer CAO."""
import shlex

RUNTIME_BOUNDARY = ('Use the returned commands, prepared inputs and bundled stage guides/schema. '
                    'Do not read/search CAO implementation scripts, tests or development docs for alternate entrypoints. '
                    'If the same diagnostic repeats without a changed input, stop retries and report its code, fields and pending action.')


def context_recovery(repo, state, scope=None):
    from scripts.project_refinement import bootstrap_scope
    scopes = state.get('scopes') or [scope or 'root']
    unavailable = None
    try:
        initialization = all(bootstrap_scope(repo, name) for name in scopes)
    except (ValueError, OSError, RuntimeError) as exc:
        initialization = False
        unavailable = str(exc)
    owner = 'project_initialization' if initialization else 'migration_work'
    diagnostics = [{**row, 'code': 'PROJECT_CONTEXT_CONFLICT', 'field': row.get('scope', 'root') + '.' + row.get('field', '')}
                   for row in state.get('differences', [])]
    commands = ['cao project context diff', 'cao project inspect']
    if initialization:
        for row in state.get('differences', []):
            if row.get('field') and row.get('observed'):
                commands.append('cao project context set --scope ' + shlex.quote(row['scope']) + ' --'
                                + row['field'].replace('_', '-') + ' ' + shlex.quote(row['observed'])
                                + ' --source user-specified --evidence <actual-user-evidence-id>')
        instruction = ('This is unbound project initialization, not a migration Work. '
                       'Use actual user evidence to correct/refine the named background fields with project context set; '
                       'if a choice is missing ask only that choice. For saved identity answers, resume project setup identity to synchronize them. '
                       'Replan/check affected setup, materialize scripts, and run verification check --project before creating Work. ')
    else:
        instruction = ('Preserve the established product stack. Restore unexpected manifest drift before creating a migration Work; '
                       'an authorized material replacement uses project context change propose/apply with its owning Work. ')
    if unavailable:
        owner = 'state_repair'
        diagnostics.append({'code': 'PROJECT_RECOVERY_STATE_UNAVAILABLE', 'detail': unavailable})
        instruction = 'Project recovery metadata is unavailable. Preserve current files; inspect the named state path and restore its recorded authority before retrying. Do not create a temporary Work or overwrite state. '
    return {'owner': owner, 'commands': commands, 'diagnostics': diagnostics,
            'instruction': instruction + RUNTIME_BOUNDARY, 'stop_on_unchanged_error': True}


def setup_recovery(repo, args, code):
    from scripts.project_context import load_context, read
    scope = getattr(args, 'scope', None) or 'root'
    if isinstance(scope, list):
        scope = scope[0] if scope else 'root'
    context = load_context(repo) or {}
    data = context.get('scopes', {}).get(scope, {})
    framework = data.get('technology', {}).get('framework', {}).get('value')
    from scripts.setup_frameworks import spring_boot
    def input_object(path):
        try:
            if (not path.resolve().is_relative_to(repo.resolve())
                    or any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(repo))):
                return {}
            value = read(path)
        except (ValueError, OSError):
            return {}
        return value if isinstance(value, dict) else {}
    interview = input_object(repo / '.orchestrator/project/identity' / (scope + '.json'))
    prepared = input_object(repo / '.orchestrator/inputs' / ('project-' + scope) / 'setup.json')
    prepared_rule = prepared.get('rule') if isinstance(prepared.get('rule'), dict) else {}
    rule = (getattr(args, 'rule', None) or interview.get('rule')
            or prepared_rule.get('id') or data.get('identity', {}).get('rule', {}).get('id') or 'explicit')
    command = 'cao project setup identity --scope ' + shlex.quote(scope) + ' --rule ' + rule
    if code in {'PROJECT_FRAMEWORK_VERSION_REQUIRED', 'PROJECT_DEPENDENCY_VERSION_REQUIRED', 'PROJECT_IDENTITY_REQUIRED'}:
        instruction = 'Resume `' + command + '`; ask/save only the returned missing field using actual user evidence. '
        expected = 'Exact framework/dependency release major.minor.patch; JUnit generation 5 may be managed by the confirmed Spring Boot BOM.'
    elif code in {'PROJECT_SETUP_FRAMEWORK_PLAN_REQUIRED', 'PROJECT_SETUP_FRAMEWORK_VERSION_MISMATCH', 'PROJECT_SETUP_FRAMEWORK_CHECK_REQUIRED'}:
        instruction = ('Inspect .orchestrator/inputs/project-' + scope + '/setup.json; framework_support must be the exact accepted string '
                       + repr(framework) + ' and framework load checks must be real. ')
        if spring_boot(framework):
            instruction += 'After completing saved identity/version answers, `cao project setup draft --rule ' + rule + ' --scope ' + shlex.quote(scope) + ' --refresh` regenerates the supported Spring Boot input; inspect it before plan/apply/check. '
        expected = 'A framework-aware manifest/entry and load check using the exact user-confirmed framework release.'
    else:
        instruction = 'Inspect the named field in the exact prepared setup input and bundled project-setup schema; retry plan/apply/check after changing that input. '
        if code == 'PROJECT_SETUP_EXPLICIT_PLAN_REQUIRED':
            instruction += 'Preserve the confirmed stack/version; use `project setup draft` without --rule and fill its explicit plan instead of choosing another framework release. '
        expected = 'A valid prepared project input with recorded user provenance.'
    return {'diagnostics': [{'code': code, 'input': '.orchestrator/inputs/project-' + scope + '/setup.json', 'expected': expected}],
            'next': {'type': 'agent', 'stage': 'project_setup', 'owner': 'project', 'instruction': instruction + RUNTIME_BOUNDARY}}
