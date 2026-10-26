"""Explicit evidence relocation preserves execution only after byte-equivalence.

Content-changing renderer upgrades are deliberately not equivalent relocations.
"""
import hashlib
from copy import deepcopy

from scripts.path_guard import repo_file


def relocate(repo, old, proposed):
    if not old:
        raise ValueError('NATIVE_ROUTE_REPAIR_EXISTING_DECISION_REQUIRED')
    fields = ('provider', 'requirement_sha256', 'assessment_revision', 'profile', 'required_gates',
              'workflow', 'entrypoint', 'route', 'build_route', 'provider_version',
              'required_artifacts', 'primary_artifact', 'omitted', 'approval_required')
    if any(old.get(key) != proposed.get(key) for key in fields):
        raise ValueError('NATIVE_ROUTE_REPAIR_SEMANTIC_CHANGE')
    before, after = old.get('rules', []), proposed.get('rules', [])
    if not before or len(before) != len(after):
        raise ValueError('NATIVE_ROUTE_REPAIR_RULES_NOT_EQUIVALENT')
    moves = {}
    for a, b in zip(before, after):
        if {k: v for k, v in a.items() if k != 'path'} != {k: v for k, v in b.items() if k != 'path'}:
            raise ValueError('NATIVE_ROUTE_REPAIR_RULES_NOT_EQUIVALENT')
        for item in (a, b):
            if hashlib.sha256(repo_file(repo, item['path']).read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('NATIVE_ROUTE_REPAIR_SOURCE_STALE')
        roots = [repo_file(repo, value['path']).parent for value in (a, b)]
        names = {'step-03-implement.md', 'step-04-review.md', 'step-oneshot.md'}
        names.update(path.name for root in roots for path in root.glob('step-05-*.md'))
        for name in sorted(names):
            left, right = (repo_file(repo, value['path']).parent / name for value in (a, b))
            if left.is_file() != right.is_file() or left.is_file() and left.read_bytes() != right.read_bytes():
                raise ValueError('NATIVE_ROUTE_REPAIR_CONTRACT_NOT_EQUIVALENT: ' + name)
            if left.is_file():
                moves[left.relative_to(repo).as_posix()] = right.relative_to(repo).as_posix()
        moves[a['path']] = b['path']
    result = deepcopy(old)
    result['rules'] = deepcopy(after)
    result['reason'] = proposed['reason']
    if old.get('contracts') or proposed.get('contracts'):
        old_contracts = old.get('contracts')
        if old_contracts is None:
            # Historical routes can point at templates. Migrate only when every
            # rendered contract has a known, byte-identical old counterpart.
            inverse = {new: previous for previous, new in moves.items()}
            old_contracts = deepcopy(proposed.get('contracts', {}))
            for role, item in old_contracts.items():
                if role == 'layers':
                    continue
                previous = inverse.get(item['path'])
                if not previous or hashlib.sha256(repo_file(repo, previous).read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('NATIVE_ROUTE_REPAIR_CONTRACT_NOT_EQUIVALENT')
                item['path'] = previous
        def semantics(value):
            return {k: ({x: y for x, y in v.items() if x != 'path'} if isinstance(v, dict) else v) for k, v in value.items()}
        if semantics(old_contracts or {}) != semantics(proposed.get('contracts', {})):
            raise ValueError('NATIVE_ROUTE_REPAIR_CONTRACT_NOT_EQUIVALENT')
        result['contracts'] = deepcopy(proposed.get('contracts', {}))
    return result, moves


def migrate_evidence(sdd, moves):
    sdd.setdefault('evidence_aliases', {}).update({new: old for old, new in moves.items() if old != new})
    for name in ('implementation_preparation', 'native_implementation'):
        contract = (sdd.get(name) or {}).get('contract') or {}
        if contract.get('path') in moves:
            contract['path'] = moves[contract['path']]
    records = sdd.get('native_reviews') or {}
    contract = records.get('_contract') or {}
    contract['sources'] = [moves.get(path, path) for path in contract.get('sources', [])]
    # Frozen reviewer input identities are historical evidence; don't rewrite them.
    # Review-layer input path changes remain explicitly stale until separately restored.
