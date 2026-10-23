"""Field diagnostics for native review contracts; legacy codes stay compatible."""
import hashlib
import json


class ReviewValidationError(ValueError):
    def __init__(self, legacy_code, diagnostics):
        super().__init__(legacy_code)
        self.diagnostics = diagnostics


def invalid(code, field, expected, actual=None, *, legacy=None):
    raise ReviewValidationError(legacy or code, [{"code": code, "field": field,
                                "expected": expected, "actual": actual}])


def report_shape(report, *, dispatch):
    errors = []
    for field, kind in (("run_id", str), ("input_sha256", str), ("reviewer", dict),
                        ("findings_file", str), ("findings", list)):
        value = report.get(field)
        if not isinstance(value, kind) or (kind == str and not value.strip()):
            errors.append({"code": "NATIVE_REVIEW_REPORT_FIELD_REQUIRED", "field": "$." + field,
                           "actual": value, "expected": kind.__name__})
    reviewer = report.get("reviewer")
    if isinstance(reviewer, dict):
        for key in ("mode", "session_id", "model"):
            if not isinstance(reviewer.get(key), str) or not reviewer[key].strip():
                errors.append({"code": "NATIVE_REVIEW_REPORT_FIELD_REQUIRED", "field": "$.reviewer." + key,
                               "actual": reviewer.get(key), "expected": "nonempty string"})
    findings = report.get("findings")
    for index, item in enumerate(findings if isinstance(findings, list) else []):
        prefix = f"$.findings[{index}]"
        if not isinstance(item, dict):
            errors.append({"code": "NATIVE_REVIEW_TRIAGE_INVALID", "field": prefix, "expected": "object"})
            continue
        fields = {"verdict": {"high", "medium", "low", "false", "maybe-false"},
                  "disposition": {"patch", "defer", "reject", "bad_spec", "intent_gap"} if dispatch else {"patch", "defer", "reject"}}
        for field, allowed in fields.items():
            if not isinstance(item.get(field), str) or item[field] not in allowed:
                errors.append({"code": "NATIVE_REVIEW_TRIAGE_INVALID", "field": prefix + "." + field,
                               "expected": sorted(allowed), "actual": item.get(field)})
        for field in ("id", "evidence"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append({"code": "NATIVE_REVIEW_TRIAGE_INVALID", "field": prefix + "." + field,
                               "expected": "nonempty string", "actual": item.get(field)})
    if errors:
        raise ReviewValidationError("NATIVE_REVIEW_REPORT_INVALID", errors)


def failure(repo, work, args, exc):
    """Stable repair identity, including state: stale retries do not launch new work."""
    from scripts.runtime_store import read_record, safe_path, write_record
    diagnostics = getattr(exc, "diagnostics", None) or [{"code": str(exc), "field": "review",
                                                        "expected": "current native review contract and evidence"}]
    state = {"review": (work.get("sdd") or {}).get("native_reviews"),
             "route": (work.get("sdd") or {}).get("route_decision"),
             "action": args.review_action, "layer": getattr(args, "layer", None),
             "diagnostics": diagnostics}
    from scripts.cao_providers import host_session_identity
    from scripts.path_guard import repo_file
    state['request'] = {key: value for key, value in vars(args).items() if not callable(value)}
    state['session'] = host_session_identity(None)
    report = getattr(args, 'report', None)
    if report:
        try:
            state['report_sha256'] = hashlib.sha256(repo_file(repo, report).read_bytes()).hexdigest()
        except (ValueError, OSError, RuntimeError):
            state['report_sha256'] = None
    repair_id = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()[:24]
    count = 1
    try:
        path = safe_path(repo, work["id"], "review-retry.json")
        previous = read_record(path) or {}
        if previous.get("repair_id") == repair_id:
            count = previous.get("count", 0) + 1
        write_record(path, {"schema_version": 1, "repair_id": repair_id, "count": count}, immutable=False)
    except (ValueError, OSError, RuntimeError):
        pass
    return {"ok": False, "error": str(exc), "diagnostics": diagnostics, "repair_id": repair_id,
            "retry_count": count, "pause_retry": count >= 2,
            "retry_when": "Correct the listed fields or change the failed prerequisite; unchanged input must not be retried or delegated again.",
            "next": "Use `cao conformance show` for current obligations. Reuse a valid native result; do not rerun code review merely to repair report metadata. For stale inputs prepare a new batch in the coordinator, then launch one NEW capable session per layer."}
