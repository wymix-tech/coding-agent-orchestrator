"""Reuse confirmed field identities with their actual observed user provenance."""
from scripts.project_sources import user_evidence


def resolve(repo, scope, values, raw, context):
    previous = context['scopes'][scope].get('identity') or {}
    supplied = raw.get('identity_evidence', {})
    if not isinstance(supplied, dict) or any(key not in values for key in supplied):
        raise ValueError('PROJECT_IDENTITY_EVIDENCE_INVALID')
    sources, records = {}, []
    for key, value in values.items():
        inherited = (previous.get('sources') or {}).get(key) if previous.get('values', {}).get(key) == value else None
        source = supplied.get(key) or inherited or {'id': raw.get('evidence'), 'source': raw.get('source', 'user-specified')}
        if isinstance(source, str):
            source = {'id': source, 'source': raw.get('source', 'user-specified')}
        if not isinstance(source, dict) or source.get('source') not in {'user-specified', 'user-delegated'}:
            raise ValueError('PROJECT_IDENTITY_EVIDENCE_INVALID')
        evidence = user_evidence(repo, source.get('id'), {key: value}, source['source'] == 'user-delegated')
        sources[key] = {'id': evidence['id'], 'source': evidence['source']}
        if evidence not in records:
            records.append(evidence)
    return records, sources
