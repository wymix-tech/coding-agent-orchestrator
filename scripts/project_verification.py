"""Project-level verification declaration bindings, without another script source."""
import shlex
from pathlib import Path
from scripts.project_context import context_state, digest, fingerprint, load_context, selected_scopes
from scripts.technology_guidance import technology_instruction


def validate_entries(repo, scripts, scopes=None):
    """Reject known contradictory runners; opaque project wrappers stay explicit.

    Do not guess that a wrapper's implementation language is the product language.
    Arbitrary shell and custom tooling still need the existing baseline review.
    """
    from scripts.project_sources import compact
    context = load_context(repo)
    if not context:
        return
    scopes = selected_scopes(context, scopes)
    allowed_build, allowed_test = set(), set()
    for name in {*scopes, 'root'}:
        technology = context.get('scopes', {}).get(name, {}).get('technology', {})
        allowed_build.add(compact(technology.get('build_tool', {}).get('value', '')))
        allowed_test.add(compact(technology.get('test_framework', {}).get('value', '')))
    for script in scripts:
        try:
            tokens = shlex.split(script['command'])
        except ValueError as exc:
            raise ValueError('VERIFICATION_COMMAND_INVALID') from exc
        names = {Path(token).name.casefold() for token in tokens[:3]}
        tool = next((tool for tool in ('mvn', 'mvnw', 'gradle', 'gradlew', 'pytest', 'vitest', 'jest', 'cargo') if tool in names), None)
        expected = {'mvn': 'maven', 'mvnw': 'maven', 'gradle': 'gradle', 'gradlew': 'gradle',
                    'cargo': 'cargo', 'pytest': 'pytest', 'vitest': 'vitest', 'jest': 'jest'}
        if tool:
            accepted = allowed_test if tool in {'pytest', 'vitest', 'jest'} else allowed_build
            if expected[tool] not in accepted:
                raise ValueError('PROJECT_VERIFICATION_CONTEXT_MISMATCH: ' + tool + ' conflicts with accepted project runners')


def verification_binding(repo, scopes=None):
    context = load_context(repo)
    if not context:
        raise ValueError('PROJECT_CONTEXT_REQUIRED')
    scopes = selected_scopes(context, scopes)
    return {'version': 1, 'scopes': scopes, 'fingerprint': fingerprint(context, scopes),
            'scope_fingerprints': {scope: fingerprint(context, [scope]) for scope in scopes}}


def declaration_state(repo, cfg, scopes=None, work=None):
    context = load_context(repo)
    if not context:
        return {'ready': False, 'reason': 'PROJECT_CONTEXT_REQUIRED', 'next': 'Run `cao project inspect` and establish background.'}
    state = context_state(repo, scopes, work)
    if not state['ready']:
        return state
    verification = cfg.get('verification') or {}
    binding = verification.get('context_binding')
    if binding:
        # Standards can cover more modules than the current Work.
        expected = {scope: fingerprint(context, [scope]) for scope in state['scopes']}
        recorded = binding.get('scope_fingerprints') or {scope: binding.get('fingerprint') for scope in binding['scopes']}
        if any(recorded.get(scope) != sha for scope, sha in expected.items()):
            return {'ready': False, 'reason': 'PROJECT_VERIFICATION_CONTEXT_MISMATCH',
                    'next': 'Review affected build/test entries, then `cao verification configure` with the correct --scope; preserve baseline review/reanchor.'}
    if not binding:
        return {'ready': False, 'reason': 'PROJECT_VERIFICATION_PLAN_REQUIRED',
                'next': 'Reuse actual project entries with `cao verification configure`, then run `cao verification check`. To create wrappers first reserve exact paths with `cao verification prepare --path .orchestrator/scripts/verification/root/check.py`. A plan-only declaration cannot unlock Work.'}
    from scripts.verification_setup import configured_scripts, setup_instruction
    try:
        scripts = configured_scripts(cfg)
    except ValueError:
        return {'ready': False, 'reason': 'VERIFICATION_BASELINE_REQUIRED', 'next': setup_instruction('cao')}
    plan = verification.get('plan') or {}
    try:
        validate_entries(repo, scripts or plan.get('scripts', []), state['scopes'])
    except ValueError as exc:
        return {'ready': False, 'reason': 'PROJECT_VERIFICATION_CONTEXT_MISMATCH', 'difference': str(exc),
                'next': 'Restore entries matching the accepted project runners, then configure and review/reanchor the actual baseline.'}
    from scripts.verification_setup import setup_state
    if not setup_state(cfg)['ready']:
        return {'ready': False, 'reason': 'VERIFICATION_BASELINE_REQUIRED', 'next': setup_instruction('cao')}
    from scripts.project_baseline import readiness
    from scripts.verification_probe import scoped_config
    baseline = readiness(repo, scoped_config(repo, cfg, state['scopes'])['verification']['scripts'], state['scopes'])
    if not baseline['ready']:
        return baseline
    return {'ready': True, 'state': 'configured' if scripts else 'planned', 'context_binding': binding}


def project_prerequisite(repo, work=None, scopes=None):
    from scripts.cao_runtime import config
    from scripts.project_context import binding_state
    state = binding_state(repo, work) if work else context_state(repo, scopes, check_sources=True)
    if state['ready']:
        from scripts.project_architecture import state as architecture_state
        architecture = architecture_state(repo, scopes, work)
        if not architecture['ready']:
            state = architecture
    if state['ready']:
        from scripts.project_setup import prerequisite
        setup = prerequisite(repo, work, scopes)
        if setup:
            return setup
        state = declaration_state(repo, config(repo), scopes, work)
    if state['ready']:
        from scripts.verification_probe import project_state
        state = project_state(repo, selected_scopes(load_context(repo), scopes, work), work=work)
    if state['ready']:
        return None
    return {'type': 'agent', 'owner': 'project', 'stage': 'verification_setup' if state['reason'] in {'VERIFICATION_BASELINE_REQUIRED', 'PROJECT_VERIFICATION_CHECK_REQUIRED', 'PROJECT_VERIFICATION_SCRIPTS_REQUIRED'} else 'project_setup', 'instruction': state['next'] + ' ' + technology_instruction('cao'),
            'reason': state['reason'], 'project_state': state}


def preparation_context(repo, work=None, *, purpose='project_baseline_preparation'):
    """Project inputs only. Work identity is a deferred continuation pointer."""
    from scripts.cao_runtime import config
    from scripts.verification_setup import setup_state, PROJECT_PREPARATION_POLICY
    context = load_context(repo) or {}
    try:
        scopes = selected_scopes(context, work=work) if context else []
    except ValueError:
        scopes = []
    project = context_state(repo, scopes, work)
    project['technology'] = {name: {'paths': scope.get('paths', []),
        'technology': {field: {key: value[key] for key in ('value', 'status', 'version') if key in value}
                       for field, value in scope.get('technology', {}).items()},
        'identity': (scope.get('identity') or {}).get('values', {})}
        for name, scope in project.get('technology', {}).items()}
    return {'owner': 'project', 'purpose': purpose, 'preparation_policy': PROJECT_PREPARATION_POLICY,
            'scopes': scopes, 'project_context': project,
            'verification_setup': setup_state(config(repo)),
            'pending_work': {'id': work['id'], 'status': work['status']} if work else None,
            'baseline_basis': 'Use accepted project technology, product manifests, native build/test/CI entries and project-wide quality standards. Work requirements, acceptance criteria, decisions and handoff content are not baseline-generation inputs. SDD owns business behavior tests and task acceptance.'}
