"""Bounded continuity views; observations never grant lifecycle gates."""
from copy import deepcopy
import subprocess


def page(items, limit=20):
    items = list(items or [])
    return {'items': deepcopy(items[:limit]), 'total': len(items),
            'omitted': max(0, len(items) - limit)}


def snapshot(repo, work):
    from scripts.cao_runtime import sha256_file
    from scripts.path_guard import repo_file
    sdd = work.get('sdd') or {}
    requirement = deepcopy(work.get('requirement'))
    try:
        actual = sha256_file(repo_file(repo, requirement['path']))
    except (OSError, ValueError, TypeError, KeyError):
        actual = None
    workspace = {'vcs': 'none', 'head': None, 'branch': None}
    if (repo / '.git').exists():
        workspace['vcs'] = 'git'
        for key, args in [('head', ['rev-parse', 'HEAD']),
                          ('branch', ['symbolic-ref', '--short', '-q', 'HEAD'])]:
            try:
                result = subprocess.run(['git', *args], cwd=repo, capture_output=True,
                                        text=True, timeout=3)
                workspace[key] = result.stdout.strip() if result.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                workspace[key] = None
    return {'requirement': requirement, 'observed_requirement_sha256': actual,
            'project_binding': deepcopy(work.get('project_binding')),
            'native': {key: deepcopy(sdd.get(key)) for key in
                       ('provider', 'native_ref', 'route_decision', 'plan_scope', 'native_state',
                        'workflow', 'completion_evidence', 'native_reviews', 'implementation_evidence')},
            'workspace': workspace,
            'capture_limits': ['Native fields are last recorded observations; inspect original sources.',
                              'Git HEAD is a location, not proof of product contents or process termination.']}


def view(work, handoff=None):
    resume = work.get('resume') or {}
    old = (handoff or {}).get('continuity') or {}
    differences = []
    if handoff:
        if old and old.get('requirement') != work.get('requirement'):
            differences.append('requirement_binding_changed')
        if handoff.get('execution_generation') is not None and handoff['execution_generation'] != work.get('execution_generation', 0):
            differences.append('execution_generation_changed')
        if not old:
            differences.append('legacy_handoff_missing_source_snapshot')
    return {'work_id': work['id'], 'state_revision': work.get('state_revision'),
            'execution_generation': work.get('execution_generation', 0),
            'completed': page(resume.get('completed')), 'pending': page(resume.get('pending')),
            'handoff': {'id': (handoff or {}).get('id'), 'version': (handoff or {}).get('version'),
                        'summary': (handoff or {}).get('summary'),
                        'pending': page((handoff or {}).get('pending')),
                        'differences': differences,
                        'inspect': 'cao handoff show --id ' + handoff['id'] if handoff else None},
            'native': {key: deepcopy((work.get('sdd') or {}).get(key)) for key in ('provider', 'native_ref', 'native_state')},
            'process_evidence': page((work.get('sdd') or {}).get('implementation_evidence')),
            'authority': 'Current Work and original native sources; handoff is historical context.'}


def render(work, handoff=None):
    import json
    return '\n## Continuity and handoff\n' + json.dumps(view(work, handoff), ensure_ascii=False)
