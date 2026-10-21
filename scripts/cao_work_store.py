"""Authoritative Work Item persistence, index recovery, and durable lifecycle events."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from scripts.cao_defaults import INDEX_REL, ORCH, WORK_DIR
from scripts.cao_runtime import load_json, locked_repo, now, rel, save_json, sha256_bytes
from scripts.openspec_archive import relocate as relocate_openspec_archive
from scripts.state_store import backup_once


def stable_work_id(repo: Path, requirement: Path) -> str:
    key = rel(requirement, repo)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(key).stem).strip("-").lower() or "work"
    return f"{slug}-{sha256_bytes(key.encode())[:8]}"


def work_path(repo: Path, work_id: str) -> Path:
    return repo / WORK_DIR / f"{work_id}.json"


def empty_index() -> dict[str, Any]:
    return {"version": 1, "active_work": None, "work_items": []}


def load_index(repo: Path) -> dict[str, Any]:
    return recover_index(repo)


@locked_repo
def recover_index(repo: Path) -> dict[str, Any]:
    """Rebuild a missing, corrupt or stale projection from authoritative Work Items."""
    works = [load_json(p, None) for p in sorted((repo / WORK_DIR).glob("*.json"))]
    works = [work for work in works if isinstance(work, dict)]
    for work in works:
        if work.get("pending_events"):
            drain_events(repo, work)
    active = [work["id"] for work in works if work.get("status") == "active"]
    projection = {"version": 1, "active_work": active[0] if len(active) == 1 else None,
                  "work_items": [work_summary(work) for work in works]}
    try:
        index = load_json(repo / INDEX_REL, None)
    except (json.JSONDecodeError, UnicodeError):
        index = None
    if not isinstance(index, dict) or any(index.get(key) != value for key, value in projection.items()):
        save_index(repo, projection)
        index = projection
    if len(active) > 1:
        raise RuntimeError("MULTIPLE_ACTIVE_WORK_ITEMS: " + ",".join(active))
    return index


def save_index(repo: Path, index: dict[str, Any]) -> None:
    index["updated_at"] = now()
    save_json(repo / INDEX_REL, index)


def _read_work(repo: Path, work_id: str) -> dict[str, Any] | None:
    p = work_path(repo, work_id)
    return load_json(p, None) if p.exists() else None


@locked_repo
def load_work(repo: Path, work_id: str) -> dict[str, Any] | None:
    work = _read_work(repo, work_id)
    if work and work.get("pending_events"):
        drain_events(repo, work)
    if work:
        for migration in relocate_openspec_archive(repo, work, now()):
            queue_event(work, {"event": "sdd.native_paths.relocated", "source": "openspec",
                               "work_item_id": work["id"], **migration})
        if work.get("pending_events"):
            save_work(repo, work)
    return work


def sync_index_for_work(repo: Path, work: dict[str, Any]) -> None:
    """Project committed authoritative records; never trust an old active pointer."""
    recover_index(repo)


def other_active_work(repo: Path, wid: str) -> list[str]:
    return [work["id"] for path in sorted((repo / WORK_DIR).glob("*.json"))
            if isinstance(work := load_json(path, None), dict)
            and work.get("status") == "active" and work["id"] != wid]


def _activation_denied(repo: Path, wid: str) -> bool:
    others = other_active_work(repo, wid)
    if others:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_ACTIVE",
                          "active_work_items": others,
                          "next": "Suspend the active work first, or explicitly switch with `cao start --requirement <path> --replace`."},
                         ensure_ascii=False, indent=2))
    return bool(others)


def _persist_work(repo: Path, work: dict[str, Any]) -> None:
    """Write authority while holding the transaction, without recursively draining."""
    current = _read_work(repo, work["id"])
    revision = int((current or {}).get("state_revision", 0))
    if int(work.get("state_revision", 0)) != revision:
        raise RuntimeError("STALE_WORK_REVISION")
    # Queue maintenance must still work for existing legacy conflicts; new activation cannot.
    if work.get("status") == "active" and (current or {}).get("status") != "active":
        if others := other_active_work(repo, work["id"]):
            raise RuntimeError("WORK_ITEM_ALREADY_ACTIVE: " + ",".join(others))
    if current and (current.get("integrity_version") != 2 or
                    ((current.get("acceptance") or {}).get("version") != 2
                     and (work.get("acceptance") or {}).get("version") == 2)):
        backup_once(repo, work_path(repo, work["id"]))
    saved = {"pending_events": [], **work, "integrity_version": 2,
             "state_revision": revision + 1, "updated_at": now()}
    save_json(work_path(repo, work["id"]), saved)
    # Advance the caller only after authority commits.
    work.update(saved)


@locked_repo
def save_work(repo: Path, work: dict[str, Any]) -> None:
    current = _read_work(repo, work["id"])
    generation = int((current or {}).get("execution_generation", 0))
    if current and any(current.get(key) != work.get(key) for key in ("status", "requirement", "acceptance")):
        generation += 1
    work["execution_generation"] = max(generation, int(work.get("execution_generation", 0)))
    _persist_work(repo, work)
    if work.get("pending_events"):
        drain_events(repo, work)
    sync_index_for_work(repo, work)


def queue_event(work: dict, event: dict) -> None:
    key = str(event.get("idempotency_key") or uuid.uuid4().hex)
    work.setdefault("pending_events", []).append({**event,
        "event_id": event.get("event_id") or "evt-" + key, "idempotency_key": key})


@locked_repo
def drain_events(repo: Path, work: dict) -> None:
    fresh = _read_work(repo, work["id"])
    if fresh is None:
        raise RuntimeError("WORK_ITEM_NOT_FOUND")
    pending = fresh.get("pending_events") or []
    for event in pending:
        _append_lifecycle_event(repo, event)
    if pending:
        fresh["pending_events"] = []
        _persist_work(repo, fresh)
    work.clear()
    work.update(fresh)


@locked_repo
def mutate_work(repo: Path, wid: str, update: Callable[[dict], None]) -> dict:
    work = load_work(repo, wid)
    if work is None:
        raise RuntimeError("WORK_ITEM_NOT_FOUND")
    update(work)
    save_work(repo, work)
    return work


def _refresh_work(repo: Path, work: dict[str, Any]) -> None:
    """Helpers modify fresh authority, not a caller's potentially stale snapshot."""
    fresh = load_work(repo, work["id"])
    if fresh is None:
        raise RuntimeError("WORK_ITEM_NOT_FOUND")
    work.clear()
    work.update(fresh)


@locked_repo
def _append_lifecycle_event(repo: Path, event: dict[str, Any]) -> bool:
    """Append under the repository lock; keyless calls are independent operations."""
    key = str(event.get("idempotency_key") or uuid.uuid4().hex)
    event = {**event, "idempotency_key": key, "event_id": event.get("event_id") or "evt-" + key}
    path = repo / ORCH / "lifecycle" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    duplicate = False
    needs_separator = False
    if path.exists():
        with path.open("r", encoding="utf-8") as existing:
            for line in existing:
                needs_separator = not line.endswith("\n")
                try:
                    if json.loads(line).get("idempotency_key") == key:
                        duplicate = True
                except (json.JSONDecodeError, AttributeError):
                    continue
    with path.open("a", encoding="utf-8") as stream:
        # Keep a truncated tail as history, but do not concatenate the recovered event.
        if needs_separator:
            stream.write("\n")
        if not duplicate:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        # A visible duplicate may come from a failed fsync; certify durability on retries too.
        os.fsync(stream.fileno())
    if os.name != "nt":
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    return not duplicate


def list_lifecycle_events(repo: Path, work_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    path = repo / ORCH / "lifecycle" / "events.jsonl"
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and (not work_id or event.get("work_item_id") == work_id):
                events.append(event)
    return events[-max(1, limit):][::-1]


def execution_phase(execution: dict) -> str | None:
    """Machine phase is independent of an Agent's free-form checkpoint label."""
    known = {"verification": "verification", "review": "review", "implementation": "implementation",
             "done": "done", "ready-for-dev": "ready", "reopened": "planning",
             "path-selected": "planning", "planning": "planning", "path-assessment": "planning",
             "native-sdd-workflow-selected": "planning", "native-sdd-workflow-executed": "implementation",
             "native-sdd-workflow-completed": "review"}
    return known.get(execution.get("step")) or execution.get("phase")


def work_summary(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": work["id"],
        "title": work["title"],
        "status": work["status"],
        "step": work.get("execution", {}).get("step"),
        "next_action": work.get("execution", {}).get("next_action"),
        "execution_profile": (work.get("path_assessment") or {}).get("profile"),
        "native_route": (work.get("sdd") or {}).get("route_decision"),
        "implementation_state": (work.get("sdd") or {}).get("implementation_state"),
        "sdd": work.get("sdd"),
        "blockers": work.get("blockers") or [],
        "decision_count": len(work.get("decisions") or []),
        "verification": (work.get("verification") or {}).get("status"),
        "legacy_evidence": ((work.get("acceptance") or {}).get("version") != 2
                            or bool(work.get("verification") and work["verification"].get("version") != 2)
                            or bool(work.get("path_assessment") and not work["path_assessment"].get("revision"))),
        "last_agent": work.get("resume", {}).get("last_agent"),
    }


def resolve_work(repo: Path, work_id: str | None = None) -> dict[str, Any] | None:
    index = load_index(repo)
    wid = work_id or index.get("active_work")
    return load_work(repo, wid) if wid else None
