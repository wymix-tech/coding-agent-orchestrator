"""Best-effort host observations with start-time Work binding, never execution proof."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from scripts.cao_tool_policy import _collect_values_for_keys, _event_key, _shell_segments, tool_is_mutating
from scripts.runtime_store import event_key, observation, read_record, safe_path, settings


def call_key(raw: dict, host: str, session: str) -> str | None:
    call = _event_key(raw, "tool_call_id", "toolCallId", "tool_use_id", "call_id")
    return "call-" + event_key(host, session, call) if call and session else None


def binding(repo: Path, raw: dict, host: str, session: str) -> dict | None:
    key = call_key(raw, host, session)
    return read_record(safe_path(repo, "_unassigned", "observations", key + ".json")) if key else None


def _put(repo: Path, wid: str, identity: str, record: dict) -> None:
    path = safe_path(repo, wid, "observations", identity + ".json")
    previous = read_record(path)
    occurred = previous.get("occurred_at") if previous else time.time()
    observation(repo, wid, identity, {**record, "occurred_at": occurred,
                                    "recorded_at": occurred, "idempotency_key": identity})


def _meaningful(raw: dict, repo=None) -> bool:
    tool = _event_key(raw, "tool_name", "toolName", "toolname", "tool", "name").lower()
    commands = _collect_values_for_keys(raw, {"command", "cmd"})
    if not commands and tool in {"bash", "shell"}:
        payload = raw.get("input") or raw.get("arguments")
        if isinstance(payload, str):
            commands = [payload]
    if commands:
        try:
            segments = [part for command in commands for part in _shell_segments(command)]
            # Control commands create their own lifecycle/run records. No recursion.
            from scripts.cao_tool_policy import cao_control_tokens
            if segments and all(cao_control_tokens(part, repo) for part in segments):
                return False
        except ValueError:
            pass
        if any(re.search(r"\b(test|pytest|unittest|build|verify|mvn|gradle|openspec|specify|bmad)\b", c) for c in commands):
            return True
    return tool_is_mutating(raw)


def observe(repo: Path, work: dict | None, raw: dict, host: str, session: str,
            event: str, result: dict) -> str | None:
    if event not in {"pre_tool", "post_tool"}:
        return None
    try:
        if settings(repo)["host_observation"] == "off":
            return None
        key = call_key(raw, host, session)
        route = binding(repo, raw, host, session) if key else None
        denied = result.get("decision") == "deny"
        failed = bool(raw.get("isError", raw.get("is_error", False)))
        if not route and not denied and not failed and not _meaningful(raw, repo):
            return None
        wid = route["bound_work_id"] if route else work["id"] if work and event == "pre_tool" else "_unassigned"
        tool = _event_key(raw, "tool_name", "toolName", "toolname", "tool", "name")[:100]
        if event == "pre_tool" and key and not route:
            # Routing metadata is bounded by the unassigned observation quota too.
            _put(repo, "_unassigned", key, {"event": "tool.bound", "source": "host_observation",
                 "bound_work_id": wid, "host": host, "session_id": session, "tool": tool})
        identity = (key + ("-pre" if event == "pre_tool" else "-post")) if key else "partial-" + uuid.uuid4().hex
        record = {"event": "gate.denied" if denied else "tool.started" if event == "pre_tool" else "tool.finished",
                  "source": "host_observation", "host": host, "session_id": session or None,
                  "tool_call_id": _event_key(raw, "tool_call_id", "toolCallId", "tool_use_id", "call_id") or None,
                  "tool": tool, "coverage": "correlated" if key and (event == "pre_tool" or route) else "partial",
                  "decision": result.get("decision"), "is_error": failed if event == "post_tool" else None}
        code = raw.get("exit_code", raw.get("exitCode"))
        if isinstance(code, int) and not isinstance(code, bool):
            record["exit_code"] = code
        if denied:
            match = re.match(r"(?:CAO:\s*)?([A-Z][A-Z0-9_]{2,})\b", result.get("reason", ""))
            record["reason_code"] = match.group(1) if match else "CAO_GATE_DENIED"
            record["reason"] = result.get("reason", "")[:2000]
        _put(repo, wid, identity, record)
        return None
    except (ValueError, OSError, RuntimeError):
        # Keep the original authorization decision and avoid publishing payloads.
        return "CAO_RUNTIME_OBSERVATION_FAILED: diagnostic recording unavailable; gate decision unchanged"
