"""Render native layer prompts and enforce observed reviewer input boundaries."""
import hashlib
import json
import re
import shlex
from pathlib import Path
from scripts.path_guard import repo_file


def layer_prompt(content, layer):
    headers = list(re.finditer(r'(?m)^####[^\n]*\(`?([a-z][a-z0-9-]*)`?\)[^\n]*\n', content))
    selected = next((m for m in headers if m[1] == layer), None)
    if not selected:
        raise ValueError('NATIVE_REVIEW_LAYER_NOT_ACTIVE')
    tail = content[selected.end():]
    stop = re.search(r'(?m)^#{1,4} |^If a layer.s instruction requires subagents', tail)
    body = tail[:stop.start()] if stop else tail
    prefix = re.search(r'Launch a context-free subagent with this prompt:\s*\n', body)
    return (body[prefix.end():] if prefix else body).strip()


def render(repo, work, run, instructions, manifest):
    from scripts.review_dispatch import bundle_root
    from scripts.review_observation import task_marker
    from scripts.review_changes import unified_diff
    from scripts.state_store import atomic_json
    root = bundle_root(repo, work, run['layer'], run)
    root.mkdir(parents=True, exist_ok=True)
    diff = root / 'changes.diff'
    diff.write_text(unified_diff(repo, work, manifest), encoding='utf-8')
    # Native patch/finalize may refresh its working diff; reviewer input stays immutable.
    (root / 'current-changes.diff').write_bytes(diff.read_bytes())
    source = repo_file(repo, instructions)
    content = source.read_text()
    prompt = layer_prompt(content, run['layer'])
    claims = ((work.get('sdd') or {}).get('route_decision') or {}).get('primary_artifact')
    prompt = prompt.replace('{diff_file}', str(diff))
    if '{claims_file}' in prompt:
        if run['layer'] != 'edge-case-hunter' or not claims:
            raise ValueError('NATIVE_REVIEW_CLAIMS_CONTEXT_INVALID')
        prompt = prompt.replace('{claims_file}', str(repo_file(repo, claims)))
    # Keep global language/model rules without exposing the coordinator's other layers.
    rules = re.search(r'(?ms)^## RULES\s*\n(.*?)(?=^## |\Z)', content)
    global_rules = rules[1].strip() if rules else ''
    task = task_marker(run, run['layer']) + '\n\n' + (global_rules + '\n\n' if global_rules else '') + prompt + '\n'
    prompt_path = root / 'reviewer-prompt.md'
    prompt_path.write_text(task, encoding='utf-8')
    allowed = {diff.relative_to(repo).as_posix(), prompt_path.relative_to(repo).as_posix()}
    # Only references in THIS layer's prompt are reviewer instruction inputs.
    for raw in re.findall(r'`([^`\n]+\.md)`', prompt):
        if '{' in raw:
            continue
        path = repo_file(repo, raw)
        if path.is_file() and path.relative_to(repo).as_posix() != claims:
            allowed.add(path.relative_to(repo).as_posix())
    products = [row['path'] for row in manifest['files']]
    policy = {'version': 1, 'layer': run['layer'], 'prompt': prompt_path.relative_to(repo).as_posix(),
              'prompt_sha256': hashlib.sha256(task.encode()).hexdigest(),
              'diff': diff.relative_to(repo).as_posix(), 'allowed': sorted(allowed),
              'product_paths': products if run['layer'] != 'blind-hunter' else [],
              'claims': claims if run['layer'] == 'edge-case-hunter' else None,
              'files': {name: hashlib.sha256(repo_file(repo, name).read_bytes()).hexdigest() for name in allowed},
              'join_required': True, 'launch_mode': 'synchronous',
              'limitations': 'Observed tool paths/order are enforced; tracing quality and hidden backend context are not semantically certified.'}
    if policy['claims']:
        policy['files'][claims] = hashlib.sha256(repo_file(repo, claims).read_bytes()).hexdigest()
    policy_path = root / 'context-policy.json'
    atomic_json(policy_path, policy)
    run['context_package'] = {'path': policy_path.relative_to(repo).as_posix(), 'sha256': hashlib.sha256(policy_path.read_bytes()).hexdigest()}
    return {'prompt_file': policy['prompt'], 'prompt_sha256': policy['prompt_sha256'],
            'diff_file': policy['diff'], 'native_diff_file': (root / 'current-changes.diff').relative_to(repo).as_posix(), 'launch': {'mode': 'synchronous', 'wait_for_all': True,
            'pi': {'mode': 'parallel', 'async': False, 'worktree_argument': 'top-level-only'}},
            'context_package': run['context_package']}


def policy(repo, run, *, check_claims=True):
    ref = run.get('context_package')
    if not ref:
        return None
    path = repo_file(repo, ref['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
        raise ValueError('NATIVE_REVIEW_CONTEXT_PACKAGE_CHANGED')
    value = json.loads(path.read_text())
    for name, sha in value['files'].items():
        if name == value.get('claims') and not check_claims:
            continue
        if hashlib.sha256(repo_file(repo, name).read_bytes()).hexdigest() != sha:
            raise ValueError('NATIVE_REVIEW_CONTEXT_INPUT_CHANGED')
    return value


def prompt_matches(repo, run, prompt):
    value = policy(repo, run)
    return value is None or repo_file(repo, value['prompt']).read_text().strip() == prompt.strip().removeprefix('Task: ')


def targets(raw):
    from scripts.cao_tool_policy import _collect_path_strings, _event_key, SHELL_TOOLS
    tool = _event_key(raw, 'tool_name', 'toolName', 'tool', 'name').lower()
    payload = raw.get('tool_input') or raw.get('input') or raw.get('arguments') or {}
    if tool in SHELL_TOOLS:
        command = payload.get('command') or payload.get('cmd') or ''
        if re.search(r'[;&|<>`$\n]', command):
            return tool, [], False
        try:
            words = shlex.split(command)
        except ValueError:
            return tool, [], False
        if not words or words[0] not in {'cat', 'sed', 'head', 'tail', 'wc', 'rg', 'grep', 'ls', 'find'}:
            return tool, [], False
        program, args = words[0], words[1:]
        if program == 'sed':
            if len(args) < 3 or args[0] != '-n' or not re.fullmatch(r'[1-9][0-9]*(?:,[1-9][0-9]*)?p', args[1]):
                return tool, [], False
            return tool, args[2:], True
        if program in {'rg', 'grep'}:
            flags = {'-n', '-i', '-F', '-S', '-l', '--fixed-strings', '--line-number', '--ignore-case', '--files-with-matches'}
            while args and args[0] in flags:
                args = args[1:]
            if len(args) < 2 or args[0].startswith('-'):
                return tool, [], False
            paths = args[1:]
            return tool, paths, not any(p.startswith('-') for p in paths)
        if program not in {'cat', 'head', 'tail', 'wc'}:
            return tool, [], False
        paths, index = [], 0
        while index < len(args):
            word = args[index]
            if program in {'head', 'tail'} and word in {'-n', '-c'}:
                index += 1
                if index >= len(args) or not re.fullmatch(r'[1-9][0-9]*', args[index]):
                    return tool, [], False
            elif word.startswith('-'):
                allowed = {'--'} | ({'-n', '-b', '-E', '-T'} if program == 'cat' else {'-l', '-c', '-m'} if program == 'wc' else set())
                if word not in allowed:
                    return tool, [], False
            else:
                paths.append(word)
            index += 1
        return tool, paths, True
    return tool, _collect_path_strings(payload), True


def guard(repo, work, session, raw):
    if not session or not session.get('review_ticket'):
        return None
    run = (work.get('sdd', {}).get('native_reviews') or {}).get(session['review_ticket']['layer']) or {}
    if (run.get('execution_binding') or {}).get('session_id') != session['id']:
        return 'NATIVE_REVIEW_EXECUTION_UNBOUND: bind the ticket before inspection'
    value = policy(repo, run)
    if value is None:
        return None
    tool, paths, supported = targets(raw)
    if tool == 'contact_supervisor':
        return None
    if tool not in {'read', 'read_file', 'ls', 'find', 'grep', 'search_code', 'bash', 'shell', 'exec_command', 'write', 'write_file'} or not supported:
        return 'NATIVE_REVIEW_CONTEXT_TOOL_UNSUPPORTED: use explicit scoped read/search tools; request coordinator checks when needed'
    allowed = set(value['allowed']) | set(value['product_paths'])
    instruction_paths = set(value['allowed']) - {value['diff'], value['prompt']}
    if (value['claims'] and session.get('review_trace_paths') and value['diff'] in session.get('review_read_paths', [])
            and instruction_paths <= set(session.get('review_read_paths', []))):
        allowed.add(value['claims'])
    if tool in {'write', 'write_file'}:
        from scripts.review_dispatch import bundle_root
        root = bundle_root(repo, work, run['layer'], run)
        # Reviewers may emit findings, never alter their frozen input package.
        for raw_path in paths:
            path = repo_file(repo, raw_path)
            if path.parent != root or path.name not in {'findings.md', 'findings.json'}:
                return 'NATIVE_REVIEW_PRODUCT_WRITE_FORBIDDEN: return findings or write the designated findings file'
        return None if paths else 'NATIVE_REVIEW_CONTEXT_PATH_REQUIRED'
    observed = []
    for raw_path in paths:
        try:
            path = repo_file(repo, raw_path)
        except (ValueError, OSError):
            return 'NATIVE_REVIEW_CONTEXT_PATH_FORBIDDEN'
        if path.exists():
            name = path.relative_to(repo).as_posix()
            if path.is_dir() and tool in {'ls', 'find'} and value['layer'] != 'blind-hunter':
                # Directory listing names is allowed only inside product trees.
                if not any(p.startswith(name + '/') for p in value['product_paths']):
                    return 'NATIVE_REVIEW_CONTEXT_PATH_FORBIDDEN: ' + name
            elif name not in allowed:
                return 'NATIVE_REVIEW_CONTEXT_PATH_FORBIDDEN: ' + name
            observed.append(name)
    if not observed:
        return 'NATIVE_REVIEW_CONTEXT_PATH_REQUIRED: specify an allowed input/product path'
    return None


def observe_reads(repo, work, session, raw):
    if guard(repo, work, session, raw):
        return
    run = work['sdd']['native_reviews'][session['review_ticket']['layer']]
    value = policy(repo, run)
    if value is None:
        return
    tool, paths, _ = targets(raw)
    if tool in {'write', 'write_file', 'contact_supervisor'}:
        return
    names = []
    for name in paths:
        try:
            path = repo_file(repo, name)
            if path.is_file():
                names.append(path.relative_to(repo).as_posix())
        except (ValueError, OSError):
            continue
    session['review_read_paths'] = sorted(set(session.get('review_read_paths', [])) | set(names))
    if value['diff'] in session['review_read_paths']:
        session['review_trace_paths'] = sorted(set(session.get('review_trace_paths', [])) | (set(names) & set(value['product_paths'])))
    from scripts.cao_sessions import save_session
    save_session(repo, session)


def launch_guard(repo, work, raw):
    """Validate reviewer subagent payloads before handing them to the host."""
    from scripts.cao_tool_policy import _event_key
    if _event_key(raw, 'tool_name', 'toolName', 'tool', 'name').lower() != 'subagent':
        return None
    payload = raw.get('tool_input') or raw.get('input') or raw.get('arguments') or {}
    nested = payload.get('args') if isinstance(payload.get('args'), dict) else {}
    if 'CAO_REVIEW_TICKET' in json.dumps(payload) and payload.get('async') is not False:
        return 'NATIVE_REVIEW_SYNCHRONOUS_REQUIRED: reviewer workflows require top-level async:false'
    tasks = payload.get('tasks') or nested.get('tasks') or [payload]
    if not isinstance(tasks, list):
        return None
    records = (work.get('sdd') or {}).get('native_reviews') or {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        prompt = task.get('task') or task.get('prompt') or ''
        marker = re.search(r'(?m)^CAO_REVIEW_TICKET (\{[^\n]+\})', prompt)
        if not marker:
            continue
        try:
            ticket = json.loads(marker[1])
            run = records[ticket['layer']]
        except (ValueError, KeyError):
            return 'NATIVE_REVIEW_TASK_MISMATCH'
        if not prompt_matches(repo, run, prompt):
            return 'NATIVE_REVIEW_PROMPT_PACKAGE_REQUIRED: use prompt_file verbatim'
        if payload.get('async') is not False or task.get('async') is True or payload.get('mode') in {'async', 'detached', 'background'}:
            return 'NATIVE_REVIEW_SYNCHRONOUS_REQUIRED: launch blocking reviewer calls and await all results'
        if ('tasks' in payload or 'tasks' in nested) and 'worktree' in task:
            return 'NATIVE_REVIEW_HOST_ARGUMENT_INVALID: worktree belongs at the host top level'
    return None


def require_collected(repo, work):
    from scripts.cao_sessions import load_session
    records = (work.get('sdd') or {}).get('native_reviews') or {}
    for layer in (records.get('_contract') or {}).get('layers', []):
        run = records.get(layer) or {}
        if not run.get('context_package') or run.get('status') in {'classified', 'passed'}:
            continue
        binding = run.get('execution_binding') or {}
        session = load_session(repo, binding.get('session_id'), host=binding.get('host')) or {}
        if not session.get('review_output_sha256'):
            raise ValueError('NATIVE_REVIEW_ALL_OUTPUTS_REQUIRED: wait for final output from ' + layer)


def launch_payload(repo, work):
    """Build one blocking Pi batch from frozen prompt bytes."""
    records = (work.get('sdd') or {}).get('native_reviews') or {}
    layers = (records.get('_contract') or {}).get('layers', [])
    if not layers or any(not (records.get(layer) or {}).get('context_package') for layer in layers):
        return None
    tasks = []
    for layer in layers:
        run = records[layer]
        if run.get('status') != 'running' or run.get('execution_binding'):
            continue
        package = policy(repo, run, check_claims=False)
        tasks.append({'task': (repo / package['prompt']).read_text()})
    return {'async': False, 'worktree': False, 'tasks': tasks} if tasks else None


def workflow_launch(repo, work, definition):
    """Use the documented Pi workflow runs.run API in one blocking call."""
    from scripts.review_dispatch import agent_capability
    capability = agent_capability(repo, definition, 'pi')
    path = Path(definition)
    if not path.is_absolute():
        path = repo / path
    header = re.match(r'\A---\s*\n(.*?)\n---', path.read_text(), re.S)
    name = re.search(r'(?m)^name:[ \t]*([^\n]+)', header[1]) if header else None
    if not name:
        raise ValueError('NATIVE_REVIEW_AGENT_NAME_REQUIRED')
    agent = name[1].strip().strip('"\'')
    batch = launch_payload(repo, work)
    if not batch:
        return None
    calls = [f'runs.run({json.dumps("review-" + str(index))}, {json.dumps({"agent": agent, "task": task["task"]})})'
             for index, task in enumerate(batch['tasks'])]
    return {'async': False, 'script': 'return await Promise.all([' + ','.join(calls) + ']);',
            'capability': capability, 'resume_policy': 'Use returned runId, never a mission/session id. Inspect status by exact runId; resume eligibility is authoritative. Await workflow runs.run with resume for blocking recovery.'}
