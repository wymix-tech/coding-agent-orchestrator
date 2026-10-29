"""Versioned native completion bindings and one pre-verification evidence policy."""
from copy import deepcopy


VERSION = 2
NATIVE_VERIFICATION_GATES = {"sdd_acceptance", "security_verification", "integration_verification",
                             "compatibility_verification", "data_verification"}


def enabled(work):
    return bool(work and work.get('lifecycle_contract') == VERSION)


def prerequisites(repo, work):
    """Collect native/process duties without requiring a future technical run."""
    from scripts.cao_completion import lifecycle_status
    result = lifecycle_status(repo, work, pre_verification=True)
    return result


def inputs(repo, work, technical=None):
    from scripts.cao_runtime import config, sha256_file
    from scripts.cao_completion import verification_inputs
    from scripts.cao_plans import provider_verification_plan_state
    from scripts.native_obligations import digest
    # Baseline execution transport has its own freshness domain. Product code,
    # tests and configuration remain in code_snapshot.
    technical = technical if technical is not None else verification_inputs(repo, work, config(repo))
    sdd = work.get('sdd') or {}
    from scripts.evidence_domains import dependencies, scope
    from scripts.project_architecture import snapshot as architecture_snapshot
    architecture = architecture_snapshot(repo, (work.get('project_binding') or {}).get('scopes', ['root']))
    return {
        'work_id': work['id'], 'provider': sdd.get('provider'),
        'requirement_sha256': sha256_file(repo / work['requirement']['path']),
        'code_snapshot': technical['code_snapshot'],
        'assessment_revision': (work.get('path_assessment') or {}).get('revision'),
        'scope_sha256': digest(scope(repo, work)),
        'native_dependencies_sha256': digest(dependencies(repo, work, [command for row in ((sdd.get('completion_evidence') or {}).get('observations') or []) for command in row.get('commands', [])])),
        'route_id': (sdd.get('route_decision') or {}).get('id'),
    }


def evidence_state(repo, work, *, evaluation=None):
    """Validate original native reports; never execute a second acceptance suite."""
    from scripts.cao_runtime import sha256_file
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.path_guard import provider_artifact
    record = (work.get('sdd') or {}).get('completion_evidence') or {}
    pending = {'ready': False, 'state': 'pending', 'reason': 'NATIVE_COMPLETION_EVIDENCE_REQUIRED',
               'next': 'Finish the native workflow and bind its actual test/acceptance completion report with `cao lifecycle bind --report <native-report>`; no separate CAO functional executor is required.'}
    repair = (work.get('sdd') or {}).get('completion_repair') or {}
    if repair.get('kind') == 'product':
        return {**pending, 'state': 'stale', 'reason': 'NATIVE_PRODUCT_REPAIR_REQUIRED',
                'changed_inputs': ['native_product_repair'],
                'next': 'Reopen the affected native task, repair and complete its native review/acceptance. Capture its actual command output with cao lifecycle execute, then bind that report.'}
    from scripts.native_execution import receipts
    executions = receipts(work)
    if executions and executions[-1].get('status') != 'passed':
        return {**pending, 'state': 'blocked', 'reason': 'NATIVE_EXECUTION_NOT_PASSED',
                'next': 'Inspect the native execution runtime and resolve its failure before explicitly rerunning the native command.'}
    if not record:
        return pending
    try:
        from scripts.completion_repairs import current as current_repair
        repair = current_repair(repo, work)
        if repair:
            return {**pending, 'state': 'blocked', 'reason': 'NATIVE_COMPLETION_REPAIR_PAUSED' if repair['paused'] else repair['reason'],
                    'repair': repair, 'next': 'Correct the original native report/source error, then bind the corrected evidence; do not repeat code reviews for report repair.'}
        from scripts.evidence_domains import capture, changed as domain_changes
        commands = [command for row in record.get('observations', []) for command in row.get('commands', [])]
        evaluation = evaluation if evaluation is not None else {}
        domains = capture(repo, work, commands, evaluation=evaluation)
        diagnostics = domain_changes(record.get('domains') or {}, domains) if record.get('domains') else {}
        current = inputs(repo, work, technical=evaluation['technical'])
        changed = [key for key, value in current.items() if record.get('inputs', {}).get(key) != value]
        if changed:
            return {**pending, 'state': 'stale', 'reason': 'NATIVE_COMPLETION_EVIDENCE_STALE',
                    'changed_inputs': changed, **diagnostics,
                    'next': 'Complete the affected native work and refresh its actual completion report; then bind it again. Preserve unaffected native review history.'}
        if not record.get('domains') or not record.get('observations'):
            return {**pending, 'state': 'stale', 'reason': 'NATIVE_COMPLETION_BINDING_UPGRADE_REQUIRED',
                    'next': 'Review and rebind the original native reports with cao lifecycle bind; retain the original record as history.'}
        if set(diagnostics.get('changed_domains', [])) & {'native_sources', 'native_scope', 'reports', 'native_dependencies', 'policy'}:
            return {**pending, 'state': 'stale', 'reason': 'NATIVE_COMPLETION_EVIDENCE_STALE', **diagnostics}
        required = set((work.get('path_assessment') or {}).get('required_gates', [])) & NATIVE_VERIFICATION_GATES
        missing = sorted(required - set(record.get('gates', [])))
        if missing:
            return {**pending, 'reason': 'NATIVE_VERIFICATION_EVIDENCE_REQUIRED', 'pending_gates': missing,
                    'next': 'Complete these checks in the native workflow, then bind the actual report with ' + ' '.join('--gate ' + name for name in missing) + ': ' + ', '.join(missing)}
        reports = record.get('reports') or []
        if not reports:
            return pending
        provider = work['sdd']['provider']
        parsed = []
        for ref in reports:
            path = provider_artifact(repo, ref['path'], provider_output_dirs(provider, repo))
            if sha256_file(path) != ref['sha256']:
                return {**pending, 'state': 'stale', 'reason': 'NATIVE_COMPLETION_REPORT_CHANGED',
                        'changed_paths': [ref['path']]}
            from scripts.native_reports import read as read_native_report
            parsed.append(read_native_report(repo, work, path))
        from scripts.native_reports import validate_coverage
        validate_coverage(repo, work, reports, parsed, record.get('gates', []))
        from scripts.evidence_archive import validate_manifest
        validate_manifest(repo, work, reports, record.get('selected_evidence_ids') or [])
        if parsed != record.get('observations'):
            return {**pending, 'state': 'stale', 'reason': 'NATIVE_COMPLETION_ADAPTER_CHANGED',
                    'next': 'Review the changed native report interpretation and explicitly rebind the original reports.'}
        return {'ready': True, 'state': 'passed', 'owner': provider, 'reports': reports,
                'inputs': current, 'provenance': record.get('provenance'), 'observations': record.get('observations'), **diagnostics,
                'note': 'Bound native execution evidence, not a second CAO functional acceptance run.'}
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        return {**pending, 'state': 'blocked', 'reason': 'NATIVE_COMPLETION_SOURCE_INVALID', 'error': str(exc)}


def conformance(repo, work, *, native=None, evaluation=None):
    from scripts.conformance import summarize
    native = prerequisites(repo, work) if native is None else native
    report = summarize(repo, work, native)
    evidence = evidence_state(repo, work, evaluation=evaluation)
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
    obligations = prerequisites(repo, work)
    evaluation = {}
    report = conformance(repo, work, native=obligations, evaluation=evaluation)
    current_inputs = None
    run = work.get('verification') or {}
    try:
        current_inputs = evaluation.get('technical')
        if current_inputs is None:
            current_inputs = verification_inputs(repo, work, config(repo))
        fresh = verification_fresh(work, current_inputs)
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
    action = next_action(repo, work, native=obligations, evidence=report['native_completion_evidence'], current_inputs=current_inputs)
    return {'ready': ready, 'reason': blockers[0] if blockers else None, 'blockers': blockers,
            'conformance': report, 'technical_acceptance': technical,
            'native_completion': native, 'task_acceptance': {**native, 'fresh': native['ready']},
            'lifecycle_ready': report['ready'], 'next_action': action, 'next': action['instruction']}


def next_action(repo, work, *, native=None, evidence=None, current_inputs=None):
    """One precedence shared by next, sync, verification and completion."""
    native = prerequisites(repo, work) if native is None else native
    if not native['ready']:
        blocker = native['blockers'][0]
        return {'type': 'agent', 'stage': 'verification_setup' if blocker.get('obligation') == 'baseline' else 'execution', 'reason': blocker.get('reason'),
                'blocker': blocker, 'instruction': blocker.get('next') or 'Run `cao conformance check` and resolve the reported native obligation before binding completion evidence.'}
    evidence = evidence_state(repo, work) if evidence is None else evidence
    if not evidence['ready']:
        return {'type': 'agent', 'stage': 'execution' if evidence.get('changed_inputs') else 'completion',
                'reason': evidence['reason'], 'blocker': evidence, 'instruction': evidence['next']}
    from scripts.cao_completion import verification_inputs
    from scripts.cao_runtime import config
    from scripts.completion_policy import verification_fresh
    run = work.get('verification') or {}
    if run.get('status') in {'failed', 'blocked', 'error', 'stale'}:
        return {'type': 'agent', 'stage': 'recovery', 'reason': 'PROJECT_BASELINE_' + run['status'].upper(),
                'instruction': 'Inspect verification.results and runtime logs before retrying. Diagnose product defects versus environment or baseline failures; record with cao lifecycle repair --kind product|environment|baseline|unknown --reason <diagnosis>. For a product defect, reopen the affected native task and repair through SDD; refresh native execution evidence after edits. For environment failures restore the environment and rerun cao verify. Baseline standard changes require existing review/reanchor; do not weaken checks.'}
    if not verification_fresh(work, current_inputs if current_inputs is not None else verification_inputs(repo, work, config(repo))):
        return {'type': 'agent', 'stage': 'completion', 'reason': 'PROJECT_BASELINE_REQUIRED',
                'instruction': 'Inspect `cao conformance check`, then run `cao verify` against the accepted project baseline.'}
    return {'type': 'agent', 'stage': 'completion', 'instruction': 'Run `cao done`.'}


def binding_command(args):
    import json
    from scripts.cao_runtime import repo_root, ensure_initialized, now, sha256_file
    from scripts.cao_work_store import resolve_work, save_work, queue_event
    from scripts.state_store import repo_transaction
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.path_guard import provider_artifact
    if args.lifecycle_command in {'preflight', 'migrate', 'rollback', 'protect', 'recover'}:
        from scripts.lifecycle_migration import command as migration_command
        return migration_command(args)
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if not work or work.get('status') not in {'active', 'suspended'}:
            raise ValueError('ACTIVE_OR_SUSPENDED_WORK_REQUIRED')
        if not enabled(work):
            raise ValueError('LIFECYCLE_MIGRATION_REQUIRED: run cao lifecycle migrate')
        if work.get('status') != 'active':
            raise ValueError('WORK_NOT_ACTIVE')
        if args.lifecycle_command == 'dependencies':
            from scripts.evidence_domains import files
            from scripts.path_guard import repo_file
            names = [repo_file(repo, name).relative_to(repo).as_posix() for name in args.path]
            for name in names:
                if name.startswith('.orchestrator/') and not name.startswith('.orchestrator/scripts/verification/'):
                    raise ValueError('NATIVE_DEPENDENCY_MANAGED_STATE_FORBIDDEN')
            observed = files(repo, names)
            if any(row.get('missing') for row in observed.values()):
                raise ValueError('NATIVE_DEPENDENCY_MISSING')
            key = 'native_review_dependencies' if args.review else 'native_execution_dependencies'
            work['sdd'][key] = sorted(set(work['sdd'].get(key, [])) | set(names))
            queue_event(work, {'event': 'sdd.dependencies.declared', 'work_item_id': work['id'], 'paths': names, 'use': key})
            save_work(repo, work)
            print(json.dumps({'ok': True, 'dependencies': work['sdd'][key]}, ensure_ascii=False))
            return 0
        ready = prerequisites(repo, work)
        if not ready['ready']:
            print(json.dumps({'ok': False, 'error': 'NATIVE_COMPLETION_REQUIRED', **ready}, ensure_ascii=False))
            return 2
        if getattr(args, 'evidence', None):
            from scripts.evidence_archive import select
            args.report = list(dict.fromkeys(args.report + select(repo, work, args.evidence)))
        if not args.report:
            raise ValueError('NATIVE_COMPLETION_REPORT_REQUIRED')
        from scripts.native_execution import receipts
        reports = []
        candidate = deepcopy(work)
        old_reports = [ref['path'] for ref in (work['sdd'].get('completion_evidence') or {}).get('reports', [])]
        retained = sorted(set(work['sdd'].get('native_completion_report_outputs', [])) | set(old_reports))
        candidate['sdd']['native_completion_report_outputs'] = retained
        candidate['sdd']['completion_evidence'] = {'reports': [{'path': name} for name in args.report]}
        observations = []
        for name in args.report:
            path = provider_artifact(repo, name, provider_output_dirs(work['sdd']['provider'], repo))
            if path.suffix.lower() not in {'.md', '.json', '.yaml', '.yml', '.txt', '.log'} or (not path.read_bytes().strip() and not any(ref.get('path') == path.relative_to(repo).as_posix() for row in receipts(work) for ref in row.get('reports', []))):
                raise ValueError('NATIVE_COMPLETION_REPORT_INVALID')
            from scripts.native_reports import read as read_native_report
            observations.append(read_native_report(repo, candidate, path))
            ref = {'path': path.relative_to(repo).as_posix(), 'sha256': sha256_file(path)}
            if ref not in reports:
                reports.append(ref)
        # An original result already claimed by a different Work is not fresh
        # execution for this one. Inspect stored records without refreshing or
        # mutating historical Work. The repository transaction serializes claims.
        from scripts.cao_defaults import WORK_DIR
        from scripts.cao_runtime import load_json
        for path in (repo / WORK_DIR).glob('*.json'):
            if path.stem == work['id']:
                continue
            other = load_json(path, {}) or {}
            claimed = ((other.get('sdd') or {}).get('completion_evidence') or {}).get('reports') or []
            if any(ref in claimed for ref in reports):
                raise ValueError('NATIVE_COMPLETION_REPORT_OTHER_WORK: bind a fresh native result for this Work')
        sdd = work['sdd']
        workflow = sdd.get('workflow') or {}
        gates = sorted(set(args.gate or []))
        required = set((work.get('path_assessment') or {}).get('required_gates', []))
        if set(gates) - required:
            raise ValueError('NATIVE_COMPLETION_GATE_UNKNOWN')
        from scripts.evidence_domains import capture
        candidate['sdd']['completion_evidence'] = {'reports': reports, 'observations': observations}
        record = {'version': VERSION, 'at': now(), 'gates': gates, 'reports': reports,
                  'observations': observations, 'domains': capture(repo, candidate, [command for row in observations for command in row.get('commands', [])]),
                  'inputs': inputs(repo, candidate),
                  'provenance': {'kind': 'native_workflow_binding', 'workflow': workflow.get('name'),
                                 'execution': deepcopy(workflow.get('execution')),
                                 'review_ids': [v.get('id') for v in (sdd.get('native_reviews') or {}).values() if isinstance(v, dict) and v.get('id')]}}
        from scripts.native_execution import receipt_matches, receipts
        previous = sdd.get('completion_evidence') or {}
        from scripts.evidence_domains import changed as changed_domains
        material_changes = set(changed_domains(previous.get('domains', {}), record['domains'])['changed_domains']) & {'product', 'native_sources', 'native_scope', 'native_dependencies', 'policy'}
        changed_native_inputs = bool(previous and (previous.get('inputs') != record['inputs'] or material_changes))
        from scripts.native_reports import validate_coverage
        record['covered_gates'] = validate_coverage(repo, candidate, reports, observations, gates)
        record['evidence_protocol'] = 2
        execution_reports = [ref for ref, observation in zip(reports, observations)
                             if not (observation.get('check_records') and set(observation['evidence_types']) <= {'acceptance_result', 'security_result', 'manual_result'})]
        if (execution_reports and (changed_native_inputs or receipts(candidate) or (sdd.get('completion_repair') or {}).get('kind') == 'product')
                and not receipt_matches(repo, candidate, execution_reports)):
            raise ValueError('NATIVE_COMPLETION_REEXECUTION_REQUIRED: run the affected native command with cao lifecycle execute --command <native-command> --report <native-log>, then bind its actual output')
        from scripts.evidence_archive import validate_manifest
        record['selected_evidence_ids'] = validate_manifest(repo, candidate, reports, getattr(args, 'evidence', None) or [])
        old = sdd.get('completion_evidence')
        if old and old.get('inputs') == record['inputs'] and old.get('reports') == reports and old.get('gates', []) == gates and old.get('domains') == record['domains'] and old.get('observations') == observations and old.get('selected_evidence_ids', []) == record['selected_evidence_ids']:
            changed = bool(sdd.pop('completion_binding_failure', None))
            if (sdd.get('completion_repair') or {}).get('kind') == 'product':
                sdd.setdefault('completion_repair_history', []).append(sdd.pop('completion_repair'))
                queue_event(work, {'event': 'sdd.completion.repair.resolved', 'work_item_id': work['id']})
                changed = True
            if changed:
                save_work(repo, work)
            print(json.dumps({'ok': True, 'status': 'unchanged', 'work_id': work['id']}))
            return 0
        if old:
            sdd.setdefault('completion_evidence_history', []).append(old)
        sdd.pop('completion_binding_failure', None)
        if (sdd.get('completion_repair') or {}).get('kind') == 'product':
            sdd.setdefault('completion_repair_history', []).append(sdd.pop('completion_repair'))
        sdd['native_completion_report_outputs'] = sorted(set(retained) | {ref['path'] for ref in reports})
        sdd['completion_evidence'] = record
        queue_event(work, {'event': 'sdd.completion.bound', 'work_item_id': work['id'],
                           'reports': reports, 'occurred_at': now()})
        save_work(repo, work)
        print(json.dumps({'ok': True, 'evidence': record, 'next': next_action(repo, work)}, ensure_ascii=False))
        return 0


def command(args):
    if args.lifecycle_command == 'executions':
        from scripts.native_execution import inspect_executions
        return inspect_executions(args)
    if args.lifecycle_command == 'declare':
        from scripts.native_validation import declare
        return declare(args)
    if args.lifecycle_command == 'repair':
        from scripts.native_execution import repair
        return repair(args)
    if args.lifecycle_command == 'execute':
        from scripts.native_execution import command as execute_native
        return execute_native(args)
    if args.lifecycle_command != 'bind':
        return binding_command(args)
    import json
    from scripts.cao_runtime import repo_root
    from scripts.cao_work_store import resolve_work
    from scripts.state_store import repo_transaction
    from scripts.completion_repairs import current, record, response
    repo = repo_root(args.repo)
    request = {key: sorted(set(getattr(args, key, None) or [])) for key in ('report', 'gate', 'evidence')}
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if work and enabled(work):
            row = current(repo, work)
            if row and row['paused'] and row.get('request') == request:
                print(json.dumps(response(row), ensure_ascii=False))
                return 2
        try:
            return binding_command(args)
        except (ValueError, OSError) as error:
            if str(error).split(':', 1)[0] in {'NATIVE_COMPLETION_GATE_UNKNOWN', 'NATIVE_COMPLETION_REPORT_REQUIRED'}:
                print(json.dumps({'ok': False, 'error': str(error), 'next': 'Correct the bind request parameters and retry; native reports do not need to change.'}))
                return 2
            work = resolve_work(repo, args.work)
            if not work or not enabled(work) or work.get('status') != 'active':
                raise
            row = record(repo, work, args.report, error, request)
            print(json.dumps(response(row), ensure_ascii=False))
            return 2


def register(sub):
    descriptions = {
        'preflight': 'Inspect legacy migration eligibility without changing state',
        'migrate': 'Migrate active Work to native evidence lifecycle',
        'rollback': 'Restore an unchanged migration checkpoint without losing later progress',
        'protect': 'Enroll existing contract-2 Work in guarded persistence',
        'recover': 'Restore last authorized Work and quarantine unsupported writer output',
        'repair': 'Record diagnosis of a failed project baseline',
        'execute': 'Capture an actual native command result for current inputs',
        'executions': 'Inspect current and historical native execution receipts',
        'declare': 'Declare a literal verification command from a bound native source',
        'dependencies': 'Declare shared inputs used by native execution or reviewers',
        'bind': 'Bind original native test/acceptance completion reports',
    }
    for name in ('preflight', 'migrate', 'rollback', 'protect', 'recover', 'dependencies', 'declare', 'executions', 'repair', 'execute', 'bind'):
        parser = sub.add_parser(name, help=descriptions[name])
        parser.add_argument('--repo', default='.')
        parser.add_argument('--work')
        if name in {'migrate', 'rollback', 'recover'}:
            parser.add_argument('--check', action='store_true', help='Read-only preflight; do not change Work or guard state')
        if name == 'executions':
            parser.add_argument('--limit', type=int, choices=range(1, 101), default=20)
            parser.add_argument('--cursor', type=int, choices=range(0, 100001), default=0)
        if name == 'declare':
            parser.add_argument('--command', required=True)
            parser.add_argument('--source', required=True)
            parser.add_argument('--reason', required=True)
        if name == 'repair':
            parser.add_argument('--kind', choices=('product', 'environment', 'baseline', 'unknown'), required=True)
            parser.add_argument('--reason', required=True)
        if name == 'execute':
            parser.add_argument('--command', required=True)
            parser.add_argument('--report', required=True)
            parser.add_argument('--timeout-seconds', type=float, default=300)
            parser.add_argument('--retry-of')
        if name == 'dependencies':
            parser.add_argument('--path', action='append', required=True)
            parser.add_argument('--review', action='store_true', help='Declare shared inputs used by native reviewers')
        if name == 'bind':
            parser.add_argument('--report', action='append', default=[])
            parser.add_argument('--evidence', action='append', default=[])
            parser.add_argument('--gate', action='append', help='Work verification duty covered by the native report; repeat for every applicable duty')
        parser.set_defaults(fn=command)
