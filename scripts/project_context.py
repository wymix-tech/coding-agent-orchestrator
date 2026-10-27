"""Versioned project facts. Current context is the only commit pointer.

Revisions are written before the pointer. Orphan revisions are never adopted;
the pointer contains the complete revision, so audit can be recovered from it.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

from scripts.state_store import atomic_json, repo_transaction

ROOT = Path('.orchestrator/project')
FIELDS = ('language', 'framework', 'build_tool', 'test_framework', 'storage', 'delivery', 'package_manager', 'runtime')
REQUIRED = ('language', 'build_tool', 'test_framework')
STATES = {'accepted', 'pending', 'conflict', 'not_applicable'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def read(path, default=None):
    if not path.exists():
        return deepcopy(default)
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('PROJECT_STATE_TOO_LARGE')
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('PROJECT_STATE_INVALID: ' + str(path)) from exc


def relative(repo, value):
    value = str(value).replace('\\', '/')
    if not value or Path(value).is_absolute() or '..' in value.split('/') or ':' in value:
        raise ValueError('PROJECT_SCOPE_INVALID')
    if not (repo / value).resolve().is_relative_to(repo.resolve()):
        raise ValueError('PROJECT_SCOPE_INVALID')
    return Path(value).as_posix()


def semantic(context, scopes=None):
    from scripts.project_sources import compact
    selected = set(scopes or context.get('scopes', {}))
    # Root constraints apply to every explicit module.
    if 'root' in context.get('scopes', {}):
        selected.add('root')
    result = {}
    for name in sorted(selected):
        scope = context.get('scopes', {}).get(name)
        if scope is None:
            raise ValueError('PROJECT_SCOPE_REQUIRED: ' + name)
        result[name] = {'paths': sorted(scope['paths']),
                        'technology': {key: {'value': compact(item.get('value')), 'status': item['status']}
                                       for key, item in sorted(scope['technology'].items())},
                        'versions': scope.get('versions', {}),
                        **({'identity': {key: value for key, value in scope['identity'].items() if key != 'sources'}} if scope.get('identity') else {})}
    return result


def fingerprint(context, scopes=None):
    return digest(semantic(context, scopes))


def load_context(repo):
    value = read(repo / ROOT / 'context.json')
    if value is None:
        return None
    try:
        if value['schema_version'] != 1 or not isinstance(value['scopes'], dict) or not value['scopes']:
            raise ValueError()
        if not isinstance(value.get('evidence'), list) or not isinstance(value.get('active_changes', []), list):
            raise ValueError()
        previous = value.get('previous_revision')
        if previous and not re.fullmatch(r'ctx-[a-f0-9]{64}', previous):
            raise ValueError()
        for name, scope in value['scopes'].items():
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or not scope['paths']:
                raise ValueError()
            for path in scope['paths']:
                relative(repo, path)
            for key, item in scope['technology'].items():
                if key not in FIELDS or item['status'] not in STATES:
                    raise ValueError()
                if item['status'] == 'accepted' and not item.get('value'):
                    raise ValueError()
        if value['content_sha256'] != fingerprint(value):
            raise ValueError()
        revision = value['revision']
        if not re.fullmatch(r'ctx-[a-f0-9]{64}', revision):
            raise ValueError()
        saved = read(repo / ROOT / 'revisions' / (revision + '.json'))
        if saved != value:
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError('PROJECT_CONTEXT_INVALID: restore the committed context and revision; do not initialize over them') from exc
    return value


def commit_context(repo, candidate, expected_revision):
    with repo_transaction(repo):
        current = load_context(repo)
        actual = current['revision'] if current else None
        if expected_revision != actual:
            raise ValueError('PROJECT_CONTEXT_REVISION_CONFLICT: current=' + str(actual))
        candidate = deepcopy(candidate)
        candidate.pop('revision', None)
        candidate['schema_version'] = 1
        candidate['content_sha256'] = fingerprint(candidate)
        revision = 'ctx-' + digest(candidate)
        candidate['revision'] = revision
        path = repo / ROOT / 'revisions' / (revision + '.json')
        if path.exists() and read(path) != candidate:
            raise ValueError('PROJECT_REVISION_CORRUPT')
        atomic_json(path, candidate)
        atomic_json(repo / ROOT / 'context.json', candidate)
        return candidate


def selected_scopes(context, requested=None, work=None):
    if requested:
        scopes = sorted(set(requested))
    elif work and work.get('project_binding'):
        scopes = work['project_binding']['scopes']
    else:
        names = set(context.get('scopes', {})) - {'root'}
        if len(names) > 1:
            raise ValueError('PROJECT_SCOPE_REQUIRED')
        scopes = sorted(names or {'root'})
    semantic(context, scopes)
    return scopes


def context_state(repo, scopes=None, work=None, check_sources=False):
    from scripts.project_technology import category_conflicts
    context = load_context(repo)
    if not context:
        return {'ready': False, 'reason': 'PROJECT_CONTEXT_REQUIRED', 'missing_fields': ['root.' + key for key in REQUIRED],
                'next': 'Run `cao project inspect`; preserve explicit user choices, confirm only missing fields, then `cao project context set --help`.'}
    try:
        scopes = selected_scopes(context, scopes, work)
    except ValueError:
        return {'ready': False, 'reason': 'PROJECT_SCOPE_REQUIRED', 'scopes': list(context['scopes']),
                'next': 'Choose the affected module with --scope; do not reselect the project stack.'}
    missing, conflicts = [], []
    category_errors = []
    for name, scope in semantic(context, scopes).items():
        # A root containing only common constraints does not need its own stack.
        keys = REQUIRED if name in scopes else ()
        for key in keys:
            if scope['technology'].get(key, {}).get('status') not in {'accepted', 'not_applicable'}:
                missing.append(name + '.' + key)
        conflicts += [name + '.' + key for key, item in scope['technology'].items() if item['status'] == 'conflict']
        category_errors.extend(category_conflicts({key: item['value'] for key, item in scope['technology'].items()
                                                  if item['status'] == 'accepted'}, name + '.'))
    conflicts += [item['field'] for item in category_errors]
    changes = []
    for change_id in context.get('active_changes', []):
        change = read(repo / ROOT / 'changes' / (change_id + '.json'))
        if change and (set(change['scopes']) & set(scopes) or 'root' in change['scopes']):
            if not work or work.get('id') != change['owner_work']:
                changes.append(change_id)
    state = {'ready': not (missing or conflicts or changes), 'reason': None, 'revision': context['revision'],
             'scopes': scopes, 'fingerprint': fingerprint(context, scopes), 'missing_fields': missing, 'conflicts': conflicts,
             'technology': {name: context['scopes'][name] for name in sorted({*scopes, 'root'} & set(context['scopes']))}}
    if changes:
        state.update(reason='PROJECT_CONTEXT_CHANGE_IN_PROGRESS', changes=changes,
                     next='Resume the owner migration Work; conflicting tasks must wait.')
    elif conflicts:
        state.update(reason='PROJECT_CONTEXT_CONFLICT', next='Run `cao project context diff`; resolve the conflicting sources explicitly.')
        if category_errors:
            state.update(diagnostics=category_errors,
                         next='Correct the miscategorized fields with actual user evidence using `cao project context set`, then review and refresh any affected Work project binding. Do not infer a build tool from a framework.')
    elif missing:
        state.update(reason='PROJECT_CONTEXT_REQUIRED', next='Confirm only missing fields and save them with `cao project context set`.')
    if check_sources and state['ready']:
        from scripts.project_inspection import drift
        differences = drift(repo, context, scopes, work)
        if differences:
            state.update(ready=False, reason='PROJECT_CONTEXT_CONFLICT', differences=differences,
                         next='Run `cao project inspect` and `cao project context diff`; use an authorized project change for material replacements.')
            from scripts.project_recovery import context_recovery
            recovery = context_recovery(repo, state)
            state.update(recovery=recovery, diagnostics=recovery['diagnostics'], next=recovery['instruction'])
    return state


def make_binding(repo, scopes=None):
    context = load_context(repo)
    if not context:
        raise ValueError('PROJECT_CONTEXT_REQUIRED')
    scopes = selected_scopes(context, scopes)
    return {'version': 1, 'revision': context['revision'], 'scopes': scopes,
            'fingerprint': fingerprint(context, scopes)}


def binding_state(repo, work):
    state = context_state(repo, work=work, check_sources=True)
    if not state['ready']:
        return state
    binding = (work or {}).get('project_binding')
    if work and not binding:
        return {**state, 'ready': False, 'reason': 'WORK_PROJECT_BINDING_REQUIRED',
                'next': 'Run `cao project bind --work ' + work['id'] + '` to attach the current background without resetting progress; old results are not recertified.'}
    if binding and binding['fingerprint'] != state['fingerprint']:
        return {**state, 'ready': False, 'reason': 'WORK_PROJECT_BINDING_STALE',
                'next': 'Review affected project fields and verification baseline, then `cao project bind --work ' + work['id'] + ' --refresh`; refresh related evidence.'}
    return state
