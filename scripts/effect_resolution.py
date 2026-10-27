"""Explicit recorded inspection of a host effect; not an execution receipt."""
import json


def settle(repo, work, args):
    from scripts.runtime_queries import events
    from scripts.path_guard import repo_file
    from scripts.cao_runtime import sha256_file, now
    from scripts.cao_work_store import queue_event, save_work
    key = (args.host, args.session, args.call)
    starts = [e for e in events(repo, work['id']) if e.get('event') == 'tool.started' and
              (e.get('host'), e.get('session_id'), e.get('tool_call_id')) == key]
    if not starts:
        raise ValueError('HOST_EFFECT_NOT_FOUND')
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
