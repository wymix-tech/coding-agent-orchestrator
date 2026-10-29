"""Return an incomplete native check report grounded in a current receipt."""
import json


def command(args):
    from scripts.cao_runtime import repo_root, sha256_file
    from scripts.cao_work_store import resolve_work
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
        path = provider_artifact(repo, args.log, provider_output_dirs(work['sdd']['provider'], repo))
        ref = {'path': path.relative_to(repo).as_posix(), 'sha256': sha256_file(path)}
        if not receipt_matches(repo, work, [ref]):
            raise ValueError('NATIVE_COMPLETION_REFERENCE_EXECUTION_REQUIRED')
        report = {'work_id': work['id'], 'provider': work['sdd']['provider'],
                  'requirement_sha256': work['requirement']['sha256'], 'code_snapshot': inputs(repo, work)['code_snapshot'],
                  'evidence_type': 'acceptance_result', 'status': 'pending',
                  'checks': [{'id': args.check_id, 'description': args.description, 'status': 'pending',
                              'gates': args.gate, 'evidence': [ref]}]}
    print(json.dumps({'ok': True, 'draft': report, 'next': 'Save under the provider output directory; confirm native criterion results before setting passed. Declare new report outputs before execution so their bytes do not become product inputs. Binding revalidates all scope, hashes and receipts.'}))
    return 0
