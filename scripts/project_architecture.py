"""Scope-owned Markdown architecture contracts. Never execute rule text."""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path
from scripts.project_context import ROOT, load_context, selected_scopes, relative, read
from scripts.state_store import atomic_json, repo_transaction

DIRECTORY = ROOT / 'architecture'
SECTIONS = ('Applicability', 'Directory tree', 'Responsibilities', 'Naming', 'Calls',
            'Dependencies', 'Data conversion', 'Module boundaries', 'Constraints', 'Tests', 'Exceptions')


def technology_token(value):
    if str(value).strip().lower() in {'c#', 'csharp', 'cs'}:
        return 'csharp'
    token = re.sub(r'[^a-z0-9]', '', str(value).lower())
    return {'ts': 'typescript', 'js': 'javascript'}.get(token, token)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def safe(repo, name):
    relative(repo, str(name))
    path = repo / name
    if any(p.is_symlink() for p in (path, *path.parents) if p != repo and repo in p.parents):
        raise ValueError('ARCHITECTURE_SYMLINK')
    return path


def parse(text, *, template=False):
    if len(text.encode()) > 128 * 1024:
        raise ValueError('ARCHITECTURE_TOO_LARGE')
    match = re.fullmatch(r'---\r?\n(.*?)\r?\n---\r?\n(.*)', text, re.S)
    if not match:
        raise ValueError('ARCHITECTURE_FRONTMATTER_REQUIRED')
    # JSON is a YAML 1.2 subset; YAML uses safe loading without references.
    try:
        try:
            meta = json.loads(match[1])
        except ValueError:
            try:
                import yaml
            except ImportError as exc:
                raise ValueError('ARCHITECTURE_YAML_PARSER_UNAVAILABLE: install declared project package dependencies or use JSON frontmatter') from exc
            # Reject aliases and tags: rule metadata is a small plain data tree.
            if re.search(r'(^|\s)[&*!][A-Za-z]', match[1]):
                raise ValueError('ARCHITECTURE_YAML_REFERENCE_UNSUPPORTED')
            try:
                meta = yaml.safe_load(match[1])
            except yaml.YAMLError as exc:
                raise ValueError('ARCHITECTURE_YAML_INVALID') from exc
        cao = meta['metadata']['cao']
        if not (re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', meta['name'])):
            raise ValueError('ARCHITECTURE_METADATA_INVALID')
        if not (isinstance(meta['description'], str) and meta['description'].strip()):
            raise ValueError('ARCHITECTURE_METADATA_INVALID')
        if not (type(cao['schema_version']) is int and cao['schema_version'] == 1 and type(cao['rule_version']) is int and cao['rule_version'] > 0):
            raise ValueError('ARCHITECTURE_METADATA_INVALID')
        if not (re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', cao['rule_id'])):
            raise ValueError('ARCHITECTURE_METADATA_INVALID')
        if not (isinstance(cao['stack'], dict) and isinstance(cao['style'], str) and cao['style'] and isinstance(cao.get('scope'), str) and cao['scope']):
            raise ValueError('ARCHITECTURE_METADATA_INVALID')
        if not (all(isinstance(v, str) for v in cao['stack'].values())):
            raise ValueError('ARCHITECTURE_METADATA_INVALID')
        if not (set(cao) <= {'schema_version', 'rule_id', 'rule_version', 'stack', 'style', 'scope', 'source_roots', 'test_roots', 'checks'}):
            raise ValueError('ARCHITECTURE_METADATA_INVALID')
        for key in ('source_roots', 'test_roots'):
            if not (isinstance(cao[key], list) and cao[key]):
                raise ValueError('ARCHITECTURE_METADATA_INVALID')
            for value in cao[key]:
                if not (isinstance(value, str) and value and not Path(value).is_absolute()):
                    raise ValueError('ARCHITECTURE_METADATA_INVALID')
                if not ('..' not in value.replace('\\', '/').split('/') and ':' not in value):
                    raise ValueError('ARCHITECTURE_METADATA_INVALID')
        check_ids = set()
        for check in cao.get('checks', []):
            if check.get('id') in check_ids:
                raise ValueError('ARCHITECTURE_DUPLICATE_CHECK_ID')
            check_ids.add(check.get('id'))
            if not (set(check) == {'id', 'kind', 'from', 'forbidden'}):
                raise ValueError('ARCHITECTURE_METADATA_INVALID')
            if not (check['kind'] in {'java-package-dependency', 'java-source-layout'}):
                raise ValueError('ARCHITECTURE_METADATA_INVALID')
            if not (all(isinstance(check[k], str) and check[k] for k in check)):
                raise ValueError('ARCHITECTURE_METADATA_INVALID')
    except (ValueError, KeyError, TypeError, AssertionError, AttributeError, RecursionError):
        raise ValueError('ARCHITECTURE_METADATA_INVALID') from None
    body = match[2]
    if any(not re.search(r'^## ' + re.escape(section) + r'\s*\n\S', body, re.M) for section in SECTIONS):
        raise ValueError('ARCHITECTURE_SECTIONS_REQUIRED')
    if '```text\n' not in body or not re.search(r'ARCH-[A-Z]+-\d+', body):
        raise ValueError('ARCHITECTURE_CONSTRAINTS_REQUIRED')
    if not template and re.search(r'\{\{.*?\}\}', text):
        raise ValueError('ARCHITECTURE_UNRESOLVED_VARIABLE')
    return cao


def template_dir():
    from scripts.cao_instructions import package_root_dir
    root = package_root_dir()
    if not root:
        raise ValueError('ARCHITECTURE_RESOURCES_UNAVAILABLE')
    return root / 'templates' / 'architecture'


def catalog(repo):
    rows = {}
    for base, origin in ((template_dir(), 'builtin'), (safe(repo, DIRECTORY / 'custom'), 'custom')):
        if not base.exists():
            continue
        for path in sorted(base.rglob('*.md')):
            if path.is_symlink() or path.stat().st_size > 128 * 1024:
                raise ValueError('ARCHITECTURE_UNSAFE_RESOURCE')
            if origin == 'custom':
                safe(repo, path.relative_to(repo))
            text = path.read_text(encoding='utf-8')
            meta = parse(text, template=True)
            rid = meta['rule_id']
            if rid in rows:
                raise ValueError('ARCHITECTURE_DUPLICATE_ID: ' + rid)
            rows[rid] = {'metadata': meta, 'origin': origin, 'path': str(path), 'text': text}
    return rows


def scope_file(scope):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', scope) or scope in {'.', '..'}:
        raise ValueError('ARCHITECTURE_SCOPE_INVALID')
    return DIRECTORY / ('SKILL.md' if scope == 'root' else scope + '/SKILL.md')


def receipt_path(scope):
    scope_file(scope)
    return DIRECTORY / 'accepted' / (scope + '.json')


def snapshot(repo, scopes):
    result = []
    for scope in sorted(set(scopes) | {'root'}):
        path = safe(repo, scope_file(scope))
        receipt = read(safe(repo, receipt_path(scope)))
        if not path.exists() and not receipt:
            continue
        if not path.is_file() or not receipt:
            raise ValueError('ARCHITECTURE_ACCEPTANCE_REQUIRED: ' + scope)
        text = path.read_text(encoding='utf-8')
        meta = validate_instance(repo, scope, text)
        if receipt.get('sha256') != sha(text):
            raise ValueError('ARCHITECTURE_RULE_STALE: ' + scope)
        result.append({'scope': scope, 'path': path.relative_to(repo).as_posix(),
                       'rule_id': meta['rule_id'], 'version': meta['rule_version'], 'sha256': sha(text)})
    return result


def state(repo, scopes=None, work=None, *, required=False):
    context = load_context(repo)
    if not context:
        return {'ready': False, 'reason': 'PROJECT_CONTEXT_REQUIRED', 'next': 'Run cao project inspect.'}
    scopes = selected_scopes(context, scopes, work)
    from scripts.cao_runtime import config
    required = required or (not work and config(repo).get('architecture_policy') == 1)
    try:
        rules = snapshot(repo, scopes)
        missing = [s for s in scopes if context['scopes'][s]['technology'].get('language', {}).get('status') != 'not_applicable' and not any(r['scope'] in {'root', s} for r in rules)]
        if required and missing:
            raise ValueError('ARCHITECTURE_REQUIRED: ' + ','.join(missing))
        expected = (work or {}).get('project_binding', {}).get('architecture')
        if work and expected is None and rules:
            raise ValueError('WORK_ARCHITECTURE_BINDING_REQUIRED')
        if expected is not None and expected != rules:
            raise ValueError('WORK_ARCHITECTURE_BINDING_STALE')
        return {'ready': True, 'rules': rules, 'missing_scopes': missing,
                'instruction': 'Read every applicable architecture Skill before planning, writes and review. Preserve dependency and data boundaries; semantic review belongs to the provider.'}
    except (ValueError, OSError) as exc:
        return {'ready': False, 'reason': str(exc).split(':')[0], 'detail': str(exc),
                'next': 'Run `cao project architecture list`; select the applicable style and collect missing package identity with `cao project setup identity`. Draft using accepted scope/identity paths, then validate/diff/apply. For changed rules inspect diff; for existing Work review and run `cao project bind --refresh --work <id>`, preserving progress.'}


def context_excerpt(repo, work):
    return json.dumps(state(repo, work=work), ensure_ascii=False)


def draft(repo, scope, template, variables):
    context = load_context(repo)
    if not context or scope not in context['scopes']:
        raise ValueError('PROJECT_SCOPE_REQUIRED')
    row = catalog(repo).get(template)
    if not row:
        raise ValueError('ARCHITECTURE_TEMPLATE_UNKNOWN')
    text = row['text']
    values = {'scope': scope, **variables}
    for key, value in values.items():
        if not re.fullmatch(r'[A-Za-z0-9_]+', key) or not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
            raise ValueError('ARCHITECTURE_VARIABLE_INVALID')
        text = text.replace('{{' + key + '}}', value)
    meta = parse(text)
    tech = context['scopes'][scope]['technology']
    normalize = technology_token
    for field, value in meta['stack'].items():
        actual = tech.get(field, {})
        if actual.get('status') == 'accepted' and normalize(str(actual['value'])) != normalize(value):
            raise ValueError('ARCHITECTURE_STACK_MISMATCH: ' + field)
    for root in meta['source_roots'] + meta['test_roots']:
        relative(repo, root)
        if not any(root == p or root.startswith(p.rstrip('/') + '/') or p == '.' for p in context['scopes'][scope]['paths']):
            raise ValueError('ARCHITECTURE_SCOPE_PATH_MISMATCH')
    path = safe(repo, ROOT / 'architecture-inputs' / (scope + '.md'))
    if path.exists():
        raise ValueError('ARCHITECTURE_DRAFT_EXISTS: preserve/edit existing input')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(text)
    return {'path': str(path.relative_to(repo)), 'source': row['origin'], 'sha256': sha(text)}


def validate_instance(repo, scope, text):
    meta = parse(text)
    context = load_context(repo)
    if not context or scope not in context['scopes'] or meta.get('scope') != scope:
        raise ValueError('ARCHITECTURE_SCOPE_MISMATCH')
    normalize = technology_token
    for field, value in meta['stack'].items():
        actual = context['scopes'][scope]['technology'].get(field, {})
        if actual.get('status') == 'accepted' and normalize(actual['value']) != normalize(value):
            raise ValueError('ARCHITECTURE_STACK_MISMATCH: ' + field)
    for root in meta['source_roots'] + meta['test_roots']:
        safe(repo, Path(root))
        if not any(p == '.' or root == p or root.startswith(p.rstrip('/') + '/') for p in context['scopes'][scope]['paths']):
            raise ValueError('ARCHITECTURE_SCOPE_PATH_MISMATCH')
    return meta


def writable_input(repo, target):
    try:
        path = Path(target)
        if path.is_absolute():
            path = path.relative_to(repo)
        safe(repo, path)
        return (path.parent == ROOT / 'architecture-inputs' and path.suffix == '.md') or (
            path.is_relative_to(DIRECTORY / 'custom') and path.suffix == '.md')
    except (ValueError, OSError):
        return False


def accept(repo, scope, evidence, expected, delegated=False):
    from scripts.project_sources import user_evidence
    if not evidence:
        raise ValueError('ARCHITECTURE_EVIDENCE_REQUIRED')
    with repo_transaction(repo):
        path = safe(repo, scope_file(scope))
        old = path.read_text(encoding='utf-8') if path.exists() else ''
        if sha(old) != expected:
            raise ValueError('ARCHITECTURE_REVISION_CONFLICT')
        text = safe(repo, ROOT / 'architecture-inputs' / (scope + '.md')).read_text(encoding='utf-8')
        meta = validate_instance(repo, scope, text)
        user_evidence(repo, evidence, {'architecture': meta['style'] if delegated else meta['rule_id']}, delegated)
        if meta['scope'] != scope:
            raise ValueError('ARCHITECTURE_SCOPE_MISMATCH')
        path.parent.mkdir(parents=True, exist_ok=True)
        import os, tempfile
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        atomic_json(safe(repo, receipt_path(scope)), {'sha256': sha(text), 'evidence': evidence})
    return {'status': 'accepted', 'sha256': sha(text), 'path': str(path.relative_to(repo))}


def check(repo, scopes):
    rows = snapshot(repo, scopes)
    results = []
    for row in rows:
        meta = parse(safe(repo, Path(row['path'])).read_text())
        for rule in meta.get('checks', []):
            failures = []
            inspected = 0
            for root in meta['source_roots']:
                source = safe(repo, Path(root))
                for file in source.rglob('*.java') if source.exists() else []:
                    safe(repo, file.relative_to(repo))
                    inspected += 1
                    if rule['kind'] == 'java-source-layout':
                        if file.parent == source and file.name not in {'Application.java', 'Main.java', 'package-info.java', 'module-info.java'} and not file.name.endswith('Application.java'):
                            failures.append({'path': str(file.relative_to(repo)), 'dependency': 'Business classes must belong to feature/layer packages.'})
                        continue
                    # Strip comments/string literals before recognizing Java package/import tokens.
                    text = re.sub(r'""".*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*', ' ', file.read_text(), flags=re.S)
                    package = re.search(r'\bpackage\s+([\w.]+)\s*;', text)
                    if package and rule['from'] in package[1].split('.'):
                        for target in re.findall(r'\bimport\s+(?:static\s+)?([\w.*]+)\s*;', text):
                            if rule['forbidden'] in target.split('.'):
                                failures.append({'path': str(file.relative_to(repo)), 'dependency': target})
            results.append({'rule_id': rule['id'], 'status': 'failed' if failures else 'passed' if inspected else 'not_applicable', 'violations': failures, 'inspected_files': inspected,
                            'coverage': 'Java direct source-root files only.' if rule['kind'] == 'java-source-layout' else 'Java explicit imports only; fully qualified references, reflection and semantic boundaries require provider review.'})
        results.append({'rule_id': row['rule_id'] + ':semantic', 'status': 'unsupported',
                        'next': 'Provider review must read this Skill and assess responsibilities, module boundaries and data conversion.'})
    return {'ready': not any(r['status'] == 'failed' for r in results), 'results': results,
            'semantic_review_required': True}


def dispatch(args):
    from scripts.cao_runtime import repo_root
    repo = repo_root(args.repo)
    action = args.architecture_action
    if action == 'list':
        result = {key: {k: v for k, v in row.items() if k != 'text'} for key, row in catalog(repo).items()}
    elif action == 'inspect':
        context = load_context(repo)
        if not context or args.scope not in context['scopes']:
            raise ValueError('PROJECT_SCOPE_REQUIRED')
        from scripts.project_inspection import inspect
        result = {'architecture': state(repo, [args.scope]),
                  'product': inspect(repo, {args.scope: context['scopes'][args.scope]}),
                  'templates': {key: row['metadata'] for key, row in catalog(repo).items()},
                  'next': 'Preserve observed product structure. Choose an explicitly accepted style; generic-custom supports existing and unlisted stacks.'}
    elif action == 'draft':
        result = draft(repo, args.scope, args.template, dict(v.split('=', 1) for v in args.variable))
    elif action == 'apply':
        result = accept(repo, args.scope, args.evidence, args.expected_sha256, args.source == 'user-delegated')
    elif action == 'diff':
        path = safe(repo, scope_file(args.scope))
        old = path.read_text() if path.exists() else ''
        new = safe(repo, ROOT / 'architecture-inputs' / (args.scope + '.md')).read_text()
        result = {'expected_sha256': sha(old), 'diff': ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True)))}
    elif action == 'validate':
        path = safe(repo, Path(args.path)) if args.path else safe(repo, ROOT / 'architecture-inputs' / (args.scope + '.md'))
        result = {'valid': True, 'metadata': parse(path.read_text(encoding='utf-8'))}
    elif action == 'check':
        result = check(repo, [args.scope])
    else:
        result = state(repo, [args.scope], required=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get('ready') is False else 0


def register(actions):
    commands = actions.add_parser('architecture', help='Scope-owned architecture Skills').add_subparsers(dest='architecture_action', required=True)
    for name in ('list', 'inspect', 'show', 'draft', 'validate', 'diff', 'apply', 'check'):
        p = commands.add_parser(name)
        p.add_argument('--repo', default='.')
        p.add_argument('--scope', default='root')
        p.set_defaults(fn=dispatch)
        if name == 'draft':
            p.add_argument('--template', required=True)
            p.add_argument('--variable', action='append', default=[])
        if name == 'validate':
            p.add_argument('--path')
        if name == 'apply':
            p.add_argument('--evidence', required=True)
            p.add_argument('--expected-sha256', required=True)
            p.add_argument('--source', choices=['user-specified', 'user-delegated'], default='user-specified')
