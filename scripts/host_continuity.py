"""Prevent an old bound session from silently writing a newly active Work."""
def permits_reattach(raw, repo):
    from scripts.cao_tool_policy import _collect_values_for_keys, _event_key, SHELL_TOOLS, control_shell_segments, tool_cwd
    commands = _collect_values_for_keys(raw, {'cmd', 'command'})
    payload = raw.get('input') or raw.get('tool_input') or raw.get('arguments') or raw.get('params')
    if not commands and isinstance(payload, str) and _event_key(raw, 'toolName', 'tool_name', 'tool', 'name').lower() in SHELL_TOOLS:
        commands = [payload]
    if not commands:
        return False
    for command in commands:
        calls = control_shell_segments(command, tool_cwd(raw, repo))
        if not calls:
            return False
        for rest in calls:
            if rest and rest[0] == 'runtime' and (len(rest) < 2 or rest[1] not in {'status', 'events', 'runs', 'show', 'logs', 'recover'}):
                return False
            if rest and rest[0] == 'handoff' and (len(rest) < 2 or rest[1] not in {'list', 'show'}):
                return False
            if not rest or rest[0] not in {'resume', 'switch', 'status', 'list', 'context', 'sessions', 'runtime', 'handoff'}:
                return False
    return True
