"""Task-scoped diagnostic facts. Work Items remain the sole gate authority."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from scripts.state_store import atomic_json, repo_transaction

VERSION = 1
MAX_RECORD_BYTES = 1024 * 1024
DEFAULTS = {
    "logs": {"mode": "bounded", "segment_bytes": 1024 * 1024,
             "max_bytes_per_stream": 8 * 1024 * 1024},
    "retention": {"max_bytes_per_work": 128 * 1024 * 1024, "closed_work_days": 30},
    "host_observation": "meaningful",
    "max_observations": 10000,
    "max_observation_bytes": 8 * 1024 * 1024,
    "redact_values": [],
}


def identifier(value: str) -> str:
    if (not isinstance(value, str) or len(value) > 200 or value in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", value)):
        raise ValueError("RUNTIME_ID_INVALID")
    return value


def queue_record(repo: Path, work: dict, event: str, payload: dict, *, source="cao_command",
                 event_id: str | None = None) -> str:
    from scripts.cao_work_store import queue_event
    from scripts.runtime_logs import redact_object
    import uuid
    identity = event_id or "evt-" + uuid.uuid4().hex
    queue_event(work, {"schema_version": VERSION, "event_id": identity,
                      "idempotency_key": identity, "event": event,
                      "work_item_id": work["id"], "source": source,
                      "occurred_at": time.time(), "recorded_at": time.time(),
                      "payload": redact_object(payload, settings(repo)["redact_values"]),
                      **({"run_id": payload["run_id"]} if payload.get("run_id") else {})})
    return identity


def safe_path(repo: Path, *parts: str) -> Path:
    repo = repo.resolve()
    path = repo / ".orchestrator" / "runtime"
    for part in parts:
        path /= identifier(part)
    for current in (path, *path.parents):
        if current == repo:
            break
        if current.is_symlink():
            raise ValueError("RUNTIME_SYMLINK")
    if not path.resolve().is_relative_to(repo):
        raise ValueError("RUNTIME_PATH_INVALID")
    return path


def settings(repo: Path) -> dict:
    from scripts.cao_runtime import config
    raw = config(repo).get("runtime", {})
    if not isinstance(raw, dict):
        raise ValueError("RUNTIME_CONFIG_INVALID")
    return validate_settings({'runtime': raw})


def validate_settings(cfg: dict) -> dict:
    raw = cfg.get('runtime', {})
    result = json.loads(json.dumps(DEFAULTS))
    for key, value in raw.items():
        if key in {"logs", "retention"}:
            if not isinstance(value, dict):
                raise ValueError("RUNTIME_CONFIG_INVALID")
            result[key].update(value)
        else:
            result[key] = value
    if result["logs"]["mode"] not in {"off", "bounded"}:
        raise ValueError("RUNTIME_CONFIG_INVALID")
    if result["host_observation"] not in {"off", "meaningful"}:
        raise ValueError("RUNTIME_CONFIG_INVALID")
    for group, keys in (("logs", ("segment_bytes", "max_bytes_per_stream")),
                        ("retention", ("max_bytes_per_work", "closed_work_days"))):
        for key in keys:
            value = result[group][key]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1024 ** 4:
                raise ValueError("RUNTIME_CONFIG_INVALID")
    for key in ("max_observations", "max_observation_bytes"):
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not 1 <= result[key] <= 1024 ** 4:
            raise ValueError("RUNTIME_CONFIG_INVALID")
    if result["logs"]["max_bytes_per_stream"] > min(result["logs"]["segment_bytes"], 16 * 1024 * 1024) * 1024:
        raise ValueError("RUNTIME_CONFIG_INVALID")
    secrets = result.get("redact_values")
    if (not isinstance(secrets, list) or any(not isinstance(value, str) or not value
            or "\n" in value or "\r" in value or len(value) > 16384 for value in secrets)):
        raise ValueError("RUNTIME_CONFIG_INVALID")
    return result


def read_record(path: Path) -> dict | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("RUNTIME_PATH_INVALID")
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > MAX_RECORD_BYTES:
            raise ValueError("RUNTIME_RECORD_TOO_LARGE")
        data = stream.read(min(size + 1, MAX_RECORD_BYTES + 1))
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("RUNTIME_RECORD_TOO_LARGE")
    result = json.loads(data)
    if not isinstance(result, dict) or result.get("schema_version", VERSION) != VERSION:
        raise ValueError("RUNTIME_VERSION_UNSUPPORTED")
    return result


def write_record(path: Path, record: dict, *, immutable: bool = True) -> None:
    if len(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True).encode()) + 1 > MAX_RECORD_BYTES:
        raise ValueError("RUNTIME_RECORD_TOO_LARGE")
    if path.is_symlink():
        raise ValueError("RUNTIME_SYMLINK")
    if immutable and path.exists():
        if read_record(path) != record:
            raise ValueError("RUNTIME_RECORD_CONFLICT")
        return
    atomic_json(path, record)
    if os.name != "nt":
        path.chmod(0o600)


def ensure_manifest(repo: Path, wid: str) -> Path:
    root = safe_path(repo, wid)
    path = root / "manifest.json"
    manifest = read_record(path)
    if manifest is None:
        write_record(path, {"schema_version": VERSION, "work_item_id": wid, "created_at": time.time()})
    elif manifest.get("work_item_id") != wid:
        raise ValueError("RUNTIME_WORK_MISMATCH")
    return root


def run_path(repo: Path, wid: str, run_id: str, *parts: str) -> Path:
    return safe_path(repo, wid, "runs", run_id, *parts)


def start_run(repo: Path, wid: str, run_id: str, kind: str, inputs: dict,
              *, source: str = "cao_executor", retry_of: str | None = None,
              generation: int | None = None, sequence: int | None = None) -> dict:
    with repo_transaction(repo):
        ensure_manifest(repo, wid)
        record = {"schema_version": VERSION, "work_item_id": wid, "run_id": run_id,
                  "kind": kind, "source": source, "started_at": time.time(),
                  "inputs": inputs, "retry_of": retry_of,
                  "generation": generation, "sequence": sequence}
        from scripts.runtime_logs import redact_object
        record["inputs"] = redact_object(inputs, settings(repo)["redact_values"])
        path = run_path(repo, wid, run_id, "start.json")
        previous = read_record(path)
        if previous:
            record["started_at"] = previous["started_at"]
        write_record(path, record)
        return record


def record_receipt(repo: Path, wid: str, run_id: str, receipt: dict,
                   command_id: str | None = None) -> None:
    parts = ("commands", command_id, "receipt.json") if command_id else ("receipt.json",)
    with repo_transaction(repo):
        write_record(run_path(repo, wid, run_id, *parts),
                     {"schema_version": VERSION, "run_id": run_id,
                      "work_item_id": wid, **receipt})


def project_result(repo: Path, wid: str, run_id: str, result: dict) -> dict:
    """Optional projection failure never rolls back a committed Work."""
    from scripts.runtime_logs import redact_object
    try:
        value = redact_object(result, settings(repo)["redact_values"])
        value.update(schema_version=VERSION, run_id=run_id, work_item_id=wid)
        for key in ("status", "source"):
            if key in result:
                value[key] = result[key]
        with repo_transaction(repo):
            write_record(run_path(repo, wid, run_id, "result.json"), value, immutable=False)
        return {"ok": True}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": "RUNTIME_PROJECTION_FAILED", "reason": str(exc)}


def observation(repo: Path, wid: str, event_id: str, record: dict) -> dict:
    from scripts.runtime_logs import redact_object
    with repo_transaction(repo):
        folder = safe_path(repo, wid, "observations")
        path = safe_path(repo, wid, "observations", event_id + ".json")
        # Quotas also bound lookup cost; do not create an unbounded telemetry spool.
        value = redact_object(record, settings(repo)["redact_values"])
        value.update(schema_version=VERSION, event_id=event_id, work_item_id=wid)
        for key in ("source", "event", "idempotency_key", "bound_work_id", "host", "session_id", "tool_call_id"):
            if key in record:
                value[key] = record[key]
        if path.exists():
            write_record(path, value)
            return value
        cfg = settings(repo)
        quota_path = safe_path(repo, wid, "observation-quota.json")
        quota = read_record(quota_path)
        if quota is None:
            count, size = 0, 0
            if folder.exists():
                for item in folder.iterdir():
                    checked = safe_path(repo, wid, "observations", item.name)
                    count += 1
                    size += checked.stat().st_size
        else:
            count, size = quota.get("count"), quota.get("bytes")
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (count, size)):
                raise ValueError("RUNTIME_QUOTA_INVALID: run runtime rebuild")
        payload_size = len(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode()) + 1
        if count >= cfg["max_observations"] or size + payload_size > cfg["max_observation_bytes"]:
            raise RuntimeError("RUNTIME_OBSERVATION_QUOTA")
        ensure_manifest(repo, wid)
        # Reserve first; a crash can waste quota but can never undercount growth.
        write_record(quota_path, {"schema_version": VERSION, "count": count + 1,
                                 "bytes": size + payload_size}, immutable=False)
        write_record(path, value)
        return value


def event_key(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def work_events(repo: Path, wid: str):
    """Stream authoritative lifecycle history; do not use a possibly stale cache."""
    path = repo / ".orchestrator/lifecycle/events.jsonl"
    if not path.is_file() or path.is_symlink():
        return
    with path.open(encoding="utf-8") as stream:
        while True:
            line = stream.readline(MAX_RECORD_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_RECORD_BYTES:
                while line and not line.endswith("\n"):
                    line = stream.readline(MAX_RECORD_BYTES + 1)
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(event, dict) and event.get("work_item_id") == wid:
                yield event


def run_records(repo: Path, wid: str):
    folder = safe_path(repo, wid, "runs")
    if folder.exists():
        for path in folder.iterdir():
            identifier(path.name)
            safe_path(repo, wid, "runs", path.name)
            if path.is_dir():
                yield path


def official_runs(work: dict) -> dict:
    records = list(work.get("verification_history") or []) + [work.get("verification") or {}]
    records.append(((work.get("sdd") or {}).get("acceptance") or {}).get("run") or {})
    records.extend((work.get("sdd") or {}).get("completion_executions") or [])
    return {str(item.get("run_id") or item.get("id")): item for item in records
            if item.get("run_id") or item.get("id")}



def record_denial(repo: Path, work: dict | None, action: str, result: dict) -> dict:
    """Best-effort CLI precondition history; never changes the rejection itself."""
    import uuid
    try:
        occurred = time.time()
        identity = 'deny-' + uuid.uuid4().hex
        observation(repo, work['id'] if work else '_unassigned', identity, {
            'event': 'gate.denied', 'source': 'cao_command', 'action': action,
            'occurred_at': occurred, 'recorded_at': occurred, 'idempotency_key': identity,
            'reason_code': str(result.get('error') or 'CAO_PRECONDITION_DENIED').split(':', 1)[0][:100],
            'reason': str(result.get('reason') or result.get('error') or '')[:2000],
            'next': str(result.get('next') or '')[:2000]})
    except (ValueError, OSError, RuntimeError):
        # A diagnostic failure cannot alter an already rejected operation.
        pass
    return result
