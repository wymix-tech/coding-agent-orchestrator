"""Read diagnostics and rebuild disposable projections without granting gates."""
from __future__ import annotations

import base64
import heapq
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from scripts.runtime_execution import owner_status
from scripts.runtime_logs import redact_object
from scripts.runtime_store import (
    identifier, official_runs, read_record, run_path, run_records, safe_path, settings, work_events, write_record,
)

TERMINAL = {"passed", "failed", "blocked", "error", "stale", "superseded", "abandoned", "cancelled"}


def timestamp(value) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            return 0.0


def _key(record: dict) -> tuple:
    return (-timestamp(record.get("occurred_at", record.get("started_at"))),
            str(record.get("event_id", record.get("run_id", ""))))


def page(records, limit: int, cursor: str | None) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("RUNTIME_LIMIT_INVALID")
    after = None
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], (float, int)) or not isinstance(value[1], str):
                raise ValueError()
            after = tuple(value)
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("RUNTIME_CURSOR_INVALID") from None
    selected = heapq.nsmallest(limit + 1, (record for record in records
                                          if after is None or _key(record) > after), key=_key)
    more = len(selected) > limit
    selected = selected[:limit]
    token = base64.urlsafe_b64encode(json.dumps(_key(selected[-1])).encode()).decode() if more else None
    return {"items": selected, "next_cursor": token}


def events(repo: Path, wid: str):
    for event in work_events(repo, wid):
        yield {**event, "record_type": "lifecycle"}
    folder = safe_path(repo, wid, "observations")
    if folder.exists():
        with os.scandir(folder) as entries:
            for entry in entries:
                identifier(entry.name)
                if entry.name.endswith(".json"):
                    # The parent was validated once; read_record refuses linked facts.
                    value = read_record(Path(entry.path))
                    if value:
                        yield {**value, "record_type": "observation"}


def authorities(repo: Path, work: dict) -> dict:
    result = official_runs(work)
    # Finished events preserve old acceptance results, including replaced Skills.
    for event in work_events(repo, work["id"]):
        if event.get("event") not in {"verification.finished", "sdd.acceptance.finished"}:
            continue
        payload = event.get("payload") or event
        rid = event.get("run_id") or payload.get("run_id")
        if rid and payload.get("status") in TERMINAL:
            result[rid] = payload
    return result


def inspect_run(repo: Path, work: dict, rid: str, official: dict | None = None) -> dict:
    wid = work["id"]
    start = read_record(run_path(repo, wid, rid, "start.json"))
    if start is None:
        raise ValueError("RUNTIME_RUN_NOT_FOUND")
    receipt = read_record(run_path(repo, wid, rid, "receipt.json"))
    projection_error = None
    try:
        projection = read_record(run_path(repo, wid, rid, "result.json"))
    except (ValueError, OSError) as exc:
        projection, projection_error = None, type(exc).__name__
    accepted = (official if official is not None else authorities(repo, work)).get(rid) or {}
    terminal = accepted.get("status") in TERMINAL
    if start["kind"] == "verification":
        current = work.get("verification") or {}
    elif start["kind"] == "native_completion":
        rows = (work.get("sdd") or {}).get("completion_executions") or []
        current = rows[-1] if rows else {}
    elif start["kind"] == "sdd_acceptance":
        current = (((work.get("sdd") or {}).get("acceptance") or {}).get("run") or {})
    else:
        current = {}
    current_id = current.get("run_id") or current.get("id")
    superseded = not terminal and bool(current_id) and (current_id != rid or (
        start.get("generation") is not None and start["generation"] != work.get("execution_generation")))
    owner = owner_status(repo, wid, rid)
    if start["kind"].startswith("provider_") and receipt:
        recovery = "complete"  # Execution fact only; no Work admission is granted.
    elif terminal:
        recovery = "complete" if projection else "projection_missing"
    elif owner == "running":
        recovery = "running"
    elif superseded:
        recovery = "superseded"
    elif receipt:
        recovery = "result_uncommitted"
    elif start.get("source") == "agent_declaration":
        recovery = "outcome_unknown"
    elif accepted.get("status") == "running":
        recovery = "outcome_unknown"
    else:
        recovery = "orphaned_start"
    # A projection alone is never evidence of admission.
    result = {"run_id": rid, "started_at": start["started_at"], "kind": start["kind"],
              "source": start["source"], "start": start, "receipt": receipt,
              "projection": projection, "projection_error": projection_error, "owner": owner, "recovery_status": recovery,
              "admission_status": accepted.get("status") if terminal else "superseded" if superseded else "pending",
              "current_run": current_id == rid,
              "process_status": "exited" if receipt else "running" if owner == "running" else "unknown",
              "next": "Inspect actual artifacts and processes; use the existing verify/acceptance command for a new run when appropriate. No command is rerun by recovery."}
    if start['kind'] == 'native_completion' and not terminal:
        result['next'] = 'Inspect actual output and effects with cao runtime show --run ' + rid + '; wait for a live executor. For an interrupted unknown result, record inspection using cao runtime abandon --run ' + rid + ' --evidence <inspection-file> --reason <diagnosis> before a new lifecycle execute. Never replay automatically.'
    if receipt:
        statuses = receipt.get("commands") or []
        if any(item.get("timed_out") for item in statuses):
            result["process_status"] = "timed_out"
        elif any(item.get("process_status") == "launch_failed" for item in statuses):
            result["process_status"] = "launch_failed"
    return result


def runs(repo: Path, work: dict):
    official = authorities(repo, work)
    for folder in run_records(repo, work["id"]):
        record = inspect_run(repo, work, folder.name, official)
        yield {key: value for key, value in record.items() if key not in {"start", "receipt", "projection"}}


def log_tail(repo: Path, wid: str, rid: str, command: str, stream: str, tail: int) -> dict:
    if stream not in {"stdout", "stderr"} or not 1 <= tail <= 1000:
        raise ValueError("RUNTIME_LOG_ARGUMENT_INVALID")
    receipt = read_record(run_path(repo, wid, rid, "commands", command, "receipt.json"))
    if not receipt:
        raise ValueError("RUNTIME_COMMAND_RECEIPT_REQUIRED")
    pruned = read_record(run_path(repo, wid, rid, "pruned.json"))
    metadata = (receipt.get("logs") or {}).get(stream) or {}
    data = bytearray()
    # Read at most 1 MiB, seeking across retained segments from the end.
    cap = 1024 * 1024
    if not pruned:
        for segment in reversed(metadata.get("segments") or []):
            path = run_path(repo, wid, rid, "commands", command, segment["path"])
            with path.open("rb") as handle:
                handle.seek(0, 2)
                count = min(handle.tell(), cap - len(data))
                handle.seek(-count, 2)
                data[:0] = handle.read(count)
            if len(data) >= cap or data.count(b"\n") > tail:
                break
    return {"run_id": rid, "command_id": command, "stream": stream,
            "text": "\n".join(data.decode("utf-8", errors="replace").splitlines()[-tail:]),
            "metadata": metadata, "pruned": bool(pruned), "read_limited": len(data) >= cap}


def rebuild(repo: Path, work: dict) -> dict:
    wid = work["id"]
    root = safe_path(repo, wid)
    refs = []
    secrets = settings(repo)["redact_values"]
    for record in authorities(repo, work).values():
        rid = record.get("run_id") or record.get("id")
        if rid and record.get("status") in TERMINAL:
            if read_record(run_path(repo, wid, rid, "start.json")):
                write_record(run_path(repo, wid, rid, "result.json"), redact_object({
                    **record, "schema_version": 1, "work_item_id": wid, "run_id": rid}, secrets), immutable=False)
            for key in ("evidence_hashes", "output_hashes"):
                for path, digest in (record.get(key) or {}).items():
                    refs.append({"run_id": rid, "path": path, "sha256": digest})
    for ref in (work.get("sdd") or {}).get("artifacts") or []:
        if isinstance(ref, dict):
            refs.append(ref)
    workflow = (work.get("sdd") or {}).get("workflow") or {}
    for path, digest in (workflow.get("artifact_hashes") or {}).items():
        refs.append({"source": "agent_declaration", "path": path, "sha256": digest})
    native = (work.get("sdd") or {}).get("native_state") or {}
    if native.get("source"):
        refs.append({"source": "provider_observation", "path": native["source"],
                     "sha256": native.get("evidence_sha256"), "plan_scope": (work.get("sdd") or {}).get("plan_scope")})
    observation_folder = safe_path(repo, wid, "observations")
    count, size = 0, 0
    if observation_folder.exists():
        for item in observation_folder.iterdir():
            path = safe_path(repo, wid, "observations", item.name)
            count += 1
            size += path.stat().st_size
    write_record(safe_path(repo, wid, "observation-quota.json"), {
        "schema_version": 1, "count": count, "bytes": size}, immutable=False)
    from scripts.state_store import atomic_json
    # Derived cache, built by streaming. Bounded-memory readers use authority.
    root.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=root, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for event in work_events(repo, wid):
                handle.write(json.dumps(redact_object(event, secrets), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, safe_path(repo, wid, "events.jsonl"))
    finally:
        Path(name).unlink(missing_ok=True)
    atomic_json(safe_path(repo, wid, "evidence", "index.json"), {
        "schema_version": 1, "work_item_id": wid, "derived": True, "references": redact_object(refs, secrets)})
    return {"rebuilt": ["events.jsonl", "evidence/index.json", "run result projections"]}
