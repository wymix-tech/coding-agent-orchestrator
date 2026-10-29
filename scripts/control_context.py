"""Observed invocation identity for a switch; never inferred from idle sessions."""
import time
from scripts.state_store import atomic_json
from scripts.runtime_store import event_key, read_record


def observe(repo, raw, host, session, event):
    from scripts.cao_tool_policy import _event_key, control_shell_segments
    if _event_key(raw, 'toolName', 'tool_name', 'tool', 'name').lower() not in {'bash', 'shell'}:
        return
    payload = raw.get('input') or raw.get('tool_input') or raw.get('arguments') or {}
    command = payload.get('command') if isinstance(payload, dict) else None
    call = _event_key(raw, 'tool_call_id', 'toolCallId', 'tool_use_id', 'call_id')
    if not call or not session:
        return
    folder = repo / '.orchestrator/control-calls'
    path = folder / (event_key(host, session, call) + '.json')
    if folder.is_symlink() or path.is_symlink():
        raise ValueError('CONTROL_CONTEXT_SYMLINK')
    if event == 'post_tool':
        path.unlink(missing_ok=True)
        return
    if not command:
        return
    try:
        parts = control_shell_segments(command, repo)
        if not parts or len(parts) != 1:
            return
        tokens = parts[0]
        if 'switch' not in tokens or '--to' not in tokens or '--check' in tokens:
            return
        target = tokens[tokens.index('--to') + 1]
    except (ValueError, IndexError):
        return
    folder = repo / '.orchestrator/control-calls'
    if folder.is_symlink():
        raise ValueError('CONTROL_CONTEXT_SYMLINK')
    path = folder / (event_key(host, session, call) + '.json')
    if path.is_symlink():
        raise ValueError('CONTROL_CONTEXT_SYMLINK')
    if event == 'post_tool':
        path.unlink(missing_ok=True)
    elif event == 'pre_tool':
        atomic_json(path, {'host': host, 'session': session, 'target': target, 'at': time.time()})


def identity(repo, target):
    rows = []
    folder = repo / '.orchestrator/control-calls'
    if folder.is_symlink():
        raise ValueError('CONTROL_CONTEXT_SYMLINK')
    for path in folder.glob('*.json'):
        row = read_record(path)
        if row and row.get('target') == target and 0 <= time.time() - row.get('at', 0) < 120:
            rows.append((row['host'], row['session']))
    if len(rows) > 1:
        raise ValueError('SWITCH_SESSION_AMBIGUOUS: supply --host and --session')
    return rows[0] if rows else None
