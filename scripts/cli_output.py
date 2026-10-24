"""Compact JSON presentation with original status, errors and process exit code."""
import json


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
