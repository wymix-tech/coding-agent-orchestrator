"""Content-addressed original report bytes, never a replacement native task source."""
import hashlib
import os
import tempfile
from scripts.runtime_store import safe_path, settings


def retain(repo, wid, data):
    digest = hashlib.sha256(data).hexdigest()
    path = safe_path(repo, wid, 'evidence-bodies', digest + '.bin')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('EVIDENCE_ARCHIVE_CORRUPT')
        return path.relative_to(repo).as_posix()
    root = safe_path(repo, wid)
    used = 0
    if root.exists():
        for base, dirs, names in os.walk(root, followlinks=False):
            for name in dirs + names:
                entry = __import__('pathlib').Path(base) / name
                if entry.is_symlink():
                    raise ValueError('RUNTIME_SYMLINK')
            used += sum((__import__('pathlib').Path(base) / n).stat().st_size for n in names)
    if used + len(data) > settings(repo)['retention']['max_bytes_per_work']:
        raise ValueError('EVIDENCE_ARCHIVE_QUOTA')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return path.relative_to(repo).as_posix()


def select(repo, work, ids):
    from scripts.cao_runtime import sha256_file
    from scripts.path_guard import repo_file
    records = (work.get('sdd') or {}).get('implementation_evidence') or []
    selected = []
    for identity in ids:
        row = next((r for r in records if r['id'] == identity), None)
        if not row:
            raise ValueError('IMPLEMENTATION_EVIDENCE_NOT_FOUND')
        path = repo_file(repo, row['path'])
        if not path.is_file() or sha256_file(path) != row['sha256']:
            raise ValueError('IMPLEMENTATION_EVIDENCE_STALE')
        from scripts.lifecycle_contract import inputs
        if row.get('inputs') != inputs(repo, work):
            raise ValueError('IMPLEMENTATION_EVIDENCE_INPUT_CHANGED')
        if row.get('generation') != work.get('execution_generation', 0) or row.get('requirement') != work.get('requirement'):
            raise ValueError('IMPLEMENTATION_EVIDENCE_INPUT_CHANGED')
        # A later failure/unknown on overlapping scope cannot be hidden by selecting an older pass.
        index = records.index(row)
        for later in records[index + 1:]:
            overlapping = not row.get('unit_ref') or not later.get('unit_ref') or later['unit_ref'] == row['unit_ref']
            if overlapping and later['kind'] in {'test', 'acceptance'} and later.get('observation', {}).get('outcome') != 'passed':
                superseded = any(r.get('supersedes') == later['id'] and r['id'] in ids for r in records)
                if not superseded:
                    raise ValueError('IMPLEMENTATION_EVIDENCE_LATER_UNRESOLVED_RESULT')
        selected.append(row['path'])
    return list(dict.fromkeys(selected))
