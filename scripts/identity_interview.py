"""Durable field-by-field project identity intake, without creating Work."""
import re
from scripts.project_context import load_context, read
from scripts.project_sources import user_evidence
from scripts.project_setup import scope_path
from scripts.state_store import atomic_json


def path(repo, scope):
    scope_path(repo, scope)
    result = repo / '.orchestrator/project/identity' / (scope + '.json')
    if result.is_symlink() or result.parent.is_symlink():
        raise ValueError('PROJECT_IDENTITY_SYMLINK')
    return result


def collect(repo, args):
    from scripts.setup_rules import describe
    context = load_context(repo)
    if not context or args.scope not in context['scopes']:
        raise ValueError('PROJECT_CONTEXT_REQUIRED')
    framework = context['scopes'][args.scope]['technology'].get('framework', {}).get('value')
    spec = describe(args.rule, framework=framework) if args.rule != 'explicit' else {}
    location = path(repo, args.scope)
    existing = read(location)
    fields = spec['fields'] if args.rule != 'explicit' else args.fields or (existing or {}).get('fields')
    if not fields or len(set(fields)) != len(fields) or any(not re.fullmatch(r'[a-z][a-z0-9_]*', f) for f in fields):
        raise ValueError('PROJECT_IDENTITY_FIELDS_REQUIRED')
    record = existing or {'rule': args.rule, 'fields': fields, 'values': {}, 'sources': {}}
    if record['rule'] == args.rule and record['fields'] == fields[:len(record['fields'])]:
        record['fields'] = fields  # New framework fields never erase earlier answers.
    if record['rule'] != args.rule or record['fields'] != fields:
        raise ValueError('PROJECT_IDENTITY_RULE_CONFLICT')
    previous = context['scopes'][args.scope].get('identity') or {}
    for field in fields:
        if field not in record['values'] and field in previous.get('values', {}):
            record['values'][field] = previous['values'][field]
            record['sources'][field] = previous['sources'][field]
    version = context['scopes'][args.scope].get('versions', {}).get('framework')
    if 'framework_version' in fields and version and record['values'].get('framework_version') != version:
        for proof in reversed(context['evidence']):
            if proof.get('scope', args.scope) != args.scope:
                continue
            try:
                evidence = user_evidence(repo, proof.get('id'), {'framework_version': version}, proof.get('source') == 'user-delegated')
            except ValueError:
                continue
            record['values']['framework_version'] = version
            record['sources']['framework_version'] = {'id': evidence['id'], 'source': evidence['source']}
            break
        else:
            if 'framework_version' in record['values']:
                raise ValueError('PROJECT_SOURCE_REQUIRED: restore evidence for the current confirmed framework version; do not replay an older identity answer')
    if args.field is not None:
        if args.field not in fields or not args.value or not args.value.strip():
            raise ValueError('PROJECT_IDENTITY_FIELD_INVALID')
        evidence = user_evidence(repo, args.evidence, {args.field: args.value})
        if args.rule != 'explicit':
            from scripts.setup_rules import validate_identity
            validate_identity(args.rule, {args.field: args.value}, partial=True, framework=framework)
        from scripts.project_refinement import sync_identity
        sync_identity(repo, args.scope, args.rule, args.field, args.value, evidence)
        record['values'][args.field] = args.value
        record['sources'][args.field] = {'id': evidence['id'], 'source': 'user-specified'}
    elif args.value is not None or args.evidence is not None:
        raise ValueError('PROJECT_IDENTITY_FIELD_REQUIRED')
    # Recheck saved provenance on resume. A new host/session never erases answers.
    for field, value in record['values'].items():
        source = record['sources'][field]
        evidence = user_evidence(repo, source['id'], {field: value}, source['source'] == 'user-delegated')
        from scripts.project_refinement import sync_identity
        sync_identity(repo, args.scope, args.rule, field, value, evidence)
    atomic_json(location, record)
    missing = [f for f in fields if f not in record['values']]
    guidance = spec.get('field_guidance', {})
    return {**record, 'ready': not missing, 'missing_fields': missing,
            'next_field': missing[0] if missing else None,
            'field_guidance': guidance,
            'next': ('Ask the user to enter only ' + missing[0] + '; wait, then save this answer with project setup identity --field/--value/--evidence. Do not invent defaults or ask for JSON.'
                     + ' ' + guidance.get(missing[0], '') if missing else 'All identity answers saved. Run project setup draft; reuse these per-field sources.')}
