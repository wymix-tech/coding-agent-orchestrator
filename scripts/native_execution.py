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
        if row.get('inputs') != current or row.get('domains') != material_inputs(repo, work):
            continue
        if row.get('status') != 'passed' or row.get('generation') != work.get('execution_generation', 0):
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
        path = provider_artifact(repo, args.report, provider_output_dirs(work['sdd']['provider'], repo))
        if path.suffix.lower() not in {'.md', '.txt', '.log'} or not path.parent.is_dir():
            raise ValueError('NATIVE_EXECUTION_LOG_PATH_REQUIRED')
        rows = work['sdd'].setdefault('completion_executions', [])
        if rows and rows[-1]['status'] == 'running':
            previous = rows[-1]['run_id']
            if owner_status(repo, work['id'], previous) == 'running' or args.retry_of != previous:
                raise ValueError('NATIVE_EXECUTION_UNRESOLVED: inspect runtime show; explicitly retry with --retry-of ' + previous + ' only after inspecting unknown effects')
            rows[-1]['status'] = 'abandoned'
        run_id = 'native-' + uuid.uuid4().hex
        captured = inputs(repo, work)
        row = {'run_id': run_id, 'status': 'running', 'inputs': captured,
               'generation': work.get('execution_generation', 0), 'domains': material_inputs(repo, work), 'at': now()}
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
            valid = work.get('status') == 'active' and work.get('execution_generation', 0) == row['generation'] and inputs(repo, work) == captured and material_inputs(repo, work) == row['domains']
            row.update(status='failed', exit_code=result['exit_code'], runtime=result['runtime'])
            if valid and result['exit_code'] == 0:
                # Store actual runner output, never merely attach an existing report.
                body = result.get('stdout_tail', '') + '\n' + result.get('stderr_tail', '')
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
