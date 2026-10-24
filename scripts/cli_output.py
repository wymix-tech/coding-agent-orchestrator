"""Compact JSON presentation with original status, errors and process exit code."""
import json


def summarize(value):
    if not isinstance(value, dict):
        return value
    keys = ('ready', 'blockers', 'lifecycle_ready', 'decision', 'ok', 'error', 'reason', 'reason_code', 'status', 'state', 'next', 'diagnostics',
            'repair_id', 'retry_count', 'pause_retry', 'retry_when', 'report', 'source', 'source_sha256',
            'layers', 'acceptance_preflight', 'active_layers', 'reused', 'reviewer_start', 'acceptance_review_required', 'coverage')
    result = {key: value[key] for key in keys if key in value}
    for key in ('review', 'verification'):
        if isinstance(value.get(key), dict):
            item = value[key]
            result[key] = {field: item[field] for field in
                           ('id', 'run_id', 'status', 'run_status', 'reason', 'reason_code', 'next',
                            'classified_count', 'raw_count', 'loopback_required', 'input_sha256',
                            'execution_binding', 'changed_inputs') if field in item}
    if isinstance(value.get('reviews'), list):
        result['reviews'] = [{key: item[key] for key in
                              ('layer', 'run_id', 'status', 'classified_count', 'report', 'reviewer_start', 'task_contract', 'capability') if key in item}
                             for item in value['reviews']]
    if not result or set(result) <= {'ok', 'decision'}:
        return value
    return result


def emit_summary(text):
    try:
        value = json.loads(text)
    except ValueError:
        print(text, end='')
    else:
        print(json.dumps(summarize(value), ensure_ascii=False))
