"""Version-checked explicit requirement revision with durable byte preservation."""
from copy import deepcopy
import base64
import json
import uuid


def token(work, candidate_sha256, request):
    """Bind business state and the reviewed candidate; ignore storage bookkeeping."""
    from scripts.work_operations import semantic
    from scripts.lifecycle_guard import digest
    business = deepcopy(work)
    business.get('resume', {}).pop('last_agent', None)
    return digest({'work': semantic(business), 'candidate': candidate_sha256, 'request': request})


def recovery_instruction(work):
    return ("Preserve the current requirement as a separate candidate file; do not edit or restore state JSON. "
            "Run `cao amend --work " + work['id'] + " --requirement <candidate-path> --request <original-request> --check`, "
            "then apply that exact candidate with --apply --expected-token <expected_token>. "
            "If the original was edited in place, amend uses its retained original bytes when available. "
            "On a stale token inspect the actual changed business state before previewing again; do not guess revision numbers or retry unchanged failures.")


def command(args):
    from scripts.cao_runtime import repo_root, sha256_file, now
    from scripts.recovery_plan import read_work
    from scripts.path_guard import repo_file
    from scripts.state_store import repo_transaction, atomic_json
    from scripts.work_operations import root, barrier
    from scripts.completion_policy import reset_requirement
    repo = repo_root(args.repo)
    with repo_transaction(repo):
        barrier(repo)
        work = read_work(repo, args.work)
        if work['status'] not in {'active', 'suspended'}:
            raise ValueError('ACTIVE_OR_SUSPENDED_WORK_REQUIRED')
        target = repo_file(repo, work['requirement']['path'])
        candidate = repo_file(repo, args.requirement)
        original = target.read_bytes()
        if sha256_file(target) != work['requirement']['sha256']:
            from hashlib import sha256
            try:
                original = base64.b64decode(work.get('requirement_original_base64', ''), validate=True)
            except (ValueError, TypeError):
                original = b''
            if sha256(original).hexdigest() != work['requirement']['sha256'] and getattr(args, 'original', None):
                original = repo_file(repo, args.original).read_bytes()
            if sha256(original).hexdigest() != work['requirement']['sha256']:
                raise ValueError('REQUIREMENT_ORIGINAL_UNAVAILABLE: recover the exact original from Git or an audited artifact and pass --original <recovered-file>; its hash must match the anchored requirement. Keep the edited candidate; do not retry start --replace.')
        data = candidate.read_bytes()
        if not data.strip() or len(data) > 8 * 1024 * 1024:
            raise ValueError('REQUIREMENT_CONTENT_REQUIRED')
        from hashlib import sha256
        digest = sha256(data).hexdigest()
        report = {'ok': True, 'work_id': work['id'], 'expected_revision': work.get('state_revision'), 'expected_token': token(work, digest, args.request),
                  'before': work['requirement'], 'after': {'path': work['requirement']['path'], 'sha256': digest},
                  'affected': ['native_scope', 'native_completion', 'cao_conformance', 'project_baseline'],
                  'request': args.request}
        if not args.apply:
            print(json.dumps({**report, 'check_only': True, 'next': recovery_instruction(work)}))
            return 0
        expected_token = getattr(args, 'expected_token', None)
        if expected_token is not None:
            if expected_token != token(work, digest, args.request):
                raise ValueError('STALE_WORK_TOKEN: business state changed; inspect and preview the candidate again')
        elif args.expected_revision != work.get('state_revision'):
            raise ValueError('STALE_WORK_REVISION: use amend --check and --expected-token to tolerate tool observation updates')
        if digest == work['requirement']['sha256']:
            print(json.dumps({**report, 'status': 'unchanged'}))
            return 0
        planned = deepcopy(work)
        planned['requirement_original_base64'] = base64.b64encode(data).decode()
        reset_requirement(planned, report['after'])
        planned.setdefault('requirement_history', []).append({'requirement': work['requirement'], 'request': args.request, 'original_base64': base64.b64encode(original).decode(), 'at': now()})
        planned['execution_generation'] = work.get('execution_generation', 0) + 1
        planned.setdefault('resume', {})['summary'] = 'Requirement explicitly amended; inspect affected native scope before continuing.'
        planned.setdefault('execution', {})['step'] = 'path-assessment'
        identity = 'amend-' + uuid.uuid4().hex
        journal = {'version': 1, 'id': identity, 'kind': 'amend', 'status': 'prepared',
                   'target': work['id'], 'changes': [{'before': work, 'after': planned}],
                   'requirement_path': work['requirement']['path'],
                   'before_bytes': base64.b64encode(target.read_bytes()).decode(),
                   'after_bytes': base64.b64encode(data).decode()}
        from scripts.lifecycle_guard import digest as intent_digest
        journal['intent_sha256'] = intent_digest({k: v for k, v in journal.items() if k != 'status'})
        atomic_json(root(repo) / (identity + '.json'), journal)
        from scripts.work_operations import recover
        result = recover(repo, identity)
        print(json.dumps(result))
        return 0


def register_parser(sub):
    p = sub.add_parser('amend', help='Preview/apply an explicit version-checked requirement revision')
    p.add_argument('--repo', default='.')
    p.add_argument('--work', required=True)
    p.add_argument('--requirement', required=True, help='Candidate document; original source is retained until apply')
    p.add_argument('--request', required=True, help='Original request or recorded request identity')
    p.add_argument('--original', help='Recovered original for legacy Work; must match the anchored requirement hash')
    p.add_argument('--expected-revision', type=int, help='Legacy strict storage revision; prefer --expected-token')
    p.add_argument('--expected-token', help='Business-state token returned by amend --check')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    p.set_defaults(fn=command)
