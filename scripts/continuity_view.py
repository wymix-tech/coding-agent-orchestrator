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
    from scripts.cao_snapshot import code_snapshot
    product_files = []
    product = code_snapshot(repo, manifest=product_files)
    if workspace['vcs'] == 'git':
        result = subprocess.run(['git', 'status', '--porcelain=v1', '-z', '--untracked-files=all'], cwd=repo, capture_output=True, timeout=3)
        workspace['dirty_paths'] = result.stdout.decode('utf-8', errors='replace').split('\0') if result.returncode == 0 else None
    from scripts.native_units import units
    try:
        observed_units = units(repo, work)
        unit_error = None
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        observed_units, unit_error = [], str(exc)
    from scripts.runtime_queries import runs
    unresolved = [r for r in runs(repo, work) if r['recovery_status'] not in {'complete', 'projection_missing', 'superseded'}]
    from scripts.evidence_domains import capture
    try:
        domains = capture(repo, work)
        domain_error = None
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        domains, domain_error = {}, str(exc)
    from scripts.cao_provider_workflows import provider_native_skills
    skills = {r['name']: sha256_file(repo_file(repo, r['rel_path'])) for r in provider_native_skills(repo, sdd.get('provider'))}
    if code_snapshot(repo) != product:
        raise ValueError('HANDOFF_INPUT_CHANGED_DURING_CAPTURE')
    return {'capture_complete': not domain_error and not unit_error, 'current_unit': (work.get('resume') or {}).get('current_unit'), 'read_before_resume': (work.get('resume') or {}).get('read_before_resume', []), 'evidence_domains': domains, 'domain_error': domain_error, 'provider_skills': skills, 'native_units': observed_units, 'native_unit_error': unit_error, 'unresolved_runs': unresolved, 'product_snapshot': product, 'product_files': product_files, 'requirement': requirement, 'observed_requirement_sha256': actual,
            'project_binding': deepcopy(work.get('project_binding')),
            'native': {key: deepcopy(sdd.get(key)) for key in
                       ('provider', 'native_ref', 'route_decision', 'plan_scope', 'native_state',
                        'workflow', 'completion_evidence', 'native_reviews', 'implementation_evidence')},
            'workspace': workspace,
            'capture_limits': ['Native fields are last recorded observations; inspect original sources.',
                              'Git HEAD is a location, not proof of product contents or process termination.']}


def view(work, handoff=None):
    resume = work.get('resume') or {}
    from scripts.coordination_items import active_pending
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
            'current_unit': resume.get('current_unit'), 'read_before_resume': resume.get('read_before_resume', []),
            'coordination_items': page([r for r in resume.get('items', []) if r.get('status') == 'open']),
            'completed': page(resume.get('completed')), 'pending': page(active_pending(work)),
            'handoff': {'id': (handoff or {}).get('id'), 'version': (handoff or {}).get('version'),
                        'summary': (handoff or {}).get('summary'),
                        'pending': page((handoff or {}).get('pending')),
                        'differences': differences,
                        'inspect': 'cao handoff show --id ' + handoff['id'] if handoff else None},
            'native': {key: deepcopy((work.get('sdd') or {}).get(key)) for key in ('provider', 'native_ref', 'native_state')},
            'process_evidence': page((work.get('sdd') or {}).get('implementation_evidence')),
            'native_units': page(old.get('native_units')),
            'unresolved_runs': old.get('unresolved_runs', []),
            'authority': 'Current Work and original native sources; handoff is historical context.'}


def render(work, handoff=None):
    import json
    return '\n## Continuity and handoff\n' + json.dumps(view(work, handoff), ensure_ascii=False)
