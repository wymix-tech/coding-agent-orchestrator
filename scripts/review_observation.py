"""Bind review tickets to host-observed task prompts and original output bytes.

This is provenance, not semantic certification. Never infer roles from ordering.
"""
import hashlib
import json
import re

from scripts.cao_sessions import save_session
from scripts.state_store import atomic_json

PREFIX = 'CAO_REVIEW_TICKET '


def ticket(run, layer):
    return {'run_id': run['id'], 'layer': layer, 'input_sha256': run['input_sha256']}


def task_marker(run, layer):
    return PREFIX + json.dumps(ticket(run, layer), sort_keys=True, separators=(',', ':'))


def observe(repo, work, session, raw, event):
    if not work or not session:
        return
    if event in {'prompt_submit', 'subagent_start'}:
        prompt = raw.get('prompt')
        if not isinstance(prompt, str) or len(prompt.encode()) > 128 * 1024:
            return
        markers = re.findall(r'(?m)^(?:Task:[ \t]*)?' + PREFIX + r'(\{[^\n]+\})\s*$', prompt)
        if len(markers) != 1:
            return
        try:
            value = json.loads(markers[0])
        except ValueError:
            return
        if not isinstance(value, dict) or not isinstance(value.get('layer'), str):
            return
        reviews = (work.get('sdd') or {}).get('native_reviews') or {}
        run = reviews.get(value.get('layer')) or {}
        if not run or value != ticket(run, value.get('layer')):
            return
        if session['id'] in run.get('existing_session_ids', []):
            return
        previous = session.get('review_ticket')
        if previous and previous != value:
            return  # An existing task cannot be relabeled by a later prompt.
        session['review_ticket'] = value
        save_session(repo, session)
    if session.get('review_ticket'):
        auto_bind(repo, work, session)
        if event == 'post_tool' and not raw.get('isError', raw.get('is_error', False)):
            collect_written_artifact(repo, work, session, raw)
    if event in {'turn_end', 'stop', 'subagent_stop'} and session.get('review_ticket'):
        message = raw.get('last_assistant_message')
        if isinstance(message, str) and message.strip() and len(message.encode()) <= 1024 * 1024:
            session['review_output_sha256'] = hashlib.sha256(message.strip().encode()).hexdigest()
            run = (work.get('sdd', {}).get('native_reviews') or {}).get(session['review_ticket']['layer']) or {}
            binding = run.get('execution_binding') or {}
            if binding.get('session_id') == session['id']:
                from scripts.review_dispatch import bundle_root
                root = bundle_root(repo, work, run['layer'], run)
                original = root / 'original-message.txt'
                if not original.exists():
                    root.mkdir(parents=True, exist_ok=True)
                    original.write_text(message, encoding='utf-8')
                    findings = extract_findings(message)
                    path = root / 'message-findings.md'
                    path.write_text(findings, encoding='utf-8')
                    session['review_message_artifact'] = artifact_record(repo, path)
            save_session(repo, session)


def extract_findings(message):
    # Only the host's explicit trailing transport envelope is removed.
    return re.sub(r'\n```acceptance-report[ \t]*\n.*?\n```[ \t]*\s*\Z', '', message, flags=re.S).strip()


def artifact_record(repo, path):
    return {'path': path.relative_to(repo).as_posix(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'collector_version': 1}


def auto_bind(repo, work, session):
    value = session['review_ticket']
    run = (work.get('sdd', {}).get('native_reviews') or {}).get(value['layer']) or {}
    if run.get('execution_binding') or session.get('kind') != 'subagent':
        return
    from scripts.cao_sessions import load_session
    parent = load_session(repo, session.get('parent_session_id'), host=session.get('host'))
    if not parent or parent.get('kind') != 'primary' or parent.get('status') != 'active':
        return
    from scripts.review_binding import delegated_reviewer
    from scripts.native_obligations import review_inputs
    from scripts.cao_snapshot import code_snapshot
    try:
        delegated_reviewer(repo, run, review_inputs(repo, work, code_snapshot),
                           (parent['host'], parent['host_session_id']), session['id'],
                           run['model'], run['id'], run['input_sha256'])
    except ValueError as exc:
        session['review_binding_error'] = str(exc)
        save_session(repo, session)
        return
    from scripts.review_dispatch import stamp_report
    stamp_report(repo, work, value['layer'], run)
    from scripts.cao_work_store import save_work
    save_work(repo, work)


def collect_written_artifact(repo, work, session, raw):
    # Only a successful host-observed write by the bound reviewer may attest a file.
    name = raw.get('tool_name') or raw.get('toolName') or raw.get('name')
    payload = raw.get('tool_input') or raw.get('input') or raw.get('arguments') or raw.get('args') or {}
    if name not in {'write', 'write_file'} or not isinstance(payload, dict):
        return
    run = (work.get('sdd', {}).get('native_reviews') or {}).get(session['review_ticket']['layer']) or {}
    if (run.get('execution_binding') or {}).get('session_id') != session['id']:
        return
    from scripts.path_guard import provider_artifact
    from scripts.bmad_project import output_dirs
    try:
        path = provider_artifact(repo, payload.get('path') or payload.get('file_path') or '', output_dirs(repo))
        content = payload.get('content')
        if not isinstance(content, str) or len(content.encode()) > 1024 * 1024 or path.read_bytes() != content.encode():
            return
        from scripts.review_dispatch import bundle_root
        root = bundle_root(repo, work, run['layer'], run)
        root.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot = root / ('findings-' + sha + path.suffix)
        if not snapshot.exists():
            snapshot.write_bytes(path.read_bytes())
        records = session.setdefault('review_artifacts', [])
        record = {**artifact_record(repo, snapshot), 'source_path': path.relative_to(repo).as_posix()}
        if record not in records:
            records.append(record)
        save_session(repo, session)
    except (ValueError, OSError, RuntimeError):
        return


def validate_source(repo, run, source):
    if run.get('task_binding_version') != 1:
        return  # Existing historical evidence is not retroactively recertified.
    from scripts.cao_sessions import load_session
    binding = run.get('execution_binding') or {}
    session = load_session(repo, binding.get('session_id'), host=binding.get('host')) or {}
    if session.get('review_ticket') != ticket(run, run['layer']):
        raise ValueError('NATIVE_REVIEW_TASK_MISMATCH')
    for record in [*session.get('review_artifacts', []), session.get('review_message_artifact') or {}]:
        if not record:
            continue
        from scripts.path_guard import repo_file
        snapshot = repo_file(repo, record['path'])
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('NATIVE_REVIEW_COLLECTED_EVIDENCE_CHANGED')
        if hashlib.sha256(source.read_bytes()).hexdigest() == record['sha256']:
            return
    expected = session.get('review_output_sha256')
    if not expected:
        raise ValueError('NATIVE_REVIEW_OUTPUT_UNOBSERVED: collect the bound reviewer final output through its host adapter')
    actual = hashlib.sha256(source.read_text(encoding='utf-8-sig').strip().encode()).hexdigest()
    if expected != actual:
        raise ValueError('NATIVE_REVIEW_OUTPUT_SOURCE_MISMATCH: preserve the bound reviewer original output unchanged')


def diagnose(repo, work, layer):
    from scripts.cao_sessions import load_session
    from scripts.path_guard import repo_file
    run = (work.get('sdd', {}).get('native_reviews') or {}).get(layer) or {}
    binding = run.get('execution_binding') or {}
    session = load_session(repo, binding.get('session_id'), host=binding.get('host')) or {}
    artifacts = []
    for record in [*session.get('review_artifacts', []), session.get('review_message_artifact') or {}]:
        if not record:
            continue
        row = dict(record)
        try:
            row['actual_sha256'] = hashlib.sha256(repo_file(repo, row['path']).read_bytes()).hexdigest()
            row['intact'] = row['actual_sha256'] == row['sha256']
        except (ValueError, OSError, RuntimeError):
            row['intact'] = False
        artifacts.append(row)
    phase = ('resolved' if run.get('status') == 'passed' else 'classified' if run.get('status') == 'classified'
             else 'collected' if artifacts and any(a['intact'] for a in artifacts)
             else 'bound' if binding else 'prepared')
    return {'ok': True, 'layer': layer, 'run_id': run.get('id'), 'input_sha256': run.get('input_sha256'),
            'phase': phase, 'binding': binding, 'session_status': session.get('status'),
            'binding_error': session.get('review_binding_error'),
            'original_message_sha256': session.get('review_output_sha256'), 'artifacts': artifacts,
            'next': ('Run review draft with an intact artifact path; preserve findings and judge every entry.' if phase == 'collected'
                     else 'Collect the bound reviewer output; use review recover with the exact original message for legacy evidence.' if phase == 'bound'
                     else 'Continue the current review phase; do not restart for metadata repair.')}


def recover(repo, work, layer, source):
    """Restore legacy output only after matching its previously observed full message."""
    from scripts.cao_sessions import load_session
    from scripts.path_guard import provider_artifact
    from scripts.bmad_project import output_dirs
    from scripts.review_dispatch import bundle_root
    run = (work.get('sdd', {}).get('native_reviews') or {}).get(layer) or {}
    binding = run.get('execution_binding') or {}
    session = load_session(repo, binding.get('session_id'), host=binding.get('host')) or {}
    if not binding or session.get('review_ticket') != ticket(run, layer):
        raise ValueError('NATIVE_REVIEW_EXECUTION_UNBOUND')
    path = provider_artifact(repo, source, output_dirs(repo))
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('NATIVE_REVIEW_OUTPUT_TOO_LARGE')
    message = path.read_text(encoding='utf-8-sig')
    expected = session.get('review_output_sha256')
    if not expected or hashlib.sha256(message.strip().encode()).hexdigest() != expected:
        raise ValueError('NATIVE_REVIEW_OUTPUT_SOURCE_MISMATCH: recovery requires the exact observed original message')
    root = bundle_root(repo, work, layer, run)
    root.mkdir(parents=True, exist_ok=True)
    original = root / 'recovered-original-message.txt'
    findings = root / 'recovered-findings.md'
    content = extract_findings(message)
    for target, text in ((original, message), (findings, content)):
        if target.exists() and target.read_bytes() != text.encode():
            raise ValueError('NATIVE_REVIEW_COLLECTED_EVIDENCE_CHANGED')
        target.write_text(text, encoding='utf-8')
    session['review_message_artifact'] = {**artifact_record(repo, findings), 'recovered_from_sha256': expected}
    save_session(repo, session)
    return {'ok': True, 'findings_file': findings.relative_to(repo).as_posix(),
            'original_message_sha256': expected, 'next': 'Generate a fresh triage with review draft; do not reuse judgments from another output.'}
