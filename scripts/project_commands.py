"""Project setup commands, background bindings and explicit migration ownership."""
import json
import re
from copy import deepcopy

from scripts.cao_runtime import config, ensure_initialized, locked_command, repo_root
from scripts.cao_work_store import load_index, load_work, queue_event, save_work
from scripts.project_context import (FIELDS, ROOT, binding_state, commit_context, context_state,
                                     digest, fingerprint, load_context, make_binding, read, relative)
from scripts.project_inspection import drift, save_observation
from scripts.project_sources import compact, user_evidence
from scripts.project_technology import category_conflicts, validate_categories
from scripts.state_store import atomic_json


def emit(value):
    print(json.dumps({'ok': True, **value}, ensure_ascii=False, indent=2))
    return 0


def active(repo, wid=None):
    return load_work(repo, wid or load_index(repo).get('active_work'))


def fields(args):
    result = {key: getattr(args, key) for key in FIELDS if getattr(args, key, None) is not None}
    for key in getattr(args, 'not_applicable', None) or []:
        if key in result:
            raise ValueError('PROJECT_FIELD_CONFLICT: ' + key)
        result[key] = 'not_applicable'
    if not result:
        raise ValueError('PROJECT_FIELDS_REQUIRED')
    return result


def evidence_for(repo, args, values, observed):
    source = args.source
    if source in {'user-specified', 'user-delegated'}:
        return user_evidence(repo, args.evidence, values, source == 'user-delegated')
    matches = [item for item in observed['observations'] if item['scope'] == args.scope
               and item['path'] == args.evidence and item['established']
               and all(compact(item['technology'].get(key)) == compact(value) for key, value in values.items())]
    if not matches:
        raise ValueError('PROJECT_REPOSITORY_SOURCE_INVALID: inspect existing product manifests and accept only observed fields')
    return {'source': source, 'scope': args.scope, 'path': matches[0]['path'],
            'sha256': matches[0]['sha256'], 'technology': matches[0]['technology'], 'covered_fields': sorted(values)}


def candidate_for(repo, args, context, allow_change=False):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.scope):
        raise ValueError("PROJECT_SCOPE_INVALID")
    if args.expected_revision and args.expected_revision != (context or {}).get('revision'):
        raise ValueError('PROJECT_CONTEXT_REVISION_CONFLICT')
    values = fields(args)
    validate_categories(values, args.scope + ".")
    candidate = deepcopy(context or {'schema_version': 1, 'scopes': {}, 'evidence': [], 'active_changes': []})
    scope = candidate['scopes'].setdefault(args.scope, {'paths': ['.'] if args.scope == 'root' else [], 'technology': {}})
    requested_paths = getattr(args, 'path', None)
    if requested_paths:
        paths = sorted({relative(repo, path) for path in requested_paths})
        if context and args.scope in context['scopes'] and scope['paths'] and scope['paths'] != paths and not allow_change:
            raise ValueError('PROJECT_CHANGE_REQUIRED: scope paths changed')
        scope['paths'] = paths
    if not scope['paths']:
        raise ValueError('PROJECT_SCOPE_PATH_REQUIRED')
    observed = save_observation(repo, candidate, active(repo))
    evidence = evidence_for(repo, args, values, observed)
    for key, value in values.items():
        old = scope['technology'].get(key, {})
        wrong_category = bool(category_conflicts({key: old.get('value')}))
        if old.get('status') in {'accepted', 'not_applicable'} and compact(old.get('value')) != compact(value) and not allow_change and not wrong_category:
            raise ValueError('PROJECT_CHANGE_REQUIRED: ' + args.scope + '.' + key)
        if old.get('status') == 'accepted' and compact(old.get('value')) == compact(value):
            value = old['value']
        scope['technology'][key] = {'value': value, 'status': 'not_applicable' if value == 'not_applicable' else 'accepted'}
    for entry in getattr(args, 'version', None) or []:
        key, separator, value = entry.partition('=')
        if not separator or key not in FIELDS or not value.strip():
            raise ValueError('PROJECT_VERSION_INVALID: use --version language=21')
        if args.source not in {'user-specified', 'user-delegated'}:
            raise ValueError('PROJECT_VERSION_SOURCE_REQUIRED')
        user_evidence(repo, args.evidence, {key: value.strip()}, args.source == 'user-delegated')
        versions = scope.setdefault('versions', {})
        if key in versions and versions[key] != value.strip() and not allow_change:
            raise ValueError('PROJECT_CHANGE_REQUIRED: version ' + key)
        versions[key] = value.strip()
    if evidence not in candidate['evidence']:
        candidate['evidence'].append(evidence)
    return candidate


def queue_project_event(repo, work, event, data):
    if work:
        queue_event(work, {'event': event, 'source': 'cao', 'work_item_id': work['id'], **data})
        save_work(repo, work)


@locked_command
def cmd_project(args):
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    config(repo)
    context = load_context(repo)
    command = args.project_action
    work = active(repo, getattr(args, 'work', None))
    if command == 'inspect':
        from scripts.startup_request import pending_request
        from scripts.project_sources import observe_prompt
        pending = pending_request(repo)
        if pending:
            observe_prompt(repo, pending.get('prompt'), pending.get('host'), pending.get('session_id'))
        observed = save_observation(repo, context, work)
        evidence = [read(path) for path in sorted((repo / ROOT / 'evidence').glob('user-*.json'))]
        return emit({'project_context': context_state(repo, work=work), 'inspection': observed, 'user_evidence': evidence,
                     'next': 'Accept only observed product facts or actual user choices using `cao project context set`; do not create a temporary Work.'})
    if command == 'show':
        return emit({'context': context, 'state': context_state(repo, work=work)})
    if command == 'diff':
        return emit({'differences': drift(repo, context, list(context['scopes']), work) if context else [],
                     'context': context_state(repo, work=work)})
    if command == 'decisions':
        # Only ancestors linked from the commit pointer are accepted history.
        revisions, cursor, seen = [], context, set()
        while cursor:
            if cursor["revision"] in seen or len(seen) > 10000:
                raise ValueError("PROJECT_HISTORY_INVALID")
            seen.add(cursor["revision"])
            revisions.append(cursor)
            previous = cursor.get('previous_revision')
            cursor = read(repo / ROOT / 'revisions' / (previous + '.json')) if previous else None
        return emit({'revisions': revisions})
    if command == 'set':
        candidate = candidate_for(repo, args, context)
        if candidate == context or (context and {k: v for k, v in candidate.items() if k != 'previous_revision'} ==
                                   {k: v for k, v in context.items() if k != 'previous_revision'}):
            return emit({'status': 'unchanged', 'context': context})
        if context:
            candidate['previous_revision'] = context['revision']
        saved = commit_context(repo, candidate, args.expected_revision or (context or {}).get('revision'))
        return emit({'status': 'saved', 'context': saved, 'state': context_state(repo, work=work)})
    if command == 'bind':
        if not work or work.get('status') not in {'active', 'suspended', 'todo'}:
            raise ValueError('PROJECT_BIND_ACTIVE_OR_PAUSED_WORK_REQUIRED')
        scopes = args.scope or (work.get('project_binding') or {}).get('scopes')
        state = context_state(repo, scopes, work, check_sources=True)
        if not state['ready']:
            return emit({'ok': False, **state}) or 2
        binding = make_binding(repo, scopes)
        previous = work.get('project_binding')
        if previous and previous['fingerprint'] != binding['fingerprint'] and not args.refresh:
            raise ValueError('WORK_PROJECT_BINDING_STALE: review and use --refresh')
        if previous == binding:
            return emit({'status': 'unchanged', 'project_binding': binding})
        work['project_binding'] = binding
        # The original runs remain untouched. Different input formats are stale.
        queue_project_event(repo, work, 'project.binding.updated', {'previous': previous, 'binding': binding})
        return emit({'project_binding': binding, 'note': 'Progress and original runs retained; refresh applicable evidence before closure.'})
    if command == 'propose':
        if not context:
            raise ValueError('PROJECT_CONTEXT_REQUIRED')
        target = candidate_for(repo, args, context, allow_change=True)
        if fingerprint(target) == fingerprint(context):
            raise ValueError('PROJECT_CHANGE_NO_DIFFERENCE')
        change = {'version': 1, 'status': 'proposed', 'base_revision': context['revision'],
                  'scopes': [args.scope], 'target': target}
        identity = 'change-' + digest(change)
        change['id'] = identity
        atomic_json(repo / ROOT / 'changes' / (identity + '.json'), change)
        return emit({'change': change, 'next': 'Create or select a migration Work using the stable background, then `cao project context change apply --change ' + identity + ' --work <id>`.'})
    if command == 'apply':
        identity = args.change
        if not identity.startswith('change-') or len(identity) != 71 or any(c not in '0123456789abcdef' for c in identity[7:]):
            raise ValueError('PROJECT_CHANGE_INVALID')
        change_path = repo / ROOT / 'changes' / (identity + '.json')
        change = read(change_path)
        if not change or not work or work.get('status') != 'active':
            raise ValueError('PROJECT_CHANGE_OWNER_REQUIRED')
        if context and identity in context.get('active_changes', []):
            if change.get('owner_work') != work['id']:
                raise ValueError('PROJECT_CHANGE_OWNER_CONFLICT')
        else:
            if not context or context['revision'] != change['base_revision']:
                raise ValueError('PROJECT_CONTEXT_REVISION_CONFLICT')
            if context.get('active_changes'):
                raise ValueError('PROJECT_CONTEXT_CHANGE_IN_PROGRESS')
            if not work.get('project_binding') or not binding_state(repo, work)['ready']:
                raise ValueError('WORK_PROJECT_BINDING_REQUIRED')
            change.update(status='applying', owner_work=work['id'])
            atomic_json(change_path, change)
            candidate = deepcopy(change['target'])
            candidate['active_changes'] = [identity]
            candidate['previous_revision'] = context['revision']
            context = commit_context(repo, candidate, context['revision'])
        work['project_binding'] = make_binding(repo, work['project_binding']['scopes'])
        work['project_change'] = identity
        queue_project_event(repo, work, 'project.change.applied', {'change_id': identity, 'binding': work['project_binding']})
        return emit({'context': context, 'change': change,
                     'next': 'Migrate the product and actual verification entries; review/reanchor standards, then obtain both acceptance results before done.'})
    raise ValueError('PROJECT_ACTION_INVALID')


def finish_change(repo, work):
    identity = work.get('project_change')
    if not identity:
        return
    context = load_context(repo)
    if identity not in context.get('active_changes', []):
        return
    change = read(repo / ROOT / 'changes' / (identity + '.json'))
    if change.get('owner_work') != work['id']:
        raise ValueError('PROJECT_CHANGE_OWNER_CONFLICT')
    candidate = deepcopy(context)
    candidate['active_changes'].remove(identity)
    candidate['previous_revision'] = context['revision']
    commit_context(repo, candidate, context['revision'])
    change['status'] = 'completed'
    atomic_json(repo / ROOT / 'changes' / (identity + '.json'), change)


def register_parser(sub):
    project = sub.add_parser('project', help='Project background, independent of Work decisions')
    actions = project.add_subparsers(required=True)
    from scripts.project_setup import register
    register(actions)
    for name in ('inspect', 'decisions'):
        parser = actions.add_parser(name)
        parser.add_argument('--repo', default='.')
        parser.set_defaults(fn=cmd_project, project_action=name)
    parser = actions.add_parser('bind', help='Attach background to an existing Work without resetting progress')
    parser.add_argument('--repo', default='.')
    parser.add_argument('--work')
    parser.add_argument('--scope', action='append')
    parser.add_argument('--refresh', action='store_true')
    parser.set_defaults(fn=cmd_project, project_action='bind')
    context = actions.add_parser('context').add_subparsers(required=True)
    for name in ('show', 'diff', 'set'):
        parser = context.add_parser(name)
        parser.add_argument('--repo', default='.')
        parser.set_defaults(fn=cmd_project, project_action=name)
        if name == 'set':
            _choice_args(parser)
    changes = context.add_parser('change').add_subparsers(required=True)
    parser = changes.add_parser('propose')
    parser.add_argument('--repo', default='.')
    _choice_args(parser)
    parser.set_defaults(fn=cmd_project, project_action='propose')
    parser = changes.add_parser('apply')
    parser.add_argument('--repo', default='.')
    parser.add_argument('--change', required=True)
    parser.add_argument('--work', required=True)
    parser.set_defaults(fn=cmd_project, project_action='apply')


def _choice_args(parser):
    parser.add_argument('--scope', default='root')
    parser.add_argument('--path', action='append', help='Explicit module path; repeat for multiple roots')
    for key in FIELDS:
        parser.add_argument('--' + key.replace('_', '-'))
    parser.add_argument('--not-applicable', action='append', choices=FIELDS)
    parser.add_argument('--source', required=True, choices=('user-specified', 'repository-established', 'user-delegated'))
    parser.add_argument('--evidence', required=True, help='Observed user evidence ID or established manifest path from project inspect')
    parser.add_argument('--version', action='append', help='Explicit version, e.g. language=21; no latest defaults')
    parser.add_argument('--expected-revision', help='Compare-and-swap revision; default is the revision read under the repository lock')


def error_context(args, code):
    """Make project setup failures recoverable without guessing state mutations."""
    instruction = 'Run `cao project inspect` and `cao project context show`; correct only the reported fields and retry the project command.'
    kind = 'agent'
    if code == 'PROJECT_CONTEXT_REVISION_CONFLICT':
        instruction = 'Run `cao project context show` and `cao project context diff`; review the concurrent update and retry with its accepted --expected-revision. Do not overwrite it.'
    elif code == 'PROJECT_FIELD_CATEGORY_MISMATCH':
        instruction = 'Move the known technology to diagnostics.expected_field using its observed evidence. Ask only for the still-missing choice; a framework name does not authorize a build tool. Retry the corrected project context set command.'
    elif code.startswith(('PROJECT_SOURCE', 'PROJECT_DELEGATION', 'PROJECT_REPOSITORY_SOURCE')):
        kind = 'human'
        instruction = 'Run `cao project inspect` for actual host-observed user evidence or established product paths. Restore any missing explicit user choice through the host; source labels and Agent-authored files do not authorize choices.'
    elif code == 'PROJECT_CHANGE_REQUIRED':
        instruction = 'Inspect `cao project context diff`; use `cao project context change propose` and apply the authorized change to its migration Work.'
    elif code in {'PROJECT_CONTEXT_INVALID', 'PROJECT_STATE_INVALID', 'PROJECT_REVISION_CORRUPT'}:
        kind = 'human'
        instruction = 'Restore the complete committed context and matching immutable revision from a known backup. Preserve current files; do not use init --force or default empty state.'
    elif code == 'PROJECT_VERIFICATION_CONTEXT_MISMATCH':
        instruction = 'Restore actual verification entries matching the accepted project runners; configure and review/reanchor the changed baseline before continuing.'
    result = {'reason_code': code, 'scope': getattr(args, 'scope', None),
              'next': {'type': kind, 'stage': 'project_setup', 'instruction': instruction}}
    if code == 'PROJECT_CONTEXT_REVISION_CONFLICT':
        try:
            current = load_context(repo_root(args.repo))
            result['current_revision'] = (current or {}).get('revision')
        except (ValueError, OSError):
            pass
    return result
