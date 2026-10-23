"""Pure contracts for independent SDD final acceptance, not native execution proof."""
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath

from scripts.completion_policy import normalize_timeout

DECLARATION_INVALID = "SDD_ACCEPTANCE_DECLARATION_INVALID"
RESULT_INVALID = "SDD_ACCEPTANCE_RESULT_INVALID"


class AcceptanceValidationError(ValueError):
    """Keep stable error codes while supplying an actionable JSON field path."""
    def __init__(self, code, field, expected):
        super().__init__(code)
        self.diagnostic = {"field": field, "expected": expected}


def digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(value, error: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(error)
    return value.strip()


def _relative(value, error: str) -> str:
    value = _text(value, error)
    if ("\\" in value or PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError(error)
    return value


def normalize_declaration(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(DECLARATION_INVALID)
    spec = _relative(raw.get("spec"), DECLARATION_INVALID)
    raw_sources, raw_outputs = raw.get("sources", []), raw.get("outputs", [])
    criteria, executor = raw.get("criteria"), raw.get("executor")
    if (not isinstance(raw_sources, list) or not isinstance(raw_outputs, list)
            or not isinstance(criteria, list) or not criteria or not isinstance(executor, dict)):
        raise ValueError(DECLARATION_INVALID)
    sources = {spec, *(_relative(path, DECLARATION_INVALID) for path in raw_sources)}
    normalized, ids = [], set()
    for item in criteria:
        if not isinstance(item, dict):
            raise ValueError(DECLARATION_INVALID)
        identity = _text(item.get("id"), DECLARATION_INVALID)
        source = _relative(item.get("source"), DECLARATION_INVALID)
        kind = _text(item.get("kind"), DECLARATION_INVALID)
        description = _text(item.get("description"), DECLARATION_INVALID)
        if identity in ids or kind not in {"requirement", "design"}:
            raise ValueError(DECLARATION_INVALID)
        ids.add(identity)
        sources.add(source)
        normalized.append({"id": identity, "source": source, "kind": kind, "description": description})
        if "covers" in item:
            covers = item["covers"]
            if not isinstance(covers, list) or not covers or any(not isinstance(value, str) or not value.startswith("src-") for value in covers):
                raise ValueError(DECLARATION_INVALID)
            normalized[-1]["covers"] = sorted(set(covers))
    if not any(item["kind"] == "requirement" for item in normalized):
        raise ValueError(DECLARATION_INVALID)
    mode = _text(executor.get("mode"), DECLARATION_INVALID)
    entrypoint = _text(executor.get("entrypoint"), DECLARATION_INVALID)
    if mode not in {"command", "skill"}:
        raise ValueError(DECLARATION_INVALID)
    try:
        timeout = normalize_timeout(executor.get("timeout_seconds", 300.0))
    except ValueError as exc:
        raise ValueError(DECLARATION_INVALID) from exc
    outputs = [_relative(path, DECLARATION_INVALID) for path in raw_outputs]
    if len(set(outputs)) != len(outputs) or sources.intersection(outputs):
        raise ValueError(DECLARATION_INVALID)
    return {"spec": spec, "sources": sorted(sources), "criteria": normalized,
            "executor": {"mode": mode, "entrypoint": entrypoint, "timeout_seconds": timeout},
            "outputs": sorted(outputs)}


def declaration_hash(declaration: dict) -> str:
    return digest(normalize_declaration(declaration))


def normalize_report(raw: dict, declaration: dict, run_id: str, input_sha256: str, provider: str) -> list[dict]:
    if not isinstance(raw, dict):
        raise AcceptanceValidationError(RESULT_INVALID, "$", "one report object")
    if any(raw.get(key) != value for key, value in
           (("run_id", run_id), ("input_sha256", input_sha256), ("provider", provider))):
        raise AcceptanceValidationError("SDD_ACCEPTANCE_RESULT_IDENTITY", "$.run_id/input_sha256/provider", "echo the current frozen request identity")
    items = raw.get("items")
    if not isinstance(items, list):
        raise AcceptanceValidationError(RESULT_INVALID, "$.items", "an array with exactly one result for each declared criterion")
    expected = [item["id"] for item in normalize_declaration(declaration)["criteria"]]
    results = {}
    for index, item in enumerate(items):
        field = f"$.items[{index}]"
        if not isinstance(item, dict):
            raise AcceptanceValidationError(RESULT_INVALID, field, "an object with id, status and evidence")
        try:
            identity = _text(item.get("id"), RESULT_INVALID)
        except ValueError as exc:
            raise AcceptanceValidationError(RESULT_INVALID, field + ".id", "a nonempty declared criterion ID") from exc
        if identity in results or identity not in expected:
            raise AcceptanceValidationError("SDD_ACCEPTANCE_RESULT_COVERAGE", field + ".id", "each declared criterion ID exactly once")
        status = item.get("status")
        evidence = item.get("evidence")
        if not isinstance(status, str) or status not in {"passed", "failed", "blocked"}:
            raise AcceptanceValidationError(RESULT_INVALID, field + ".status", "passed, failed or blocked")
        if not isinstance(evidence, list) or not evidence:
            raise AcceptanceValidationError(RESULT_INVALID, field + ".evidence", "a nonempty array: [{path: <repo-file>, reason: <what was checked>}]")
        refs = []
        for ref_index, ref in enumerate(evidence):
            ref_field = f"{field}.evidence[{ref_index}]"
            if not isinstance(ref, dict):
                raise AcceptanceValidationError(RESULT_INVALID, ref_field, "an object with path and reason")
            try:
                path = _relative(ref.get("path"), RESULT_INVALID)
            except ValueError as exc:
                raise AcceptanceValidationError(RESULT_INVALID, ref_field + ".path", "a repository-relative evidence file") from exc
            try:
                reason = _text(ref.get("reason"), RESULT_INVALID)
            except ValueError as exc:
                raise AcceptanceValidationError(RESULT_INVALID, ref_field + ".reason", "a nonempty description of the actual check") from exc
            refs.append({"path": path, "reason": reason})
        results[identity] = {"id": identity, "status": status, "evidence": refs}
    # Exact coverage is authoritative; an overall claim cannot fill missing items.
    if set(results) != set(expected):
        raise AcceptanceValidationError("SDD_ACCEPTANCE_RESULT_COVERAGE", "$.items", "exact criterion coverage; missing: " + ", ".join(sorted(set(expected) - set(results))))
    return [results[identity] for identity in expected]


def result_state(results: list[dict], exit_code: int) -> str:
    if (isinstance(exit_code, bool) or not isinstance(exit_code, int) or not isinstance(results, list)
            or any(not isinstance(item, dict) or not isinstance(item.get("status"), str)
                   or item["status"] not in {"passed", "failed", "blocked"} for item in results)):
        raise ValueError(RESULT_INVALID)
    if exit_code >= 3 or exit_code < 0:
        return "error"
    if exit_code == 2:
        return "blocked"
    if exit_code == 1 or any(item["status"] == "failed" for item in results):
        return "failed"
    if not results or any(item["status"] == "blocked" for item in results):
        return "blocked"
    return "passed"


def is_fresh(run: dict | None, inputs: dict, hashes: dict[str, str]) -> bool:
    return bool(run and run.get("status") == "passed"
                and run.get("input_sha256") == digest(inputs)
                and run.get("inputs") == inputs
                and {**run.get("evidence_hashes", {}), **run.get("output_hashes", {})} == hashes)
