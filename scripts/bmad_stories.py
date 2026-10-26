"""Stable BMAD ordered stories inventory and colocated Build records.

The upstream schema is a top-level sequence of scalar records, not a YAML
status file. Accept that bounded shape; never treat an unknown shape as empty.
"""
import json
import re
from pathlib import Path

from scripts.path_guard import provider_artifact
from scripts.bmad_project import output_dirs


def inventory(path):
    entries = []
    current = None
    scalar_block = False
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        start = re.match(r'^-\s+(.*)$', line)
        if start:
            current = {}
            entries.append(current)
            value = start[1]
        elif line.startswith('  ') and current is not None:
            value = line[2:]
            if scalar_block and (value.startswith(' ') or not re.match(r'[a-z_]+:', value)):
                continue
        else:
            raise ValueError('BMAD_STORIES_FORMAT_INVALID: ' + path.name)
        match = re.fullmatch(r'([a-z_]+):\s*(.*?)\s*', value)
        if not match:
            raise ValueError('BMAD_STORIES_FORMAT_UNSUPPORTED: ' + path.name)
        key, raw = match.groups()
        if key in current or key == 'status':
            raise ValueError('BMAD_STORIES_FIELD_INVALID: ' + key)
        scalar_block = bool(re.fullmatch(r'[>|][-+]?', raw))
        if scalar_block:
            decoded = '<block scalar>'
        elif raw.startswith('"'):
            try:
                decoder = json.JSONDecoder()
                decoded, end = decoder.raw_decode(raw)
                if raw[end:].strip() and not raw[end:].lstrip().startswith('#'):
                    raise ValueError('trailing content')
            except ValueError as exc:
                raise ValueError('BMAD_STORIES_SCALAR_INVALID: ' + key) from exc
        elif raw.startswith("'"):
            quoted = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", raw)
            if not quoted:
                raise ValueError('BMAD_STORIES_SCALAR_INVALID: ' + key)
            decoded = quoted[1].replace("''", "'")
        else:
            if key == 'id':
                raise ValueError('BMAD_STORIES_ID_MUST_BE_QUOTED')
            decoded = re.split(r'\s+#', raw, maxsplit=1)[0]
        current[key] = decoded
    ids = []
    for entry in entries:
        if not all(isinstance(entry.get(key), str) and entry[key].strip() for key in ('id', 'title', 'description')):
            raise ValueError('BMAD_STORIES_REQUIRED_FIELDS')
        value = entry['id']
        if not re.fullmatch(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', value) or any(
                value == other or value.startswith(other + '-') or other.startswith(value + '-') for other in ids):
            raise ValueError('BMAD_STORIES_ID_INVALID: ' + value)
        ids.append(value)
    if not ids:
        raise ValueError('BMAD_STORIES_EMPTY')
    return ids


def story_states(repo, relative, known=()):
    path = provider_artifact(repo, relative, output_dirs(repo))
    if not path.is_file():
        raise ValueError('PLAN_SOURCE_MISSING: ' + relative)
    ids = inventory(path)
    if set(known) - set(ids):
        raise ValueError('BMAD_STORY_REMOVED: ' + ', '.join(sorted(set(known) - set(ids))))
    from scripts.cao_native_sync import _read_native_status
    states, files = {}, {}
    for value in ids:
        candidates = list((path.parent / 'stories').glob(value + '-*.md'))
        if len(candidates) > 1:
            raise ValueError('BMAD_STORY_SOURCE_AMBIGUOUS: ' + value)
        if not candidates:
            states[value] = 'backlog'
            continue
        source = provider_artifact(repo, str(candidates[0]), output_dirs(repo))
        raw, _, _, _ = _read_native_status('bmad', [source])
        states[value] = raw or 'unknown'
        files[value] = source.relative_to(repo.resolve()).as_posix()
    return ids, states, files


def discover(repo, work):
    sdd = work.get('sdd') or {}
    refs = [sdd.get('native_ref'), (sdd.get('route_decision') or {}).get('primary_artifact'),
            *((sdd.get('workflow') or {}).get('artifacts') or [])]
    refs += [item.get('path') for item in (sdd.get('route_decision') or {}).get('required_artifacts', [])]
    found = set()
    for ref in filter(None, refs):
        try:
            source = provider_artifact(repo, ref, output_dirs(repo))
        except ValueError:
            if Path(ref).name == 'stories.yaml':
                raise
            continue  # Other native bindings keep their existing source diagnostics.
        if source.name == 'stories.yaml':
            found.add(source.relative_to(repo.resolve()).as_posix())
        anchors = [source if source.is_dir() else source.parent]
        if source.parent.name == 'stories':
            anchors.append(source.parent.parent)
        for anchor in anchors:
            if (anchor / 'stories.yaml').is_file():
                found.add((anchor / 'stories.yaml').relative_to(repo.resolve()).as_posix())
    return found


def state(repo, work):
    scope = (work.get('sdd') or {}).get('plan_scope') or {}
    bound = {item['path']: item for item in scope.get('plans', []) if Path(item['path']).name == 'stories.yaml'}
    try:
        paths = set(bound) | discover(repo, work)
        if not paths:
            return {'ready': True, 'state': 'not_applicable'}
        observations, pending = {}, []
        from copy import deepcopy
        effective = deepcopy(scope)
        effective.setdefault('plans', [])
        effective.update(version=1, mode='bound')
        for relative in sorted(paths):
            plan = bound.get(relative) or {'path': relative, 'item_ids': []}
            ids, states, files = story_states(repo, relative, plan.get('item_ids', []))
            observations[relative] = {'selectors': ids, 'states': states, 'sources': files}
            pending.extend(f'{relative}#{value} ({status})' for value, status in states.items() if status != 'done')
            from scripts.bmad_units import completed
            pending.extend(f'{relative}#{value} (native review evidence missing or stale)' for value, status in states.items()
                           if status == 'done' and not completed(repo, work, files[value]))
            effective['plans'] = [item for item in effective['plans'] if item['path'] != relative]
            effective['plans'].append({**plan, 'item_ids': ids})
        return {'ready': not pending, 'state': 'pending' if pending else 'done',
                'raw': 'in_progress' if pending else 'done', 'canonical': 'in_progress' if pending else 'completed',
                'reason': 'BMAD_STORIES_NOT_DONE' if pending else None,
                'scope': effective, 'observations': observations, 'pending_plan_items': pending,
                'next': 'Invoke installed bmad-build for every pending story using its spec folder and story id. Preserve the full stories.yaml inventory; do not substitute the current SPEC for the whole requirement.' if pending else None}
    except (ValueError, OSError, UnicodeError, RuntimeError) as exc:
        return {'ready': False, 'state': 'blocked', 'reason': str(exc),
                'next': 'Restore the bound native stories inventory and unique story records; do not remove work from the requirement to pass verification.'}
