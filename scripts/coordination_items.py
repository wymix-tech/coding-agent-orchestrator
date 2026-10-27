"""Explicit coordination items, separate from provider-owned native task status."""
from scripts.cao_runtime import now, new_id


def update(work, args):
    resume = work.setdefault('resume', {})
    items = resume.setdefault('items', [])
    from hashlib import sha256
    for field, flag, state in [('pending', 'pending', 'open'), ('completed', 'completed', 'resolved')]:
        notes = list(resume.get(field) or [])
        if getattr(args, flag, None):
            notes.append(getattr(args, flag))
        for index, text in enumerate(notes):
            identity = 'legacy-' + sha256((field + ':' + str(index) + ':' + text).encode()).hexdigest()[:24]
            if not any(row['id'] == identity for row in items):
                items.append({'id': identity, 'kind': 'legacy_note', 'summary': text, 'status': state, 'source_field': field, 'source_index': index, 'created_at': now()})
    if getattr(args, 'unit', None):
        from scripts.cao_runtime import repo_root
        from scripts.native_units import units
        if args.unit not in {r['id'] for r in units(repo_root(args.repo), work)}:
            raise ValueError('NATIVE_UNIT_NOT_BOUND')
        resume['current_unit'] = args.unit
    if len(getattr(args, 'read', None) or []) > 50:
        raise ValueError('COORDINATION_READ_LIST_LIMIT')
    if getattr(args, 'read', None):
        from scripts.cao_runtime import repo_root
        from scripts.path_guard import repo_file
        repo = repo_root(args.repo)
        resume['read_before_resume'] = [repo_file(repo, value).relative_to(repo).as_posix() for value in args.read]
    if getattr(args, 'item', None):
        row = {'id': new_id('item'), 'kind': 'coordination', 'summary': args.item,
               'status': 'open', 'created_at': now()}
        items.append(row)
    for flag, state in [('resolve', 'resolved'), ('cancel', 'cancelled')]:
        identity = getattr(args, flag, None)
        if identity:
            row = next((r for r in items if r['id'] == identity), None)
            if not row:
                raise ValueError('COORDINATION_ITEM_NOT_FOUND')
            if row['status'] not in {'open', state}:
                raise ValueError('COORDINATION_ITEM_ALREADY_CLOSED')
            row.update(status=state, resolved_at=now())


def active_pending(work):
    resume = work.get('resume') or {}
    closed = {row['source_index'] for row in resume.get('items', [])
              if row.get('source_field') == 'pending' and row.get('status') != 'open'}
    return [text for index, text in enumerate(resume.get('pending') or []) if index not in closed]
