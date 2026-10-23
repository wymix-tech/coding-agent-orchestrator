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
                'next': 'Reuse actual project entries with `cao verification configure`, or save `cao verification configure --plan-only --test-script "<planned command>"`; no product files are needed.'}
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
    if not scripts and not plan.get('scripts'):
        return {'ready': False, 'reason': 'PROJECT_VERIFICATION_PLAN_REQUIRED',
                'next': 'Declare actual entries or use `cao verification configure --plan-only --test-script "<planned command>"`.'}
    return {'ready': True, 'state': 'configured' if scripts else 'planned', 'context_binding': binding}


def project_prerequisite(repo, work=None, scopes=None):
    from scripts.cao_runtime import config
    from scripts.project_context import binding_state
    state = binding_state(repo, work) if work else context_state(repo, scopes, check_sources=True)
    if state['ready']:
        state = declaration_state(repo, config(repo), scopes, work)
    if state['ready']:
        return None
    return {'type': 'agent', 'stage': 'verification_setup' if state['reason'] == 'VERIFICATION_BASELINE_REQUIRED' else 'project_setup', 'instruction': state['next'] + ' ' + technology_instruction('cao'),
            'reason': state['reason'], 'project_state': state}
