"""Incremental native artifact declarations, independent of completion admission."""
from copy import deepcopy
import json


def command(args):
    from scripts.cao_runtime import repo_root, ensure_initialized, sha256_file, now
    from scripts.cao_work_store import resolve_work, save_work, queue_event
    from scripts.cao_provider_workflows import provider_output_dirs
    from scripts.path_guard import provider_artifact
    from scripts.state_store import repo_transaction
    from scripts.lifecycle_guard import digest
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    with repo_transaction(repo):
        work = resolve_work(repo, args.work)
        if not work:
            raise ValueError('WORK_ITEM_NOT_FOUND')
        records = (work.get('sdd') or {}).get('implementation_evidence') or []
        if args.evidence_command == 'list':
            rows = []
            for row in records:
                item = deepcopy(row)
                try:
                    path = provider_artifact(repo, row['path'], provider_output_dirs(work['sdd']['provider'], repo))
                    item['content_state'] = 'unchanged' if sha256_file(path) == row['sha256'] else 'changed'
                except (OSError, ValueError):
                    item['content_state'] = 'missing'
                rows.append(item)
            print(json.dumps({'ok': True, 'items': rows[-args.limit:], 'total': len(rows),
                              'omitted': max(0, len(rows) - args.limit)}, ensure_ascii=False))
            return 0
        if work.get('status') != 'active':
            raise ValueError('WORK_NOT_ACTIVE')
        path = provider_artifact(repo, args.path, provider_output_dirs(work['sdd']['provider'], repo))
        if not path.is_file() or not 0 < path.stat().st_size <= 8 * 1024 * 1024:
            raise ValueError('IMPLEMENTATION_EVIDENCE_INVALID')
        # Attachment observes present bytes, never claims execution-time inputs.
        before = sha256_file(path)
        observation = {'outcome': 'unknown'}
        if args.kind in {'test', 'acceptance'}:
            from scripts.native_reports import read
            try:
                observation = read(repo, work, path)
            except (ValueError, OSError, UnicodeError) as exc:
                observation = {'outcome': 'failed' if str(exc).startswith('NATIVE_COMPLETION_REPORT_NOT_PASSED') else 'unknown', 'diagnostic': str(exc)[:1200]}
        record = {'work_id': work['id'], 'provider': work['sdd']['provider'],
                  'kind': args.kind, 'path': path.relative_to(repo).as_posix(),
                  'sha256': before, 'unit_ref': args.unit,
                  'requirement': deepcopy(work.get('requirement')),
                  'scope': deepcopy(work['sdd'].get('plan_scope')),
                  'generation': work.get('execution_generation', 0),
                  'source': 'agent_declaration', 'observation': observation,
                  'input_observation': 'attachment_time_only; execution inputs not certified'}
        if sha256_file(path) != before:
            raise ValueError('IMPLEMENTATION_EVIDENCE_CHANGED_DURING_CAPTURE')
        identity = 'evidence-' + digest(record)[:24]
        if any(row['id'] == identity for row in records):
            print(json.dumps({'ok': True, 'status': 'unchanged', 'evidence_id': identity}))
            return 0
        record.update(id=identity, schema_version=1, attached_at=now())
        work['sdd'].setdefault('implementation_evidence', []).append(record)
        queue_event(work, {'event': 'sdd.implementation_evidence.attached',
                          'work_item_id': work['id'], 'evidence_id': identity})
        save_work(repo, work)
        print(json.dumps({'ok': True, 'evidence': record,
                          'note': 'Process declaration only; use lifecycle bind after native completion.'}, ensure_ascii=False))
        return 0


def register_parser(sub):
    parser = sub.add_parser('evidence', help='Preserve native process declarations without granting completion')
    actions = parser.add_subparsers(dest='evidence_command', required=True)
    for name in ('attach', 'list'):
        p = actions.add_parser(name)
        p.add_argument('--repo', default='.')
        p.add_argument('--work')
        if name == 'attach':
            p.add_argument('--path', required=True)
            p.add_argument('--kind', choices=['plan', 'implementation', 'test', 'review', 'fix', 'acceptance', 'handoff'], required=True)
            p.add_argument('--unit')
        else:
            p.add_argument('--limit', type=int, choices=range(1, 501), default=50)
        p.set_defaults(fn=command)
