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


def request_identity(repo, work, args):
    from scripts.path_guard import repo_file
    from scripts.cao_providers import host_session_identity
    run = ((work.get('sdd') or {}).get('native_reviews') or {}).get(getattr(args, 'layer', None) if isinstance(getattr(args, 'layer', None), str) else None) or {}
    value = {'request': {key: val for key, val in vars(args).items() if not callable(val)},
             'session': host_session_identity(None), 'run': run}
    try:
        from scripts.cao_snapshot import code_snapshot
        from scripts.native_obligations import review_inputs
        value['current_inputs'] = review_inputs(repo, work, code_snapshot)
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        value['current_inputs'] = str(exc)
    child_ref = getattr(args, 'reviewer_session', None)
    if child_ref:
        from scripts.cao_sessions import load_session
        identity = value['session']
        child = load_session(repo, child_ref, host=identity[0]) if identity else None
        value['child'] = {key: (child or {}).get(key) for key in
                          ('id', 'status', 'work_item_id', 'parent_session_id', 'observed_model', 'observed_tools', 'review_ticket', 'review_output_sha256')}
    paths = [getattr(args, key, None) for key in ('report', 'source', 'instructions', 'agent_definition')]
    report = getattr(args, 'report', None)
    if report and args.review_action == 'classify-batch':
        try:
            manifest = json.loads(repo_file(repo, report).read_text())
            if isinstance(manifest, dict):
                paths += [path for path in manifest.values() if isinstance(path, str)]
        except (ValueError, OSError, RuntimeError):
            pass
    value['files'] = {}
    for path in filter(None, paths):
        try:
            raw = repo_file(repo, path).read_bytes()
            value['files'][path] = hashlib.sha256(raw).hexdigest()
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
            source = payload.get('findings_file') if isinstance(payload, dict) else None
            if isinstance(source, str):
                value['files'][source] = hashlib.sha256(repo_file(repo, source).read_bytes()).hexdigest()
        except (ValueError, OSError, RuntimeError):
            value['files'][path] = None
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def paused_failure(repo, work, args):
    from scripts.runtime_store import read_record, safe_path
    try:
        record = read_record(safe_path(repo, work['id'], 'review-retry.json')) or {}
        if args.review_action not in {'status', 'diagnose', 'recover', 'resume', 'migrate'} and record.get('request_id') == request_identity(repo, work, args) and record.get('count', 0) >= 2:
            return record.get('response')
    except (ValueError, OSError, RuntimeError):
        pass
    return None


def failure(repo, work, args, exc):
    """Count a logical fault across cosmetic report edits, sessions and other layers."""
    from scripts.runtime_store import read_record, safe_path, write_record
    diagnostics = getattr(exc, 'diagnostics', None) or [{'code': str(exc), 'field': 'review',
                                                        'expected': 'current native review contract and evidence'}]
    run = ((work.get('sdd') or {}).get('native_reviews') or {}).get(getattr(args, 'layer', None) if isinstance(getattr(args, 'layer', None), str) else None) or {}
    fault = {'work': work['id'], 'input': run.get('input_sha256'),
             'layer': getattr(args, 'layer', None),
             'diagnostics': [{key: row.get(key) for key in ('code', 'field')} for row in diagnostics]}
    repair_id = hashlib.sha256(json.dumps(fault, sort_keys=True).encode()).hexdigest()[:24]
    count, history = 1, {}
    path = None
    try:
        path = safe_path(repo, work['id'], 'review-retry.json')
        previous = read_record(path) or {}
        history = previous.get('faults', {})
        count = history.get(repair_id, 0) + 1
    except (ValueError, OSError, RuntimeError):
        pass
    response = {'ok': False, 'error': str(exc), 'diagnostics': diagnostics, 'repair_id': repair_id,
                'retry_count': count, 'pause_retry': count >= 2,
                'retry_when': 'Correct the named prerequisite or judgment with evidence. Identical failed requests are paused; report cosmetics do not reset the fault count.',
                'next': ('Use `cao sdd review diagnose --layer <layer>` to inspect bound artifacts and hashes. For legacy output, `cao sdd review recover --layer <layer> --source <original-message>` requires the exact observed full message. Reuse findings; do not restart reviewers for metadata repair. ' + ('Dispatch route: draft, classify ALL layers, patch, then resolve.' if (work.get('sdd', {}).get('route_decision') or {}).get('build_route') == 'dispatch' else 'Oneshot route: draft, classify ALL layers, patch, then submit a resolved JSON report.'))}
    code = str(exc).split(':', 1)[0]
    if code.startswith('NATIVE_REVIEW_RESOLUTION_'):
        response['next'] = ('Inspect the named per-finding resolution field. Use `cao sdd review resolution-draft --layer <layer>` and preserve its existing report; fill original content hashes, actual changed-file references and current verification assertions/results. Inspect `cao lifecycle executions` for reusable current native checks; run only missing checks. Refresh code_snapshot after edits, then resolve (dispatch) or submit (oneshot). Do not restart reviewers for resolution metadata.')
    elif code == 'NATIVE_REVIEW_ALL_OUTPUTS_REQUIRED':
        response['next'] = 'Wait for every bound reviewer final output. Run `cao sdd review resume` to collect existing evidence, then classify all layers before patches; do not start another round.'
    elif code in {'NATIVE_REVIEW_PROMPT_PACKAGE_REQUIRED', 'NATIVE_REVIEW_SYNCHRONOUS_REQUIRED', 'NATIVE_REVIEW_HOST_ARGUMENT_INVALID', 'NATIVE_REVIEW_CONTEXT_PATH_FORBIDDEN', 'NATIVE_REVIEW_CONTEXT_TOOL_UNSUPPORTED'}:
        response['next'] = 'Use the current prepared prompt_file verbatim in a context-free reviewer task. Set async:false and await all layers. Use scoped read tools and only the inputs allowed by that layer; ask the coordinator for checks requiring other tools. Preserve current tickets.'
    elif code == 'NATIVE_REVIEW_DRAFT_EXISTS':
        response['next'] = 'Run `cao sdd review resume` and complete the existing report.json judgments; do not overwrite triage or rewrite original findings.'
    elif code == 'NATIVE_REVIEW_SOURCE_AMBIGUOUS':
        response['next'] = 'Inspect `cao sdd review diagnose --layer <layer>`, select the actual attested findings artifact, then run `cao sdd review draft --layer <layer> --source <attested-path>`; do not reconstruct findings.'
    if path:
        history[repair_id] = count
        try:
            write_record(path, {'schema_version': 1, 'input_sha256': run.get('input_sha256'), 'layer': getattr(args, 'layer', None), 'repair_id': repair_id, 'count': count,
                               'faults': dict(list(history.items())[-128:]),
                               'request_id': request_identity(repo, work, args), 'response': response}, immutable=False)
        except (ValueError, OSError, RuntimeError):
            pass
    return response


def read_report(path):
    """Describe format failures without asking the model to rewrite native findings."""
    try:
        report = json.loads(path.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        invalid('NATIVE_REVIEW_REPORT_JSON_REQUIRED', '$',
                'triage JSON; preserve native findings and run review draft --source RAW',
                {'line': exc.lineno, 'column': exc.colno})
    if not isinstance(report, dict):
        invalid('NATIVE_REVIEW_REPORT_INVALID', '$', 'triage object, not the native findings array', type(report).__name__)
    return report
