"""Observed invocation identity for a switch; never inferred from idle sessions."""
import time
import argparse
from scripts.state_store import atomic_json
from scripts.runtime_store import event_key, read_record


class _SwitchParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def switch_targets(repo, command, cwd):
    """Use the actual switch grammar after trusted shell/launcher parsing."""
    from scripts.cao_tool_policy import control_shell_invocations
    from scripts.work_operations import register_parser
    calls = control_shell_invocations(command, cwd)
    if not calls:
        return []
    parser = _SwitchParser(add_help=False)
    register_parser(parser.add_subparsers())
    targets = []
    for call in calls:
        arguments = call['arguments']
        if arguments[0] != 'switch' or any(flag in arguments for flag in ('--help', '-h')):
            continue
        try:
            args = parser.parse_args(arguments)
        except ValueError:
            continue
        if not args.switch_action and args.to and not args.check and (call['cwd'] / args.repo).resolve() == repo.resolve():
            targets.append(args.to)
    return sorted(set(targets))


def observe(repo, raw, host, session, event):
    from scripts.cao_tool_policy import _event_key, SHELL_TOOLS, tool_cwd
    if _event_key(raw, 'toolName', 'tool_name', 'tool', 'name').lower() not in SHELL_TOOLS:
        return
    payload = raw.get('input') or raw.get('tool_input') or raw.get('arguments') or raw.get('params') or {}
    command = _event_key(payload, 'command', 'cmd') if isinstance(payload, dict) else payload
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
    if not isinstance(command, str) or not command:
        return
    try:
        targets = switch_targets(repo, command, tool_cwd(raw, repo))
    except (ValueError, OSError):
        return
    if targets and event == 'pre_tool':
        atomic_json(path, {'host': host, 'session': session, 'targets': targets, 'at': time.time()})


def identity(repo, target):
    rows = []
    folder = repo / '.orchestrator/control-calls'
    if folder.is_symlink():
        raise ValueError('CONTROL_CONTEXT_SYMLINK')
    for path in folder.glob('*.json'):
        row = read_record(path)
        if row and target in row.get('targets', [row.get('target')]):
            if not 0 <= time.time() - row.get('at', 0) < 120:
                raise ValueError('SWITCH_INVOCATION_EXPIRED: supply --host and --session')
            rows.append((row['host'], row['session']))
    if len(rows) > 1:
        raise ValueError('SWITCH_SESSION_AMBIGUOUS: supply --host and --session')
    return rows[0] if rows else None
