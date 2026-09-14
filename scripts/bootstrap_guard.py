#!/usr/bin/env python3
"""Self-bootstrap guard for Coding Agent Orchestrator.

The guard has one job: make first activation safe and idempotent.  It treats
`.orchestrator/config.yaml` as the bootstrap marker.  Missing configuration may
be created through Safe Auto bootstrap; malformed configuration and unresolved
project authority are surfaced as ACTION_REQUIRED instead of being overwritten.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

import project_bootstrap

CONFIG_REL = Path(".orchestrator/config.yaml")


def _load_config(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    if yaml is None:
        return None, "PyYAML is required to validate existing orchestrator configuration"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return None, f"cannot parse {CONFIG_REL.as_posix()}: {exc}"
    if not isinstance(doc, dict):
        return None, f"{CONFIG_REL.as_posix()} must contain a YAML object"
    orch = doc.get("orchestrator")
    if not isinstance(orch, dict):
        return None, f"{CONFIG_REL.as_posix()} is missing the orchestrator object"
    return doc, None


def inspect(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    path = repo / CONFIG_REL
    doc, error = _load_config(path)
    if not path.exists():
        return {
            "status": "UNBOOTSTRAPPED",
            "initialized": False,
            "config": str(path),
            "next_action": "safe_auto_init",
        }
    if error:
        return {
            "status": "ACTION_REQUIRED",
            "initialized": False,
            "config": str(path),
            "error": "INVALID_ORCHESTRATOR_CONFIG",
            "detail": error,
            "next_action": "repair_config_manually",
        }
    assert doc is not None
    orch = doc.get("orchestrator") or {}
    bootstrap = orch.get("bootstrap") if isinstance(orch, dict) else {}
    unresolved = (bootstrap or {}).get("unresolved") if isinstance(bootstrap, dict) else []
    if unresolved:
        return {
            "status": "ACTION_REQUIRED",
            "initialized": True,
            "config": str(path),
            "unresolved": unresolved,
            "next_action": "resolve_init_ambiguity",
        }
    return {
        "status": "BOOTSTRAPPED",
        "initialized": True,
        "config": str(path),
        "next_action": "resume_or_intake",
    }


def ensure(repo: Path, *, host: str = "auto", activation: bool = True) -> dict[str, Any]:
    """Ensure project bootstrap exactly once when safe.

    Existing valid config is never regenerated. Existing malformed config is never
    overwritten. Missing config is initialized using project_bootstrap Safe Auto.
    """
    repo = repo.resolve()
    before = inspect(repo)
    safe_host = host if host in {"auto", "claude-code", "codex", "pi", "none"} else "auto"
    if before["status"] != "UNBOOTSTRAPPED":
        if before["status"] == "BOOTSTRAPPED" and safe_host != "none":
            host_reconcile = project_bootstrap.ensure_selected_host(repo, safe_host, activation=activation)
            return {**before, "performed": False, "host_reconcile": host_reconcile}
        return {**before, "performed": False}

    result = project_bootstrap.initialize(repo, host=safe_host, activation=activation)
    after = inspect(repo)
    return {
        **after,
        "performed": True,
        "bootstrap_result": result,
        "status": result.get("status") if result.get("status") == "ACTION_REQUIRED" else after.get("status"),
        "next_action": result.get("next_action") or after.get("next_action"),
    }
