"""Versioned native completion bindings and one pre-verification evidence policy."""
from copy import deepcopy


VERSION = 2
NATIVE_VERIFICATION_GATES = {"security_verification", "integration_verification",
                             "compatibility_verification", "data_verification"}


def enabled(work):
    return bool(work and work.get('lifecycle_contract') == VERSION)


def prerequisites(repo, work):
    """Collect native/process duties without requiring a future technical run."""
    from scripts.cao_completion import lifecycle_status
    result = lifecycle_status(repo, work, pre_verification=True)
    return result


def inputs(repo, work):
    from scripts.cao_runtime import config, sha256_file
    from scripts.cao_completion import verification_inputs
    from scripts.cao_plans import provider_verification_plan_state
    from scripts.native_obligations import digest
    # Baseline execution transport has its own freshness domain. Product code,
    # tests and configuration remain in code_snapshot.
    technical = verification_inputs(repo, work, config(repo))
    sdd = work.get('sdd') or {}
    return {
        'work_id': work['id'], 'provider': sdd.get('provider'),
        'requirement_sha256': sha256_file(repo / work['requirement']['path']),
        'code_snapshot': technical['code_snapshot'],
        'assessment_revision': (work.get('path_assessment') or {}).get('revision'),
        'scope_sha256': digest(provider_verification_plan_state(repo, work)),
        'route_id': (sdd.get('route_decision') or {}).get('id'),
    }


def evidence_state(repo, work):
    """Validate original native reports; never execute a second acceptance suite."""
    from scripts.cao_runtime import sha256_file
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.path_guard import provider_artifact
    record = (work.get('sdd') or {}).get('completion_evidence') or {}
    pending = {'ready': False, 'state': 'pending', 'reason': 'NATIVE_COMPLETION_EVIDENCE_REQUIRED',
               'next': 'Finish the native workflow and bind its actual test/acceptance completion report with `cao lifecycle bind --report <native-report>`; no separate CAO functional executor is required.'}
    if not record:
        return pending
    try:
        current = inputs(repo, work)
        changed = [key for key, value in current.items() if record.get('inputs', {}).get(key) != value]
        if changed:
            return {**pending, 'state': 'stale', 'reason': 'NATIVE_COMPLETION_EVIDENCE_STALE',
                    'changed_inputs': changed,
                    'next': 'Complete the affected native work and refresh its actual completion report; then bind it again. Preserve unaffected native review history.'}
        required = set((work.get('path_assessment') or {}).get('required_gates', [])) & NATIVE_VERIFICATION_GATES
        missing = sorted(required - set(record.get('gates', [])))
        if missing:
            return {**pending, 'reason': 'NATIVE_VERIFICATION_EVIDENCE_REQUIRED', 'pending_gates': missing,
                    'next': 'Complete these checks in the native workflow, then bind the actual report with ' + ' '.join('--gate ' + name for name in missing) + ': ' + ', '.join(missing)}
        reports = record.get('reports') or []
        if not reports:
            return pending
        provider = work['sdd']['provider']
        for ref in reports:
            path = provider_artifact(repo, ref['path'], provider_output_dirs(provider, repo))
            if not path.read_bytes().strip() or sha256_file(path) != ref['sha256']:
                return {**pending, 'state': 'stale', 'reason': 'NATIVE_COMPLETION_REPORT_CHANGED',
                        'changed_paths': [ref['path']]}
        return {'ready': True, 'state': 'passed', 'owner': provider, 'reports': reports,
                'inputs': current, 'provenance': record.get('provenance'),
                'note': 'Bound native execution evidence, not a second CAO functional acceptance run.'}
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        return {**pending, 'state': 'blocked', 'reason': 'NATIVE_COMPLETION_SOURCE_INVALID', 'error': str(exc)}


def conformance(repo, work):
    from scripts.conformance import summarize
    native = prerequisites(repo, work)
    report = summarize(repo, work, native)
    evidence = evidence_state(repo, work)
    if not evidence['ready']:
        report['ready'] = False
        report['blockers'].append({'requirement_id': 'native_completion_evidence',
                                   'reason_code': evidence['reason'], 'repair_action': evidence['next'],
                                   'status': evidence['state'], 'owner': work['sdd']['provider']})
    report['native_completion_evidence'] = evidence
    return report


def completion(repo, work):
    from scripts.cao_completion import verification_inputs
    from scripts.cao_runtime import config
    from scripts.completion_policy import verification_fresh
    if work.get('status') == 'completed':
        result = deepcopy(work.get('completion_result') or {})
        return {**result, 'ready': False, 'historical': True,
                'reason': 'work status is completed', 'next': 'Work is completed; explicitly reopen before validating current inputs.'}
    report = conformance(repo, work)
    run = work.get('verification') or {}
    try:
        fresh = verification_fresh(work, verification_inputs(repo, work, config(repo)))
    except (ValueError, OSError, RuntimeError) as exc:
        fresh = False
        report['input_error'] = str(exc)
    technical = {'status': 'passed' if fresh else 'stale' if run.get('status') == 'passed' else run.get('status', 'pending'),
                 'fresh': fresh, 'run_id': run.get('run_id'), 'checks': run.get('results', [])}
    native = report['native_completion_evidence']
    blockers = [row.get('reason_code') or row['requirement_id'] for row in report['blockers']]
    if not fresh:
        blockers.append('PROJECT_BASELINE_NOT_PASSED')
    ready = report['ready'] and fresh
    action = next_action(repo, work)
    return {'ready': ready, 'reason': blockers[0] if blockers else None, 'blockers': blockers,
            'conformance': report, 'technical_acceptance': technical,
            'native_completion': native, 'task_acceptance': {**native, 'fresh': native['ready']},
            'lifecycle_ready': report['ready'], 'next_action': action, 'next': action['instruction']}


def next_action(repo, work):
    """One precedence shared by next, sync, verification and completion."""
    native = prerequisites(repo, work)
    if not native['ready']:
        blocker = native['blockers'][0]
        return {'type': 'agent', 'stage': 'verification_setup' if blocker.get('obligation') == 'baseline' else 'execution', 'reason': blocker.get('reason'),
                'instruction': blocker.get('next') or 'Continue the selected native workflow; complete all associated tasks and native review/finalize obligations.'}
    evidence = evidence_state(repo, work)
    if not evidence['ready']:
        return {'type': 'agent', 'stage': 'execution' if evidence.get('changed_inputs') else 'completion',
                'reason': evidence['reason'], 'instruction': evidence['next']}
    from scripts.cao_completion import verification_inputs
    from scripts.cao_runtime import config
    from scripts.completion_policy import verification_fresh
    if not verification_fresh(work, verification_inputs(repo, work, config(repo))):
        return {'type': 'agent', 'stage': 'completion', 'reason': 'PROJECT_BASELINE_REQUIRED',
                'instruction': 'Inspect `cao conformance check`, then run `cao verify` against the accepted project baseline.'}
    return {'type': 'agent', 'stage': 'completion', 'instruction': 'Run `cao done`.'}


def command(args):
    import json
    from scripts.cao_runtime import repo_root, ensure_initialized, now, sha256_file
    from scripts.cao_work_store import resolve_work, save_work, queue_event
    from scripts.state_store import repo_transaction
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.path_guard import provider_artifact
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if not work or work.get('status') not in {'active', 'suspended'}:
            raise ValueError('ACTIVE_OR_SUSPENDED_WORK_REQUIRED')
        if args.lifecycle_command == 'migrate':
            if enabled(work):
                print(json.dumps({'ok': True, 'status': 'unchanged', 'work_id': work['id']}))
                return 0
            if work.get('lifecycle_contract', 1) != 1:
                raise ValueError('LIFECYCLE_CONTRACT_UNSUPPORTED')
            if ((work.get('verification') or {}).get('status') == 'running'
                    or (((work.get('sdd') or {}).get('acceptance') or {}).get('run') or {}).get('status') == 'running'):
                raise ValueError('LIFECYCLE_RUN_ACTIVE: finish or recover the running verification/acceptance before migration')
            previous = deepcopy(work)
            work['lifecycle_contract'] = VERSION
            work['execution_generation'] = int(work.get('execution_generation', 0)) + 1
            work.setdefault('lifecycle_migrations', []).append({'from': 1, 'to': VERSION,
                'at': now(), 'previous_execution': previous.get('execution'),
                'previous_acceptance': previous.get('sdd', {}).get('acceptance')})
            queue_event(work, {'event': 'cao.lifecycle.migrated', 'work_item_id': work['id'],
                               'from_contract': 1, 'to_contract': VERSION, 'occurred_at': now()})
            save_work(repo, work)
            from scripts.cao_native_sync import reconcile_native_sdd_state
            reconcile_native_sdd_state(repo, work)
            print(json.dumps({'ok': True, 'status': 'migrated', 'work_id': work['id'],
                              'next': next_action(repo, work)}, ensure_ascii=False))
            return 0
        if not enabled(work):
            raise ValueError('LIFECYCLE_MIGRATION_REQUIRED: run cao lifecycle migrate')
        if work.get('status') != 'active':
            raise ValueError('WORK_NOT_ACTIVE')
        ready = prerequisites(repo, work)
        if not ready['ready']:
            print(json.dumps({'ok': False, 'error': 'NATIVE_COMPLETION_REQUIRED', **ready}, ensure_ascii=False))
            return 2
        reports = []
        for name in args.report:
            path = provider_artifact(repo, name, provider_output_dirs(work['sdd']['provider'], repo))
            if path.suffix.lower() not in {'.md', '.json', '.yaml', '.yml', '.txt', '.log'} or not path.read_bytes().strip():
                raise ValueError('NATIVE_COMPLETION_REPORT_INVALID')
            ref = {'path': path.relative_to(repo).as_posix(), 'sha256': sha256_file(path)}
            if ref not in reports:
                reports.append(ref)
        sdd = work['sdd']
        workflow = sdd.get('workflow') or {}
        gates = sorted(set(args.gate or []))
        required = set((work.get('path_assessment') or {}).get('required_gates', []))
        if set(gates) - required:
            raise ValueError('NATIVE_COMPLETION_GATE_UNKNOWN')
        record = {'version': VERSION, 'at': now(), 'gates': gates, 'reports': reports, 'inputs': inputs(repo, work),
                  'provenance': {'kind': 'native_workflow_binding', 'workflow': workflow.get('name'),
                                 'execution': deepcopy(workflow.get('execution')),
                                 'review_ids': [v.get('id') for v in (sdd.get('native_reviews') or {}).values() if isinstance(v, dict) and v.get('id')]}}
        old = sdd.get('completion_evidence')
        if old and old.get('inputs') == record['inputs'] and old.get('reports') == reports and old.get('gates', []) == gates:
            print(json.dumps({'ok': True, 'status': 'unchanged', 'work_id': work['id']}))
            return 0
        if old:
            sdd.setdefault('completion_evidence_history', []).append(old)
        sdd['completion_evidence'] = record
        queue_event(work, {'event': 'sdd.completion.bound', 'work_item_id': work['id'],
                           'reports': reports, 'occurred_at': now()})
        save_work(repo, work)
        print(json.dumps({'ok': True, 'evidence': record, 'next': next_action(repo, work)}, ensure_ascii=False))
        return 0


def register(sub):
    for name in ('migrate', 'bind'):
        parser = sub.add_parser(name, help='Migrate active Work to native evidence lifecycle' if name == 'migrate' else 'Bind original native test/acceptance completion reports')
        parser.add_argument('--repo', default='.')
        parser.add_argument('--work')
        if name == 'bind':
            parser.add_argument('--report', action='append', required=True)
            parser.add_argument('--gate', action='append', help='Work verification duty covered by the native report; repeat for every applicable duty')
        parser.set_defaults(fn=command)
