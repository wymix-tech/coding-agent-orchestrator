"""Incremental native artifact declarations, independent of completion admission."""
from copy import deepcopy
import json
import pathlib


def records(repo, work):
    from scripts.path_guard import repo_file
    from scripts.runtime_store import read_record
    from scripts.lifecycle_guard import digest
    rows = []
    for ref in (work.get('sdd') or {}).get('implementation_evidence') or []:
        if 'record' not in ref:
            rows.append(ref)
            continue
        row = read_record(repo_file(repo, ref['record']))
        if not row or row.get('id') != ref['id'] or digest(row) != ref['sha256']:
            raise ValueError('IMPLEMENTATION_EVIDENCE_RECORD_CORRUPT')
        rows.append(row)
    return rows


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
        refs = (work.get('sdd') or {}).get('implementation_evidence') or []
        if args.evidence_command == 'list':
            if args.cursor < 0:
                raise ValueError('EVIDENCE_CURSOR_INVALID')
            selected = deepcopy(work)
            selected['sdd']['implementation_evidence'] = list(reversed(refs))[args.cursor:args.cursor + args.limit]
            evidence_rows = records(repo, selected)
        else:
            evidence_rows = records(repo, work)
        if args.evidence_command == 'show':
            import base64
            from scripts.path_guard import repo_file
            from hashlib import sha256
            row = next((r for r in evidence_rows if r['id'] == args.id), None)
            if not row:
                raise ValueError('IMPLEMENTATION_EVIDENCE_NOT_FOUND')
            data = repo_file(repo, row['archive']).read_bytes() if row.get('archive') else None
            if data is not None and sha256(data).hexdigest() != row['sha256']:
                raise ValueError('EVIDENCE_ARCHIVE_CORRUPT')
            exported = None
            if args.output:
                if data is None:
                    raise ValueError('HISTORICAL_CONTENT_UNAVAILABLE')
                target = provider_artifact(repo, args.output, provider_output_dirs(work['sdd']['provider'], repo))
                if target.suffix.lower() != pathlib.Path(row['path']).suffix.lower():
                    raise ValueError('EVIDENCE_EXPORT_FORMAT_MISMATCH')
                import os
                with target.open('xb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                exported = target.relative_to(repo).as_posix()
            omitted = data is not None and len(data) > 65536
            print(json.dumps({'ok': True, 'evidence': row, 'original_base64': base64.b64encode(data).decode() if data is not None and not omitted and not exported else None, 'exported': exported, 'body_omitted': omitted, 'next': 'Use evidence show --id <id> --output <new-provider-file> for a large original.' if omitted else None, 'historical_content_unavailable': data is None}))
            return 0
        if args.evidence_command == 'list':
            rows = []
            for row in evidence_rows:
                item = deepcopy(row)
                try:
                    path = provider_artifact(repo, row['path'], provider_output_dirs(work['sdd']['provider'], repo))
                    item['content_state'] = 'unchanged' if sha256_file(path) == row['sha256'] else 'changed'
                except (OSError, ValueError):
                    item['content_state'] = 'missing'
                rows.append(item)
            print(json.dumps({'ok': True, 'items': rows, 'total': len(refs),
                              'next_cursor': args.cursor + len(rows) if args.cursor + len(rows) < len(refs) else None,
                              'omitted': max(0, len(refs) - len(rows))}, ensure_ascii=False))
            return 0
        historical = args.historical
        if work.get('status') != 'active' and not (work.get('status') == 'suspended' and historical):
            raise ValueError('WORK_NOT_ACTIVE: suspended evidence requires --historical')
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
                  'attempt_id': args.attempt, 'historical_only': historical, 'native_step': (work.get('execution') or {}).get('step'),
                  'input_observation': 'attachment_time_only; execution inputs not certified'}
        if sha256_file(path) != before:
            raise ValueError('IMPLEMENTATION_EVIDENCE_CHANGED_DURING_CAPTURE')
        from scripts.lifecycle_contract import inputs
        candidate = deepcopy(work)
        candidate['sdd'].setdefault('implementation_evidence', []).append(deepcopy(record))
        record['inputs'] = inputs(repo, candidate)
        if args.supersedes:
            if args.supersedes not in {r['id'] for r in evidence_rows}:
                raise ValueError('IMPLEMENTATION_EVIDENCE_NOT_FOUND')
            record['supersedes'] = args.supersedes
        if args.session:
            from scripts.cao_sessions import load_session
            session = load_session(repo, args.session, args.host)
            if not session or session.get('work_item_id') != work['id']:
                raise ValueError('EVIDENCE_SESSION_NOT_BOUND')
            record['session_id'] = session['id']
            record['parent_session_id'] = session.get('parent_session_id')
        if args.tool_call:
            if not args.session:
                raise ValueError('EVIDENCE_PRODUCER_SESSION_REQUIRED')
            from scripts.runtime_queries import events
            observed = next((event for event in events(repo, work['id']) if event.get('event') == 'tool.finished' and event.get('tool_call_id') == args.tool_call and event.get('host') == session['host'] and event.get('session_id') == session['host_session_id']), None)
            if not observed:
                raise ValueError('EVIDENCE_TOOL_RESULT_NOT_OBSERVED')
            record.update(source='correlated_host_observation', tool_call_id=args.tool_call, attempt_id='tool:' + args.tool_call)
        if args.run:
            from scripts.runtime_queries import inspect_run
            run = inspect_run(repo, work, args.run)
            if not run['receipt']:
                raise ValueError('EVIDENCE_EXECUTION_RECEIPT_REQUIRED')
            record['historical_generation'] = run['start'].get('generation') != work.get('execution_generation', 0)
            record['execution_generation'] = run['start'].get('generation')
            record['attempt_id'] = args.run
            record['run_id'] = args.run
            record['source'] = 'executor_receipt' if run['start']['source'] == 'cao_executor' else 'agent_declaration'
            record['execution_inputs'] = run['start']['inputs']
        identity = 'evidence-' + digest(record)[:24]
        if any(row['id'] == identity for row in evidence_rows):
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
            if args.supersedes not in {r['id'] for r in evidence_rows}:
                raise ValueError('IMPLEMENTATION_EVIDENCE_NOT_FOUND')
            record['supersedes'] = args.supersedes
        from scripts.runtime_store import safe_path, write_record
        record_path = safe_path(repo, work['id'], 'implementation-evidence', identity + '.json')
        write_record(record_path, record)
        work['sdd'].setdefault('implementation_evidence', []).append({'id': identity, 'record': record_path.relative_to(repo).as_posix(), 'sha256': digest(record)})
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
            p.add_argument('--output', help='Export archived bytes to a new provider-owned file; existing files are never overwritten')
        if name == 'attach':
            p.add_argument('--path', required=True)
            p.add_argument('--kind', choices=['plan', 'implementation', 'test', 'review', 'fix', 'acceptance', 'handoff'], required=True)
            p.add_argument('--unit')
            p.add_argument('--supersedes')
            execution = p.add_mutually_exclusive_group()
            execution.add_argument('--run')
            execution.add_argument('--tool-call')
            p.add_argument('--attempt', help='Declared native attempt identity; does not certify execution')
            p.add_argument('--historical', action='store_true', help='Preserve late evidence for suspended Work without granting completion')
            p.add_argument('--session')
            p.add_argument('--host')
        elif name == 'list':
            p.add_argument('--cursor', type=int, default=0)
            p.add_argument('--limit', type=int, choices=range(1, 501), default=50)
        p.set_defaults(fn=command)
