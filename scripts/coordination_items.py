"""Explicit coordination items, separate from provider-owned native task status."""
from scripts.cao_runtime import now, new_id


def update(work, args):
    resume = work.setdefault('resume', {})
    items = resume.setdefault('items', [])
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
