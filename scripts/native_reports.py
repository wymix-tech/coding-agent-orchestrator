"""Read native result declarations and runner logs without rewriting artifacts.

Unknown prose is not a machine pass. These adapters certify report shape and
declared outcome, never the semantic correctness of the product itself.
"""
import json
import re


SUCCESS = {'passed', 'pass', 'success', 'successful', 'done', 'completed', 'complete', '通过', '成功', '已完成', '完成'}
FAILURE = {'failed', 'fail', 'failure', 'error', 'blocked', 'pending', 'running', 'in_progress', 'in-review', '失败', '未通过', '受阻', '待完成'}


def outcome(value):
    value = str(value).strip().strip('*`"\'').casefold()
    if value in SUCCESS:
        return 'passed'
    if value in FAILURE:
        return 'failed'
    return 'unknown'


def read(repo, work, path, *, execution=None):
    if execution is None:
        from scripts.cao_runtime import sha256_file
        relative = path.relative_to(repo).as_posix()
        from scripts.native_execution import receipts
        execution = next((row for row in reversed(receipts(work))
                          if row.get('status') == 'passed' and {'path': relative, 'sha256': sha256_file(path)} in row.get('reports', [])), None)
    text = path.read_text(encoding='utf-8-sig')
    if (not text.strip() and not execution) or len(text.encode()) > 8 * 1024 * 1024:
        raise ValueError('NATIVE_COMPLETION_REPORT_INVALID: empty or oversized report')
    provider = work['sdd']['provider']
    if provider not in {'bmad', 'openspec', 'speckit'}:
        raise ValueError('NATIVE_COMPLETION_PROVIDER_UNSUPPORTED')
    declarations, identities, exit_codes, commands = [], {}, [], []
    checks = 0
    check_records, declared_types = [], []
    if path.suffix.lower() == '.json':
        payload = json.loads(text)
        if not isinstance(payload, (dict, list)) or not payload:
            raise ValueError('NATIVE_COMPLETION_REPORT_INVALID')
        rows = [payload] if isinstance(payload, dict) else payload
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('NATIVE_COMPLETION_REPORT_INVALID')
            for key in ('work_id', 'work_item_id', 'provider', 'requirement_sha256', 'code_snapshot'):
                if key in row:
                    if key in identities and identities[key] != row[key]:
                        raise ValueError('NATIVE_COMPLETION_REPORT_IDENTITY_CONFLICT')
                    identities[key] = row[key]
            if row.get('evidence_type'):
                declared_types.append(str(row['evidence_type']))
            for key in ('status', 'outcome', 'result'):
                if key in row:
                    if not isinstance(row[key], str):
                        raise ValueError('NATIVE_COMPLETION_REPORT_INVALID')
                    declarations.append(outcome(row[key]))
            if 'exit_code' in row:
                if type(row['exit_code']) is not int:
                    raise ValueError('NATIVE_COMPLETION_REPORT_INVALID')
                exit_codes.append(row['exit_code'])
            for key in ('tests', 'results', 'items', 'checks'):
                if isinstance(row.get(key), list):
                    for item in row[key]:
                        if isinstance(item, dict) and 'status' in item:
                            declarations.append(outcome(item['status']))
                            checks += 1
                            check_records.append(item)
                elif isinstance(row.get(key), dict):
                    tests = row[key]
                    count = tests.get('count', tests.get('total'))
                    if type(count) is int:
                        checks += count
                        if count <= 0:
                            declarations.append('failed')
                    if any(type(tests.get(n)) is int and tests[n] > 0 for n in ('failed', 'errors', 'failures')):
                        declarations.append('failed')
        if isinstance(payload, dict) and isinstance(payload.get('command'), str):
            commands.append(payload['command'])
        adapter = provider + ':json-result-v1'
    else:
        # Field-based Markdown/frontmatter and bounded flat YAML declarations.
        # Ignore fenced examples: they are not authoritative result fields.
        fields, outside = [], []
        fence = None
        for line in text.splitlines():
            mark = re.match(r'^\s*(`{3,}|~{3,})', line)
            if mark:
                token = mark[1][0]
                fence = None if fence == token else token if fence is None else fence
                continue
            if fence:
                continue
            outside.append(line)
            match = re.match(r'^\s*(?:[-*]\s+)?(?:\*\*)?(evidence_type|status|outcome|result|状态|结果|work_id|work_item_id|provider|requirement_sha256|code_snapshot|exit_code|exit|command|命令)(?:\*\*)?\s*[:：](?:\*\*)?\s*(.+?)\s*$', line, re.I)
            if match:
                fields.append((match[1].casefold(), match[2].strip().strip('`')))
        for key, value in fields:
            if key == 'evidence_type':
                declared_types.append(value.strip("\"'"))
            elif key in {'status', 'outcome', 'result', '状态', '结果'}:
                declarations.append(outcome(value))
            elif key in {'exit', 'exit_code'}:
                if not re.fullmatch(r'-?\d+', value):
                    raise ValueError('NATIVE_COMPLETION_REPORT_INVALID')
                exit_codes.append(int(value))
            elif key in {'command', '命令'}:
                commands.append(value)
            else:
                value = value.strip('"\'')
                if key in identities and identities[key] != value:
                    raise ValueError('NATIVE_COMPLETION_REPORT_IDENTITY_CONFLICT')
                identities[key] = value
        text = '\n'.join(outside)
        # Familiar real runner summaries are evidence, not arbitrary success prose.
        tests = re.findall(r'(?m)^Ran (\d+) tests? in .+$', text)
        if tests and re.search(r'(?m)^OK(?:\s*\(.*\))?\s*$', text):
            checks += sum(map(int, tests))
            declarations.append('passed' if checks else 'failed')
        maven = re.findall(r'Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)', text)
        if maven:
            checks += sum(int(row[0]) for row in maven)
            declarations.append('failed' if not checks or any(int(f) or int(e) for _, f, e in maven) else 'passed')
        pytest = re.findall(r'(?m)^[= ]*(\d+) passed(?:[^\n]*) in [^\n]+$', text)
        if pytest:
            checks += sum(map(int, pytest))
            declarations.append('passed' if checks else 'failed')
        if re.search(r'(?m)^[= ]*.*\b[1-9]\d* failed[^\n]* in [^\n]+$', text):
            declarations.append('failed')
        node_count = re.search(r'(?m)^(?:#|ℹ) tests (\d+)', text)
        node_fail = re.search(r'(?m)^(?:#|ℹ) fail (\d+)', text)
        node_pass = re.search(r'(?m)^(?:#|ℹ) pass (\d+)', text)
        if node_count and node_fail and node_pass:
            checks += int(node_pass[1])
            declarations.append('passed' if int(node_pass[1]) > 0 and int(node_fail[1]) == 0 else 'failed')
        if re.search(r'(?m)^FAILED\b|\bBUILD FAILURE\b|^FAILURES!\s*$', text):
            declarations.append('failed')
        adapter = provider + ':native-text-v1'
    if execution:
        commands = list(dict.fromkeys([*commands, execution['command']]))
        exit_codes.append(execution['exit_code'])
    if any(code != 0 for code in exit_codes) or 'failed' in declarations:
        raise ValueError('NATIVE_COMPLETION_REPORT_NOT_PASSED')
    if 'unknown' in declarations or not (declarations or exit_codes):
        raise ValueError('NATIVE_COMPLETION_REPORT_UNSUPPORTED: no recognized native result; bind the original native test/result output, not an unclassified prose summary')
    # An exit-only report must still be attached to recorded native execution.
    workflow = (work.get('sdd') or {}).get('workflow') or {}
    if not declarations and not execution and not (workflow.get('execution') or {}).get('command'):
        raise ValueError('NATIVE_COMPLETION_EXECUTION_SOURCE_REQUIRED')
    expected = {'work_id': work['id'], 'work_item_id': work['id'], 'provider': provider,
                'requirement_sha256': work['requirement']['sha256']}
    for key, value in expected.items():
        if key in identities and identities[key] != value:
            raise ValueError('NATIVE_COMPLETION_REPORT_WRONG_SCOPE: ' + key)
    if 'code_snapshot' in identities:
        from scripts.lifecycle_contract import inputs
        if identities['code_snapshot'] != inputs(repo, work)['code_snapshot']:
            raise ValueError('NATIVE_COMPLETION_REPORT_WRONG_SNAPSHOT')
    types = sorted(set(declared_types)) or ['test_result' if checks else 'execution_result' if execution else 'workflow_declaration']
    if set(types) - {'test_result', 'execution_result', 'acceptance_result', 'security_result', 'manual_result', 'workflow_declaration'}:
        raise ValueError('NATIVE_COMPLETION_EVIDENCE_TYPE_UNSUPPORTED')
    coverage = {'verification'} if checks else set()
    for item in check_records:
        gates = item.get('gates', [])
        if not isinstance(gates, list) or not all(isinstance(g, str) for g in gates):
            raise ValueError('NATIVE_COMPLETION_CHECK_GATES_INVALID')
        if (item.get('id') and item.get('description') and outcome(item.get('status')) == 'passed'
                and item.get('evidence')):
            coverage.update(gates)
    return {'adapter': adapter, 'outcome': 'passed', 'identities': identities,
            'evidence_types': types, 'gate_coverage': sorted(coverage),
            'check_records': check_records,
            'checks_observed': checks, 'exit_codes': exit_codes, 'commands': commands,
            'source': path.relative_to(repo).as_posix()}


def validate_coverage(repo, work, reports, observations, gates, *, previous_binding=None):
    """Result declarations cannot be promoted to checks by CLI gate flags."""
    from scripts.native_execution import receipt_matches
    covered = set()
    for observation in observations:
        validate_references(repo, work, observation, previous_binding=previous_binding)
    for ref, row in zip(reports, observations):
        if 'workflow_declaration' in row['evidence_types'] and not row['check_records']:
            raise ValueError('NATIVE_COMPLETION_CHECK_EVIDENCE_REQUIRED: document/workflow status is not a verification result')
        observed = receipt_matches(repo, work, [ref], binding=previous_binding)
        identities = row['identities']
        scoped = identities.get('work_id', identities.get('work_item_id')) == work['id']
        from scripts.lifecycle_contract import inputs
        scoped = scoped and identities.get('code_snapshot') == inputs(repo, work)['code_snapshot']
        scoped = scoped and identities.get('requirement_sha256') == work['requirement']['sha256']
        if not observed and not scoped:
            raise ValueError('NATIVE_COMPLETION_EXECUTION_SOURCE_REQUIRED: capture actual output with cao lifecycle execute, or supply a native check report identifying this Work and code_snapshot')
        covered.update(row['gate_coverage'])
        if observed and row['exit_codes'] and row['commands']:
            covered.add('verification')
    missing = sorted(set(gates) - covered)
    if missing:
        raise ValueError('NATIVE_COMPLETION_GATE_EVIDENCE_REQUIRED: checks with id, description, status, evidence and gates are required for ' + ', '.join(missing))
    return sorted(covered)


def validate_references(repo, work, observation, *, previous_binding=None):
    """Dereference check evidence before granting declared gate coverage."""
    from scripts.path_guard import repo_file
    from scripts.cao_runtime import sha256_file
    from scripts.native_execution import receipt_matches
    execution_gates = {'verification', 'integration_verification', 'security_verification'}
    for index, check in enumerate(observation['check_records']):
        if not check.get('gates'):
            continue
        location = f'checks[{index}].evidence'
        refs = check.get('evidence')
        if not isinstance(refs, list) or not refs:
            raise ValueError('NATIVE_COMPLETION_REFERENCE_REQUIRED: ' + location + ' must be a nonempty array')
        verified = []
        for offset, ref in enumerate(refs):
            field = f'{location}[{offset}]'
            if not isinstance(ref, dict) or not isinstance(ref.get('path'), str) or not isinstance(ref.get('sha256'), str):
                raise ValueError('NATIVE_COMPLETION_REFERENCE_REQUIRED: ' + field)
            path = repo_file(repo, ref['path'])
            if not path.is_file():
                raise ValueError('NATIVE_COMPLETION_REFERENCE_MISSING: ' + field + '.path')
            actual = sha256_file(path)
            if actual != ref['sha256']:
                raise ValueError(f'NATIVE_COMPLETION_REFERENCE_CHANGED: {field}.sha256 expected={ref["sha256"]} actual={actual}')
            verified.append({'path': path.relative_to(repo).as_posix(), 'sha256': actual})
        if execution_gates.intersection(check['gates']) and not receipt_matches(repo, work, verified, binding=previous_binding):
            raise ValueError('NATIVE_COMPLETION_REFERENCE_EXECUTION_REQUIRED: ' + location)
