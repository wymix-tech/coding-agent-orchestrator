"""Return an incomplete native check report grounded in a current receipt."""
import json


def command(args):
    from scripts.cao_runtime import repo_root, sha256_file
    from scripts.cao_work_store import resolve_work, save_work
    from scripts.state_store import repo_transaction
    from scripts.path_guard import provider_artifact
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.native_execution import receipt_matches
    from scripts.lifecycle_contract import inputs
    repo = repo_root(args.repo)
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if not work or work.get('status') != 'active':
            raise ValueError('ACTIVE_WORK_REQUIRED')
        target = provider_artifact(repo, args.output, provider_output_dirs(work['sdd']['provider'], repo))
        if target.suffix.lower() != '.json':
            raise ValueError('NATIVE_REPORT_JSON_OUTPUT_REQUIRED')
        output = target.relative_to(repo).as_posix()
        outputs = work['sdd'].setdefault('native_completion_report_outputs', [])
        if getattr(args, 'prepare', False):
            if output not in outputs:
                if target.exists():
                    raise ValueError('NATIVE_REPORT_OUTPUT_EXISTS: choose a new report path; existing sources cannot be excluded')
                outputs.append(output)
                save_work(repo, work)
            print(json.dumps({'ok': True, 'output': output, 'next': 'Execute native validation, then run lifecycle report-draft with this output and the captured log.'}))
            return 0
        if output not in outputs:
            raise ValueError('NATIVE_REPORT_OUTPUT_UNREGISTERED: run lifecycle report-prepare before native execution')
        if target.exists():
            raise ValueError('NATIVE_REPORT_OUTPUT_EXISTS: preserve existing judgments; choose and register a new output for a new draft')
        path = provider_artifact(repo, args.log, provider_output_dirs(work['sdd']['provider'], repo))
        ref = {'path': path.relative_to(repo).as_posix(), 'sha256': sha256_file(path)}
        if not receipt_matches(repo, work, [ref]):
            raise ValueError('NATIVE_COMPLETION_REFERENCE_EXECUTION_REQUIRED')
        report = {'work_id': work['id'], 'provider': work['sdd']['provider'],
                  'requirement_sha256': work['requirement']['sha256'], 'code_snapshot': inputs(repo, work)['code_snapshot'],
                  'evidence_type': 'acceptance_result', 'status': 'pending',
                  'checks': [{'id': args.check_id, 'description': args.description, 'status': 'pending',
                              'gates': args.gate, 'evidence': [ref]}]}
        from scripts.state_store import atomic_json
        atomic_json(target, report)
    print(json.dumps({'ok': True, 'report': output, 'draft': report, 'next': 'Confirm native criterion results in this pending report, then bind it. No passing result is inferred; scope and receipts are revalidated.'}))
    return 0
