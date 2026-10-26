"""Canonical accepted standards and narrowly declared evaluator transport."""
from copy import deepcopy
import json
from pathlib import Path

from scripts.path_guard import repo_file
from scripts.sdd_acceptance_policy import normalize_declaration


def acceptance_review_scope(repo, work):
    record = ((work.get('sdd') or {}).get('acceptance') or {})
    accepted = record.get('declaration') or {}
    if not accepted:
        return set(), None
    declaration = normalize_declaration(accepted)
    standard = {key: deepcopy(declaration[key]) for key in ('spec', 'sources', 'criteria')}
    for item in standard['criteria']:
        item.pop('covers', None)
    excluded = set()
    path = record.get('declaration_file')
    if path:
        raw = normalize_declaration(json.loads(repo_file(repo, path).read_text()))
        for key in ('evidence_policy', 'lifecycle_metadata'):
            if declaration.get(key) and not raw.get(key):
                raw[key] = declaration[key]
        if raw != declaration:
            return set(), None  # An unaccepted declaration change stays visible.
        excluded.add(path)
    executor = declaration['executor']
    if executor.get('review_impact') == 'acceptance_only' and executor['mode'] == 'command':
        from scripts.cao_provider_workflows import workflow_command_identities
        for identity in workflow_command_identities(executor['entrypoint']):
            candidate = Path(identity)
            candidate = candidate if candidate.is_absolute() else repo / candidate
            if candidate.is_file() and candidate.resolve().is_relative_to(repo / 'scripts/verification'):
                excluded.add(candidate.relative_to(repo).as_posix())
    return excluded, {'content': standard}
