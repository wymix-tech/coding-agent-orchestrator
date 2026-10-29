"""Explicit command declarations grounded in the current native source."""
import hashlib
import json
import re
import shlex


def sources(work):
    sdd = work.get('sdd') or {}
    names = set((sdd.get('workflow') or {}).get('artifacts') or [])
    route = sdd.get('route_decision') or {}
    if route.get('primary_artifact'):
        names.add(route['primary_artifact'])
    names.update(row['path'] for row in (sdd.get('plan_scope') or {}).get('plans', []))
    ref = (sdd.get('spec_binding') or {}).get('spec') or {}
    if ref.get('path'):
        names.add(ref['path'])
    return names



def execution_command(command):
    """Do not admit literal report replay or inline result synthesis as a check."""
    from pathlib import Path
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens or re.search(r'[\n\r;&|<>`$]', command):
        return False
    if Path(tokens[0]).name == 'env':
        tokens = [token for token in tokens[1:] if '=' not in token]
    if not tokens:
        return False
    program = Path(tokens[0]).name
    data_tools = {'cat', 'echo', 'printf', 'cp', 'tee', 'head', 'tail', 'sed', 'awk', 'jq', 'true', 'false', 'sleep', 'ls', 'find', 'pwd', 'wc', 'touch'}
    if program in data_tools:
        return False
    if program in {'sh', 'bash', 'zsh', 'dash'} and len(tokens) > 1 and tokens[1].startswith('-') and 'c' in tokens[1]:
        return False
    if program.startswith('python') and len(tokens) > 1 and tokens[1].startswith('-') and 'c' in tokens[1]:
        return False
    if program in {'node', 'perl', 'ruby'} and any(token in {'-e', '--eval', '-p', '--print'} for token in tokens[1:2]):
        return False
    return True

def literal(text, command):
    # A whole line or code span is an explicit command, prose substrings are not.
    normalized = shlex.split(command)
    if not normalized or re.search(r'[\n\r;&|<>`$]', command):
        return False
    candidates = re.findall(r'`([^`\n]+)`', text)
    candidates.extend(line.strip() for line in text.splitlines())
    for candidate in candidates:
        try:
            if shlex.split(candidate) == normalized:
                return True
        except ValueError:
            continue
    return False


def commands(repo, work):
    from scripts.path_guard import repo_file
    result = []
    for row in (work.get('sdd') or {}).get('native_validation_commands', []):
        if row['source'] not in sources(work) or row.get('requirement_sha256') != work['requirement']['sha256']:
            continue
        path = repo_file(repo, row['source'])
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row['source_sha256'] and literal(path.read_text(), row['command']):
            result.append(row['command'])
    # Bound, unchanged SPEC commands are available before the first host run.
    binding = (work.get('sdd') or {}).get('spec_binding') or {}
    ref = binding.get('spec') or {}
    if binding.get('requirement_sha256') == work['requirement']['sha256'] and ref.get('path') and ref.get('sha256'):
        path = repo_file(repo, ref['path'])
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == ref['sha256']:
            for candidate in command_candidates(path.read_text()):
                try:
                    tokens = shlex.split(candidate) if execution_command(candidate) else []
                except ValueError:
                    continue
                if native_check(tokens):
                    result.append(candidate)
    return list(dict.fromkeys(result))


def declare(args):
    from scripts.cao_runtime import repo_root, now
    from scripts.cao_work_store import resolve_work, save_work, queue_event
    from scripts.path_guard import provider_artifact
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.state_store import repo_transaction
    repo = repo_root(args.repo)
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if not work or work.get('status') != 'active' or work.get('lifecycle_contract') != 2:
            raise ValueError('ACTIVE_CONTRACT_2_WORK_REQUIRED')
        path = provider_artifact(repo, args.source, provider_output_dirs(work['sdd']['provider'], repo))
        source = path.relative_to(repo).as_posix()
        if source not in sources(work) or not args.reason.strip() or not execution_command(args.command) or not literal(path.read_text(), args.command):
            raise ValueError('NATIVE_VALIDATION_SOURCE_REQUIRED: command must be literal in a bound native specification/plan')
        row = {'command': args.command, 'source': source, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'requirement_sha256': work['requirement']['sha256'], 'reason': args.reason, 'at': now()}
        rows = work['sdd'].setdefault('native_validation_commands', [])
        if not any(all(old.get(k) == row[k] for k in ('command', 'source', 'source_sha256', 'requirement_sha256')) for old in rows):
            rows.append(row)
            queue_event(work, {'event': 'sdd.validation.declared', 'work_item_id': work['id'], **row})
            save_work(repo, work)
    print(json.dumps({'ok': True, 'declaration': row, 'next': 'Run cao lifecycle execute with this command to collect its actual output.'}))
    return 0


def command_candidates(text):
    """Inline commands and complete lines in shell fences."""
    result = re.findall(r'(?<!`)`([^`\n]+)`(?!`)', text)
    for block in re.findall(r'(?ms)^```(?:bash|sh|shell|console)?[ \t]*\n(.*?)^```', text):
        result.extend(line.strip().removeprefix('$ ') for line in block.splitlines() if line.strip() and not line.lstrip().startswith('#'))
    return list(dict.fromkeys(result))


def native_check(tokens):
    if not tokens:
        return False
    from pathlib import Path
    program = Path(tokens[0]).name
    if program == 'pytest':
        return True
    if program.startswith('python') and tokens[1:3] == ['-m', 'pytest']:
        return True
    if program in {'mvn', 'mvnw', 'gradle', 'gradlew', 'go', 'cargo', 'npm', 'pnpm', 'yarn', 'dotnet'}:
        return any(token in {'test', 'verify', 'check', 'build', 'compile', 'package', 'lint', 'audit', 'test:unit', 'test:integration'} for token in tokens[1:])
    return False
