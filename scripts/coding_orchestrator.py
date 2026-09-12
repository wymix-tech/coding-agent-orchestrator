#!/usr/bin/env python3
"""Unified front controller for Coding Agent Orchestrator v6.5.

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
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

import context_plane
import execution_state_manager as sm
import install_host_adapter
import project_bootstrap
import project_discovery
import semantic_intake_pipeline
import session_context

ROOT = Path(__file__).resolve().parents[1]
VERSION = "6.5"


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


def _ensure_state(repo: Path, request: str, work_id: str | None, title: str | None, sdd: dict[str, Any] | None) -> tuple[Path, dict[str, Any], bool]:
    state_path = repo / ".orchestrator" / "execution-state.yaml"
    if state_path.exists():
        return state_path, sm._load(state_path), False
    provider = (sdd or {}).get("provider") or "generic"
    authority = (sdd or {}).get("authority_mode") or ("orchestrator" if provider == "generic" else "hybrid")
    native_ref = (sdd or {}).get("native_state_ref")
    # TRIVIAL is a neutral provisional floor: code mutation remains blocked until the
    # Decision Engine returns CLASSIFIED, and later escalation can add obligations.
    state = sm.create_state(
        work_id or _work_id(request), title or _title(request), "TRIVIAL",
        provider=provider if provider in sm.PROVIDERS else "other",
        authority_mode=authority if authority in sm.AUTHORITY_MODES else "orchestrator",
        native_state_ref=native_ref,
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
    if not sm.completion_status(state).get("done"):
        return None
    archive = repo / ".orchestrator" / "archive" / str((state.get("work_item") or {}).get("id") or "work-item")
    archive.mkdir(parents=True, exist_ok=True)
    moved: dict[str, str] = {}
    for p in [state_path, repo / ".orchestrator" / "execution-history.jsonl"]:
        if p.exists():
            target = archive / p.name
            if target.exists():
                target = archive / f"{p.stem}-{state.get('revision',0)}{p.suffix}"
            shutil.move(str(p), str(target)); moved[p.name] = str(target)
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
        f"CBM: {'available' if ci.get('available') else 'not found'}",
        f"Spring layered policy: {'enabled' if policies.get('spring_layered_enabled') else 'not auto-enabled'}",
        f"Next: {result.get('next_action')}",
    ]
    for w in result.get("warnings") or []:
        lines.append(f"WARN {w.get('code')}: {w.get('detail')}")
    for u in result.get("unresolved") or []:
        lines.append(f"ACTION {u.get('code')}: {u.get('detail')}")
    return "\n".join(lines)


def cmd_init(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    result = project_bootstrap.initialize(
        args.repo, sdd=args.sdd, host=args.host, architecture=args.architecture,
        force=args.force, ci=args.ci,
    )
    code = 2 if args.ci and result.get("status") == "ACTION_REQUIRED" else 0
    return code, result, _human_init(result)


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
        f"Hosts: {', '.join((result.get('hosts') or {}).get('names') or []) or 'none'}",
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
    add("orchestrator_config", "PASS" if config.exists() else "FAIL", str(config), required=True)
    policy = repo / ".orchestrator" / "policies" / "manifest.yaml"
    add("policy_manifest", "PASS" if policy.exists() else "FAIL", str(policy), required=True)
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
        result = {"status": "READY_FOR_INTAKE" if (repo/".orchestrator/config.yaml").exists() else "UNINITIALIZED", "active_work_item": None, "next_action": "run_intake" if (repo/".orchestrator/config.yaml").exists() else "run_init"}
        return 0, result, f"Status: {result['status']}\nNext: {result['next_action']}"
    state = sm._load(sp); resume = sm.resume_summary(state)
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
    return 0, result, text


def cmd_intake(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo = args.repo.resolve()
    if not (repo / ".orchestrator" / "config.yaml").exists():
        return 2, {"status":"ERROR","error":"PROJECT_NOT_INITIALIZED","next_action":"coding-orchestrator init"}, "Project is not initialized. Run 'coding-orchestrator init' first."
    request = args.request_file.read_text(encoding="utf-8") if args.request_file else (args.request or "")
    if not request.strip():
        return 2, {"status":"ERROR","error":"REQUEST_REQUIRED"}, "A request is required."
    archived = _archive_closed_state(repo)
    sdd = _selected_sdd(repo, args.sdd)
    if sdd is None:
        return 2, {"status":"ACTION_REQUIRED","error":"SDD_AUTHORITY_REQUIRED"}, "Multiple SDD authorities detected. Specify --sdd openspec|bmad|generic."
    state_path, state, created = _ensure_state(repo, request, args.work_id, args.title, sdd)
    argv = [
        "--repo", str(repo), "--request", request,
        "--state", str(state_path), "--sync-state", "--actor", args.actor,
        "--context-role", args.role, "--policy-stage", args.stage,
        "--sdd-provider", str(sdd.get("provider") or "generic"),
    ]
    if args.base_ref: argv += ["--base-ref", args.base_ref]
    if args.resolutions: argv += ["--resolutions", str(args.resolutions)]
    if args.cbm_fixture: argv += ["--cbm-fixture", str(args.cbm_fixture)]
    if args.degraded: argv += ["--allow-cbm-unavailable"]
    if args.sdd_ref: argv += ["--sdd-ref", str(args.sdd_ref)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = semantic_intake_pipeline.main(argv)
    raw = buf.getvalue().strip()
    try:
        pipeline = json.loads(raw) if raw else {"status":"UNKNOWN"}
    except Exception:
        pipeline = {"status":"UNKNOWN", "raw_output": raw}
    bootstrap = session_context.build_bootstrap(repo, role=args.role, session_type="auto")
    bp_json, bp_md = session_context.persist_bootstrap(repo, bootstrap)
    result = {
        "status": pipeline.get("status"), "state_created": created, "archived_previous": archived,
        "work_item": (sm._load(state_path).get("work_item") if state_path.exists() else None),
        "pipeline": pipeline,
        "bootstrap": {"json": str(bp_json), "markdown": str(bp_md), "session_type": bootstrap.get("session_type")},
    }
    flow = pipeline.get("flow_profile")
    text = f"Intake: {pipeline.get('status')}\nFlow: {flow or 'not classified'}\nWork item: {(result.get('work_item') or {}).get('id')}\nNext: {bootstrap.get('next_action')}"
    return code, result, text


def cmd_resume(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo=args.repo.resolve()
    doc=session_context.build_bootstrap(repo, role=args.role, session_type="auto", host=args.host)
    session_context.persist_bootstrap(repo, doc)
    return 0, doc, session_context.render_bootstrap(doc)


def cmd_verify(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo=args.repo.resolve(); sp=repo/".orchestrator/execution-state.yaml"
    if not sp.exists():
        return 2,{"status":"ERROR","error":"NO_ACTIVE_WORK_ITEM"},"No active work item."
    state=sm._load(sp); completion=sm.completion_status(state)
    manifest_status=None
    mp=repo/".orchestrator/intake/context-manifest.json"
    if mp.exists():
        try: manifest_status=context_plane.validate_manifest(repo,json.loads(mp.read_text(encoding="utf-8")))
        except Exception as exc: manifest_status={"status":"ERROR","error":str(exc)}
    result={"status":"READY_TO_CLOSE" if completion.get("governance_close_ready") else "NOT_READY","completion":completion,"context":manifest_status,"required_gate_failures":sm.required_gate_failures(state)}
    lines=[f"Verification readiness: {result['status']}"]
    for r in completion.get("close_guard_failures") or []: lines.append(f"- {r}")
    if manifest_status: lines.append(f"Context: {manifest_status.get('status')}")
    return (0 if result["status"]=="READY_TO_CLOSE" else 1),result,"\n".join(lines)


def cmd_host_install(args: argparse.Namespace) -> tuple[int, dict[str, Any], str]:
    repo=args.repo.resolve(); hosts=["claude-code","codex","pi"] if args.host=="all" else [args.host]
    installed=project_bootstrap._install_hosts(repo,hosts)
    result={"status":"INSTALLED","hosts":installed}
    return 0,result,"Installed: "+", ".join(installed)


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
    x.set_defaults(func=cmd_init)

    x=sub.add_parser("discover",help="show conservative project/host/SDD discovery")
    x.set_defaults(func=cmd_discover)
    x=sub.add_parser("doctor",help="diagnose installation and current project health")
    x.set_defaults(func=cmd_doctor)
    x=sub.add_parser("status",help="show active execution status")
    x.add_argument("--role",default="implementer",choices=sorted(session_context.ROLES)); x.set_defaults(func=cmd_status)

    x=sub.add_parser("intake",help="start or reassess a work item through the V6 semantic intake pipeline")
    x.add_argument("request",nargs="?")
    x.add_argument("--request-file",type=Path)
    x.add_argument("--work-id"); x.add_argument("--title")
    x.add_argument("--sdd",choices=["openspec","bmad","generic"])
    x.add_argument("--sdd-ref",type=Path)
    x.add_argument("--base-ref")
    x.add_argument("--resolutions",type=Path)
    x.add_argument("--role",default="implementer",choices=sorted(session_context.ROLES))
    x.add_argument("--stage",default="implementation",choices=["bootstrap","planning","implementation","review","verification"])
    x.add_argument("--actor",default="coding-orchestrator")
    x.add_argument("--degraded",action="store_true",help="explicitly allow CBM unavailable; Decision Engine still fails closed on missing evidence")
    x.add_argument("--cbm-fixture",type=Path,help=argparse.SUPPRESS)
    x.set_defaults(func=cmd_intake)

    x=sub.add_parser("resume",help="rebuild and print the current Session Bootstrap")
    x.add_argument("--role",default="implementer",choices=sorted(session_context.ROLES)); x.add_argument("--host")
    x.set_defaults(func=cmd_resume)
    x=sub.add_parser("verify",help="check governance readiness; does not invent or bypass project test commands")
    x.set_defaults(func=cmd_verify)

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
