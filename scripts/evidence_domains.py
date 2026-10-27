"""Content manifests and exact invalidation domains for native completion."""
from pathlib import Path
import re


def files(repo, names):
    from scripts.path_guard import repo_file
    from scripts.cao_runtime import sha256_file
    result = {}
    for name in sorted(set(names)):
        path = repo_file(repo, name)
        result[name] = {'sha256': sha256_file(path), 'executable': path.stat().st_mode & 0o111} if path.is_file() else {'missing': True}
    return result


def dependencies(repo, work, commands=()):
    """Track declared inputs and literal sourced helpers in managed wrappers.

    All other product dependencies already belong to the product manifest.
    Dynamic managed imports require explicit declarations, rather than guessing.
    """
    from scripts.cao_provider_workflows import workflow_command_identities
    from scripts.path_guard import repo_file
    sdd = work.get('sdd') or {}
    declared = set(sdd.get('native_execution_dependencies') or []) | set(sdd.get('native_review_dependencies') or [])
    names = set(declared)
    execution = (sdd.get('workflow') or {}).get('execution') or {}
    for command in [execution.get('command') or '', *commands]:
        for identity in workflow_command_identities(command):
            candidate = Path(identity)
            candidate = candidate if candidate.is_absolute() else repo / candidate
            if candidate.is_file() and candidate.resolve().is_relative_to(repo.resolve()):
                name = candidate.resolve().relative_to(repo.resolve()).as_posix()
                if name.startswith('.orchestrator/scripts/verification/'):
                    names.add(name)
    queue, seen = list(names), set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        if len(seen) > 1000:
            raise ValueError('NATIVE_DEPENDENCY_SCAN_LIMIT')
        path = repo_file(repo, name)
        if not path.is_file():
            continue
        if path.suffix not in {'.sh', '.bash', '.zsh'}:
            continue
        for line in path.read_text().splitlines():
            match = re.match(r'^\s*(?:source|\.)\s+([\S]+)', line)
            if not match:
                continue
            value = match[1].strip('"\'')
            if any(c in value for c in '$`*?'):
                if not declared:
                    raise ValueError('NATIVE_DEPENDENCY_DECLARATION_REQUIRED: declare dynamic helper inputs with cao lifecycle dependencies')
                continue
            target = (path.parent / value).resolve()
            if not target.is_file():
                target = (repo / value).resolve()
            if not target.is_relative_to(repo.resolve()):
                raise ValueError('NATIVE_DEPENDENCY_OUTSIDE_REPO')
            relative = target.relative_to(repo.resolve()).as_posix()
            names.add(relative)
            queue.append(relative)
    return files(repo, names)


def scope(repo, work):
    from copy import deepcopy
    from scripts.cao_plans import provider_verification_plan_state
    from scripts.native_metadata import semantic_hash
    from scripts.path_guard import repo_file
    result = deepcopy(provider_verification_plan_state(repo, work))
    for key in ('raw', 'canonical', 'state', 'ready', 'next', 'reason'):
        result.pop(key, None)
    route = (work.get('sdd') or {}).get('route_decision') or {}
    source = route.get('primary_artifact')
    if route.get('provider') == 'bmad' and route.get('build_route') == 'dispatch' and source:
        row = result.get('observations', {}).get(source)
        if row and 'sha256' in row:
            row['sha256'] = semantic_hash(repo_file(repo, source))
    return result


def capture(repo, work, commands=()):
    from scripts.acceptance_inputs import technical_inputs
    from scripts.cao_runtime import config
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.path_guard import provider_artifact
    from scripts.native_metadata import semantic_hash
    manifest = []
    technical = technical_inputs(repo, work, config(repo), manifest=manifest)
    sdd = work.get('sdd') or {}
    route = sdd.get('route_decision') or {}
    sources = set((sdd.get('workflow') or {}).get('artifacts') or [])
    sources.update(row['path'] for row in route.get('required_artifacts', []))
    for key in ('spec', 'criteria'):
        ref = (sdd.get('spec_binding') or {}).get(key)
        if isinstance(ref, dict) and ref.get('path'):
            sources.add(ref['path'])
    reports = {r['path'] for r in (sdd.get('completion_evidence') or {}).get('reports', [])}
    native_sources = files(repo, sources - reports)
    if (sdd.get('provider') == 'bmad' and route.get('build_route') == 'dispatch'
            and route.get('primary_artifact') in native_sources):
        source = route['primary_artifact']
        path = provider_artifact(repo, source, provider_output_dirs('bmad', repo))
        if path.is_file():
            native_sources[source] = {'semantic_sha256': semantic_hash(path)}
    from scripts.managed_verification import input_hashes
    from scripts.native_obligations import digest
    verification = config(repo).get('verification') or {}
    declaration = {key: verification[key] for key in ('scripts', 'timeout_seconds', 'environment_variables', 'maximum_evidence_age_seconds', 'context_binding') if key in verification}
    return {'product': {row['path']: {k: v for k, v in row.items() if k != 'path'} for row in manifest},
            'native_sources': native_sources, 'native_scope': {**scope(repo, work).get('observations', {}),
                'scope:bindings': {key: value for key, value in scope(repo, work).items() if key != 'observations'}},
            'reports': files(repo, reports),
            'native_dependencies': dependencies(repo, work, commands),
            'baseline': {'config': technical['verification_config_sha256'], 'declaration_sha256': digest(declaration),
                         'files': input_hashes(repo, (work.get('project_binding') or {}).get('scopes'))},
            'policy': {k: technical.get(k) for k in ('project_context_sha256', 'rules_snapshot', 'environment_sha256')}}


def changed(previous, current):
    domains, paths = [], set()
    for name in sorted(set(previous) | set(current)):
        old, new = previous.get(name, {}), current.get(name, {})
        if old == new:
            continue
        domains.append(name)
        if name == 'baseline':
            if old.get('declaration_sha256') != new.get('declaration_sha256'):
                paths.add('.orchestrator/config.json')
            old, new = old.get('files', {}), new.get('files', {})
        for path in set(old) | set(new):
            if old.get(path) != new.get(path):
                paths.add(path if '/' in path or '.' in path else 'policy:' + path)
    affected = set()
    if set(domains) & {'product', 'native_sources', 'native_scope', 'native_dependencies', 'policy'}:
        affected.update({'native_completion', 'cao_conformance'})
    if 'product' in domains or 'policy' in domains or 'baseline' in domains:
        affected.add('project_baseline')
    if 'reports' in domains:
        affected.update({'native_completion', 'cao_conformance'})
    return {'changed_domains': domains, 'changed_paths': sorted(paths),
            'affected_evidence': sorted(affected),
            'still_valid_evidence': sorted({'native_completion', 'cao_conformance', 'project_baseline'} - affected)}
