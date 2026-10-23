"""Small, source-bound project memory. Never an approval or completion authority."""
import hashlib
import json
import re
import time

from scripts.cao_runtime import config
from scripts.path_guard import repo_file
from scripts.runtime_logs import redact
from scripts.runtime_store import identifier, safe_path, write_record
from scripts.state_store import atomic_json, repo_transaction

ROOT = '.orchestrator/knowledge'
LIMIT = 1000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def enabled(repo):
    options = config(repo).get('knowledge', {})
    if not isinstance(options, dict):
        raise ValueError('KNOWLEDGE_CONFIG_INVALID')
    value = options.get('enabled', True)
    if not isinstance(value, bool):
        raise ValueError('KNOWLEDGE_CONFIG_INVALID')
    return value


def path_for(repo, name='store.json'):
    path = repo / ROOT / name
    for current in (path, *path.parents):
        if current == repo:
            break
        if current.is_symlink():
            raise ValueError('KNOWLEDGE_SYMLINK')
    return path


def load_store(repo):
    path = path_for(repo)
    if not path.exists():
        return {'schema_version': 1, 'generation': 0, 'records': {}}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('KNOWLEDGE_STORE_TOO_LARGE')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema_version') != 1 or not isinstance(value.get('records'), dict):
        raise ValueError('KNOWLEDGE_STORE_INVALID')
    if not isinstance(value.get('generation'), int):
        raise ValueError('KNOWLEDGE_STORE_INVALID')
    for key, row in value['records'].items():
        if (not isinstance(row, dict) or row.get('id') != key
                or not isinstance(row.get('scope'), dict)
                or not {'kind', 'id'} <= row['scope'].keys()
                or not isinstance(row.get('source_refs'), list)
                or not all(isinstance(ref, dict) for ref in row['source_refs'])
                or not all(isinstance(row.get(field), str) for field in
                           ('status', 'authority', 'kind', 'subject', 'statement', 'origin_work_id'))):
            raise ValueError('KNOWLEDGE_STORE_INVALID')
    return value


def source_ref(repo, path, *, frozen=False):
    source = repo_file(repo, path)
    if not source.is_file():
        raise ValueError('KNOWLEDGE_SOURCE_MISSING')
    if frozen:
        from scripts.native_route import intent_hash
        sha = intent_hash(source)
    else:
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
    return {'path': source.relative_to(repo).as_posix(), 'sha256': sha,
            'selector': 'frozen_intent' if frozen else 'file'}


def current(repo, record):
    try:
        if record.get('authority') == 'provider_accepted':
            from scripts.cao_work_store import load_work
            from scripts.cao_native_sync import native_route_state
            origin = load_work(repo, identifier(record['origin_work_id']))
            if not origin:
                return False
            approval = (origin.get('sdd') or {}).get('spec_approval') or {}
            expected = record.get('validation_refs') or []
            if (not native_route_state(repo, origin).get('ready') or not approval.get('evidence')
                    or approval.get('status') != 'approved' or not expected
                    or approval.get('id') != expected[0].get('approval_id')):
                return False
        refs = record.get('source_refs') or []
        return bool(refs) and all(source_ref(repo, item['path'], frozen=item.get('selector') == 'frozen_intent') == item for item in refs)
    except (ValueError, OSError, RuntimeError, KeyError):
        return False


def candidate(repo, work, kind, subject, statement, scope, refs, authority='observed'):
    secrets = config(repo).get('runtime', {}).get('redact_values', [])
    statement = redact(statement, secrets)[:2000]
    row = {'kind': kind, 'subject': subject, 'statement': statement, 'scope': scope,
           'authority': authority, 'source_refs': refs, 'origin_work_id': work['id'],
           'validation_refs': [], 'status': 'candidate'}
    row['id'] = 'knowledge-' + digest({key: row[key] for key in ('kind', 'subject', 'statement', 'scope', 'authority', 'source_refs')})[:32]
    return row


def extract(repo, work):
    """Deterministic extraction from accepted inputs, never arbitrary model claims."""
    from scripts.project_context import load_context
    from scripts.cao_native_sync import native_route_state
    from scripts.spec_intent import frozen_intent
    result = []
    context = load_context(repo)
    if context:
        ref = source_ref(repo, '.orchestrator/project/context.json')
        scopes = (work.get('project_binding') or {}).get('scopes') or ['root']
        for name in sorted(set(scopes) | {'root'}):
            for key, item in context.get('scopes', {}).get(name, {}).get('technology', {}).items():
                if item.get('status') == 'accepted':
                    # Observed accepted configuration is not a new user authorization.
                    result.append(candidate(repo, work, 'technology', key, str(item['value']),
                                            {'kind': 'project' if name == 'root' else 'module', 'id': name}, [ref]))
    scripts = config(repo).get('verification', {}).get('scripts', [])
    if scripts:
        ref = source_ref(repo, '.orchestrator/config.json')
        for entry in scripts:
            if isinstance(entry, dict) and isinstance(entry.get('command'), str):
                result.append(candidate(repo, work, 'verification_command', entry.get('name', 'test'), entry['command'],
                                        {'kind': 'project', 'id': 'root'}, [ref]))
    sdd = work.get('sdd') or {}
    route = sdd.get('route_decision') or {}
    if route.get('provider') == 'bmad' and native_route_state(repo, work).get('ready'):
        path = route['primary_artifact']
        text = repo_file(repo, path).read_text(encoding='utf-8-sig')
        block = frozen_intent(text, required=True)
        approval = sdd.get('spec_approval') or {}
        if approval.get('status') == 'approved' and approval.get('evidence'):
            ref = source_ref(repo, path, frozen=True)
            for index, line in enumerate(block.splitlines()):
                if re.match(r'^\*\*(?:Always|Never|Approach|Decision)[^*]*\*\*', line):
                    item = candidate(repo, work, 'constraint', f'intent-line-{index}', line,
                                     {'kind': 'work', 'id': work['id']}, [ref], 'provider_accepted')
                    item['validation_refs'] = [{'approval_id': approval['id'], 'work_id': work['id']}]
                    result.append(item)
        code_map = re.search(r'(?ms)^## Code Map\s*\n(.*?)(?=^## |\Z)', text)
        for reference in re.findall(r'`([^`\n]+)`', code_map[1] if code_map else '')[:50]:
            try:
                ref = source_ref(repo, reference)
            except (ValueError, OSError, RuntimeError):
                continue
            result.append(candidate(repo, work, 'module_entry', reference, reference,
                                    {'kind': 'project', 'id': 'root'}, [source_ref(repo, path), ref]))
    return result


def collect(repo, work, extra=None):
    if not enabled(repo):
        return {'status': 'disabled', 'count': 0}
    with repo_transaction(repo):
        rows = extract(repo, work)
        if extra is not None:
            if not isinstance(extra, list) or len(extra) > 100:
                raise ValueError('KNOWLEDGE_CANDIDATES_INVALID')
            for item in extra:
                if not isinstance(item, dict) or not isinstance(item.get('statement'), str) or not item['statement'].strip():
                    raise ValueError('KNOWLEDGE_CANDIDATES_INVALID')
                # External summaries stay candidates regardless of supplied authority/status.
                sources = item.get('sources', [])
                if not isinstance(sources, list) or not all(isinstance(value, str) for value in sources):
                    raise ValueError('KNOWLEDGE_CANDIDATES_INVALID')
                refs = [source_ref(repo, value) for value in sources]
                rows.append(candidate(repo, work, 'suggestion', str(item.get('subject', 'suggestion'))[:200], item['statement'],
                                      {'kind': 'work', 'id': work['id']}, refs, 'inferred'))
        store = load_store(repo)
        records = store['records']
        for record in records.values():
            if record['status'] in {'active', 'conflicted', 'stale'} and not current(repo, record):
                record['status'] = 'stale'
        for row in rows:
            record = records.get(row['id'])
            if record:
                if record['status'] == 'stale' and current(repo, record) and record['authority'] in {'observed', 'provider_accepted'}:
                    record['status'] = 'active'
                continue
            row['created_at'] = int(time.time())
            if row['authority'] in {'observed', 'provider_accepted'} and current(repo, row):
                row['status'] = 'active'
            records[row['id']] = row
        groups = {}
        for row in records.values():
            if row['status'] in {'active', 'conflicted'} and current(repo, row):
                key = (row['kind'], row['subject'], json.dumps(row['scope'], sort_keys=True))
                groups.setdefault(key, []).append(row)
        for group in groups.values():
            status = 'conflicted' if len({row['statement'] for row in group}) > 1 else 'active'
            for row in group:
                row['status'] = status
        if len(records) > LIMIT:
            raise ValueError('KNOWLEDGE_CAPACITY_REACHED: archive reviewed history before collecting more')
        store['generation'] += 1
        # One atomic commit pointer includes all records: no partially updated index.
        if len(json.dumps(store, ensure_ascii=False).encode()) > 3 * 1024 * 1024:
            raise ValueError('KNOWLEDGE_STORE_TOO_LARGE')
        atomic_json(path_for(repo), store)
        root = safe_path(repo, work['id'], 'knowledge')
        write_record(root / 'candidates.json', {'schema_version': 1, 'work_id': work['id'], 'candidates': rows}, immutable=False)
        return {'status': 'collected', 'count': len(rows), 'generation': store['generation']}


def collect_nonblocking(repo, work):
    try:
        return collect(repo, work)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        # Saved inside the Work transaction by done. Completion is already judged.
        return {'status': 'pending', 'reason': redact(str(exc))[:500],
                'next': 'Run `cao knowledge refresh` to retry; knowledge is not a completion gate.'}


def query(repo, work=None, *, include_inactive=False, limit=8):
    if not enabled(repo):
        return []
    rows = []
    scopes = set((work or {}).get('project_binding', {}).get('scopes', [])) | {'root'}
    for record in load_store(repo)['records'].values():
        row = dict(record)
        if not current(repo, row) and row['status'] == 'active':
            row['status'] = 'stale'
        scope = row['scope']
        relevant = (scope['kind'] == 'project' or scope['kind'] == 'module' and scope['id'] in scopes
                    or scope['kind'] == 'work' and scope['id'] == (work or {}).get('id'))
        if relevant and (include_inactive or row['status'] == 'active'):
            rows.append(row)
    return sorted(rows, key=lambda row: (row['kind'], row['id']))[:max(1, min(limit, 100))]


def refresh(repo):
    from scripts.cao_work_store import load_work, save_work
    results = []
    with repo_transaction(repo):
        store = load_store(repo)
        for record in store['records'].values():
            if not current(repo, record):
                record['status'] = 'stale'
        store['generation'] += 1
        atomic_json(path_for(repo), store)
        for path in sorted((repo / '.orchestrator/work').glob('*.json'))[:1000]:
            work = load_work(repo, path.stem)
            if (work.get('knowledge_collection') or {}).get('status') == 'pending':
                work['knowledge_collection'] = collect_nonblocking(repo, work)
                save_work(repo, work)
                results.append({'work_id': work['id'], **work['knowledge_collection']})
    return {'status': 'refreshed', 'retries': results}


def refresh_nonblocking(repo):
    try:
        if not enabled(repo):
            return {'status': 'disabled'}
        return refresh(repo)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        return {'status': 'pending', 'reason': redact(str(exc))[:500]}


def context_excerpt(repo, work):
    """Read-only source freshness check; memory failure never prevents a resume."""
    try:
        rows = query(repo, work)
        selected = []
        for row in rows:
            item = {'id': row['id'], 'statement': row['statement'][:350], 'scope': row['scope'],
                    'authority': row['authority'], 'source_refs': row['source_refs']}
            if len(json.dumps([*selected, item], ensure_ascii=False)) > 6000:
                break
            selected.append(item)
        return json.dumps(selected, ensure_ascii=False)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError):
        return '[]'
