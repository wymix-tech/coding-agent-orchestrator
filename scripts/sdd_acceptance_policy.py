"""Pure contracts for independent SDD final acceptance, not native execution proof."""
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath

from scripts.completion_policy import normalize_timeout

DECLARATION_INVALID = "SDD_ACCEPTANCE_DECLARATION_INVALID"
RESULT_INVALID = "SDD_ACCEPTANCE_RESULT_INVALID"


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
        raise ValueError(RESULT_INVALID)
    if any(raw.get(key) != value for key, value in
           (("run_id", run_id), ("input_sha256", input_sha256), ("provider", provider))):
        raise ValueError("SDD_ACCEPTANCE_RESULT_IDENTITY")
    items = raw.get("items")
    if not isinstance(items, list):
        raise ValueError(RESULT_INVALID)
    expected = [item["id"] for item in normalize_declaration(declaration)["criteria"]]
    results = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(RESULT_INVALID)
        identity = _text(item.get("id"), RESULT_INVALID)
        if identity in results or identity not in expected:
            raise ValueError("SDD_ACCEPTANCE_RESULT_COVERAGE")
        status = _text(item.get("status"), RESULT_INVALID)
        evidence = item.get("evidence")
        if status not in {"passed", "failed", "blocked"} or not isinstance(evidence, list) or not evidence:
            raise ValueError(RESULT_INVALID)
        refs = []
        for ref in evidence:
            if not isinstance(ref, dict):
                raise ValueError(RESULT_INVALID)
            refs.append({"path": _relative(ref.get("path"), RESULT_INVALID),
                         "reason": _text(ref.get("reason"), RESULT_INVALID)})
        results[identity] = {"id": identity, "status": status, "evidence": refs}
    # Exact coverage is authoritative; an overall claim cannot fill missing items.
    if set(results) != set(expected):
        raise ValueError("SDD_ACCEPTANCE_RESULT_COVERAGE")
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
