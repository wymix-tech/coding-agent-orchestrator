#!/usr/bin/env python3
"""Deterministic router for short start/resume/continue intents.

START means: resolve current project state and return the next legal orchestration action.
It never invents a requirement, framework, architecture, or work item.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import bootstrap_guard
import execution_state_manager as sm
import requirement_discovery
import requirement_identity
import provider_incident
import cbm_provider

START_INTENTS = {
    "开始", "继续", "接着做", "接着", "开始吧", "继续吧",
    "start", "continue", "resume", "go", "proceed",
}


def _effective_provider_incident(repo: Path) -> dict[str, Any]:
    incident = provider_incident.inspect(repo)
    if incident.get("status") != "open":
        return {}
    try:
        health = cbm_provider.CBMProvider("codebase-memory-mcp").health()
        current_identity = provider_incident.identity(health.get("binary"), health.get("version"))
        return incident if provider_incident.is_open(repo, provider_identity=current_identity) else {}
    except Exception:
        return incident


def is_start_intent(text: str | None) -> bool:
    if not text:
        return False
    normalized = re.sub(r"[\s.!。！?？,，;；:：]+", " ", text.strip().lower()).strip()
    return normalized in START_INTENTS


def _configured_provider(repo: Path) -> str:
    try:
        import yaml  # type: ignore
        path = repo / ".orchestrator" / "config.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        sdd = raw.get("sdd") if isinstance(raw, dict) else None
        if not isinstance(sdd, dict) and isinstance(raw.get("orchestrator"), dict):
            sdd = raw["orchestrator"].get("sdd")
        if isinstance(sdd, dict) and sdd.get("provider"):
            return str(sdd["provider"])
    except Exception:
        pass
    return "generic"


def resolve(repo: Path, *, ensure_bootstrap: bool = True, host: str = "auto") -> dict[str, Any]:
    repo = repo.resolve()
    guard = bootstrap_guard.ensure(repo, host=host, activation=True) if ensure_bootstrap else bootstrap_guard.inspect(repo)
    if guard.get("status") == "ACTION_REQUIRED":
        return {
            "status": "ACTION_REQUIRED",
            "route": "RESOLVE_BOOTSTRAP",
            "next_action": guard.get("next_action") or "resolve_bootstrap_ambiguity",
            "bootstrap": guard,
        }
    if guard.get("status") == "UNBOOTSTRAPPED":
        return {"status": "UNBOOTSTRAPPED", "route": "BOOTSTRAP", "next_action": "safe_auto_init", "bootstrap": guard}

    state_path = repo / ".orchestrator" / "execution-state.yaml"
    completed_identity: tuple[str, str] | None = None
    if state_path.exists():
        try:
            state = sm._load(state_path)
            completion = sm.completion_status(state, repo)
            resume = sm.resume_summary(state, repo)
            if not completion.get("done"):
                incident = _effective_provider_incident(repo)
                if incident.get("status") == "open":
                    return {
                        "status": "PROVIDER_BLOCKED",
                        "route": "SURFACE_PROVIDER_BLOCKER",
                        "next_action": "repair_cbm_provider",
                        "provider_incident": incident,
                        "execution": resume,
                    }
                if resume.get("blocked"):
                    return {
                        "status": "BLOCKED", "route": "SURFACE_BLOCKER",
                        "next_action": resume.get("next_action") or "resolve_blocker",
                        "execution": resume, "blockers": resume.get("active_blockers") or [],
                    }
                return {
                    "status": "READY_FOR_WORK", "route": "RESUME_CURRENT_WORK",
                    "next_action": resume.get("next_action") or "resume_current_work", "execution": resume,
                }
            wi = state.get("work_item") or {}
            if wi.get("requirement_id") and wi.get("requirement_revision"):
                completed_identity = (str(wi["requirement_id"]), str(wi["requirement_revision"]))
            else:
                return {
                    "status": "ACTION_REQUIRED", "route": "MIGRATE_REQUIREMENT_HISTORY",
                    "next_action": "migrate_legacy_requirement_identity",
                    "error": "completed legacy work item has no stable requirement identity; do not guess whether a discovered requirement was already processed",
                    "work_item": wi,
                }
        except Exception as exc:
            return {"status": "ACTION_REQUIRED", "route": "REPAIR_STATE", "next_action": "repair_execution_state", "error": str(exc)}

    provider = _configured_provider(repo)
    reqs = requirement_discovery.discover(repo, provider)
    if reqs.get("status") == "ACTION_REQUIRED":
        return {
            "status": "ACTION_REQUIRED",
            "route": "LOCATE_REQUIREMENT",
            "next_action": reqs.get("next_action") or "locate_requirement_source",
            "requirements": reqs,
            "diagnostics": reqs.get("diagnostics") or [],
        }
    if reqs.get("candidates"):
        remaining = []
        pending_history: list[dict[str, Any]] = []
        for c in reqs["candidates"]:
            # The source content revision, when one exists, decides identity; the wording of a
            # resume request that merely pointed at it must never look like a new revision.
            identity = requirement_identity.candidate_identity(c, provider, repo=repo)
            already_done = requirement_identity.processed(repo, identity["requirement_id"], identity["revision_id"])
            if completed_identity == (identity["requirement_id"], identity["revision_id"]):
                already_done = True
            if already_done:
                continue
            # Completion history keyed by a pre-migration revision is not invisible: when the
            # mapping cannot be proven it must be surfaced, never quietly re-opened.
            history = requirement_identity.historical_mapping(
                repo, identity["requirement_id"], content_revision=identity["revision_id"])
            if history.get("pending_confirmation"):
                pending_history.append({**history, "candidate": {**c, **identity}})
                continue
            c = {**c, **identity}
            remaining.append(c)
        if pending_history and not remaining:
            first = pending_history[0]
            return {
                "status": "ACTION_REQUIRED",
                "route": "CONFIRM_REQUIREMENT_HISTORY",
                "next_action": "confirm_requirement_history_mapping",
                "error": ("completed work exists for a revision that could not be proven to be this "
                          "content; it is neither re-opened nor assumed done"),
                "pending_history": pending_history,
                "history_next_action": first.get("next_action"),
                "requirements": reqs,
            }
        reqs = {**reqs, "candidates": remaining, "count": len(remaining), "status": "NONE" if not remaining else ("ONE" if len(remaining) == 1 else "MULTIPLE")}
    if reqs["status"] == "NONE":
        return {
            "status": "READY_FOR_INTAKE",
            "route": "REQUEST_REQUIREMENT",
            "next_action": "ask_for_first_requirement",
            "requirements": reqs,
        }
    if reqs["status"] == "MULTIPLE":
        return {
            "status": "ACTION_REQUIRED",
            "route": "SELECT_REQUIREMENT",
            "next_action": "select_requirement",
            "requirements": reqs,
        }
    candidate = reqs["candidates"][0]
    incident = _effective_provider_incident(repo)
    if incident.get("status") == "open":
        return {
            "status": "PROVIDER_BLOCKED",
            "route": "SURFACE_PROVIDER_BLOCKER",
            "next_action": "repair_cbm_provider",
            "provider_incident": incident,
            "candidate": candidate,
        }
    return {
        "status": "READY_FOR_INTAKE",
        "route": "AUTO_INTAKE_CANDIDATE",
        "next_action": "run_intake_for_discovered_requirement",
        "requirements": reqs,
        "candidate": candidate,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--host", default="auto")
    p.add_argument("--no-bootstrap", action="store_true")
    args = p.parse_args(argv)
    result = resolve(Path(args.repo), ensure_bootstrap=not args.no_bootstrap, host=args.host)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["status"] == "ACTION_REQUIRED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
