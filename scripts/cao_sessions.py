"""Host sessions, activity, and durable handoff history."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.cao_defaults import HANDOFF_DIR, SESSION_DIR, WORK_DIR
from scripts.cao_guidance import current_work_action
from scripts.cao_runtime import load_json, locked_repo, new_id, now, repo_root, save_json
from scripts.cao_work_store import _refresh_work, load_index, load_work, save_work


def session_path(repo: Path, session_id: str) -> Path:
    """Return the storage path for a CAO-internal Session ID."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id)
    return repo / SESSION_DIR / f"{safe}.json"


def stable_session_id(host: str, host_session_id: str) -> str:
    """Build a stable internal ID that cannot collide across different hosts."""
    key = f"{host.strip().lower()}\0{host_session_id}"
    return f"session-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


def handoff_history_dir(repo: Path, work_id: str) -> Path:
    """Return the immutable history directory for a Work Item's Handoffs."""
    safe_work_id = re.sub(r"[^A-Za-z0-9._-]+", "-", work_id).strip(".-") or "work"
    return repo / HANDOFF_DIR / safe_work_id


def handoff_record_path(repo: Path, work_id: str, handoff_id: str) -> Path:
    safe_handoff_id = re.sub(r"[^A-Za-z0-9._-]+", "-", handoff_id).strip(".-") or "handoff"
    return handoff_history_dir(repo, work_id) / f"{safe_handoff_id}.json"


def _handoff_time_key(handoff: dict[str, Any]) -> tuple[float, int, str]:
    """Normalize timestamps and per-work sequence numbers for stable sorting."""
    value = handoff.get("created_at", 0)
    timestamp = 0.0
    if isinstance(value, (int, float)):
        timestamp = float(value)
    else:
        try:
            timestamp = float(str(value))
        except (TypeError, ValueError):
            try:
                from datetime import datetime
                timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                timestamp = 0.0
    try:
        sequence = int(handoff.get("sequence", 0))
    except (TypeError, ValueError):
        sequence = 0
    return timestamp, sequence, str(handoff.get("id") or "")


def list_handoff_records(repo: Path, work_id: str | None = None) -> list[dict[str, Any]]:
    """Read immutable Handoff records from the per-Work-Item history layout."""
    root = repo / HANDOFF_DIR
    if not root.is_dir():
        return []

    if work_id:
        paths = list(handoff_history_dir(repo, work_id).glob("*.json"))
    else:
        paths = [path for history_dir in root.iterdir() if history_dir.is_dir()
                 for path in history_dir.glob("*.json")]

    records: dict[str, dict[str, Any]] = {}
    for path in paths:
        record = load_json(path, None)
        if not isinstance(record, dict):
            continue
        record_work_id = record.get("work_item")
        if not isinstance(record_work_id, str) or (work_id and record_work_id != work_id):
            continue
        handoff_id = record.get("id")
        if not isinstance(handoff_id, str) or not handoff_id:
            continue
        records[handoff_id] = record
    return sorted(records.values(), key=_handoff_time_key, reverse=True)


def latest_handoff(repo: Path, work_id: str) -> dict[str, Any] | None:
    """Return the newest immutable Handoff record for a Work Item."""
    records = list_handoff_records(repo, work_id)
    return records[0] if records else None


def find_handoff(repo: Path, handoff_id: str) -> dict[str, Any] | None:
    return next((item for item in list_handoff_records(repo) if item.get("id") == handoff_id), None)


def is_current_session_record(raw: Any) -> bool:
    """Return whether a record satisfies the current Session data contract.

    Session persistence intentionally has no legacy-record normalization or
    migration path. The host-native ID and Host together determine the stable
    internal ID, and every required field must be present in the current shape.
    """
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        return False
    required = {
        "id", "host", "host_session_id", "work_item_id", "kind", "parent_session_id",
        "status", "started_at", "last_event", "last_event_at", "updated_at",
        "ended_at", "end_reason", "last_handoff_id",
    }
    if not required.issubset(raw):
        return False
    host = raw.get("host")
    native_id = raw.get("host_session_id")
    if not isinstance(host, str) or not host or not isinstance(native_id, str) or not native_id:
        return False
    if raw.get("id") != stable_session_id(host, native_id):
        return False
    if raw.get("kind") not in {"primary", "subagent"} or raw.get("status") not in {"active", "ended"}:
        return False
    if raw.get("work_item_id") is not None and not isinstance(raw.get("work_item_id"), str):
        return False
    if raw.get("parent_session_id") is not None and not isinstance(raw.get("parent_session_id"), str):
        return False
    for key in ("started_at", "last_event_at", "updated_at"):
        if not isinstance(raw.get(key), int) or isinstance(raw.get(key), bool) or raw[key] < 0:
            return False
    if raw.get("ended_at") is not None and (not isinstance(raw.get("ended_at"), int) or isinstance(raw.get("ended_at"), bool) or raw["ended_at"] < 0):
        return False
    if not isinstance(raw.get("last_event"), str) or not raw["last_event"]:
        return False
    if raw.get("end_reason") is not None and not isinstance(raw.get("end_reason"), str):
        return False
    if raw.get("last_handoff_id") is not None and not isinstance(raw.get("last_handoff_id"), str):
        return False
    return True


def _session_files(repo: Path) -> list[Path]:
    return sorted((repo / SESSION_DIR).glob("*.json")) if (repo / SESSION_DIR).exists() else []


def _read_current_session(path: Path) -> dict[str, Any] | None:
    raw = load_json(path, None)
    if not is_current_session_record(raw):
        return None
    if path.name != f"{raw['id']}.json":
        return None
    return raw


def load_session(repo: Path, session_ref: str | None, host: str | None = None) -> dict[str, Any] | None:
    """Find a current-schema Session by internal ID or host-native ID.

    Files from older Session layouts are ignored; this model deliberately does
    not migrate previous paths or fields.
    """
    if not session_ref:
        return None
    if host == "unknown":
        host = None
    direct = _read_current_session(session_path(repo, session_ref))
    if direct:
        if host and direct.get("host") not in {host, "unknown"}:
            return None
        return direct

    candidates = [
        session for path in _session_files(repo)
        if (session := _read_current_session(path)) is not None
        and (session.get("id") == session_ref or session.get("host_session_id") == session_ref)
        and (not host or session.get("host") in {host, "unknown"})
    ]
    if not candidates:
        return None
    if host:
        exact = [session for session in candidates if session.get("host") == host]
        if exact:
            candidates = exact
    if len(candidates) > 1 and not host:
        raise RuntimeError(f"Session reference {session_ref!r} is ambiguous across hosts; supply --host.")
    return candidates[0]


def save_session(repo: Path, session: dict[str, Any]) -> None:
    session["schema_version"] = 2
    session["updated_at"] = now()
    save_json(session_path(repo, session["id"]), session)


def session_activity(session: dict[str, Any], stale_after_minutes: int = 30, current_time: int | None = None) -> str:
    if session.get("status") == "ended":
        return "ended"
    current_time = now() if current_time is None else current_time
    try:
        last_event_at = int(session.get("last_event_at") or 0)
    except (TypeError, ValueError):
        last_event_at = 0
    if not last_event_at or current_time - last_event_at > max(1, stale_after_minutes) * 60:
        return "stale"
    return "recent"


def list_sessions(repo: Path) -> list[dict[str, Any]]:
    """List current-schema Session records without migrating older formats."""
    found: dict[str, dict[str, Any]] = {}
    for path in _session_files(repo):
        session = _read_current_session(path)
        if session is not None:
            found[session["id"]] = session
    return sorted(found.values(), key=lambda s: (s.get("last_event_at") or 0, s.get("id") or ""), reverse=True)


@locked_repo
def update_session_event(
    repo: Path,
    session_ref: str,
    host: str,
    event: str,
    work: dict[str, Any] | None = None,
    *,
    kind: str = "primary",
    parent_session_id: str | None = None,
) -> dict[str, Any]:
    # A delayed result reports the caller's earlier Work; it is not a reattach.
    event_work = None if event in {"post_tool", "file_changed", "session_end", "subagent_stop"} else work
    if work:
        _refresh_work(repo, work)
    session = load_session(repo, session_ref, host=host)
    if session is None:
        return register_session(repo, event_work, session_ref, host, event=event, kind=kind, parent_session_id=parent_session_id)
    if event_work and session.get('work_item_id') and session['work_item_id'] != event_work['id']:
        # A new prompt/tool event is not an explicit cross-Work attachment.
        event_work = None
    if session.get("host") == "unknown" and host and host != "unknown":
        # Re-register to re-key the stored file using the now-known Host identity.
        return register_session(
            repo,
            event_work,
            session_ref,
            host,
            event=event,
            kind=kind if kind else session.get("kind", "primary"),
            parent_session_id=parent_session_id if parent_session_id is not None else session.get("parent_session_id"),
        )
    if host and host != "unknown":
        session["host"] = host
    session["work_item_id"] = event_work.get("id") if event_work else session.get("work_item_id")
    if session.get("status") != "ended" and event not in {"session_end", "subagent_stop"}:
        session["status"] = "active"
    session["last_event"] = event
    session["last_event_at"] = now()
    if event in {"session_start", "subagent_start"} and session.get("status") == "ended":
        session["started_at"] = now()
        session["ended_at"] = None
        session["end_reason"] = None
    save_session(repo, session)
    if work and session.get("work_item_id") == work["id"]:
        work.setdefault("resume", {})["last_agent"] = host
        save_work(repo, work)
    return session


@locked_repo
def touch_session(repo: Path, session_ref: str, event: str, work: dict[str, Any] | None = None, host: str | None = None) -> dict[str, Any] | None:
    session = load_session(repo, session_ref, host=host)
    if not session:
        return None
    session["last_event"] = event
    session["last_event_at"] = now()
    if work:
        session["work_item_id"] = work.get("id")
    save_session(repo, session)
    return session


@locked_repo
def end_session(repo: Path, session_ref: str, host: str | None, reason: str, event: str = "session_end") -> dict[str, Any] | None:
    session = load_session(repo, session_ref, host=host)
    if not session:
        return None
    session["status"] = "ended"
    session["last_event"] = event
    session["last_event_at"] = now()
    session["ended_at"] = now()
    session["end_reason"] = reason
    save_session(repo, session)
    return session


def cmd_sessions_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    sessions = list_sessions(repo)
    if args.host:
        sessions = [s for s in sessions if s.get("host") == args.host]
    if args.work:
        sessions = [s for s in sessions if s.get("work_item_id") == args.work]
    if args.status:
        sessions = [s for s in sessions if s.get("status") == args.status]
    out = []
    for session in sessions:
        item = dict(session)
        item["activity"] = session_activity(session, args.stale_after_minutes)
        out.append(item)
    print(json.dumps({"ok": True, "sessions": out, "stale_after_minutes": args.stale_after_minutes}, ensure_ascii=False, indent=2))
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    session = load_session(repo, args.id, host=args.host)
    if not session:
        print(json.dumps({"ok": False, "error": "SESSION_NOT_FOUND", "id": args.id}, ensure_ascii=False, indent=2))
        return 2
    out = dict(session)
    out["activity"] = session_activity(session, args.stale_after_minutes)
    print(json.dumps({"ok": True, "session": out}, ensure_ascii=False, indent=2))
    return 0


@locked_repo
def register_session(
    repo: Path,
    work: dict[str, Any] | None,
    session_id: str,
    host: str,
    *,
    event: str = "session_start",
    kind: str | None = None,
    parent_session_id: str | None = None,
) -> dict[str, Any]:
    """Create or reattach a Session using the host-native ID as its lookup key."""
    if work:
        _refresh_work(repo, work)
    ts = now()
    existing = load_session(repo, session_id, host=host)
    previous_internal_id = existing.get("id") if existing else None
    effective_host = existing.get("host") if host == "unknown" and existing else host
    effective_host = effective_host or "unknown"
    # If an earlier adapter registered the Session before it knew the Host,
    # re-key it once the concrete Host arrives so the ID invariant stays true.
    if existing and existing.get("host") == "unknown" and effective_host != "unknown":
        internal_id = stable_session_id(effective_host, session_id)
    else:
        internal_id = existing["id"] if existing else stable_session_id(effective_host, session_id)
    if existing and existing.get("status") == "ended" and event in {"session_start", "subagent_start", "resume"}:
        started_at = ts
    else:
        started_at = (existing or {}).get("started_at", ts)
    session = {
        "schema_version": 2,
        "id": internal_id,
        "host": effective_host,
        "host_session_id": session_id,
        "work_item_id": work.get("id") if work else (existing or {}).get("work_item_id"),
        "kind": kind if kind in {"primary", "subagent"} else (existing or {}).get("kind", "primary"),
        "parent_session_id": parent_session_id if parent_session_id is not None else (existing or {}).get("parent_session_id"),
        "status": "active",
        "started_at": started_at,
        "last_event": event,
        "last_event_at": ts,
        "updated_at": ts,
        "ended_at": None,
        "end_reason": None,
        "last_handoff_id": (existing or {}).get("last_handoff_id"),
        "observed_model": (existing or {}).get("observed_model"),
        "observed_provider": (existing or {}).get("observed_provider"),
        "observed_tools": (existing or {}).get("observed_tools"),
        **{key: existing[key] for key in ('review_ticket', 'review_output_sha256', 'review_artifacts', 'review_message_artifact', 'review_binding_error', 'review_read_paths', 'review_trace_paths', 'native_session_file') if existing and key in existing},
    }
    save_session(repo, session)
    if previous_internal_id and previous_internal_id != internal_id:
        previous_path = session_path(repo, previous_internal_id)
        if previous_path.exists():
            try:
                previous_path.unlink()
            except OSError:
                pass
    if work:
        work.setdefault("resume", {})["last_agent"] = effective_host
        save_work(repo, work)
    return session


# Fields that define what a Handoff snapshot *says*. Everything else (id,
# created_at, sequence) is record metadata, so comparing only these collapses
# the identical snapshots that every Stop event produces.
HANDOFF_IDENTITY_FIELDS = (
    "work_item", "session_id", "host", "reason", "step", "next_action",
    "path_assessment", "summary", "completed", "pending", "decisions",
    "blockers", "verification", "continuity",
)


def handoff_identity(handoff: dict[str, Any]) -> dict[str, Any]:
    return {key: handoff.get(key) for key in HANDOFF_IDENTITY_FIELDS}


@locked_repo
def create_handoff(repo: Path, work: dict[str, Any], session_id: str | None, host: str | None, reason: str = "session stop") -> dict[str, Any]:
    _refresh_work(repo, work)
    session = load_session(repo, session_id, host=host) if session_id else None
    existing_records = list_handoff_records(repo, work["id"])
    sequence = max((int(item.get("sequence", 0) or 0) for item in existing_records), default=0) + 1
    current = current_work_action(repo, work)
    from scripts.coordination_items import active_pending
    pending = active_pending(work)
    if current.get('type') != 'none' and current.get('instruction') and current['instruction'] not in pending:
        pending.append(current['instruction'])
    from scripts.continuity_view import snapshot
    handoff = {
        "version": 2,
        "state_revision": work.get("state_revision"),
        "execution_generation": work.get("execution_generation", 0),
        "continuity": snapshot(repo, work),
        "sequence": sequence,
        "id": new_id("handoff"),
        "work_item": work["id"],
        "session_id": session.get("id") if session else None,
        "host_session_id": session.get("host_session_id") if session else session_id,
        "host": host if host and host != "unknown" else (session or {}).get("host"),
        "reason": reason,
        "created_at": now(),
        "step": work.get("execution", {}).get("step"),
        "next_action": current,
        "path_assessment": work.get("path_assessment"),
        "summary": work.get("resume", {}).get("summary"),
        "completed": work.get("resume", {}).get("completed", []),
        "pending": pending,
        "decisions": work.get("decisions", [])[-8:],
        "blockers": work.get("blockers", []),
        "verification": work.get("verification"),
    }
    # Each Handoff is an independent immutable record. There is no flat latest
    # projection; readers derive the latest record from this history directory.
    # Repeated identical snapshots are idempotent: Stop fires after every tool
    # call, so without this a single Work Item accumulated dozens of copies of
    # the same state and the history became unreadable.
    latest = max(existing_records, key=lambda item: int(item.get("sequence", 0) or 0), default=None)
    if latest is not None and handoff_identity(latest) == handoff_identity(handoff):
        if session:
            session["last_handoff_id"] = latest["id"]
            save_session(repo, session)
        return latest
    save_json(handoff_record_path(repo, work["id"], handoff["id"]), handoff)
    from scripts.runtime_store import queue_record
    queue_record(repo, work, "handoff.created", {"handoff_id": handoff["id"],
                 "session_id": handoff["session_id"], "reason": reason},
                 event_id="evt-" + handoff["id"])
    save_work(repo, work)
    if session:
        session["last_handoff_id"] = handoff["id"]
        save_session(repo, session)
    return handoff


def choose_resume_work(repo: Path, work_id: str | None) -> dict[str, Any] | None:
    if work_id:
        return load_work(repo, work_id)
    index = load_index(repo); active = index.get("active_work")
    if active:
        return load_work(repo, active)
    candidates = []
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        w = load_json(p, None)
        if w and w.get("status") in {"active", "suspended"}:
            candidates.append(w)
    return candidates[0] if len(candidates) == 1 else None
