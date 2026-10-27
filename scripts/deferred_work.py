"""Queue requests without occupying the active slot or executing provider commands."""
import base64
from scripts.cao_runtime import repo_root, ensure_initialized, config, now


def defer(args):
    from scripts.state_store import repo_transaction
    from scripts.cao_requirements import resolve_requirement, requirement_evidence
    from scripts.cao_work_store import stable_work_id, load_work, save_work, queue_event
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    with repo_transaction(repo):
        path = resolve_requirement(repo, args.requirement)
        if path is None:
            raise ValueError('REQUIREMENT_FILE_MISSING')
        from scripts.requirement_content import template_diagnostics
        if template_diagnostics(path):
            raise ValueError('REQUIREMENT_CONTENT_REQUIRED')
        wid = stable_work_id(repo, path)
        if existing := load_work(repo, wid):
            if existing['requirement'] != requirement_evidence(repo, path):
                from scripts.requirement_amend import recovery_instruction
                error = ValueError('REQUIREMENT_CHANGED_REANCHOR_REQUIRED')
                error.next_instruction = recovery_instruction(existing)
                raise error
            return existing
        provider = args.sdd_provider or (config(repo).get('sdd') or {}).get('provider')
        if provider not in {'bmad', 'openspec', 'speckit'}:
            raise ValueError('SDD_SELECTION_REQUIRED')
        work = {'version': 1, 'lifecycle_contract': 2, 'id': wid,
                'title': args.title or path.stem, 'status': 'todo',
                'requirement': requirement_evidence(repo, path),
                "requirement_original_base64": base64.b64encode(path.read_bytes()).decode(),
                'sdd': {'provider': provider, 'native_ref': args.sdd_ref, 'artifacts': []},
                'execution': {'step': 'path-assessment'},
                'resume': {'summary': 'Queued request; execution has not started.', 'completed': [], 'pending': []},
                'decisions': [], 'blockers': [], 'verification': None,
                'deferred_scopes': args.scope, 'created_at': now()}
        queue_event(work, {'event': 'work.queued', 'work_item_id': wid})
        save_work(repo, work)
        return work
