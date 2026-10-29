"""Read-only evidence-backed process/artefact conformance, not code review."""
import hashlib
import json
import re

from scripts.path_guard import repo_file


def native_gate_evidence(repo, work):
    """Map existing native obligations once; never trust a stored derived pass."""
    from scripts.cao_native_sync import native_route_state
    from scripts.cao_snapshot import code_snapshot
    from scripts.native_obligations import review_state, planning_state
    sdd = work.get('sdd') or {}
    result = {}
    review = review_state(repo, work, code_snapshot)
    if review.get('ready') and review.get('state') == 'recorded':
        result['implementation_review'] = {'requirement_id': 'native_review', 'owner': sdd.get('provider'),
                                           'evidence': review}
    decision = sdd.get('route_decision') or {}
    approval = sdd.get('spec_approval') or {}
    if (decision.get('provider') == 'bmad' and decision.get('approval_required')
            and approval.get('evidence') and native_route_state(repo, work).get('ready')):
        proof = {'requirement_id': 'native_spec_approval', 'owner': 'bmad',
                 'evidence': {'approval_id': approval.get('id'), 'source': decision.get('primary_artifact'),
                              'intent_sha256': approval.get('intent_sha256')}}
        result['requirements_review'] = proof
        # Direct Build's approved native SPEC carries the design; require an actual
        # nonempty Code Map rather than treating a route name as review evidence.
        path = repo_file(repo, decision['primary_artifact'])
        content = path.read_text(encoding='utf-8-sig')
        code_map = re.search(r'(?ms)^## Code Map\s*\n(.*?)(?=^## |\Z)', content)
        if decision.get('route') == 'direct-build' and code_map and code_map[1].strip():
            result['design_review'] = {**proof, 'requirement_id': 'native_spec_design'}
        elif decision.get('route') == 'full-planning' and planning_state(repo, work).get('ready'):
            architecture = [item for item in decision.get('required_artifacts', []) if item.get('role') == 'architecture']
            if architecture:
                result['design_review'] = {'requirement_id': 'native_planning', 'owner': 'bmad', 'evidence': architecture}
    return result


def summarize(repo, work, lifecycle):
    """Adapt authoritative lifecycle results, retaining every requirement and reason."""
    sdd = work.get('sdd') or {}
    route = sdd.get('route_decision') or {}
    source_refs = route.get('rules') or []
    requirements = []
    for item in lifecycle.get('obligations', []):
        name = item['obligation']
        native = name.startswith('native_') or name in {'workflow', 'plan_scope', 'light_spec'}
        if not item.get('ready') and not item.get('next') and (name in {'plan_scope', 'native_plan_complete'} or str(item.get('reason', '')).startswith('PLAN_')):
            from scripts.plan_navigation import repair
            item = {**item, 'next': repair(repo, work)}
        state = item.get('state')
        status = ('not_applicable' if item.get('ready') and state == 'not_applicable' else
                  'passed' if item.get('ready') else 'stale' if state == 'stale' else 'blocked')
        requirements.append({'requirement_id': name, 'owner': sdd.get('provider') if native else 'cao',
                             'category': 'process', 'source_refs': source_refs if native else [{'contract': 'cao.lifecycle', 'version': 1}],
                             'status': status, 'reason_code': item.get('reason') if not item.get('ready') else None,
                             'observed': item, 'expected': 'current accepted contract and matching evidence',
                             'repair_action': item.get('next'), 'retry_when': 'the reported evidence or prerequisite changes'})
    # Route validation already checks these hashes and ownership. List them
    # explicitly so missing/empty artifacts are visible without rereviewing code.
    for artifact in route.get('required_artifacts', []):
        try:
            path = repo_file(repo, artifact['path'])
            passed = path.is_file() and bool(path.read_bytes().strip())
        except (ValueError, OSError, RuntimeError):
            passed = False
        requirements.append({'requirement_id': 'artifact:' + artifact['path'], 'category': 'artifact',
                             'owner': sdd.get('provider'), 'source_refs': source_refs,
                             'status': 'passed' if passed else 'blocked',
                             'observed': artifact['path'], 'expected': 'nonempty provider-owned declared artifact',
                             'reason_code': None if passed else 'CONFORMANCE_ARTIFACT_EMPTY_OR_MISSING'})
    contract = {'schema_version': 1, 'work_id': work['id'], 'provider': sdd.get('provider'),
                'provider_version': route.get('provider_version'), 'route_id': route.get('id'),
                'assessment_revision': (work.get('path_assessment') or {}).get('revision'),
                'requirements': [{key: row.get(key) for key in ('requirement_id', 'owner', 'category', 'source_refs', 'expected')}
                                 for row in requirements]}
    contract['sha256'] = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    blocked = [row for row in requirements if row['status'] not in {'passed', 'not_applicable'}]
    return {'ready': not blocked, 'contract': contract, 'requirements': requirements,
            'blockers': blocked, 'native_gate_evidence': native_gate_evidence(repo, work),
            'note': 'CAO checks evidence and declared process/artefact obligations. Functional code review belongs to the SDD provider.'}


def cmd_conformance(args):
    from scripts.cao_completion import lifecycle_status
    from scripts.cao_runtime import repo_root, ensure_initialized
    from scripts.cao_work_store import resolve_work
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        raise ValueError('NO_WORK_ITEM')
    from scripts.lifecycle_contract import enabled, conformance
    out = conformance(repo, work) if enabled(work) else summarize(repo, work, lifecycle_status(repo, work))
    print(json.dumps({'ok': True, **out}, ensure_ascii=False, indent=2))
    return 2 if args.conformance_action == 'check' and not out['ready'] else 0


def register_parser(sub):
    actions = sub.add_parser('conformance', help='Check native process and artifact evidence without duplicate code review').add_subparsers(dest='conformance_action', required=True)
    for name in ('show', 'check'):
        parser = actions.add_parser(name)
        parser.add_argument('--repo', default='.')
        parser.add_argument('--work')
        parser.set_defaults(fn=cmd_conformance)
