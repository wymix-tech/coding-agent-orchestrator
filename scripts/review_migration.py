"""Explicit active-review upgrades with a guarded, reversible checkpoint."""
import hashlib
import json
from copy import deepcopy
from scripts.path_guard import repo_file


def migrate(repo, work, *, apply=False, rollback=None):
    from scripts.cao_work_store import save_work, queue_event
    from scripts.cao_runtime import new_id, now
    from scripts.native_obligations import digest, review_integrity
    from scripts.runtime_store import safe_path, write_record, read_record
    if work.get('status') != 'active':
        raise ValueError('ACTIVE_WORK_REQUIRED: completed Work is historical and cannot be recertified')
    records = work.get('sdd', {}).get('native_reviews') or {}
    if rollback:
        previous = next((r for r in work['sdd'].get('review_protocol_migrations', []) if r['id'] == rollback), None)
        if not previous or previous.get('rolled_back'):
            raise ValueError('NATIVE_REVIEW_MIGRATION_NOT_FOUND')
        checkpoint = repo_file(repo, previous['checkpoint'])
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != previous['checkpoint_sha256']:
            raise ValueError('NATIVE_REVIEW_MIGRATION_CHECKPOINT_CHANGED')
        saved = read_record(checkpoint)
        if digest(records) != previous['after_sha256']:
            raise ValueError('NATIVE_REVIEW_MIGRATION_PROGRESS_CHANGED: rollback cannot discard later review work')
        work['sdd']['native_reviews'] = saved['reviews']
        previous['rolled_back'] = now()
        queue_event(work, {'event': 'sdd.review.protocol.rollback', 'work_item_id': work['id'], 'migration_id': rollback})
        save_work(repo, work)
        return {'ok': True, 'status': 'rolled_back', 'migration_id': rollback}
    integrity = review_integrity(repo, work)
    if not integrity['ready']:
        return {'ok': False, 'error': integrity['reason'], 'next': 'Restore current evidence integrity before upgrading the active protocol.'}
    upgrades, blocked, historical = [], [], []
    for layer, run in records.items():
        if layer == '_contract':
            continue
        if run.get('status') == 'passed':
            historical.append({'layer': layer, 'protocol': run.get('resolution_binding_version', 'legacy'),
                               'note': 'Already resolved evidence is retained without recertification.'})
            continue
        if run.get('resolution_binding_version') == 1:
            continue
        if (not run.get('input_manifest') or run.get('finding_binding_version') != 1
                or (run.get('status') == 'classified' and any(not f.get('content_sha256') for f in run.get('findings', [])))):
            blocked.append({'layer': layer, 'reason': 'Original frozen manifest and finding-content binding are required; preserve the original protocol or explicitly replace this layer.'})
        else:
            upgrades.append(layer)
    result = {'ok': not blocked, 'status': 'preview', 'upgrade_layers': upgrades, 'blocked': blocked, 'historical': historical,
              'limitations': 'Upgrades future resolution evidence only; does not certify past reviewer context isolation.',
              'next': 'Run review migrate --apply after reviewing these exact changes; then review resume. Preserve source and judgments.'}
    if not apply or blocked:
        return result
    if not upgrades:
        return {**result, 'status': 'unchanged'}
    migration_id = new_id('review-upgrade')
    checkpoint = safe_path(repo, work['id'], 'review-upgrades', migration_id + '.json')
    write_record(checkpoint, {'schema_version': 1, 'reviews': deepcopy(records)})
    for layer in upgrades:
        records[layer]['resolution_binding_version'] = 1
    record = {'id': migration_id, 'at': now(), 'layers': upgrades,
              'checkpoint': checkpoint.relative_to(repo).as_posix(), 'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
              'after_sha256': digest(records)}
    work['sdd'].setdefault('review_protocol_migrations', []).append(record)
    queue_event(work, {'event': 'sdd.review.protocol.upgraded', 'work_item_id': work['id'], **record})
    save_work(repo, work)
    return {**result, 'status': 'upgraded', 'migration_id': migration_id}
