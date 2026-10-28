"""Record an explicitly requested native command, without adding acceptance rules."""
import json
import uuid


def material_inputs(repo, work):
    from scripts.evidence_domains import capture
    domains = capture(repo, work)
    return {key: domains[key] for key in ('product', 'native_sources', 'native_scope', 'native_dependencies', 'policy')}


def receipt_matches(repo, work, reports):
    from scripts.lifecycle_contract import inputs
    from scripts.cao_runtime import sha256_file
    from scripts.path_guard import repo_file
    current = inputs(repo, work)
    rows = (work.get('sdd') or {}).get('completion_executions') or []
    if not rows:
        return False
    accepted = []
    for row in reversed(rows):
        if row.get('generation') != work.get('execution_generation', 0) or row.get('inputs') != current or row.get('domains') != material_inputs(repo, work):
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
        validate_command(work, args.command)
        path = provider_artifact(repo, args.report, provider_output_dirs(work['sdd']['provider'], repo))
        if path.suffix.lower() not in {'.md', '.txt', '.log', '.json', '.yaml', '.yml'} or not path.parent.is_dir():
            raise ValueError('NATIVE_EXECUTION_LOG_PATH_REQUIRED')
        if path.relative_to(repo).as_posix() in material_inputs(repo, work)['native_sources']:
            raise ValueError('NATIVE_EXECUTION_REPORT_IS_SOURCE')
        from scripts.evidence_domains import dependencies
        observed_dependencies = dependencies(repo, work, [args.command])
        work['sdd']['native_execution_dependencies'] = sorted(set(work['sdd'].get('native_execution_dependencies', [])) | set(observed_dependencies))
        rows = work['sdd'].setdefault('completion_executions', [])
        if rows and rows[-1]['status'] == 'running':
            previous = rows[-1]['run_id']
            if owner_status(repo, work['id'], previous) == 'running' or args.retry_of != previous:
                raise ValueError('NATIVE_EXECUTION_UNRESOLVED: inspect runtime show; explicitly retry with --retry-of ' + previous + ' only after inspecting unknown effects')
            rows[-1]['status'] = 'abandoned'
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
                    read(repo, work, path)
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
        if not work or work.get('status') != 'active' or work.get('lifecycle_contract') != 2:
            raise ValueError('ACTIVE_CONTRACT_2_WORK_REQUIRED')
        run = work.get('verification') or {}
        if run.get('status') not in {'failed', 'blocked', 'error', 'stale'}:
            raise ValueError('FAILED_BASELINE_REQUIRED')
        if not args.reason.strip():
            raise ValueError('REPAIR_REASON_REQUIRED')
        previous = work['sdd'].get('completion_repair')
        if previous and previous.get('kind') == 'product' and args.kind != 'product':
            raise ValueError('NATIVE_PRODUCT_REPAIR_UNRESOLVED: complete native repair before changing its classification')
        if previous:
            work['sdd'].setdefault('completion_repair_history', []).append(previous)
        row = {'kind': args.kind, 'reason': args.reason, 'run_id': run.get('run_id'), 'at': now()}
        work['sdd']['completion_repair'] = row
        work['execution'].update(phase='implementation' if args.kind == 'product' else 'verification',
                                 step='implementation' if args.kind == 'product' else 'verification')
        queue_event(work, {'event': 'sdd.completion.repair.classified', 'work_item_id': work['id'], **row})
        save_work(repo, work)
    print(json.dumps({'ok': True, 'repair': row}))
    return 0


def validate_command(work, command):
    """Only replay a recorded native invocation, never cat/echo an old result."""
    import shlex
    from pathlib import Path
    def identity(value):
        tokens = shlex.split(value)
        if tokens and Path(tokens[0]).name.startswith('python'):
            tokens[0] = 'python'
        return tokens
    sdd = work.get('sdd') or {}
    expected = [value for row in (sdd.get('completion_evidence') or {}).get('observations', []) for value in row.get('commands', [])]
    expected.extend(row['command'] for row in sdd.get('completion_executions', []) if row.get('status') == 'passed' and row.get('command'))
    recorded = ((sdd.get('workflow') or {}).get('execution') or {}).get('command')
    if recorded:
        expected.append(recorded)
    if not expected or not any(identity(command) == identity(value) for value in expected):
        raise ValueError('NATIVE_EXECUTION_COMMAND_UNBOUND: use the recorded native workflow/report command; restoring a report with cat/echo is not native execution')
