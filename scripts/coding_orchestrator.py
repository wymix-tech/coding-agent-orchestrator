#!/usr/bin/env python3
"""Unified front controller for Coding Agent Orchestrator.

This CLI intentionally hides most internal scripts. Internal engines remain independently
invokable for debugging and integration tests, while normal project use goes through this
front controller.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import re
import uuid
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

import bootstrap_guard
import cbm_provider
import action_guard
import context_plane
import execution_state_manager as sm
import install_host_adapter
import host_runtime
import project_bootstrap
import project_activation
import project_discovery
import provider_incident
import semantic_intake_pipeline
import requirement_identity
import session_context
import skill_runtime
import start_router

ROOT = Path(__file__).resolve().parents[1]
VERSION = "6.5"
READINESS_KEYS = {"behavior_change", "sdd_ready", "acceptance_criteria_present",
                  "implementation_tasks_complete", "acceptance_satisfied"}


def _bool_arg(value: str) -> bool:
    v = value.strip().lower()
    if v in {"true", "1", "yes", "y"}:
        return True
    if v in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or yaml is None:
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _work_id(request: str) -> str:
    digest = hashlib.sha256(request.encode("utf-8")).hexdigest()[:8]
    return f"change-{digest}"


def _title(request: str) -> str:
    for line in request.splitlines():
        line = line.strip()
        if line:
            return line[:100]
    return "Untitled change"


def _selected_sdd(repo: Path, explicit: str | None = None) -> dict[str, Any] | None:
    if explicit:
        d = project_discovery.discover(repo)
        return project_bootstrap._resolve_sdd(d, explicit)
    cfg = _load_yaml(repo / ".orchestrator" / "config.yaml")
    sdd = cfg.get("sdd")
    if not isinstance(sdd, dict):
        sdd = (cfg.get("orchestrator") or {}).get("sdd") if isinstance(cfg.get("orchestrator"), dict) else None
    if isinstance(sdd, dict) and sdd.get("provider"):
        return sdd
    d = project_discovery.discover(repo)
    return (d.get("sdd") or {}).get("preferred")


def _ensure_state(repo: Path, request: str, work_id: str | None, title: str | None,
                  sdd: dict[str, Any] | None, *, requirement_id: str,
                  requirement_revision: str, requirement_source_ref: str | None = None,
                  native_work_item_id: str | None = None) -> tuple[Path, dict[str, Any], bool]:
    state_path = repo / ".orchestrator" / "execution-state.yaml"
    if state_path.exists():
        return state_path, sm._load(state_path), False
    provider = (sdd or {}).get("provider") or "generic"
    authority = (sdd or {}).get("authority_mode") or ("orchestrator" if provider == "generic" else "hybrid")
    native_ref = (sdd or {}).get("native_state_ref")
    state = sm.create_state(
        work_id or _work_id(request), title or _title(request), "TRIVIAL",
        provider=provider if provider in sm.PROVIDERS else "other",
        authority_mode=authority if authority in sm.AUTHORITY_MODES else "orchestrator",
        native_state_ref=native_ref,
        requirement_id=requirement_id,
        requirement_revision=requirement_revision,
        requirement_source_ref=requirement_source_ref,
        native_work_item_id=native_work_item_id,
    )
    sm.initialize(state_path, state, "coding-orchestrator")
    current = sm._load(state_path)
    try:
        current = sm.set_enforcement_enabled(state_path, True, "coding-orchestrator", current["revision"])
    except Exception:
        pass
    return state_path, current, True


def _archive_closed_state(repo: Path) -> dict[str, str] | None:
    state_path = repo / ".orchestrator" / "execution-state.yaml"
    if not state_path.exists():
        return None
    state = sm._load(state_path)
    if not sm.completion_status(state, repo).get("done"):
        return None
    wi = state.get("work_item") or {}
    if wi.get("requirement_id") and wi.get("requirement_revision"):
        requirement_identity.record(
            repo, requirement_id=str(wi["requirement_id"]), revision_id=str(wi["requirement_revision"]),
            work_item_id=str(wi.get("id") or "work-item"), provider=str((state.get("authority") or {}).get("provider") or "generic"),
            source_path=wi.get("requirement_source_ref"), native_id=wi.get("native_work_item_id"),
            status="completed", completed_at=state.get("updated_at"),
        )
    archive = repo / ".orchestrator" / "archive" / str(wi.get("id") or "work-item")
    archive.mkdir(parents=True, exist_ok=True)
    moved: dict[str, str] = {}
    for pth in [state_path, repo / ".orchestrator" / "execution-history.jsonl"]:
        if pth.exists():
            target = archive / pth.name
            if target.exists():
                target = archive / f"{pth.stem}-{state.get('revision',0)}{pth.suffix}"
            shutil.move(str(pth), str(target)); moved[pth.name] = str(target)
    return moved


def _human_init(result: dict[str, Any]) -> str:
    sdd = result.get("sdd") or {}
    hosts = result.get("hosts") or {}
    ci = result.get("code_intelligence") or {}
    policies = result.get("policies") or {}
    lines = [
        f"Coding Agent Orchestrator v{VERSION}",
        f"Status: {result.get('status')}",
        f"Repository: {result.get('repo')}",
        f"SDD: {sdd.get('provider') or 'unresolved'} ({sdd.get('authority_mode') or '-'})",
        f"Hosts installed: {', '.join(sorted((hosts.get('installed') or {}).keys())) or 'none'}",
        f"Current Agent host: {((hosts.get('current') or {}).get('host') or 'unknown')} ({(hosts.get('current') or {}).get('source') or '-'})",
        f"CBM: {'available' if ci.get('available') else 'not found'}",
        f"Spring layered policy: {'enabled' if policies.get('spring_layered_enabled') else 'not auto-enabled'}",
        f"Activation: {(result.get('activation') or {}).get('generic', {}).get('path') or 'disabled'}",
        f"Next: {result.get('next_action')}",
    ]
    runtime = result.get("runtime") or {}
    if runtime:
        lines.append(f"Skill runtime: {runtime.get('skill_root')}")
        lines.append(f"Run from repository root: {runtime.get('commands', {}).get('start')}")
    for w in result.get("warnings") or []:
        lines.append(f"WARN {w.get('code')}: {w.get('detail')}")
    for u in result.get("unresolved") or []:
        lines.append(f"ACTION {u.get('code')}: {u.get('detail')}")
    return "\n".join(lines)


def cmd_init(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    result = project_bootstrap.initialize(
        args.repo, sdd=args.sdd, host=args.host, architecture=args.architecture,
        force=args.force, ci=args.ci, activation=not args.no_activation,
    )
    # Publish the resolved runtime so every later step can invoke the CLI without searching.
    result["runtime"] = skill_runtime.describe(args.repo)
    code = 2 if args.ci and result.get("status") == "ACTION_REQUIRED" else 0
    return code, result, _human_init(result)


def cmd_where(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    """Report where this Skill package actually lives, so nothing has to guess."""
    result = skill_runtime.describe(args.repo)
    scope = ("Installed inside this repository; the paths above are repository-relative."
             if result["in_repo"] else
             "Installed outside this repository (user-level); the paths above are absolute and"
             " machine-specific, so do not write them into shared files such as AGENTS.md.")
    lines = [
        f"Skill root: {result['skill_root']}",
        f"Skill file: {result['skill_ref']}",
        f"CLI: {result['cli_ref']}",
        f"Invocation prefix: {result['prefix']}",
        "",
        "Ready to run (from the repository root):",
        f"  {result['commands']['start']}",
        f"  {result['commands']['doctor']}",
        f"  {result['commands']['status']}",
        "",
        scope,
        "Do not hardcode a Skill directory name; it is deployment-specific.",
    ]
    return 0, result, "\n".join(lines)


def cmd_discover(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    result = project_discovery.discover(args.repo)
    tech = result.get("technology") or {}
    arch = result.get("architecture") or {}
    sdd = result.get("sdd") or {}
    text = "\n".join([
        f"Discovery: {result.get('status')}",
        f"Languages: {', '.join(tech.get('languages') or []) or 'unknown'}",
        f"Frameworks: {', '.join(tech.get('frameworks') or []) or 'none detected'}",
        f"Architecture: {arch.get('style')} ({arch.get('confidence')})",
        f"SDD: {(sdd.get('preferred') or {}).get('provider') or sdd.get('status')}",
        f"Host markers/binaries: {', '.join((result.get('hosts') or {}).get('names') or []) or 'none'}",
        f"Current Agent host: {((result.get('hosts') or {}).get('current_agent') or {}).get('host') or 'unknown'} ({((result.get('hosts') or {}).get('current_agent') or {}).get('source') or '-'})",
        f"CBM: {'available' if (result.get('code_intelligence') or {}).get('available') else 'not found'}",
    ])
    return (2 if result.get("status") == "ACTION_REQUIRED" else 0), result, text


def _doctor(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    discovery = project_discovery.discover(repo)
    checks: list[dict[str, Any]] = []
    def add(name: str, status: str, detail: str, required: bool = False) -> None:
        checks.append({"name": name, "status": status, "detail": detail, "required": required})

    add("git", "PASS" if (discovery.get("git") or {}).get("detected") else "WARN", "git repository detected" if (discovery.get("git") or {}).get("detected") else "not a git repository")
    config = repo / ".orchestrator" / "config.yaml"
    guard = bootstrap_guard.inspect(repo)
    add("bootstrap_guard", "PASS" if guard.get("status") == "BOOTSTRAPPED" else ("WARN" if guard.get("status") == "UNBOOTSTRAPPED" else "FAIL"), str(guard.get("status")), required=guard.get("status") == "ACTION_REQUIRED")
    add("orchestrator_config", "PASS" if config.exists() else "FAIL", str(config), required=True)
    policy = repo / ".orchestrator" / "policies" / "manifest.yaml"
    add("policy_manifest", "PASS" if policy.exists() else "FAIL", str(policy), required=True)
    cfg_for_activation = _load_yaml(config)
    orch_cfg = cfg_for_activation.get("orchestrator") if isinstance(cfg_for_activation.get("orchestrator"), dict) else {}
    activation_cfg = (orch_cfg or {}).get("activation") if isinstance(orch_cfg, dict) else {}
    activation_enabled = True if not isinstance(activation_cfg, dict) else activation_cfg.get("enabled", True)
    agents_path = repo / "AGENTS.md"
    if activation_enabled:
        add("activation:AGENTS", "PASS" if project_activation.has_managed_block(agents_path) else "FAIL", str(agents_path), required=True)
    runtime_host = (discovery.get("hosts") or {}).get("current_agent") or host_runtime.detect_current_host()
    runtime_name = runtime_host.get("host") or "claude-code"
    runtime_detail = f"{runtime_name} via {runtime_host.get('source') or 'unknown'}"
    if runtime_host.get("fallback"):
        runtime_detail += " (Claude Code fallback)"
    add("current_agent_host", "WARN" if runtime_host.get("fallback") else "PASS", runtime_detail)
    add(
        "current_host_adapter",
        "PASS" if install_host_adapter.is_installed(repo, runtime_name) else "WARN",
        f"{runtime_name}: " + ("installed" if install_host_adapter.is_installed(repo, runtime_name) else "not installed; Bootstrap Guard will reconcile it"),
    )
    raw_sdd = discovery.get("sdd") or {}
    cfg = _load_yaml(config)
    configured_sdd = cfg.get("sdd")
    if not isinstance(configured_sdd, dict):
        configured_sdd = (cfg.get("orchestrator") or {}).get("sdd") if isinstance(cfg.get("orchestrator"), dict) else None
    configured_provider = configured_sdd.get("provider") if isinstance(configured_sdd, dict) else None
    if raw_sdd.get("requires_authority_resolution") and not configured_provider:
        add("sdd_authority", "FAIL", "multiple authorities detected and no explicit project selection exists", required=True)
    elif raw_sdd.get("requires_authority_resolution") and configured_provider:
        add("sdd_authority", "PASS", f"repository ambiguity resolved by project config: {configured_provider}", required=True)
    else:
        add("sdd_authority", "PASS", str(raw_sdd.get("status", "unknown")), required=True)
    cbm = discovery.get("code_intelligence") or {}
    add("cbm", "PASS" if cbm.get("available") else "WARN", cbm.get("binary") or "codebase-memory-mcp not found")
    if cbm.get("available") and cbm.get("binary"):
        try:
            probe = cbm_provider.CBMProvider(str(cbm["binary"])).probe_cli()
            protocol = (probe.get("cli_protocol") or {}).get("mode") or "unknown"
            pstatus = probe.get("probe_status")
            detail = f"{protocol}; version={probe.get('version') or 'unknown'}"
            if probe.get("probe_error"):
                detail += f"; {probe['probe_error']}"
            add("cbm_cli_protocol", "PASS" if pstatus == "compatible" else "WARN", detail)
        except Exception as exc:
            add("cbm_cli_protocol", "WARN", str(exc))
    incident = provider_incident.inspect(repo)
    if incident.get("status") == "open":
        current_identity = provider_incident.identity(cbm.get("binary"), cbm.get("version"))
        if provider_incident.is_open(repo, provider_identity=current_identity):
            add(
                "cbm_provider_incident", "FAIL",
                f"{incident.get('error_class') or 'UNKNOWN'}: {incident.get('error') or 'provider failure'}; automatic retry disabled; next=repair_cbm_provider",
                required=True,
            )
        else:
            add("cbm_provider_incident", "WARN", "stale incident belongs to a different CBM binary/version; next semantic intake may attempt once")
    else:
        add("cbm_provider_incident", "PASS", incident.get("status") or "none")

    state_path = repo / ".orchestrator" / "execution-state.yaml"
    if state_path.exists():
        try:
            state = sm._load(state_path)
            add("execution_state", "PASS", f"revision={state.get('revision')} phase={state.get('phase')}")
        except Exception as exc:
            add("execution_state", "FAIL", str(exc), required=True)
    else:
        add("execution_state", "INFO", "no active work item; run intake to create one")

    manifest = repo / ".orchestrator" / "intake" / "context-manifest.json"
    if manifest.exists():
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
            status = context_plane.validate_manifest(repo, doc)
            add("context_manifest", "PASS" if status.get("status") == "FRESH" else "WARN", status.get("status", "unknown"))
        except Exception as exc:
            add("context_manifest", "WARN", str(exc))
    else:
        add("context_manifest", "INFO", "not generated yet")

    host_files = {
        "claude-code": repo / ".claude" / "settings.json",
        "codex": repo / ".codex" / "hooks.json",
        "pi": repo / ".pi" / "extensions" / "coding-orchestrator.ts",
    }
    detected_hosts = set((discovery.get("hosts") or {}).get("names") or [])
    for host, path in host_files.items():
        if host in detected_hosts or path.exists():
            add(f"host:{host}", "PASS" if path.exists() else "WARN", str(path))

    if "claude-code" in detected_hosts or (repo / ".claude" / "settings.json").exists():
        claude_md = repo / "CLAUDE.md"
        add("activation:CLAUDE", "PASS" if project_activation.has_managed_block(claude_md) else "WARN", str(claude_md))

    hard_fail = [x for x in checks if x["status"] == "FAIL" and x.get("required")]
    warnings = [x for x in checks if x["status"] == "WARN"]
    return {"status": "ERROR" if hard_fail else ("WARN" if warnings else "HEALTHY"), "checks": checks, "discovery": discovery}


def cmd_doctor(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    result = _doctor(args.repo)
    lines = [f"Doctor: {result['status']}"]
    for c in result["checks"]:
        lines.append(f"{c['status']:<4} {c['name']}: {c['detail']}")
    return (2 if result["status"] == "ERROR" else 0), result, "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo = args.repo.resolve(); sp = repo / ".orchestrator" / "execution-state.yaml"
    if not sp.exists():
        guard = bootstrap_guard.inspect(repo)
        if guard.get("status") == "BOOTSTRAPPED":
            result = {"status": "READY_FOR_INTAKE", "active_work_item": None, "next_action": "run_intake", "bootstrap": guard}
            return 0, result, f"Status: {result['status']}\nNext: {result['next_action']}"
        result = {"status": guard.get("status"), "active_work_item": None, "next_action": guard.get("next_action"), "bootstrap": guard}
        return (2 if guard.get("status") == "ACTION_REQUIRED" else 0), result, f"Status: {result['status']}\nNext: {result['next_action']}"
    state = sm._load(sp); resume = sm.resume_summary(state, repo)
    try:
        bootstrap = session_context.build_bootstrap(repo, role=args.role)
    except Exception:
        bootstrap = None
    result = {"status": "ACTIVE", "execution": resume, "session": bootstrap}
    wi = resume.get("work_item") or {}; cursor=resume.get("cursor") or {}
    text = "\n".join([
        f"Work item: {wi.get('id')} - {wi.get('title')}",
        f"Flow: {resume.get('flow_profile')}",
        f"Phase: {resume.get('phase')} / {resume.get('status')}",
        f"Blocked: {resume.get('blocked')}",
        f"Task: {cursor.get('current_task_id') or '-'} {cursor.get('current_task_title') or ''}".rstrip(),
        f"Next: {resume.get('next_action')}",
        f"Revision: {resume.get('revision')}",
    ])
    # A blocked state must name the CLI that clears it; otherwise the only remaining
    # option looks like editing execution state, which is denied.
    for command in (resume.get("authorization") or {}).get("recovery") or []:
        text += f"\nRecovery: {command}"
    return 0, result, text


RESET_DISCARDS = (
    "phase returns to discovery",
    "readiness (sdd_ready, acceptance_criteria_present, implementation_tasks_complete, acceptance_satisfied) is cleared",
    "quality gates and verification obligations are cleared",
    "work item progress returns to 0/0",
)


def _resets_in_flight_work(state: dict[str, Any]) -> bool:
    """Whether revising the requirement would discard work already past planning."""
    progress = ((state.get("work_item") or {}).get("progress") or {})
    return state.get("phase") in action_guard.GUARDED_PHASES or int(progress.get("completed") or 0) > 0


def _reset_warning(state: dict[str, Any]) -> str:
    progress = ((state.get("work_item") or {}).get("progress") or {})
    done = int(progress.get("completed") or 0)
    total = int(progress.get("total") or 0)
    return (f"Revising now discards in-flight work (phase={state.get('phase')}, progress={done}/{total}): "
            + "; ".join(RESET_DISCARDS) + ".")


def _revision_changed_message(state: dict[str, Any], requirement_id: str) -> str:
    lines = [f"Requirement content changed for {requirement_id}."]
    if _resets_in_flight_work(state):
        lines.append(_reset_warning(state))
        lines.append("If the requirement did not really change (a resume/continue prompt rephrased it), "
                     "resume the existing work with `coding-orchestrator start` instead of re-running intake.")
        lines.append("Otherwise confirm the reset explicitly: `intake --revise-current --confirm-reset`.")
    else:
        lines.append("Re-run intake with --revise-current to invalidate derived evidence explicitly.")
    return " ".join(lines)


def _reset_confirmation_message(state: dict[str, Any]) -> str:
    return (_reset_warning(state)
            + " Confirm with `intake --revise-current --confirm-reset`, or resume the existing work with "
              "`coding-orchestrator start` when the requirement did not change.")


def _artifact_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return (cleaned or hashlib.sha256(value.encode()).hexdigest()[:12])[:80]


def cmd_intake(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo = args.repo.resolve()
    guard = bootstrap_guard.ensure(repo, host="auto", activation=True)
    if guard.get("status") == "ACTION_REQUIRED":
        return 2, {"status": "ACTION_REQUIRED", "error": "PROJECT_BOOTSTRAP_REQUIRES_DECISION", "bootstrap": guard}, "Project bootstrap requires an explicit authority/configuration decision before intake."
    request = args.request_file.read_text(encoding="utf-8") if args.request_file else (args.request or "")
    if not request.strip():
        return 2, {"status":"ERROR","error":"REQUEST_REQUIRED"}, "A request is required."
    archived = _archive_closed_state(repo)
    sdd = _selected_sdd(repo, args.sdd)
    if sdd is None:
        return 2, {"status":"ACTION_REQUIRED","error":"SDD_AUTHORITY_REQUIRED"}, "Multiple SDD authorities detected. Specify --sdd openspec|bmad|generic."
    provider = str(sdd.get("provider") or "generic")
    source_ref = None
    if args.sdd_ref:
        try: source_ref = args.sdd_ref.resolve().relative_to(repo).as_posix()
        except Exception: source_ref = str(args.sdd_ref)
    req_id = getattr(args, "requirement_id", None) or requirement_identity.stable_requirement_id(
        provider=provider, source_path=source_ref, native_id=getattr(args, "native_id", None),
        explicit_work_id=args.work_id, request_text=request,
    )
    req_rev = getattr(args, "requirement_revision", None) or requirement_identity.revision_id(request)
    source_rev = requirement_identity.source_revision_id(repo, source_ref)
    rephrased_request = False
    state_path = repo / ".orchestrator" / "execution-state.yaml"
    if state_path.exists():
        existing = sm._load(state_path)
        completion = sm.completion_status(existing, repo)
        if completion.get("done"):
            archived = _archive_closed_state(repo) or archived
        else:
            wi = existing.get("work_item") or {}
            existing_id = wi.get("requirement_id")
            existing_rev = wi.get("requirement_revision")
            if not existing_id:
                return 2, {
                    "status": "ACTION_REQUIRED", "error": "WORK_ITEM_IDENTITY_MIGRATION_REQUIRED",
                    "work_item": wi, "incoming_requirement_id": req_id,
                }, "Existing active work item predates stable requirement identity. Migrate/close it before intake."
            if str(existing_id) != str(req_id):
                return 2, {
                    "status": "ACTION_REQUIRED", "error": "ACTIVE_WORK_ITEM_CONFLICT",
                    "active_requirement_id": existing_id, "incoming_requirement_id": req_id,
                    "active_work_item": wi.get("id"),
                }, "A different work item is active. Close/cancel/switch it explicitly before intake."
            if str(existing_rev) != str(req_rev):
                if source_rev and requirement_identity.last_source_revision(repo, str(req_id)) == source_rev:
                    # Only the request wording changed; the authoritative source did not. A resume
                    # prompt must never masquerade as a requirement change and discard progress.
                    req_rev = str(existing_rev)
                    rephrased_request = True
                elif not getattr(args, "revise_current", False):
                    return 2, {
                        "status": "ACTION_REQUIRED", "error": "REQUIREMENT_REVISION_CHANGED",
                        "requirement_id": req_id, "active_revision": existing_rev, "incoming_revision": req_rev,
                        "resets_in_flight_work": _resets_in_flight_work(existing),
                        "next_action": "resume_current_work" if _resets_in_flight_work(existing) else "confirm_requirement_revision",
                    }, _revision_changed_message(existing, req_id)
                elif _resets_in_flight_work(existing) and not getattr(args, "confirm_reset", False):
                    return 2, {
                        "status": "ACTION_REQUIRED", "error": "REVISION_RESET_REQUIRES_CONFIRMATION",
                        "requirement_id": req_id, "active_revision": existing_rev, "incoming_revision": req_rev,
                        "next_action": "confirm_requirement_revision",
                    }, _reset_confirmation_message(existing)
                existing = sm.revise_work_item(
                    state_path, req_id, req_rev, args.actor, source_ref or "direct-request",
                    existing["revision"], requirement_source_ref=source_ref,
                    native_work_item_id=getattr(args, "native_id", None),
                )
    state_path, state, created = _ensure_state(
        repo, request, args.work_id, args.title, sdd,
        requirement_id=req_id, requirement_revision=req_rev,
        requirement_source_ref=source_ref, native_work_item_id=getattr(args, "native_id", None),
    )
    requirement_identity.record(
        repo, requirement_id=req_id, revision_id=req_rev,
        work_item_id=str((state.get("work_item") or {}).get("id")), provider=provider,
        source_path=source_ref, native_id=getattr(args, "native_id", None), status="active",
        source_revision=source_rev,
    )
    base_state_revision = state["revision"]
    run_id = uuid.uuid4().hex[:16]
    work_seg = _artifact_segment(str((state.get("work_item") or {}).get("id") or req_id))
    rev_seg = _artifact_segment(req_rev)
    outdir = repo / ".orchestrator" / "work-items" / work_seg / "revisions" / rev_seg / "runs" / run_id / "intake"
    argv = [
        "--repo", str(repo), "--request", request,
        "--state", str(state_path), "--sync-state", "--expected-state-revision", str(base_state_revision),
        "--output-dir", str(outdir), "--actor", args.actor,
        "--context-role", args.role, "--policy-stage", args.stage,
        "--sdd-provider", provider,
    ]
    if args.base_ref: argv += ["--base-ref", args.base_ref]
    if args.resolutions: argv += ["--resolutions", str(args.resolutions)]
    if args.cbm_fixture: argv += ["--cbm-fixture", str(args.cbm_fixture)]
    if args.degraded: argv += ["--allow-cbm-unavailable"]
    if getattr(args, "retry_cbm", False): argv += ["--retry-cbm"]
    if args.sdd_ref: argv += ["--sdd-ref", str(args.sdd_ref)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = semantic_intake_pipeline.main(argv)
    raw = buf.getvalue().strip()
    try:
        pipeline = json.loads(raw) if raw else {"status":"UNKNOWN"}
    except Exception:
        pipeline = {"status":"UNKNOWN", "raw_output": raw}
    run_meta = {
        "run_id": run_id, "work_item_id": (state.get("work_item") or {}).get("id"),
        "requirement_id": req_id, "requirement_revision": req_rev,
        "base_state_revision": base_state_revision, "exit_code": code,
        "pipeline_status": pipeline.get("status"),
    }
    outdir.parent.mkdir(parents=True, exist_ok=True)
    (outdir.parent / "run.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    if code != 4:
        requirement_identity.record(
            repo, requirement_id=req_id, revision_id=req_rev,
            work_item_id=str((state.get("work_item") or {}).get("id")), provider=provider,
            source_path=source_ref, native_id=getattr(args, "native_id", None), status="active",
            source_revision=source_rev,
        )
    bootstrap = session_context.build_bootstrap(repo, role=args.role, session_type="auto")
    bp_json, bp_md = session_context.persist_bootstrap(repo, bootstrap)
    result = {
        "status": pipeline.get("status"), "state_created": created, "archived_previous": archived,
        "requirement": {"id": req_id, "revision": req_rev, "source_ref": source_ref},
        "analysis_run": {"id": run_id, "dir": str(outdir), "base_state_revision": base_state_revision},
        "work_item": (sm._load(state_path).get("work_item") if state_path.exists() else None),
        "pipeline": pipeline,
        "request_rephrased_source_unchanged": rephrased_request,
        "bootstrap": {"json": str(bp_json), "markdown": str(bp_md), "session_type": bootstrap.get("session_type")},
    }
    flow = pipeline.get("flow_profile")
    text = f"Intake: {pipeline.get('status')}\nFlow: {flow or 'not classified'}\nWork item: {(result.get('work_item') or {}).get('id')}\nNext: {bootstrap.get('next_action')}"
    if rephrased_request:
        text += ("\nNOTE REQUEST_REPHRASED_SOURCE_UNCHANGED: the request wording changed but the requirement "
                 "source did not; the existing requirement revision was kept and no derived evidence was invalidated.")
    for warning in pipeline.get("warnings") or []:
        text += f"\nWARNING {warning.get('code')}: {warning.get('message')}\n-> {warning.get('next_action')}"
    if pipeline.get("status") == "NEEDS_EVIDENCE":
        template = (pipeline.get("artifacts") or {}).get("resolutions_template")
        if template:
            text += f"\nEvidence scaffold: {template}\nFill it and re-run with --resolutions <file>."
    return code, result, text



def cmd_start(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo = args.repo.resolve()
    routed = start_router.resolve(repo, ensure_bootstrap=True, host=args.host or "auto")
    route = routed.get("route")

    if route == "RESUME_CURRENT_WORK":
        execution = routed.get("execution") or {}
        wi = execution.get("work_item") or {}
        cursor = execution.get("cursor") or {}
        text = "\n".join([
            "Start: READY_FOR_WORK",
            f"Work item: {wi.get('id')} - {wi.get('title')}",
            f"Phase: {execution.get('phase')} / {execution.get('status')}",
            f"Task: {cursor.get('current_task_id') or '-'} {cursor.get('current_task_title') or ''}".rstrip(),
            f"Next: {routed.get('next_action')}",
            "Resume: continue with the reported task; a rephrased request is not a requirement change, so do not re-run intake unless the requirement source changed.",
        ])
        return 0, routed, text

    if route == "SURFACE_PROVIDER_BLOCKER":
        incident = routed.get("provider_incident") or {}
        text = "\n".join([
            "Start: PROVIDER_BLOCKED",
            f"Provider: {incident.get('provider') or 'codebase-memory-mcp'}",
            f"Class: {incident.get('error_class') or 'UNKNOWN'}",
            f"Error: {incident.get('error') or 'provider incident is open'}",
            "Next: repair_cbm_provider (do not repeatedly rerun semantic intake).",
            "After repairing CBM, run `coding-orchestrator provider reset codebase-memory-mcp`, then start again.",
        ])
        return 2, routed, text

    if route == "SURFACE_BLOCKER":
        blockers = routed.get("blockers") or []
        lines = ["Start: BLOCKED", f"Next: {routed.get('next_action')}"]
        for b in blockers[:8]:
            if isinstance(b, dict):
                lines.append(f"- {b.get('id') or b.get('code') or 'blocker'}: {b.get('reason') or b.get('detail') or b.get('description') or b}")
            else:
                lines.append(f"- {b}")
        return 1, routed, "\n".join(lines)

    if route == "REQUEST_REQUIREMENT":
        text = (
            "Start: READY_FOR_INTAKE\n"
            "No actionable requirement was discovered from the selected SDD/project requirement sources.\n"
            "Next: provide the first project goal or feature requirement. Production-code mutation remains blocked."
        )
        return 0, routed, text

    if route == "SELECT_REQUIREMENT":
        lines = ["Start: ACTION_REQUIRED", "Multiple actionable requirements were discovered. Select one before intake:"]
        for idx, c in enumerate((routed.get("requirements") or {}).get("candidates") or [], 1):
            lines.append(f"{idx}. {c.get('title')} [{c.get('path')}]")
        return 2, routed, "\n".join(lines)

    if route == "AUTO_INTAKE_CANDIDATE":
        candidate = routed.get("candidate") or {}
        if args.no_auto_intake:
            text = f"Start: READY_FOR_INTAKE\nDiscovered: {candidate.get('title')} [{candidate.get('path')}]\nNext: run intake for this requirement."
            return 0, routed, text
        request = str(candidate.get("request_text") or "").strip()
        if not request:
            return 2, {**routed, "status": "ACTION_REQUIRED", "error": "EMPTY_DISCOVERED_REQUIREMENT"}, "Discovered requirement source is empty; explicit intake is required."
        intake_args = argparse.Namespace(
            repo=repo, request=request, request_file=None, work_id=None, title=candidate.get("title"),
            sdd=None, sdd_ref=(repo / candidate["path"]) if candidate.get("path") else None,
            base_ref=None, resolutions=None, role=args.role, stage="planning", actor="coding-orchestrator:start",
            degraded=args.degraded, cbm_fixture=args.cbm_fixture, retry_cbm=getattr(args, "retry_cbm", False), revise_current=False,
            requirement_id=candidate.get("requirement_id"), requirement_revision=candidate.get("revision_id"),
            native_id=candidate.get("native_id"),
        )
        code, intake_result, intake_text = cmd_intake(intake_args)
        result = {
            "status": intake_result.get("status"),
            "route": "AUTO_INTAKE",
            "discovered_requirement": candidate,
            "intake": intake_result,
        }
        return code, result, f"Start: AUTO_INTAKE\nSource: {candidate.get('path')}\n{intake_text}"

    if route in {"RESOLVE_BOOTSTRAP", "REPAIR_STATE"} or routed.get("status") == "ACTION_REQUIRED":
        return 2, routed, f"Start: ACTION_REQUIRED\nNext: {routed.get('next_action')}"

    return 0, routed, f"Start: {routed.get('status')}\nNext: {routed.get('next_action')}"

def cmd_resume(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo=args.repo.resolve()
    guard = bootstrap_guard.ensure(repo, host=args.host or "auto", activation=True)
    if guard.get("status") == "ACTION_REQUIRED":
        return 2, guard, "Project bootstrap requires an explicit authority/configuration decision before resume."
    doc=session_context.build_bootstrap(repo, role=args.role, session_type="auto", host=args.host)
    session_context.persist_bootstrap(repo, doc)
    return 0, doc, session_context.render_bootstrap(doc)


def cmd_verify(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo=args.repo.resolve(); sp=repo/".orchestrator/execution-state.yaml"
    state = sm._load(sp) if sp.exists() else None
    facts = action_guard.collect_evidence(repo, state)
    completion = sm.completion_status(state, repo, facts)
    manifest_status = {"status": "FRESH" if facts.get("context_fresh") else "STALE",
                       "scope": "authoritative_sources", "stale_sources": facts.get("stale_context_sources", [])}
    result = {"authorization": completion["authorization"],
              "status": "READY_TO_CLOSE" if completion["governance_close_ready"] else "NOT_READY",
              "completion": completion, "context": manifest_status,
              "required_gate_failures": [r["message"] for r in completion["authorization"]["reasons"]
                                         if r["code"] in {"REQUIRED_GATE_NOT_PASSED", "GATE_EVIDENCE_STALE"}]}
    lines=[f"Verification readiness: {result['status']}"]
    for r in completion.get("close_guard_failures") or []: lines.append(f"- {r}")
    if manifest_status: lines.append(f"Context: {manifest_status.get('status')}")
    return (0 if result["status"]=="READY_TO_CLOSE" else 1),result,"\n".join(lines)


def cmd_check(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo = args.repo.resolve()
    path = repo / ".orchestrator/execution-state.yaml"
    state = sm._load(path) if path.exists() else None
    result = action_guard.authorize(repo, state, args.action, target_phase=args.phase,
                                    target_status=args.status, role=args.role, native_confirmed=args.native_confirmed,
                                    allow_governance_mutation=getattr(args, "allow_governance_mutation", False),
                                    governance_paths=getattr(args, "governance_paths", None))
    text = "Action: " + args.action + " / " + result["decision"]
    for reason in result["reasons"]:
        text += "\n" + reason["code"] + ": " + reason["message"]
    if result["next_action"]:
        text += "\nNext: " + result["next_action"]
    for command in result.get("recovery") or []:
        text += "\nRecovery: " + command
    return (0 if result["allowed"] else 1), result, text


def cmd_host_install(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo=args.repo.resolve(); hosts=["claude-code","codex","pi"] if args.host=="all" else [args.host]
    installed=project_bootstrap._install_hosts(repo,hosts)
    activation=project_activation.install(repo, hosts, apply=True)
    result={"status":"INSTALLED","hosts":installed,"activation":activation}
    return 0,result,"Installed: "+", ".join(installed)


def cmd_provider_status(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo = args.repo.resolve()
    if args.provider != "codebase-memory-mcp":
        return 2, {"status": "ERROR", "error": "UNSUPPORTED_PROVIDER", "provider": args.provider}, f"Unsupported provider: {args.provider}"
    provider = cbm_provider.CBMProvider("codebase-memory-mcp")
    health = provider.probe_cli()
    incident = provider_incident.inspect(repo)
    result = {"status": "OK" if health.get("probe_status") == "compatible" else "ATTENTION_REQUIRED", "health": health, "incident": incident}
    text = "\n".join([
        f"Provider: {args.provider}",
        f"Binary: {health.get('binary') or 'not found'}",
        f"Version: {health.get('version') or '-'}",
        f"CLI probe: {health.get('probe_status') or '-'}",
        f"Incident: {incident.get('status') or 'none'}",
        f"Next: {'repair/reset provider incident before semantic intake' if incident.get('status') == 'open' else 'provider is eligible for semantic intake'}",
    ])
    return (0 if result["status"] == "OK" else 1), result, text


def cmd_provider_reset(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    if args.provider != "codebase-memory-mcp":
        return 2, {"status": "ERROR", "error": "UNSUPPORTED_PROVIDER", "provider": args.provider}, f"Unsupported provider: {args.provider}"
    doc = provider_incident.reset(args.repo.resolve(), args.provider)
    return 0, doc, "CBM provider incident reset. The next semantic intake is allowed one fresh attempt."


def _state_path(repo: Path) -> Path:
    return repo / ".orchestrator" / "execution-state.yaml"


def _missing_state() -> tuple[int, dict[str, Any], str]:
    result = {"status": "ACTION_REQUIRED", "error": "STATE_MISSING", "next_action": "run_intake"}
    return 2, result, "No active execution state. Run `coding-orchestrator intake` before changing execution state."


def cmd_readiness(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    """Record a readiness fact through the orchestrator CLI, never by editing state files."""
    path = _state_path(args.repo)
    if not path.exists():
        return _missing_state()
    state = sm.set_readiness(path, args.key, args.value, args.actor, args.evidence_ref, args.expected_revision)
    next_action = (state.get("cursor") or {}).get("next_action")
    result = {"status": "READINESS_UPDATED", "key": args.key, "value": args.value,
              "readiness": state.get("readiness"), "revision": state.get("revision"), "next_action": next_action}
    return 0, result, (f"Readiness {args.key}={str(args.value).lower()}\n"
                       f"Revision: {state.get('revision')}\nNext: {next_action}")


def cmd_progress(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    path = _state_path(args.repo)
    if not path.exists():
        return _missing_state()
    state = sm.set_progress(path, args.completed, args.total, args.actor, args.evidence_ref,
                            args.expected_revision, args.native_confirmed)
    next_action = (state.get("cursor") or {}).get("next_action")
    result = {"status": "PROGRESS_UPDATED", "progress": ((state.get("work_item") or {}).get("progress") or {}),
              "revision": state.get("revision"), "next_action": next_action}
    return 0, result, (f"Progress: {args.completed}/{args.total}\n"
                       f"Revision: {state.get('revision')}\nNext: {next_action}")


def cmd_transition(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    path = _state_path(args.repo)
    if not path.exists():
        return _missing_state()
    try:
        state = sm.transition(path, args.phase, args.status, args.actor, args.reason,
                              args.expected_revision, args.native_confirmed, args.evidence_ref)
    except sm.TransitionDenied as exc:
        auth = getattr(exc, "authorization", {}) or {}
        reasons = "; ".join(r["message"] for r in auth.get("reasons") or [])
        result = {"status": "DENIED", "error": "TRANSITION_DENIED", "authorization": auth,
                  "next_action": auth.get("next_action"), "recovery": auth.get("recovery") or []}
        text = "\n".join([f"Transition denied: {args.phase}/{args.status}", reasons,
                          f"Next: {auth.get('next_action')}"])
        for command in result["recovery"]:
            text += f"\nRecovery: {command}"
        return 1, result, text
    next_action = (state.get("cursor") or {}).get("next_action")
    result = {"status": "TRANSITIONED", "phase": state.get("phase"), "status_detail": state.get("status"),
              "revision": state.get("revision"), "next_action": next_action}
    return 0, result, (f"Phase: {state.get('phase')} / {state.get('status')}\n"
                       f"Revision: {state.get('revision')}\nNext: {next_action}")


def build_parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="coding-orchestrator",description=__doc__)
    p.add_argument("--version",action="version",version=f"%(prog)s {VERSION}")
    p.add_argument("--json",action="store_true",help="emit machine-readable JSON")
    p.add_argument("--repo",type=Path,default=Path.cwd(),help="repository root (default: cwd)")
    sub=p.add_subparsers(dest="command",required=True)

    x=sub.add_parser("init",help="discover and initialize a repository once")
    x.add_argument("--sdd",choices=["auto","openspec","bmad","generic"],default="auto")
    x.add_argument("--host",choices=["auto","claude-code","codex","pi","all","none"],default="auto")
    x.add_argument("--architecture",choices=["auto","spring-layered","none"],default="auto")
    x.add_argument("--force",action="store_true",help="refresh generated starter config; project policy files are otherwise preserved")
    x.add_argument("--ci",action="store_true",help="fail non-interactively on unresolved authority")
    x.add_argument("--no-activation",action="store_true",help="do not install/update project activation stubs (AGENTS.md / CLAUDE.md)")
    x.set_defaults(func=cmd_init)

    x=sub.add_parser("discover",help="show conservative project/host/SDD discovery")
    x.set_defaults(func=cmd_discover)
    x=sub.add_parser("doctor",help="diagnose installation and current project health")
    x.set_defaults(func=cmd_doctor)
    x=sub.add_parser("where",help="report where this Skill and its front controller are installed")
    x.set_defaults(func=cmd_where)
    x=sub.add_parser("status",help="show active execution status")
    x.add_argument("--role",default="implementer",choices=sorted(session_context.ROLES)); x.set_defaults(func=cmd_status)

    x=sub.add_parser("intake",help="start or reassess a work item through the V6 semantic intake pipeline")
    x.add_argument("request",nargs="?")
    x.add_argument("--request-file",type=Path)
    x.add_argument("--work-id"); x.add_argument("--title")
    x.add_argument("--revise-current", action="store_true", help="explicitly accept a new revision of the active requirement and invalidate derived evidence")
    x.add_argument("--confirm-reset", action="store_true", help="with --revise-current: accept that in-flight implementation state (phase, readiness, gates, progress) is discarded")
    x.add_argument("--requirement-id", help=argparse.SUPPRESS)
    x.add_argument("--requirement-revision", help=argparse.SUPPRESS)
    x.add_argument("--native-id", help=argparse.SUPPRESS)
    x.add_argument("--sdd",choices=["openspec","bmad","generic"])
    x.add_argument("--sdd-ref",type=Path)
    x.add_argument("--base-ref")
    x.add_argument("--resolutions",type=Path)
    x.add_argument("--role",default="implementer",choices=sorted(session_context.ROLES))
    x.add_argument("--stage",default="implementation",choices=["bootstrap","planning","implementation","review","verification"])
    x.add_argument("--actor",default="coding-orchestrator")
    x.add_argument("--degraded",action="store_true",help="explicitly allow CBM unavailable; Decision Engine still fails closed on missing evidence")
    x.add_argument("--cbm-fixture",type=Path,help=argparse.SUPPRESS)
    x.add_argument("--retry-cbm",action="store_true",help="explicitly retry an open CBM provider incident once")
    x.set_defaults(func=cmd_intake)

    x=sub.add_parser("start",help="resolve short start/resume intent to the next legal orchestration action")
    x.add_argument("--role",default="implementer",choices=sorted(session_context.ROLES))
    x.add_argument("--host",default="auto")
    x.add_argument("--no-auto-intake",action="store_true",help="discover a unique requirement but do not execute intake")
    x.add_argument("--degraded",action="store_true",help="allow CBM unavailable during an automatically selected intake; evidence still fails closed")
    x.add_argument("--cbm-fixture",type=Path,help=argparse.SUPPRESS)
    x.add_argument("--retry-cbm",action="store_true",help="explicitly retry CBM if this start performs automatic intake")
    x.set_defaults(func=cmd_start)

    x=sub.add_parser("resume",help="rebuild and print the current Session Bootstrap")
    x.add_argument("--role",default="implementer",choices=sorted(session_context.ROLES)); x.add_argument("--host")
    x.set_defaults(func=cmd_resume)
    x=sub.add_parser("verify",help="check governance readiness; does not invent or bypass project test commands")
    x.set_defaults(func=cmd_verify)

    x=sub.add_parser("check", help="explain the shared execution/advance/close authorization")
    x.add_argument("--action", required=True, choices=sorted(action_guard.ACTIONS))
    x.add_argument("--native-confirmed", action="store_true", help="native adapter has confirmed the target phase/status")
    x.add_argument("--phase", choices=action_guard.PHASES)
    x.add_argument("--status", default="in_progress", choices=sorted(action_guard.STATUSES))
    x.add_argument("--role", default="implementer", choices=sorted(session_context.ROLES))
    x.add_argument("--allow-governance-mutation", action="store_true",
                   help="operator override for --action mutate_governance; never a default")
    x.add_argument("--governance-path", action="append", dest="governance_paths", metavar="PATH",
                   help="governance input being changed, for --action mutate_governance")
    x.set_defaults(func=cmd_check)

    x=sub.add_parser("readiness",help="record a readiness fact (the legal replacement for editing execution state)")
    x.add_argument("--key",required=True,choices=sorted(READINESS_KEYS))
    x.add_argument("--value",required=True,type=_bool_arg)
    x.add_argument("--evidence-ref",required=True,help="artifact proving the readiness fact (spec, plan, or test evidence)")
    x.add_argument("--actor",default="coding-orchestrator")
    x.add_argument("--expected-revision",type=int)
    x.set_defaults(func=cmd_readiness)

    x=sub.add_parser("progress",help="record implementation task progress")
    x.add_argument("--completed",type=int,required=True)
    x.add_argument("--total",type=int,required=True)
    x.add_argument("--evidence-ref",required=True)
    x.add_argument("--actor",default="coding-orchestrator")
    x.add_argument("--expected-revision",type=int)
    x.add_argument("--native-confirmed",action="store_true",help="native adapter has confirmed the progress")
    x.set_defaults(func=cmd_progress)

    x=sub.add_parser("transition",help="advance/close the active work item through the Action Guard")
    x.add_argument("--phase",required=True,choices=action_guard.PHASES)
    x.add_argument("--status",default="in_progress",choices=sorted(action_guard.STATUSES))
    x.add_argument("--reason",required=True)
    x.add_argument("--evidence-ref")
    x.add_argument("--actor",default="coding-orchestrator")
    x.add_argument("--expected-revision",type=int)
    x.add_argument("--native-confirmed",action="store_true",help="native adapter has confirmed the target phase/status")
    x.set_defaults(func=cmd_transition)

    pr=sub.add_parser("provider",help="inspect/reset semantic provider operational state"); prs=pr.add_subparsers(dest="provider_command",required=True)
    x=prs.add_parser("status"); x.add_argument("provider",choices=["codebase-memory-mcp"]); x.set_defaults(func=cmd_provider_status)
    x=prs.add_parser("reset"); x.add_argument("provider",choices=["codebase-memory-mcp"]); x.set_defaults(func=cmd_provider_reset)

    h=sub.add_parser("host",help="manage host adapters"); hs=h.add_subparsers(dest="host_command",required=True)
    x=hs.add_parser("install"); x.add_argument("host",choices=["claude-code","codex","pi","all"]); x.set_defaults(func=cmd_host_install)
    return p


def main(argv: list[str] | None = None) -> int:
    args=build_parser().parse_args(argv)
    args.repo=args.repo.resolve()
    try:
        code,result,text=args.func(args)
    except Exception as exc:
        result={"status":"ERROR","error":type(exc).__name__,"message":str(exc)}
        code=2; text=f"ERROR {type(exc).__name__}: {exc}"
    print(_json(result) if args.json else text)
    return code


if __name__=="__main__":
    raise SystemExit(main())
