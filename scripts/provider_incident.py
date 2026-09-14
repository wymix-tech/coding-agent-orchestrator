#!/usr/bin/env python3
"""Persistent provider incident/circuit state.

Provider failures are operational blockers, not missing domain evidence.  A repeated
identical provider failure is therefore surfaced once and then held open until the
provider identity changes or an operator explicitly resets/retries it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROVIDER_ID = "codebase-memory-mcp"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(repo: Path, provider: str = PROVIDER_ID) -> Path:
    return repo.resolve() / ".orchestrator" / "providers" / f"{provider}.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def identity(binary: Optional[str], version: Optional[str]) -> str:
    material = json.dumps({"binary": binary, "version": version}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def fingerprint(error_class: str, error: str, provider_identity: str) -> str:
    normalized = " ".join((error or "").strip().split())[-4000:]
    material = json.dumps({"class": error_class, "error": normalized, "provider_identity": provider_identity}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def inspect(repo: Path, provider: str = PROVIDER_ID) -> dict[str, Any]:
    return _load(_path(repo, provider))


def is_open(repo: Path, *, provider_identity: Optional[str] = None, provider: str = PROVIDER_ID) -> bool:
    incident = inspect(repo, provider)
    if incident.get("status") != "open":
        return False
    # A new binary/version is allowed one fresh attempt without manual reset.
    if provider_identity and incident.get("provider_identity") != provider_identity:
        return False
    return True


def record_failure(
    repo: Path,
    *,
    error_class: str,
    error: str,
    binary: Optional[str],
    version: Optional[str],
    diagnostics: Optional[dict[str, Any]] = None,
    provider: str = PROVIDER_ID,
) -> dict[str, Any]:
    path = _path(repo, provider)
    previous = _load(path)
    pid = identity(binary, version)
    fp = fingerprint(error_class, error, pid)
    same = previous.get("status") == "open" and previous.get("failure_fingerprint") == fp
    incident = {
        "schema_version": 1,
        "provider": provider,
        "status": "open",
        "error_class": error_class,
        "error": error,
        "binary": binary,
        "version": version,
        "provider_identity": pid,
        "failure_fingerprint": fp,
        "attempt_count": int(previous.get("attempt_count") or 0) + 1 if same else 1,
        "first_seen_at": previous.get("first_seen_at") if same else _now(),
        "last_seen_at": _now(),
        "automatic_retry": False,
        "next_action": "repair_cbm_provider",
        "diagnostics": diagnostics or {},
    }
    _write(path, incident)
    return incident


def record_success(repo: Path, *, binary: Optional[str], version: Optional[str], provider: str = PROVIDER_ID) -> dict[str, Any]:
    path = _path(repo, provider)
    previous = _load(path)
    doc = {
        "schema_version": 1,
        "provider": provider,
        "status": "healthy",
        "binary": binary,
        "version": version,
        "provider_identity": identity(binary, version),
        "last_success_at": _now(),
        "previous_failure_fingerprint": previous.get("failure_fingerprint"),
        "attempt_count": 0,
        "automatic_retry": True,
    }
    _write(path, doc)
    return doc


def reset(repo: Path, provider: str = PROVIDER_ID) -> dict[str, Any]:
    path = _path(repo, provider)
    previous = _load(path)
    doc = {
        "schema_version": 1,
        "provider": provider,
        "status": "reset",
        "reset_at": _now(),
        "previous_failure_fingerprint": previous.get("failure_fingerprint"),
        "attempt_count": 0,
        "automatic_retry": True,
    }
    _write(path, doc)
    return doc
