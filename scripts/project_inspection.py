"""Bounded product manifest observation. Tool/host directories are never scanned."""
from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.project_context import ROOT, digest, read, relative
from scripts.state_store import atomic_json

MANIFESTS = ('pom.xml', 'build.gradle', 'build.gradle.kts', 'package.json', 'pyproject.toml', 'go.mod', 'Cargo.toml')


def parse(path):
    text = path.read_text(encoding='utf-8')
    if len(text.encode()) > 1024 * 1024:
        raise ValueError('PROJECT_INSPECTION_LIMIT')
    name = path.name
    facts = {}
    if name == 'pom.xml':
        ET.fromstring(text)
        facts = {'language': 'Java', 'build_tool': 'Maven'}
        if 'spring-boot' in text:
            facts['framework'] = 'Spring Boot'
        if any(term in text for term in ('junit-jupiter', 'spring-boot-starter-test')):
            facts['test_framework'] = 'JUnit 5'
    elif name.startswith('build.gradle'):
        facts = {'language': 'Java', 'build_tool': 'Gradle'}
        if 'org.springframework.boot' in text:
            facts['framework'] = 'Spring Boot'
        if 'junit-jupiter' in text or 'useJUnitPlatform' in text:
            facts['test_framework'] = 'JUnit 5'
    elif name == 'package.json':
        package = json.loads(text)
        dependencies = {**package.get('dependencies', {}), **package.get('devDependencies', {})}
        facts = {'language': 'TypeScript' if 'typescript' in dependencies else 'JavaScript'}
        manager = str(package.get('packageManager', '')).split('@')[0]
        if manager in {'npm', 'pnpm', 'yarn', 'bun'}:
            facts['build_tool'] = manager
        elif (path.parent / 'package-lock.json').exists():
            facts['build_tool'] = 'npm'
        for key in ('react', 'vue', 'next', 'express'):
            if key in dependencies:
                facts['framework'] = key
                break
        for key in ('vitest', 'jest', 'mocha'):
            if key in dependencies:
                facts['test_framework'] = key
                break
    elif name == 'pyproject.toml':
        facts = {'language': 'Python'}
        if '[build-system]' in text and 'setuptools' in text:
            facts['build_tool'] = 'setuptools'
        if 'pytest' in text:
            facts['test_framework'] = 'pytest'
    elif name == 'go.mod':
        facts = {'language': 'Go', 'build_tool': 'Go', 'test_framework': 'Go test'}
    elif name == 'Cargo.toml':
        facts = {'language': 'Rust', 'build_tool': 'Cargo', 'test_framework': 'Cargo test'}
    return facts, digest(text)


def inspect(repo, scopes=None, work=None):
    scopes = scopes or {'root': {'paths': ['.']}}
    observations = []
    product_paths = []
    for name, scope in scopes.items():
        for base in scope['paths']:
            base = relative(repo, base)
            # Explicit modules only; never discover technology from infrastructure trees.
            if any(part.startswith('.') and part != '.' or part in {'_bmad', '_bmad-output', 'node_modules', 'openspec', 'specs'} for part in Path(base).parts):
                continue
            # Existence evidence is distinct from technology evidence. A product
            # source without a manifest is partial/existing, not an empty tool install.
            root = repo / base
            if root.is_dir():
                candidates = list(root.glob('*'))
                for folder in ('src', 'app', 'lib'):
                    directory = root / folder
                    if directory.is_dir() and not directory.is_symlink():
                        import itertools
                        candidates.extend(itertools.islice(directory.rglob('*'), 2000))
                for candidate in candidates:
                    if (candidate.is_file() and not candidate.is_symlink()
                            and candidate.suffix in {'.py', '.java', '.kt', '.js', '.mjs', '.ts', '.go', '.rs', '.c', '.cpp', '.cs', '.rb', '.php', '.swift', '.dart', '.sh', '.ps1'}
                            and not any(part in {'test', 'tests', '__pycache__', 'node_modules'} for part in candidate.relative_to(root).parts)
                            and candidate.resolve().is_relative_to(repo.resolve())):
                        product_paths.append(candidate.relative_to(repo).as_posix())
            for manifest in MANIFESTS:
                path = repo / base / manifest
                if not path.is_file() or not path.resolve().is_relative_to(repo.resolve()):
                    continue
                facts, sha = parse(path)
                relative_path = path.relative_to(repo).as_posix()
                established = not work
                if work:
                    result = subprocess.run(['git', 'log', '-1', '--format=%ct', '--', relative_path],
                                            cwd=repo, capture_output=True, text=True)
                    established = bool(result.returncode == 0 and result.stdout.strip().isdigit()
                                       and int(result.stdout.strip()) < work.get('created_at', 0))
                observations.append({'scope': name, 'path': relative_path, 'sha256': sha,
                                     'technology': facts, 'established': established})
    empty = not observations and not product_paths
    return {'schema_version': 1, 'observations': observations, 'empty_product': empty,
            'product_state': 'empty' if empty else ('existing' if observations else 'partial'),
            'product_paths': sorted(set(product_paths)),
            'note': 'Observed files and technology do not prove successful build or test execution.'}


def save_observation(repo, context=None, work=None):
    observed = inspect(repo, (context or {}).get('scopes'), work)
    atomic_json(repo / ROOT / 'observations.json', observed)
    return observed


def drift(repo, context, scopes, work=None, allow_migration=True):
    from scripts.project_sources import compact
    differences = []
    # Refresh only bounded known manifests. Expected owner migration differences are not conflicts.
    for identity in context.get('active_changes', []):
        change = read(repo / ROOT / 'changes' / (identity + '.json'))
        if allow_migration and change and work and change.get('owner_work') == work['id']:
            return []
    selected = {key: value for key, value in context['scopes'].items() if key in {*scopes, 'root'}}
    for observed in inspect(repo, selected, work)['observations']:
        scope = context['scopes'][observed['scope']]
        for key, value in observed['technology'].items():
            accepted = scope['technology'].get(key, {})
            if accepted.get('status') in {'accepted', 'not_applicable'} and compact(accepted['value']) != compact(value):
                differences.append({'scope': observed['scope'], 'field': key, 'accepted': accepted['value'],
                                    'observed': value, 'path': observed['path']})
            elif not accepted and key in {'framework', 'build_tool', 'test_framework'}:
                differences.append({'scope': observed['scope'], 'field': key, 'observed': value, 'path': observed['path'],
                                    'reason': 'new product technology has not been accepted'})
    for evidence in context.get('evidence', []):
        if evidence.get('source') == 'repository-established' and evidence.get('scope') in selected:
            accepted = context['scopes'][evidence['scope']]['technology']
            if any(compact(accepted.get(key, {}).get('value', '')) != compact(value) for key, value in evidence.get('technology', {}).items()):
                continue
            path = repo / evidence['path']
            if not path.is_file():
                differences.append({'scope': evidence['scope'], 'path': evidence['path'], 'reason': 'source missing'})
    return differences
