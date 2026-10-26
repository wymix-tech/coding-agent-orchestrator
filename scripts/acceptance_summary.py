"""Read-only dual acceptance derived from original runs, never stored pass flags."""
import argparse
import json

from scripts.cao_runtime import config, ensure_initialized, repo_root
from scripts.cao_work_store import resolve_work


def summarize(repo, work, lifecycle=None):
    from scripts.cao_completion import sdd_acceptance_state, verification_inputs
    from scripts.completion_policy import verification_fresh
    from scripts.acceptance_trace import coverage
    from scripts.project_verification import project_prerequisite
    if not work:
        return {'ready': False, 'reason': 'NO_WORK_ITEM', 'blockers': ['NO_WORK_ITEM'], 'lifecycle_ready': False}
    historical = work.get('status') == 'completed'
    run = work.get('verification') or {}
    errors, blockers = [], []
    if historical:
        fresh = False
        technical_status = run.get('status', 'pending')
    else:
        try:
            fresh = verification_fresh(work, verification_inputs(repo, work, config(repo)))
            technical_status = 'passed' if fresh else 'stale' if run.get('status') == 'passed' else run.get('status', 'pending')
        except (ValueError, OSError, RuntimeError) as exc:
            fresh, technical_status = False, 'blocked'
            errors.append(str(exc))
    technical = {'status': technical_status, 'fresh': fresh, 'run_id': run.get('run_id'),
                 'checks': [{key: item.get(key) for key in ('name', 'required', 'gates', 'status', 'exit_code', 'reason', 'test_discovery', 'runtime')} for item in run.get('results', [])], 'errors': errors}
    state = sdd_acceptance_state(repo, work)
    record = (work.get('sdd') or {}).get('acceptance') or {}
    task_run = record.get('run') or {}
    task_status = state.get('state', 'pending')
    if task_status in {'unconfigured', 'not_applicable'}:
        task_status = 'pending'
    task = {'native_formal_stage': 'not_applicable' if (work.get('path_assessment') or {}).get('profile') == 'light' else 'required',
            'status': task_status, 'fresh': bool(state['ready'] and not historical and state.get('state') != 'not_applicable'),
            'run_id': task_run.get('id'), 'items': task_run.get('results', []), 'next': state.get('next')}
    if work.get('project_binding') and not historical:
        try:
            trace = coverage(repo, work)
            passed_ids = {item['id'] for item in task_run.get('results', []) if item.get('status') == 'passed'}
            passed = sum(bool(set(ids) & passed_ids) for ids in trace['mapping'].values())
            task['coverage'] = {**trace, 'passed': passed}
            if trace['missing'] or trace['unknown'] or not trace['required']:
                task.update(status='blocked', fresh=False)
                blockers.append('TASK_ACCEPTANCE_COVERAGE_INCOMPLETE')
        except (ValueError, OSError, RuntimeError) as exc:
            task.update(status='error', fresh=False, error=str(exc))
            blockers.append('TASK_ACCEPTANCE_COVERAGE_INCOMPLETE')
    if not historical:
        project = project_prerequisite(repo, work)
        if project:
            blockers.append(project['reason'])
    else:
        project = None
    if technical['status'] != 'passed' or not technical['fresh']:
        blockers.append('ACCEPTANCE_EVIDENCE_STALE' if technical['status'] == 'stale' else 'TECHNICAL_ACCEPTANCE_NOT_PASSED')
    if task['status'] != 'passed' or not task['fresh']:
        blockers.append('ACCEPTANCE_EVIDENCE_STALE' if task['status'] == 'stale' else 'TASK_ACCEPTANCE_NOT_PASSED')
    lifecycle = lifecycle or {'ready': False, 'reason': 'LIFECYCLE_NOT_EVALUATED'}
    if not lifecycle['ready']:
        blockers.extend(item.get('reason') or 'LIFECYCLE_NOT_READY' for item in lifecycle.get('blockers', []))
        if not lifecycle.get('blockers'):
            blockers.append(lifecycle.get('reason') or 'LIFECYCLE_NOT_READY')
    blockers = list(dict.fromkeys(blockers))
    instruction = (project or {}).get('instruction')
    if not instruction and not fresh:
        instruction = 'Resolve technical blockers, then run `cao verify` against the current project baseline.'
    if not instruction and not task['fresh']:
        instruction = task.get('next') or 'Run `cao acceptance criteria`; configure complete native acceptance and execute the actual checks.'
    if not instruction:
        instruction = lifecycle.get('next') or ('Run `cao done`.' if not blockers else 'Resolve the remaining native lifecycle obligations.')
    from scripts.review_dispatch import review_counts
    from scripts.native_completion import state as native_completion
    unverified = ((work.get('sdd') or {}).get('workflow') or {}).get('unverified') or {}
    exceptions = [{'kind': 'native_workflow_unverified', **unverified}] if unverified.get('allowed') else []
    return {'work_id': work['id'], 'historical': historical, 'technical_acceptance': technical,
            'native_review_counts': review_counts(work), 'native_completion': native_completion(repo, work),
            'unverified_exceptions': exceptions,
            'task_acceptance': task, 'lifecycle_ready': lifecycle['ready'], 'ready': not blockers,
            'blockers': blockers, 'next': {'type': 'agent', 'instruction': instruction},
            'reason': blockers[0] if blockers else None}


def cmd_acceptance(args):
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        raise ValueError('NO_WORK_ITEM')
    if args.acceptance_action == 'criteria':
        from scripts.acceptance_trace import criteria, CATEGORIES
        catalog = criteria(repo, work, catalog=True)
        out = {'criteria': [item for item in catalog if item['requires_acceptance']],
               'source_items': catalog, 'catalog_kind': 'source_items', 'source_item_count': len(catalog),
               'category_counts': {category: sum(item['category'] == category for item in catalog) for category in sorted(CATEGORIES)},
               'acceptance_source_count': sum(item['requires_acceptance'] for item in catalog),
               'declared_category_counts': {kind: sum(item.get('kind') == kind for item in ((work.get('sdd') or {}).get('acceptance') or {}).get('declaration', {}).get('criteria', [])) for kind in ('requirement', 'design')},
               'declared_acceptance_count': len(((work.get('sdd') or {}).get('acceptance') or {}).get('declaration', {}).get('criteria', [])),
               'note': 'Source-item coverage is not proof of criterion-specific execution or functional acceptance.'}
    elif args.acceptance_action == 'status':
        from scripts.cao_completion import completion_status
        out = completion_status(repo, work)
    else:
        from scripts.sdd_acceptance_commands import dispatch
        return dispatch(args)
    print(json.dumps({'ok': True, **out}, ensure_ascii=False, indent=2))
    return 0


def register_parser(sub):
    actions = sub.add_parser('acceptance', help='Current technical and task acceptance').add_subparsers(dest='acceptance_action', required=True)
    for name in ('status', 'criteria', 'configure', 'show', 'run', 'begin', 'submit', 'reuse'):
        parser = actions.add_parser(name)
        parser.add_argument('--repo', default='.')
        parser.add_argument('--work')
        if name == 'configure':
            choice = parser.add_mutually_exclusive_group(required=True)
            choice.add_argument('--declaration')
            choice.add_argument('--executor-command')
            choice.add_argument('--executor-skill')
            parser.add_argument('--spec', help='Native SPEC when no existing spec binding exists')
            parser.add_argument('--output', action='append', help='Exact native Work-scoped report output')
            parser.add_argument('--replace', action='store_true')
        if name == 'reuse':
            parser.add_argument('--script', required=True, help='Name of the real verification result whose JSON items cover this declaration')
        if name == 'submit':
            parser.add_argument('--run', required=True)
            parser.add_argument('--report', required=True)
            parser.add_argument('--exit-code', type=int, default=0)
        parser.set_defaults(fn=cmd_acceptance)
