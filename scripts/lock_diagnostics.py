"""Read-only inspection available independently of repository transactions."""
import errno
import json
import os
from scripts.state_store import _lock, _unlock


def inspect(repo, work_id=None):
    path = repo / '.orchestrator/state.lock'
    locked = None
    try:
        fd = os.open(path, os.O_RDWR)
        try:
            try:
                _lock(fd)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                locked = True
            else:
                locked = False
                _unlock(fd)
        finally:
            os.close(fd)
    except FileNotFoundError:
        locked = False
    except OSError:
        locked = None
    def read(path):
        try:
            if path.stat().st_size > 1024 * 1024:
                return {'diagnostic': 'oversized'}
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None
    owner = read(repo / '.orchestrator/state-lock-owner.json')
    index = read(repo / '.orchestrator/index.json') or {}
    wid = work_id or index.get('active_work')
    work = None
    if wid and all(c.isalnum() or c in '._-' for c in wid) and wid not in {'.', '..'}:
        from scripts.cao_defaults import WORK_DIR
        # Read committed bytes only: load_work also locks, drains events and migrates.
        work = read(repo / WORK_DIR / (wid + '.json'))
        if not isinstance(work, dict):
            work = None
    return {'ok': True, 'inspection_only': True, 'completion_certified': False,
            'lock': {'held': locked, 'owner_hint': owner, 'owner_hint_authoritative': False},
            'work_id': wid, 'verification': (work or {}).get('verification'),
            'next': 'Inspect the owner and runtime receipts. Do not delete locks or infer process ownership from PID alone; wait for active execution or use normal transactional runtime recovery after release.'}


def command(args):
    from scripts.cao_runtime import repo_root
    print(json.dumps(inspect(repo_root(args.repo), args.work)))
    return 0
