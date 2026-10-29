"""Idempotent ticket recovery using collected bytes and bounded native archives."""
import hashlib
import json
from pathlib import Path
from scripts.path_guard import repo_file


def canonical_source(repo, work, layer):
    from scripts.review_observation import diagnose, validate_source
    state = diagnose(repo, work, layer)
    artifacts = state['artifacts']
    if any(not row['intact'] for row in artifacts):
        raise ValueError('NATIVE_REVIEW_COLLECTED_EVIDENCE_CHANGED: restore the immutable collected bytes')
    from scripts.review_dispatch import bundle_root
    run = work['sdd']['native_reviews'][layer]
    report = bundle_root(repo, work, layer, run) / 'report.json'
    if report.is_file():
        previous = json.loads(report.read_text())
        if previous.get('source_sha256'):
            for artifact in artifacts:
                if artifact['sha256'] == previous['source_sha256']:
                    source = repo_file(repo, artifact['path'])
                    validate_source(repo, run, source)
                    return artifact['path']
    # File findings are authoritative when they exist, a final summary is fallback.
    files = [r for r in artifacts if r.get('source_path')]
    latest = {r['source_path']: r for r in files}
    choices = list(latest.values()) or artifacts
    unique = {r['sha256']: r for r in choices}
    if len(unique) != 1:
        raise ValueError('NATIVE_REVIEW_SOURCE_AMBIGUOUS' if unique else 'NATIVE_REVIEW_OUTPUT_UNOBSERVED')
    chosen = next(iter(unique.values()))
    source = repo_file(repo, chosen['path'])
    run = work['sdd']['native_reviews'][layer]
    validate_source(repo, run, source)
    return source.relative_to(repo).as_posix()


def archive_candidates(repo, session, explicit=()):
    """Only explicit archives or the bound Pi session's repository directory."""
    candidates = [Path(name).expanduser() for name in explicit]
    recorded = session.get('native_session_file')
    if recorded:
        candidates.append(Path(recorded))
    if session.get('host') == 'pi':
        folder = Path.home() / '.pi/agent/sessions' / ('--' + str(repo.resolve()).strip('/').replace('/', '-') + '--')
        if folder.is_dir() and not folder.is_symlink():
            for index, path in enumerate(sorted(folder.glob('*.jsonl'))):
                if index >= 1000:
                    break
                candidates.append(path)
    return list(dict.fromkeys(candidates))


def archived_message(repo, session, explicit=()):
    expected = session.get('review_output_sha256')
    if not expected:
        return None
    native_id = session.get('host_session_id')
    for path in archive_candidates(repo, session, explicit):
        if not path.is_absolute():
            path = repo / path
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            continue
        with path.open(encoding='utf-8-sig') as stream:
            try:
                header = json.loads(stream.readline(2 * 1024 * 1024))
            except (ValueError, UnicodeError):
                continue
            if not isinstance(header, dict):
                continue
            identified = header.get('type') == 'session' and header.get('id') == native_id
            if not identified and native_id not in path.name:
                continue
            for line in stream:
                if len(line.encode()) > 2 * 1024 * 1024:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                message = record.get('message') or {}
                if not isinstance(message, dict) or message.get('role') != 'assistant':
                    continue
                content = message.get('content') or []
                text = content if isinstance(content, str) else '\n'.join(item['text'] for item in content if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str))
                if hashlib.sha256(text.strip().encode()).hexdigest() == expected:
                    return text
    return None


def resume(repo, work, layers=None, transcripts=()):
    from scripts.cao_sessions import load_session
    from scripts.review_observation import diagnose, recover_message, ticket
    from scripts.review_dispatch import draft_report, bundle_root
    from scripts.native_obligations import active_layers, review_integrity
    integrity = review_integrity(repo, work)
    if not integrity['ready']:
        return {'ok': False, 'error': integrity['reason'], 'state': integrity,
                'next': 'Restore the named evidence or explicitly restart only the affected stale layers; resume never changes input identity.'}
    selected = layers or active_layers(repo, work)
    outputs = []
    for layer in selected:
        run = (work['sdd'].get('native_reviews') or {}).get(layer) or {}
        state = diagnose(repo, work, layer)
        row = {'layer': layer, 'run_id': run.get('id'), 'phase': state['phase']}
        if run.get('status') in {'classified', 'passed'}:
            row['next'] = 'Continue unresolved patches and native checks, then resolve.' if run['status'] == 'classified' else 'Reuse this resolved layer.'
            outputs.append(row)
            continue
        binding = run.get('execution_binding') or {}
        session = load_session(repo, binding.get('session_id'), host=binding.get('host')) or {}
        try:
            if not binding or session.get('review_ticket') != ticket(run, layer):
                raise ValueError('NATIVE_REVIEW_EXECUTION_UNBOUND: resume cannot retroactively bind a reviewer')
            if not state['artifacts']:
                message = archived_message(repo, session, transcripts)
                if message:
                    recover_message(repo, work, layer, message)
                elif session.get('status') == 'active' and not session.get('review_output_sha256'):
                    row.update(phase='waiting', next='Wait for the bound reviewer; do not create another ticket.')
                    outputs.append(row)
                    continue
                else:
                    raise ValueError('NATIVE_REVIEW_ORIGINAL_UNAVAILABLE: supply --transcript <bound-session.jsonl> or explicitly restart this unrecoverable layer with a reason')
            source = canonical_source(repo, work, layer)
            report = bundle_root(repo, work, layer, run) / 'report.json'
            if report.is_file():
                previous = json.loads(report.read_text())
                if previous.get('findings'):
                    from scripts.native_obligations import load_findings, validate_finding_content
                    if previous.get('run_id') != run.get('id') or previous.get('input_sha256') != run.get('input_sha256'):
                        raise ValueError('NATIVE_REVIEW_RUN_STALE_OR_MISMATCH: preserve this report as historical; it belongs to another ticket')
                    validate_finding_content(run, previous, repo_file(repo, source), load_findings(repo_file(repo, source)))
                    if previous.get('findings_file') != source:
                        # Correct only the attested source pointer; preserve judgments.
                        from scripts.state_store import atomic_json
                        previous['findings_file'] = source
                        atomic_json(report, previous)
                    row.update(phase='drafted', source=source, report=report.relative_to(repo).as_posix(), next='Reuse the existing triage judgments; classify after all outputs are collected.')
                    outputs.append(row)
                    continue
            draft = draft_report(repo, work, layer, source)
            row.update(phase='drafted', source=source, report=draft['report'], next=draft['next'])
        except (ValueError, OSError, KeyError) as exc:
            row.update(phase='blocked', error=str(exc), next='Inspect this layer only. Preserve all other tickets and evidence.')
        outputs.append(row)
    return {'ok': all(row['phase'] != 'blocked' for row in outputs), 'layers': outputs,
            'next': 'Wait for all outputs, judge the preserved drafts, then classify every layer before patches. Classified layers continue through native checks and resolution.'}
