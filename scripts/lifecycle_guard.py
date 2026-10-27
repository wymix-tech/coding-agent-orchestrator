"""Detect unsupported writers; retain durable last-authorized Work authority.

This is not an OS access-control sandbox. Older binaries can alter a file, but
those writes cannot be consumed as authorized state by the current engine.
"""
from copy import deepcopy
import os
import json
from pathlib import Path

from scripts.state_store import atomic_json


def digest(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def guard_path(repo, wid):
    if not wid or Path(wid).name != wid or wid in {'.', '..'}:
        raise ValueError('WORK_ID_INVALID')
    path = repo / '.orchestrator/lifecycle/guards' / (wid + '.json')
    for parent in (path, *path.parents):
        if parent == repo:
            break
        if parent.is_symlink():
            raise ValueError('LIFECYCLE_GUARD_SYMLINK')
    return path


def read_guard(repo, wid):
    path = guard_path(repo, wid)
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('status') not in {'prepared', 'committed'}:
        raise ValueError('LIFECYCLE_GUARD_INVALID')
    if value['status'] == 'committed':
        authority = value.get('authority')
        if (not isinstance(authority, dict) or value.get('sha256') != digest(authority)
                or type(value.get('protected')) is not bool
                or value['protected'] != (authority.get('lifecycle_contract') == 2)):
            raise ValueError('LIFECYCLE_GUARD_INVALID')
    elif ('previous' not in value or not isinstance(value.get('authority'), dict)
          or (value['previous'] is not None and not isinstance(value['previous'], dict))):
        raise ValueError('LIFECYCLE_GUARD_INVALID')
    return value


def verify(repo, wid, work):
    guard = read_guard(repo, wid)
    if not guard:
        return work
    if guard.get('status') == 'prepared':
        actual = digest(work)
        old, new = guard['previous'], guard['authority']
        if actual not in {digest(old), digest(new)}:
            raise ValueError('LIFECYCLE_UNSUPPORTED_WRITER: run cao lifecycle recover --work ' + wid)
        selected = new if actual == digest(new) else old
        if selected is None:
            guard_path(repo, wid).unlink()
            return None
        guard = {'status': 'committed', 'authority': selected,
                 'sha256': digest(selected), 'protected': bool(selected and selected.get('lifecycle_contract') == 2)}
        atomic_json(guard_path(repo, wid), guard)
    if not guard.get('protected') and work and work.get('lifecycle_contract') == 2:
        raise ValueError('LIFECYCLE_UNSUPPORTED_WRITER: unregistered contract upgrade')
    if guard.get('protected'):
        if guard.get('sha256') != digest(guard.get('authority')) or guard.get('sha256') != digest(work):
            raise ValueError('LIFECYCLE_UNSUPPORTED_WRITER: run cao lifecycle recover --work ' + wid)
    return work


def persist(repo, wid, previous, saved, write):
    path = guard_path(repo, wid)
    if saved.get('lifecycle_contract') != 2 and not path.exists():
        write()
        return
    atomic_json(path, {'status': 'prepared', 'previous': deepcopy(previous), 'authority': deepcopy(saved)})
    write()
    atomic_json(path, {'status': 'committed', 'authority': saved, 'sha256': digest(saved),
                       'protected': saved.get('lifecycle_contract') == 2})


def protect(repo, work):
    if work.get('lifecycle_contract') != 2:
        raise ValueError('LIFECYCLE_CONTRACT_2_REQUIRED')
    path = guard_path(repo, work['id'])
    if path.exists():
        verify(repo, work['id'], work)
        return 'unchanged'
    atomic_json(path, {'status': 'committed', 'authority': work, 'sha256': digest(work), 'protected': True})
    return 'protected'


def recover(repo, wid, *, check=False):
    import uuid
    from scripts.cao_work_store import work_path, save_work, queue_event
    guard = read_guard(repo, wid)
    if not guard or not guard.get('protected') or guard.get('status') != 'committed':
        raise ValueError('LIFECYCLE_RECOVERY_AUTHORITY_REQUIRED')
    authority = guard['authority']
    if guard.get('sha256') != digest(authority) or authority.get('id') != wid:
        raise ValueError('LIFECYCLE_RECOVERY_AUTHORITY_INVALID')
    target = work_path(repo, wid)
    raw = target.read_bytes() if target.exists() else b''
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeError):
        actual = None
    changed = digest(actual) != guard['sha256']
    result = {'ok': True, 'work_id': wid, 'recovery_required': changed, 'check_only': check}
    if check or not changed:
        return result
    backup = repo / '.orchestrator/lifecycle/quarantine' / wid / (uuid.uuid4().hex + '.raw')
    for path in (backup, *backup.parents):
        if path == repo:
            break
        if path.is_symlink():
            raise ValueError('LIFECYCLE_QUARANTINE_SYMLINK')
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt':
        descriptor = os.open(backup.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    atomic_json(target, authority)
    restored = deepcopy(authority)
    restored['execution_generation'] = int(restored.get('execution_generation', 0)) + 1
    queue_event(restored, {'event': 'cao.lifecycle.unsupported_write.recovered', 'work_item_id': wid,
                           'quarantine': backup.relative_to(repo).as_posix()})
    save_work(repo, restored)
    return {**result, 'status': 'recovered', 'quarantine': backup.relative_to(repo).as_posix()}
