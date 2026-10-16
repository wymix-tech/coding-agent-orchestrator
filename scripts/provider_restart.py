"""Persist provider resource reload boundaries independently of Work state."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.state_store import atomic_json, repo_transaction

STATE = Path(".orchestrator/provider-restarts.json")


def records(repo: Path) -> list[dict]:
    path = repo / STATE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def require_restart(repo: Path, provider: str, hosts: list[str]) -> None:
    with repo_transaction(repo):
        pending = records(repo)
        for host in hosts or [None]:
            if not any(item["provider"] == provider and item["host"] == host for item in pending):
                pending.append({"provider": provider, "host": host})
        atomic_json(repo / STATE, pending)


def observe_start(repo: Path, host: str, session_id: str, *, primary: bool, source: str = "") -> None:
    # A resumed conversation can retain its native ID. Resource activation,
    # rather than a fabricated/different conversation ID, acknowledges reload.
    if not primary or not session_id or host not in {"pi", "codex", "claude-code"}:
        return
    # Pi also emits session_start on /new, resume and fork without reloading
    # resources. Its native reason distinguishes those from actual activation.
    if host == "pi" and source not in {"startup", "reload"}:
        return
    if host != "pi" and source and source not in {"startup", "resume", "reload"}:
        return
    with repo_transaction(repo):
        pending = records(repo)
        remaining = [item for item in pending if item["host"] not in {None, host}]
        if remaining != pending:
            atomic_json(repo / STATE, remaining)


def restart_state(repo: Path, host: str | None = None) -> dict:
    pending = [item for item in records(repo) if not host or item["host"] in {None, host}]
    if not pending:
        return {"ready": True}
    hosts = sorted({item["host"] for item in pending if item["host"]})
    target = ", ".join(hosts) or "the coding-agent host"
    instruction = (
        f"HARD STOP: SDD provider resources were installed or updated. Tell the user to restart {target} "
        "in this project so it loads the new Skills and commands. End this turn and wait; "
        "do not continue intake, planning, implementation or verification in this session. "
        "If CAO hooks are missing, install the matching adapter with `cao host install --host <host>` "
        "before restarting. The host's next primary SessionStart acknowledges resource loading "
        "(Pi: startup/reload only); "
        "then run `cao bootstrap` to resume the existing Work or recover the original request before technology clarification. Repeating bootstrap, changing the "
        "session ID or starting a subagent does not acknowledge a restart."
    )
    return {"ready": False, "reason": "HOST_RESTART_REQUIRED", "next": instruction,
            "pending": pending, "restart_required": True}
