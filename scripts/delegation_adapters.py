"""Explicit plugin-owned delegation schemas; no host implies a transport."""
import json
import re
from copy import deepcopy

REGISTRY = '.orchestrator/delegation-adapters.json'


def get(value, path):
    for key in path.split('.'):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def has(value, path):
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return False
        value = value[key]
    return True


def put(value, path, item):
    keys = path.split('.')
    for key in keys[:-1]:
        value = value.setdefault(key, {})
        if not isinstance(value, dict):
            raise ValueError('DELEGATION_PATH_CONFLICT')
    value[keys[-1]] = item


def validate(row):
    if not isinstance(row, dict) or row.get('version') != 1:
        raise ValueError('DELEGATION_ADAPTER_VERSION_REQUIRED')
    if row.get('task_mode', 'batch') not in {'single', 'batch'}:
        raise ValueError('DELEGATION_TASK_MODE_INVALID')
    if row.get('task_mode') != 'single' and not isinstance(row.get('tasks_path'), str):
        raise ValueError('DELEGATION_ADAPTER_FIELD_INVALID: tasks_path')
    for key in ('plugin', 'tool', 'prompt_field', 'agent_field', 'blocking_path'):
        if not isinstance(row.get(key), str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', row[key]):
            raise ValueError('DELEGATION_ADAPTER_FIELD_INVALID: ' + key)
    if type(row.get('blocking_value')) not in (bool, str):
        raise ValueError('DELEGATION_BLOCKING_VALUE_REQUIRED')
    if not isinstance(row.get('request', {}), dict) or not isinstance(row.get('match'), dict) or not row['match']:
        raise ValueError('DELEGATION_MATCH_REQUIRED')
    if not isinstance(row.get('result_fields', {}), dict):
        raise ValueError('DELEGATION_RESULT_FIELDS_INVALID')
    if any(type(value) not in (str, bool, int, type(None)) for value in row['match'].values()):
        raise ValueError('DELEGATION_MATCH_VALUE_INVALID')
    if not isinstance(row.get('forbidden_task_fields', []), list):
        raise ValueError('DELEGATION_FORBIDDEN_FIELDS_INVALID')
    for key in [*([row['tasks_path']] if row.get('task_mode') != 'single' else []), *row['match'], *row.get('result_fields', {}).values(), *row.get('forbidden_task_fields', [])]:
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', key):
            raise ValueError('DELEGATION_FIELD_PATH_INVALID')
    return row


def registry(repo):
    path = repo / REGISTRY
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError('DELEGATION_REGISTRY_INVALID')
    return [validate(row) for row in rows]


def normalize(repo, tool, payload):
    rows = [row for row in registry(repo) if row['tool'].lower() == tool.lower() and
            all(type(get(payload, key)) is type(value) and get(payload, key) == value for key, value in row['match'].items())]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('DELEGATION_ADAPTER_AMBIGUOUS')
    row = rows[0]
    if type(get(payload, row['blocking_path'])) is not type(row['blocking_value']) or get(payload, row['blocking_path']) != row['blocking_value']:
        raise ValueError('NATIVE_REVIEW_SYNCHRONOUS_REQUIRED: plugin=' + row['plugin'])
    tasks = [payload] if row.get('task_mode') == 'single' else get(payload, row['tasks_path'])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('DELEGATION_TASKS_REQUIRED')
    if any(not isinstance(task, dict) or not isinstance(get(task, row['prompt_field']), str) for task in tasks):
        raise ValueError('DELEGATION_PROMPT_REQUIRED')
    if any(has(task, path) for task in tasks for path in row.get('forbidden_task_fields', [])):
        raise ValueError('NATIVE_REVIEW_HOST_ARGUMENT_INVALID: plugin task field is forbidden')
    return [{'task': get(task, row['prompt_field'])} for task in tasks]


def command(args):
    from scripts.cao_runtime import repo_root
    from scripts.state_store import atomic_json, repo_transaction
    from scripts.path_guard import repo_file
    repo = repo_root(args.repo)
    if args.delegation_action == 'register':
        row = validate(json.loads(repo_file(repo, args.manifest).read_text()))
        with repo_transaction(repo):
            rows = [r for r in registry(repo) if r['plugin'] != row['plugin']]
            atomic_json(repo / REGISTRY, rows + [row])
        output = {'ok': True, 'adapter': row, 'authority': 'explicit project plugin contract; not proof of plugin installation'}
    elif args.delegation_action == 'show':
        output = {'ok': True, 'adapters': registry(repo)}
    else:
        rows = [r for r in registry(repo) if r['plugin'] == args.plugin]
        if len(rows) != 1:
            raise ValueError('DELEGATION_ADAPTER_REQUIRED: register the installed plugin contract')
        row = rows[0]
        if args.delegation_action == 'decode':
            raw = json.loads(repo_file(repo, args.result).read_text())
            output = {'ok': True, 'plugin': row['plugin'], 'identities': {key: get(raw, path) for key, path in row.get('result_fields', {}).items()},
                      'next': 'Run IDs are plugin handles, not CAO session IDs. Bind only host-observed independent sessions before inspection.'}
        else:
            from scripts.cao_work_store import resolve_work
            from scripts.review_packages import launch_payload, launch_guard
            with repo_transaction(repo):
                work = resolve_work(repo, args.work)
                if not work:
                    raise ValueError('ACTIVE_WORK_REQUIRED')
                tasks = (launch_payload(repo, work) or {}).get('tasks', [])
                payload = deepcopy(row.get('request', {}))
                for key, value in row['match'].items():
                    put(payload, key, value)
                put(payload, row['blocking_path'], row['blocking_value'])
                items = []
                for task in tasks:
                    item = {}
                    put(item, row['prompt_field'], task['task'])
                    put(item, row['agent_field'], args.agent)
                    items.append(item)
                if row.get('task_mode') == 'single':
                    requests = []
                    for task in tasks:
                        request = deepcopy(payload)
                        put(request, row['prompt_field'], task['task'])
                        put(request, row['agent_field'], args.agent)
                        requests.append(request)
                else:
                    put(payload, row['tasks_path'], items)
                    requests = [payload] if tasks else []
                for request in requests:
                    error = launch_guard(repo, work, {'toolName': row['tool'], 'input': request})
                    if error:
                        raise ValueError(error)
            output = {'ok': True, 'plugin': row['plugin'], 'tool': row['tool'], 'input': requests[0] if len(requests) == 1 else None, 'requests': requests,
                      'next': 'Execute this request using the selected installed plugin. Collect all final outputs; resume existing bindings rather than relaunching them.'}
    print(json.dumps(output))
    return 0
