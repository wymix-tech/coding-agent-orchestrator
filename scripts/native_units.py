"""Stable source-qualified native task references; ambiguity stays explicit."""
import re
from scripts.path_guard import provider_artifact
from scripts.cao_provider_workflows import provider_output_dirs


def units(repo, work):
    from scripts.cao_plans import _checkbox_tasks, _sprint_selected_states, SPRINT_STATUS_NAMES
    from scripts.native_metadata import semantic_hash
    sdd = work.get('sdd') or {}
    rows = []
    plans = list((sdd.get('plan_scope') or {}).get('plans') or [])
    ref = ((sdd.get('spec_binding') or {}).get('spec') or {}).get('path') or sdd.get('native_ref')
    if not plans and ref:
        plans = [{'path': ref}]
    for plan in plans:
        source = plan['path']
        path = provider_artifact(repo, source, provider_output_dirs(sdd['provider'], repo))
        if not path.is_file():
            raise ValueError('NATIVE_UNIT_SOURCE_MISSING: ' + source)
        revision = semantic_hash(path)
        if path.name in SPRINT_STATUS_NAMES:
            for key, state in _sprint_selected_states(path, plan.get('item_ids') or []).items():
                rows.append({'id': source + '#' + key, 'source': source, 'native_id': key,
                             'state': state, 'scope_revision': revision})
        else:
            tasks = _checkbox_tasks(path.read_text(encoding='utf-8-sig'))
            seen = set()
            for task in tasks:
                text = task['description']
                match = re.match(r'\s*(T\d+|[A-Za-z][A-Za-z0-9_-]*\d[\w-]*)\b', text)
                # Text-based identity is content-addressed; duplicate text cannot silently match.
                from hashlib import sha256
                key = match[1] if match else 'text-' + sha256(text.encode()).hexdigest()[:20]
                if key in seen:
                    raise ValueError('NATIVE_UNIT_AMBIGUOUS: ' + source)
                seen.add(key)
                rows.append({'id': source + '#' + key, 'source': source, 'native_id': key,
                             'description': text, 'state': 'done' if task['done'] else 'pending',
                             'scope_revision': revision, 'line_hint': task['line']})
            if not tasks:
                rows.append({'id': source, 'source': source, 'state': 'unknown', 'scope_revision': revision})
    return rows
