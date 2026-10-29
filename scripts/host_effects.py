"""Shared host-effect accounting; observations are not process ownership."""
def effects(repo, wid, pending_only=False):
    from scripts.runtime_queries import events
    starts, finishes = {}, {}
    for event in events(repo, wid):
        key = (event.get('host'), event.get('session_id'), event.get('tool_call_id'))
        if not key[-1]:
            continue
        if event.get('event') == 'tool.started':
            starts[key] = event
        elif event.get('event') in {'tool.finished', 'tool.effect.settled'}:
            finishes[key] = event
    result = []
    for key, start in starts.items():
        finish = finishes.get(key)
        if pending_only and finish:
            continue
        result.append({'event_id': start.get('event_id', str(key)), **{k: start.get(k) for k in ('host', 'session_id', 'tool_call_id', 'tool', 'occurred_at', 'coverage')},
                       'state': 'resolved' if finish else 'outcome_unknown',
                       'resolution': finish.get('event') if finish else None,
                       'next': None if finish else 'Inspect this exact call and its execution/plugin evidence; wait for live execution or use runtime settle-effect with matching inspection evidence. Session end alone does not settle effects.'})
    return sorted(result, key=lambda row: str(row.get('occurred_at')))
