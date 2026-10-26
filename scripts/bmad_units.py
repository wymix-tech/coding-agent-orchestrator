"""Retain completed Build review evidence while a Work moves between stories."""
from copy import deepcopy
import hashlib

from scripts.path_guard import repo_file


def archive(repo, work, next_primary):
    sdd = work.get('sdd') or {}
    old = sdd.get('route_decision') or {}
    source = old.get('primary_artifact')
    if (sdd.get('provider') != 'bmad' or not source or source == next_primary
            or old.get('requirement_sha256') != work['requirement']['sha256']):
        return
    from scripts.bmad_stories import discover, story_states
    unit_files = set()
    for inventory in discover(repo, work):
        _ids, _states, files = story_states(repo, inventory)
        unit_files.update(files.values())
    if source not in unit_files:
        return
    from scripts.cao_native_sync import _read_native_status
    from scripts.native_obligations import review_state
    from scripts.cao_snapshot import code_snapshot
    path = repo_file(repo, source)
    status, _, _, _ = _read_native_status('bmad', [path])
    review = review_state(repo, work, code_snapshot)
    from scripts.cao_native_sync import native_route_state
    original = deepcopy(work)
    original['sdd']['native_ref'] = source
    if status != 'done' or not review['ready'] or not native_route_state(repo, original)['ready']:
        raise ValueError('BMAD_STORY_HANDOFF_NOT_COMPLETE: finish the current story and its native review before switching Build units')
    records = deepcopy(sdd.get('native_reviews') or {})
    hashes = {source: hashlib.sha256(path.read_bytes()).hexdigest()}
    for layer, record in records.items():
        if layer != '_contract':
            hashes.update(record.get('evidence_hashes') or {})
    sdd.setdefault('native_units', {})[source] = {'source': source, 'route': deepcopy(old),
        'reviews': records, 'approval': deepcopy(sdd.get('spec_approval')), 'evidence_hashes': hashes,
        'requirement_sha256': work['requirement']['sha256']}
    sdd['native_reviews'] = {}
    binding = sdd.get('spec_binding') or {}
    if (binding.get('spec') or {}).get('path') == source:
        sdd.pop('spec_binding', None)


def completed(repo, work, source):
    sdd = work.get('sdd') or {}
    if source == (sdd.get('route_decision') or {}).get('primary_artifact'):
        return True  # Current unit uses the live native review/approval gates.
    record = (sdd.get('native_units') or {}).get(source) or {}
    if record.get('requirement_sha256') != work['requirement']['sha256'] or not record.get('evidence_hashes'):
        return False
    try:
        return all(hashlib.sha256(repo_file(repo, path).read_bytes()).hexdigest() == sha
                   for path, sha in record['evidence_hashes'].items())
    except (ValueError, OSError, RuntimeError):
        return False
