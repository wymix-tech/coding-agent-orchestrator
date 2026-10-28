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
        from scripts.completion_repairs import current as current_repair
        repair = current_repair(repo, work)
        if repair:
            return {**pending, 'state': 'blocked', 'reason': 'NATIVE_COMPLETION_REPAIR_PAUSED' if repair['paused'] else repair['reason'],
                    'repair': repair, 'next': 'Correct the original native report/source error, then bind the corrected evidence; do not repeat code reviews for report repair.'}
        from scripts.evidence_domains import capture, changed as domain_changes
        commands = [command for row in record.get('observations', []) for command in row.get('commands', [])]
        domains = capture(repo, work, commands)
        diagnostics = domain_changes(record.get('domains') or {}, domains) if record.get('domains') else {}
        current = inputs(repo, work)
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
            if not path.read_bytes().strip() or sha256_file(path) != ref['sha256']:
                return {**pending, 'state': 'stale', 'reason': 'NATIVE_COMPLETION_REPORT_CHANGED',
                        'changed_paths': [ref['path']]}
            from scripts.native_reports import read as read_native_report
            parsed.append(read_native_report(repo, work, path))
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
        reports = []
        candidate = deepcopy(work)
        candidate['sdd']['completion_evidence'] = {'reports': [{'path': name} for name in args.report]}
        observations = []
        for name in args.report:
            path = provider_artifact(repo, name, provider_output_dirs(work['sdd']['provider'], repo))
            if path.suffix.lower() not in {'.md', '.json', '.yaml', '.yml', '.txt', '.log'} or not path.read_bytes().strip():
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
        from scripts.evidence_archive import validate_manifest
        record['selected_evidence_ids'] = validate_manifest(repo, candidate, reports, getattr(args, 'evidence', None) or [])
        old = sdd.get('completion_evidence')
        if old and old.get('inputs') == record['inputs'] and old.get('reports') == reports and old.get('gates', []) == gates and old.get('domains') == record['domains'] and old.get('observations') == observations and old.get('selected_evidence_ids', []) == record['selected_evidence_ids']:
            if sdd.pop('completion_binding_failure', None):
                save_work(repo, work)
            print(json.dumps({'ok': True, 'status': 'unchanged', 'work_id': work['id']}))
            return 0
        if old:
            sdd.setdefault('completion_evidence_history', []).append(old)
        sdd.pop('completion_binding_failure', None)
        sdd['completion_evidence'] = record
        queue_event(work, {'event': 'sdd.completion.bound', 'work_item_id': work['id'],
                           'reports': reports, 'occurred_at': now()})
        save_work(repo, work)
        print(json.dumps({'ok': True, 'evidence': record, 'next': next_action(repo, work)}, ensure_ascii=False))
        return 0


def command(args):
    if args.lifecycle_command != 'bind':
        return binding_command(args)
    import json
    from scripts.cao_runtime import repo_root
    from scripts.cao_work_store import resolve_work
    from scripts.state_store import repo_transaction
    from scripts.completion_repairs import current, record, response
    repo = repo_root(args.repo)
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if work and enabled(work):
            row = current(repo, work)
            if row and row['paused'] and sorted(args.report) == sorted(row['reports']):
                print(json.dumps(response(row), ensure_ascii=False))
                return 2
        try:
            return binding_command(args)
        except (ValueError, OSError) as error:
            work = resolve_work(repo, args.work)
            if not work or not enabled(work) or work.get('status') != 'active':
                raise
            row = record(repo, work, args.report, error)
            print(json.dumps(response(row), ensure_ascii=False))
            return 2


def register(sub):
    descriptions = {
        'preflight': 'Inspect legacy migration eligibility without changing state',
        'migrate': 'Migrate active Work to native evidence lifecycle',
        'rollback': 'Restore an unchanged migration checkpoint without losing later progress',
        'protect': 'Enroll existing contract-2 Work in guarded persistence',
        'recover': 'Restore last authorized Work and quarantine unsupported writer output',
        'dependencies': 'Declare shared inputs used by native execution or reviewers',
        'bind': 'Bind original native test/acceptance completion reports',
    }
    for name in ('preflight', 'migrate', 'rollback', 'protect', 'recover', 'dependencies', 'bind'):
        parser = sub.add_parser(name, help=descriptions[name])
        parser.add_argument('--repo', default='.')
        parser.add_argument('--work')
        if name in {'migrate', 'rollback', 'recover'}:
            parser.add_argument('--check', action='store_true', help='Read-only preflight; do not change Work or guard state')
        if name == 'dependencies':
            parser.add_argument('--path', action='append', required=True)
            parser.add_argument('--review', action='store_true', help='Declare shared inputs used by native reviewers')
        if name == 'bind':
            parser.add_argument('--report', action='append', default=[])
            parser.add_argument('--evidence', action='append', default=[])
            parser.add_argument('--gate', action='append', help='Work verification duty covered by the native report; repeat for every applicable duty')
        parser.set_defaults(fn=command)
