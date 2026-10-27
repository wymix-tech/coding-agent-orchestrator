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
    fields = describe(args.rule)['fields'] if args.rule != 'explicit' else args.fields
    if not fields or len(set(fields)) != len(fields) or any(not re.fullmatch(r'[a-z][a-z0-9_]*', f) for f in fields):
        raise ValueError('PROJECT_IDENTITY_FIELDS_REQUIRED')
    location = path(repo, args.scope)
    record = read(location) or {'rule': args.rule, 'fields': fields, 'values': {}, 'sources': {}}
    if record['rule'] != args.rule or record['fields'] != fields:
        raise ValueError('PROJECT_IDENTITY_RULE_CONFLICT')
    previous = context['scopes'][args.scope].get('identity') or {}
    for field in fields:
        if field not in record['values'] and field in previous.get('values', {}):
            record['values'][field] = previous['values'][field]
            record['sources'][field] = previous['sources'][field]
    if args.field is not None:
        if args.field not in fields or not args.value or not args.value.strip():
            raise ValueError('PROJECT_IDENTITY_FIELD_INVALID')
        evidence = user_evidence(repo, args.evidence, {args.field: args.value})
        if args.rule != 'explicit':
            from scripts.setup_rules import validate_identity
            validate_identity(args.rule, {args.field: args.value}, partial=True)
        record['values'][args.field] = args.value
        record['sources'][args.field] = {'id': evidence['id'], 'source': 'user-specified'}
    elif args.value is not None or args.evidence is not None:
        raise ValueError('PROJECT_IDENTITY_FIELD_REQUIRED')
    # Recheck saved provenance on resume. A new host/session never erases answers.
    for field, value in record['values'].items():
        source = record['sources'][field]
        user_evidence(repo, source['id'], {field: value}, source['source'] == 'user-delegated')
    atomic_json(location, record)
    missing = [f for f in fields if f not in record['values']]
    return {**record, 'ready': not missing, 'missing_fields': missing,
            'next_field': missing[0] if missing else None,
            'next': ('Ask the user to enter only ' + missing[0] + '; wait, then save this answer with project setup identity --field/--value/--evidence. Do not invent defaults or ask for JSON.'
                     if missing else 'All identity answers saved. Run project setup draft; reuse these per-field sources.')}
