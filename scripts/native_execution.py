"""Record an explicitly requested native command, without adding acceptance rules."""
import json
import uuid


def material_inputs(repo, work):
    from scripts.evidence_domains import capture
    domains = capture(repo, work)
    return {key: domains[key] for key in ('product', 'native_sources', 'native_scope', 'native_dependencies', 'policy')}


def receipts(work):
    sdd = work.get('sdd') or {}
    return sorted([*sdd.get('completion_executions', []), *sdd.get('native_host_executions', [])], key=lambda row: row.get('at', 0))


def receipt_matches(repo, work, reports, *, binding=None):
    from scripts.lifecycle_contract import inputs
    from scripts.cao_runtime import sha256_file
    from scripts.path_guard import repo_file
    current = inputs(repo, work)
    rows = receipts(work)
    if not rows or rows[-1].get('status') != 'passed':
        return False
    repair = (work.get('sdd') or {}).get('completion_repair') or {}
    excluded = set()
    if repair.get('kind') == 'product':
        # Historical repairs have no reliable execution boundary. A new execute
        # captures it under the same transaction before admitting a new run.
        if 'execution_boundary' not in repair:
            return False
        excluded = set(repair['execution_boundary'])
    binding = binding if binding is not None else (work.get('sdd') or {}).get('completion_evidence') or {}
    # Switching sessions invalidates in-flight admission, not an already bound
    # successful result whose exact material inputs and bytes remain unchanged.
    bound_current = (repair.get('kind') != 'product' and binding.get('evidence_protocol') == 2 and binding.get('inputs') == current
                     and all(ref in binding.get('reports', []) for ref in reports))
    accepted = []
    for row in reversed(rows):
        if row.get('run_id') in excluded:
            continue
        if (row.get('generation') != work.get('execution_generation', 0) and not bound_current) or row.get('inputs') != current or row.get('domains') != material_inputs(repo, work):
            continue
        if row.get('status') != 'passed':
            break
        accepted.extend(row.get('reports', []))
    return all(ref in accepted and sha256_file(repo_file(repo, ref['path'])) == ref['sha256'] for ref in reports)



def command(args):
    from scripts.cao_runtime import repo_root, now, sha256_file
    from scripts.cao_work_store import resolve_work, load_work, save_work, queue_event
    from scripts.lifecycle_contract import inputs, enabled
    from scripts.state_store import repo_transaction
    from scripts.path_guard import provider_artifact
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.runtime_store import start_run
    from scripts.runtime_execution import execute, owner, owner_status, finish_execution
    from scripts.native_reports import read
    repo = repo_root(args.repo)
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if not work or not enabled(work) or work.get('status') != 'active':
            raise ValueError('ACTIVE_CONTRACT_2_WORK_REQUIRED')
        validate_command(work, args.command, repo)
        path = provider_artifact(repo, args.report, provider_output_dirs(work['sdd']['provider'], repo))
        if path.suffix.lower() not in {'.md', '.txt', '.log', '.json', '.yaml', '.yml'} or not path.parent.is_dir():
            raise ValueError('NATIVE_EXECUTION_LOG_PATH_REQUIRED')
        if path.relative_to(repo).as_posix() in material_inputs(repo, work)['native_sources']:
            raise ValueError('NATIVE_EXECUTION_REPORT_IS_SOURCE')
        outputs = work['sdd'].setdefault('native_execution_reports', [])
        output = path.relative_to(repo).as_posix()
        if output not in outputs:
            outputs.append(output)
        from scripts.evidence_domains import dependencies
        observed_dependencies = dependencies(repo, work, [args.command])
        work['sdd']['native_execution_dependencies'] = sorted(set(work['sdd'].get('native_execution_dependencies', [])) | set(observed_dependencies))
        rows = work['sdd'].setdefault('completion_executions', [])
        repair = work['sdd'].get('completion_repair') or {}
        if repair.get('kind') == 'product' and 'execution_boundary' not in repair:
            repair['execution_boundary'] = [row['run_id'] for row in receipts(work)]
        if rows and rows[-1]['status'] == 'running':
            previous = rows[-1]['run_id']
            if owner_status(repo, work['id'], previous) == 'running' or args.retry_of != previous:
                raise ValueError('NATIVE_EXECUTION_UNRESOLVED: inspect runtime show; explicitly retry with --retry-of ' + previous + ' only after inspecting unknown effects')
            rows[-1]['status'] = 'abandoned'
            rows[-1]['resolution'] = {'source': 'agent_declaration', 'kind': 'explicit_retry',
                                     'retry_of': previous, 'at': now()}
            queue_event(work, {'event': 'sdd.completion.execution.abandoned', 'work_item_id': work['id'],
                               'run_id': previous, 'resolution': rows[-1]['resolution']})
        run_id = 'native-' + uuid.uuid4().hex
        captured = inputs(repo, work)
        row = {'run_id': run_id, 'status': 'running', 'inputs': captured, 'command': args.command,
               'generation': work.get('execution_generation', 0), 'dependencies': observed_dependencies, 'domains': material_inputs(repo, work), 'at': now()}
        rows.append(row)
        start_run(repo, work['id'], run_id, 'native_completion', captured, generation=row['generation'])
        save_work(repo, work)
        wid = work['id']
    with owner(repo, wid, run_id):
        result = execute(repo, wid, run_id, 'command-0001', args.command, args.timeout_seconds, max_bytes=8 * 1024 * 1024)
        finish_execution(repo, wid, run_id, [result])
        with repo_transaction(repo):
            work = load_work(repo, wid)
            row = next(r for r in work['sdd']['completion_executions'] if r['run_id'] == run_id)
            valid = work['sdd']['completion_executions'][-1]['run_id'] == run_id and work.get('status') == 'active' and work.get('execution_generation', 0) == row['generation'] and inputs(repo, work) == captured and material_inputs(repo, work) == row['domains']
            row.update(status='failed', exit_code=result['exit_code'], runtime=result['runtime'])
            if valid and result['exit_code'] == 0 and max(result.get('stdout_bytes', 0), result.get('stderr_bytes', 0)) <= 8 * 1024 * 1024:
                # Store actual runner output, never merely attach an existing report.
                body = result.get('stdout_tail', '') + '\n' + result.get('stderr_tail', '')
                if path.suffix.lower() in {'.json', '.yaml', '.yml'}:
                    body = result.get('stdout_tail') or result.get('stderr_tail', '')
                path = provider_artifact(repo, args.report, provider_output_dirs(work['sdd']['provider'], repo))
                path.write_text(body, encoding='utf-8')
                try:
                    observation = read(repo, work, path, execution=row)
                    row['observation'] = observation
                    if inputs(repo, work) != captured or material_inputs(repo, work) != row['domains']:
                        raise ValueError('NATIVE_EXECUTION_INPUT_CHANGED')
                    work['sdd'].pop('completion_binding_failure', None)
                    row.update(status='passed', reports=[{'path': path.relative_to(repo).as_posix(), 'sha256': sha256_file(path)}])
                except (ValueError, OSError) as exc:
                    row['reason'] = str(exc)
            elif not valid:
                row.update(status='stale', reason='NATIVE_EXECUTION_INPUT_CHANGED')
            queue_event(work, {'event': 'sdd.completion.executed', 'work_item_id': wid, 'run_id': run_id, 'status': row['status']})
            save_work(repo, work)
    print(json.dumps({'ok': row['status'] == 'passed', 'execution': row}))
    return 0 if row['status'] == 'passed' else 2


def repair(args):
    from scripts.cao_runtime import repo_root, now
    from scripts.cao_work_store import resolve_work, save_work, queue_event
    from scripts.state_store import repo_transaction
    repo = repo_root(args.repo)
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if not work or work.get('status') != 'active' or work.get('lifecycle_contract') not in {1, 2}:
            raise ValueError('ACTIVE_CONTRACT_2_WORK_REQUIRED')
        run = work.get('verification') or {}
        if run.get('status') not in {'failed', 'blocked', 'error', 'stale'}:
            from scripts.verification_probe import read_project_result, project_standard, certification
            from scripts.project_context import load_context, selected_scopes
            records = [(scope, read_project_result(repo, scope)) for scope in selected_scopes(load_context(repo), work=work)]
            failed = [(scope, row) for scope, row in records if row.get('status') in {'failed', 'blocked', 'error'}]
            if not failed or len({row.get('run_id') for _, row in failed}) != 1:
                raise ValueError('FAILED_BASELINE_REQUIRED: inspect or rerun cao verification check --project for the bound scopes')
            for scope, row in failed:
                standard = project_standard(repo, scope)
                certified = certification(repo, scope, row) or {}
                if row.get('standard') != standard or certified.get('status') != 'passed' or certified.get('standard') != standard:
                    raise ValueError('PROJECT_VERIFICATION_CHECK_REQUIRED: establish the current project standard before classifying a product repair')
            run = {'run_id': failed[0][1]['run_id'], 'status': failed[0][1]['status'], 'source': 'project_preparation'}
        if not args.reason.strip():
            raise ValueError('REPAIR_REASON_REQUIRED')
        previous = work['sdd'].get('completion_repair')
        if previous and previous.get('kind') == 'product' and args.kind != 'product':
            raise ValueError('NATIVE_PRODUCT_REPAIR_UNRESOLVED: complete native repair before changing its classification')
        if previous:
            work['sdd'].setdefault('completion_repair_history', []).append(previous)
        row = {'kind': args.kind, 'reason': args.reason, 'run_id': run.get('run_id'), 'at': now(), 'source': run.get('source', 'work_verification')}
        if args.kind == 'product':
            row['execution_boundary'] = [item['run_id'] for item in receipts(work)]
        work['sdd']['completion_repair'] = row
        work['execution'].update(phase='implementation' if args.kind == 'product' else 'verification',
                                 step='implementation' if args.kind == 'product' else 'verification')
        queue_event(work, {'event': 'sdd.completion.repair.classified', 'work_item_id': work['id'], **row})
        save_work(repo, work)
    print(json.dumps({'ok': True, 'repair': row}))
    return 0


def validate_command(work, command, repo=None):
    """Only replay a recorded native invocation, never cat/echo an old result."""
    import shlex
    from pathlib import Path
    from scripts.native_validation import execution_command
    if not execution_command(command):
        raise ValueError('NATIVE_EXECUTION_COMMAND_UNBOUND: report replay and inline result synthesis are not native verification commands')
    def identity(value):
        tokens = shlex.split(value)
        if tokens and Path(tokens[0]).name.startswith('python'):
            tokens[0] = 'python'
        return tokens
    sdd = work.get('sdd') or {}
    expected = [value for row in (sdd.get('completion_evidence') or {}).get('observations', []) for value in row.get('commands', [])]
    expected.extend(row['command'] for row in receipts(work) if row.get('status') == 'passed' and row.get('command'))
    recorded = ((sdd.get('workflow') or {}).get('execution') or {}).get('command')
    if recorded:
        expected.append(recorded)
    if repo is not None:
        from scripts.cao_runtime import config
        from scripts.completion_policy import normalize_scripts
        expected.extend(row['command'] for row in normalize_scripts(config(repo).get('verification', {}).get('scripts', [])))
        from scripts.native_validation import commands
        expected.extend(commands(repo, work))
    if not expected or not any(identity(command) == identity(value) for value in expected):
        raise ValueError('NATIVE_EXECUTION_COMMAND_UNBOUND: use a recorded native command, accepted project check, or declare a command from the bound native specification with cao lifecycle declare')


def inspect_executions(args):
    from scripts.cao_runtime import repo_root
    from scripts.cao_work_store import resolve_work
    from scripts.lifecycle_contract import inputs
    repo = repo_root(args.repo)
    if not 0 <= args.cursor <= 100000:
        raise ValueError('NATIVE_EXECUTION_CURSOR_INVALID')
    work = resolve_work(repo, args.work)
    if not work:
        raise ValueError('NO_WORK_ITEM')
    rows = list(reversed(receipts(work)))
    try:
        current, input_error = inputs(repo, work), None
    except (ValueError, OSError, RuntimeError) as exc:
        current, input_error = None, str(exc)
    selected = rows[args.cursor:args.cursor + args.limit]
    result = [{**{k: row.get(k) for k in ('run_id', 'status', 'source', 'command', 'exit_code', 'reason', 'reports')},
               'fresh': current is not None and row.get('inputs') == current and row.get('domains') == material_inputs(repo, work)} for row in selected]
    print(json.dumps({'ok': True, 'executions': result, 'total': len(rows), 'input_error': input_error,
                      'next_cursor': args.cursor + len(selected) if args.cursor + len(selected) < len(rows) else None}))
    return 0
