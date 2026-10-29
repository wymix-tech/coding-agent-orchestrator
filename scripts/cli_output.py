"""Compact JSON presentation with original status, errors and process exit code."""
import json

OUTPUT_FLAGS = frozenset({'--compact-json', '--agent-json', '--full-json'})


def strip_output_flags(arguments):
    """Global presentation flags are accepted anywhere by the CLI."""
    return [item for item in arguments if item not in OUTPUT_FLAGS]


def summarize(value):
    if not isinstance(value, dict):
        return value
    def compact(item):
        if isinstance(item, dict):
            result = {}
            for key, val in item.items():
                if key in {'stdout', 'stderr', 'stdout_tail', 'stderr_tail'} and isinstance(val, str) and len(val) > 4000:
                    result[key] = val[-4000:]
                    result[key + '_truncated'] = True
                else:
                    result[key] = compact(val)
            return result
        if isinstance(item, list):
            return [compact(val) for val in item]
        return item
    return compact(value)


def emit_summary(text):
    try:
        value = json.loads(text)
    except ValueError:
        print(text, end='')
    else:
        print(json.dumps(summarize(value), ensure_ascii=False))


def agent_view(value):
    """Bound navigation output; full machine JSON remains explicitly available."""
    history_keys = {'native_review_history', 'route_history', 'completion_evidence_history', 'lifecycle_migrations',
                    'review_protocol_migrations', 'handoff_history', 'history'}
    manifests = {'product', 'native_sources', 'native_dependencies', 'files', 'domains'}
    def walk(item, key=None):
        if isinstance(item, dict):
            if 'task_binding_version' in item and 'input_sha256' in item:
                return {k: walk(item[k], k) for k in ('id', 'round_id', 'status', 'layer', 'raw_count', 'classified_count',
                        'model_source', 'input_manifest', 'context_package', 'resolution_binding_version') if k in item}
            if key == 'native_gate_evidence':
                return {gate: {k: walk(row[k], k) for k in ('requirement_id', 'owner') if k in row}
                        for gate, row in item.items()}
            if key == 'native_reviews':
                return {layer: {k: walk(row[k], k) for k in ('id', 'round_id', 'status', 'raw_count', 'classified_count',
                        'model_source', 'input_manifest', 'context_package', 'resolution_binding_version') if k in row}
                        for layer, row in item.items() if layer != '_contract' and isinstance(row, dict)}
            if key in {'completion_evidence', 'native_completion_evidence', 'native_completion', 'task_acceptance'}:
                return {k: walk(v, k) for k, v in item.items() if k in {'ready', 'fresh', 'state', 'reason', 'error', 'next',
                        'reports', 'pending_gates', 'covered_gates', 'evidence_protocol', 'changed_inputs', 'changed_domains',
                        'changed_paths', 'affected_evidence', 'still_valid_evidence', 'owner'}}
            result = {}
            for name, val in item.items():
                if name in history_keys:
                    result[name + '_count'] = len(val) if isinstance(val, (list, dict)) else 0
                elif name in manifests and isinstance(val, (dict, list)) and len(val) > 8:
                    result[name + '_count'] = len(val)
                elif name in {'stdout', 'stderr', 'stdout_tail', 'stderr_tail', 'output_tail'} and isinstance(val, str) and len(val) > 1200:
                    result[name] = val[-1200:]
                    result[name + '_truncated'] = True
                elif name in {'source_refs', 'observations'}:
                    result[name + '_count'] = len(val) if isinstance(val, (list, dict)) else 0
                elif name == 'observed' and isinstance(val, dict):
                    result[name] = {k: walk(v, k) for k, v in val.items() if k in {'ready', 'state', 'reason', 'next', 'pending_gates', 'pending_plan_items'}}
                else:
                    result[name] = walk(val, name)
            return result
        if isinstance(item, list):
            if key in {'native_host_executions', 'completion_executions'}:
                return {'total': len(item), 'latest': [{k: row.get(k) for k in ('run_id', 'status', 'command', 'reason', 'reports')} for row in item[-3:]], 'inspect': 'cao lifecycle executions'}
            return [walk(row, key) for row in item]
        return item
    result = walk(summarize(value))
    if isinstance(result, dict):
        result['presentation'] = {'mode': 'agent', 'full': 'Repeat this command with --full-json; review status --history lists prior rounds.'}
    return result


def emit_agent(text):
    try:
        value = json.loads(text)
    except ValueError:
        print(text, end='')
    else:
        print(json.dumps(agent_view(value), ensure_ascii=False))
