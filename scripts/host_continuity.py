"""Prevent an old bound session from silently writing a newly active Work."""
from pathlib import Path


def permits_reattach(raw, repo):
    from scripts.cao_tool_policy import _collect_values_for_keys, _shell_segments, readonly_shell
    commands = _collect_values_for_keys(raw, {'cmd', 'command'})
    if not commands:
        return False
    for command in commands:
        if not readonly_shell(command, repo):
            return False
        for tokens in _shell_segments(command):
            markers = [i for i, t in enumerate(tokens) if Path(t).name in {'cao', 'cao.py'}]
            if not markers:
                if tokens and tokens[0] == 'cd':
                    continue
                return False
            rest = [t for t in tokens[markers[-1] + 1:] if t != '--compact-json']
            if rest and rest[0] == 'runtime' and (len(rest) < 2 or rest[1] not in {'status', 'events', 'runs', 'show', 'logs', 'recover'}):
                return False
            if rest and rest[0] == 'handoff' and (len(rest) < 2 or rest[1] not in {'list', 'show'}):
                return False
            if not rest or rest[0] not in {'resume', 'switch', 'status', 'list', 'context', 'sessions', 'runtime', 'handoff'}:
                return False
    return True
