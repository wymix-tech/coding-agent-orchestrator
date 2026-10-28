"""User-authorized initialization corrections, distinct from product migrations."""
from copy import deepcopy

from scripts.project_sources import compact


def technology_key(field, value):
    token = compact(value)
    if field == 'test_framework' and token in {'junitjupiter', 'jupiter'}:
        return 'junit5'
    return token


def bootstrap_scope(repo, scope, context=None):
    """Only an empty or CAO-scaffolded scope without any Work binding qualifies.

    Historical bindings count too. Existing products cannot gain a migration
    exemption by clearing the active Work or merely asking for a new choice.
    """
    from scripts.project_context import load_context, read
    from scripts.cao_work_store import load_work
    from scripts.project_inspection import inspect
    from scripts.project_setup import scope_path
    context = context or load_context(repo)
    if not context or scope not in context['scopes'] or context.get('active_changes'):
        return False
    bases = context['scopes'][scope]['paths']
    for path in (repo / '.orchestrator/work').glob('*.json'):
        work = load_work(repo, path.stem)
        binding = (work or {}).get('project_binding')
        if not binding:
            return False  # Legacy Work may own this product without scope metadata.
        for bound in binding['scopes']:
            other = context['scopes'].get(bound, {}).get('paths', ['.'])
            if any(a == '.' or b == '.' or a == b or a.startswith(b + '/') or b.startswith(a + '/')
                   for a in bases for b in other):
                return False
    record = read(scope_path(repo, scope))
    return bool(record and record.get('plan')) or inspect(repo, {scope: context['scopes'][scope]})['empty_product']


def sync_identity(repo, scope, rule, field, value, evidence):
    """A confirmed JUnit generation refines the previously accepted JUnit family.

    Preserve the original answer as provenance; a version answer is not a new
    user quote claiming the derived framework name.
    """
    if rule not in {'java-maven', 'java-gradle'} or field not in {'junit_version', 'framework_version'}:
        return
    from scripts.project_context import load_context, commit_context
    context = load_context(repo)
    if field == 'framework_version':
        versions = context['scopes'][scope].get('versions', {})
        if versions.get('framework') == value:
            return
        if not bootstrap_scope(repo, scope, context):
            raise ValueError('PROJECT_CHANGE_REQUIRED: framework_version; use the product migration workflow')
        candidate = deepcopy(context)
        candidate['scopes'][scope].setdefault('versions', {})['framework'] = value
        candidate['evidence'].append({**evidence, 'scope': scope,
            'derivation': {'rule': rule, 'identity_field': field, 'value': value, 'field': 'versions.framework'}})
        candidate['previous_revision'] = context['revision']
        commit_context(repo, candidate, context['revision'])
        return
    current = context['scopes'][scope]['technology'].get('test_framework', {})
    old = technology_key('test_framework', current.get('value'))
    generation = value.split('.', 1)[0]
    desired = 'junit' + generation
    if old == desired:
        return
    if old != 'junit' or not bootstrap_scope(repo, scope, context):
        raise ValueError('PROJECT_IDENTITY_TECHNOLOGY_CONFLICT: junit_version; use authorized context correction before Work or a migration for bound products')
    candidate = deepcopy(context)
    candidate['scopes'][scope]['technology']['test_framework'] = {'value': 'JUnit ' + generation, 'status': 'accepted'}
    candidate['evidence'].append({**evidence, 'scope': scope,
        'derivation': {'rule': rule, 'identity_field': field, 'value': value,
                       'field': 'test_framework', 'accepted_family': current['value'], 'result': 'JUnit ' + generation}})
    candidate['previous_revision'] = context['revision']
    commit_context(repo, candidate, context['revision'])
