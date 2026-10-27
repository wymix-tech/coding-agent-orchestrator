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
        if args.evidence_command == 'show':
            import base64
            from scripts.path_guard import repo_file
            from hashlib import sha256
            row = next((r for r in records if r['id'] == args.id), None)
            if not row:
                raise ValueError('IMPLEMENTATION_EVIDENCE_NOT_FOUND')
            data = repo_file(repo, row['archive']).read_bytes() if row.get('archive') else None
            if data is not None and sha256(data).hexdigest() != row['sha256']:
                raise ValueError('EVIDENCE_ARCHIVE_CORRUPT')
            print(json.dumps({'ok': True, 'evidence': row, 'original_base64': base64.b64encode(data).decode() if data is not None else None, 'historical_content_unavailable': data is None}))
            return 0
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
        if args.unit:
            from scripts.native_units import units
            if args.unit not in {r['id'] for r in units(repo, work)}:
                raise ValueError('NATIVE_UNIT_NOT_BOUND')
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
        from scripts.lifecycle_contract import inputs
        record['inputs'] = inputs(repo, work)
        if args.supersedes:
            if args.supersedes not in {r['id'] for r in records}:
                raise ValueError('IMPLEMENTATION_EVIDENCE_NOT_FOUND')
            record['supersedes'] = args.supersedes
        if args.session:
            from scripts.cao_sessions import load_session
            session = load_session(repo, args.session, args.host)
            if not session or session.get('work_item_id') != work['id']:
                raise ValueError('EVIDENCE_SESSION_NOT_BOUND')
            record['session_id'] = session['id']
            record['parent_session_id'] = session.get('parent_session_id')
        if args.run:
            from scripts.runtime_queries import inspect_run
            run = inspect_run(repo, work, args.run)
            if not run['receipt'] or run['start'].get('generation') != work.get('execution_generation', 0):
                raise ValueError('EVIDENCE_EXECUTION_RECEIPT_REQUIRED')
            record['run_id'] = args.run
            record['source'] = 'executor_receipt' if run['start']['source'] == 'cao_executor' else 'agent_declaration'
            record['execution_inputs'] = run['start']['inputs']
        identity = 'evidence-' + digest(record)[:24]
        if any(row['id'] == identity for row in records):
            print(json.dumps({'ok': True, 'status': 'unchanged', 'evidence_id': identity}))
            return 0
        from scripts.evidence_archive import retain
        data = path.read_bytes()
        from hashlib import sha256
        if sha256(data).hexdigest() != before:
            raise ValueError('IMPLEMENTATION_EVIDENCE_CHANGED_DURING_CAPTURE')
        record.update(id=identity, schema_version=1, attached_at=now(),
                      archive=retain(repo, work['id'], data))
        if args.supersedes:
            if args.supersedes not in {r['id'] for r in records}:
                raise ValueError('IMPLEMENTATION_EVIDENCE_NOT_FOUND')
            record['supersedes'] = args.supersedes
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
    for name in ('attach', 'list', 'show'):
        p = actions.add_parser(name)
        p.add_argument('--repo', default='.')
        p.add_argument('--work')
        if name == 'show':
            p.add_argument('--id', required=True)
        if name == 'attach':
            p.add_argument('--path', required=True)
            p.add_argument('--kind', choices=['plan', 'implementation', 'test', 'review', 'fix', 'acceptance', 'handoff'], required=True)
            p.add_argument('--unit')
            p.add_argument('--supersedes')
            p.add_argument('--run')
            p.add_argument('--session')
            p.add_argument('--host')
        elif name == 'list':
            p.add_argument('--limit', type=int, choices=range(1, 501), default=50)
        p.set_defaults(fn=command)
