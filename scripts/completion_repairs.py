"""Bound repeated native report repair attempts to unchanged actual inputs."""
import re


def signature(repo, work, names):
    from scripts.lifecycle_guard import digest
    from scripts.lifecycle_contract import inputs
    from scripts.path_guard import repo_file
    from scripts.cao_runtime import sha256_file
    reports = {}
    for name in names:
        try:
            path = repo_file(repo, name)
            reports[name] = sha256_file(path) if path.is_file() else 'missing'
        except (ValueError, OSError, RuntimeError):
            reports[name] = 'unavailable'
    return digest({'reports': reports, 'inputs': inputs(repo, work)}), reports


def current(repo, work):
    row = (work.get('sdd') or {}).get('completion_binding_failure')
    if not row or row.get('reason') in {'NATIVE_COMPLETION_GATE_UNKNOWN', 'NATIVE_COMPLETION_REPORT_REQUIRED'}:
        return None
    key, _ = signature(repo, work, list(row['reports']))
    return row if row['signature'] == key else None


def record(repo, work, names, error):
    from scripts.cao_work_store import save_work, queue_event
    key, refs = signature(repo, work, names)
    previous = (work.get('sdd') or {}).get('completion_binding_failure') or {}
    attempts = min(2, int(previous.get('attempts', 0)) + 1) if previous.get('signature') == key else 1
    code = str(error).split(':', 1)[0]
    if not re.fullmatch('[A-Z][A-Z0-9_]+', code):
        code = 'NATIVE_COMPLETION_REPORT_INVALID'
    row = {'signature': key, 'reports': refs, 'attempts': attempts, 'paused': attempts >= 2,
           'reason': code, 'detail': str(error)[:1200]}
    work['sdd']['completion_binding_failure'] = row
    queue_event(work, {'event': 'sdd.completion.binding.failed', 'work_item_id': work['id'],
                       'reason': code, 'attempts': attempts, 'signature': key})
    save_work(repo, work)
    return row


def response(row):
    return {'ok': False, 'error': 'NATIVE_COMPLETION_REPAIR_PAUSED' if row['paused'] else row['reason'],
            'repair': row, 'next': 'Inspect the original native report and the reported outcome/source error. Correct those inputs before retrying; do not restart native code reviews or recreate Work for an unchanged report failure.'}
