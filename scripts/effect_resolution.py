"""Explicit recorded inspection of a host effect; not an execution receipt."""
import json


def settle(repo, work, args):
    from scripts.runtime_queries import events
    from scripts.path_guard import repo_file
    from scripts.cao_runtime import sha256_file, now
    from scripts.cao_work_store import queue_event, save_work
    key = (args.host, args.session, args.call)
    from scripts.host_effects import effects
    rows = [row for row in effects(repo, work['id']) if (row['host'], row['session_id'], row['tool_call_id']) == key]
    if not rows:
        raise ValueError('HOST_EFFECT_NOT_FOUND')
    if rows[0]['state'] == 'resolved':
        return {'ok': True, 'status': 'already_resolved', 'effect': rows[0]}
    if not args.reason.strip():
        raise ValueError('EFFECT_INSPECTION_REASON_REQUIRED')
    path = repo_file(repo, args.evidence)
    if not path.is_file() or not path.read_bytes().strip():
        raise ValueError('EFFECT_INSPECTION_EVIDENCE_REQUIRED')
    row = {'event': 'tool.effect.settled', 'work_item_id': work['id'], 'host': args.host,
           'session_id': args.session, 'tool_call_id': args.call, 'source': 'agent_declaration',
           'resolution': args.resolution, 'reason': args.reason,
           'evidence': {'path': path.relative_to(repo).as_posix(), 'sha256': sha256_file(path)},
           'occurred_at': now()}
    queue_event(work, row)
    save_work(repo, work)
    return {'ok': True, 'resolution': row, 'note': 'Explicit inspection declaration; no process termination or completion is certified.'}
