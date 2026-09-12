#!/usr/bin/env python3
"""Safe, idempotent project bootstrap for Coding Agent Orchestrator v6.5."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

import install_host_adapter
import project_discovery
import session_context

ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise RuntimeError(f"expected object in {path}")
    return value


def _dump_yaml(path: Path, data: dict[str, Any]) -> None:
    if yaml is None:
        raise RuntimeError("PyYAML is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _copy_if_missing(src: Path, dst: Path, force: bool = False) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not force:
        return "preserved"
    shutil.copy2(src, dst)
    return "created" if not dst.exists() else "updated"


def _install_policies(repo: Path, discovery: dict[str, Any], architecture: str = "auto", force: bool = False) -> dict[str, Any]:
    src = ROOT / "policies"
    dst = repo / ".orchestrator" / "policies"
    dst.mkdir(parents=True, exist_ok=True)
    actions: dict[str, str] = {}
    for name in ["common-engineering.yaml", "spring-boot-layered.yaml"]:
        target = dst / name
        existed = target.exists()
        if not existed or force:
            shutil.copy2(src / name, target)
        actions[name] = "preserved" if existed and not force else ("updated" if existed else "created")

    manifest_path = dst / "manifest.yaml"
    manifest_existed = manifest_path.exists()
    if manifest_existed and not force:
        return {"manifest": str(manifest_path), "status": "preserved", "files": actions, "spring_layered_enabled": None}

    manifest = _load_yaml(src / "manifest.yaml")
    detected_pack = (discovery.get("architecture") or {}).get("auto_enable_policy_pack")
    enable_spring = architecture == "spring-layered" or (architecture == "auto" and detected_pack == "spring-boot-layered")
    if architecture == "none":
        enable_spring = False
    for pack in manifest.get("packs") or []:
        if pack.get("id") == "spring-boot-layered":
            pack["enabled"] = bool(enable_spring)
    manifest.setdefault("bootstrap", {})
    manifest["bootstrap"].update({
        "architecture_mode": architecture,
        "detected_style": (discovery.get("architecture") or {}).get("style"),
        "detected_confidence": (discovery.get("architecture") or {}).get("confidence"),
        "spring_layered_auto_enabled": bool(enable_spring),
    })
    _dump_yaml(manifest_path, manifest)
    return {"manifest": str(manifest_path), "status": "updated" if manifest_existed else "created", "files": actions, "spring_layered_enabled": bool(enable_spring)}


def _resolve_sdd(discovery: dict[str, Any], requested: str) -> dict[str, Any] | None:
    sdd = discovery.get("sdd") or {}
    candidates = sdd.get("candidates") or []
    if requested != "auto":
        if requested == "generic":
            return {"provider": "generic", "authority_mode": "orchestrator", "native_state_ref": None, "reason": "explicit selection"}
        matches = [x for x in candidates if x.get("provider") == requested]
        if matches:
            return matches[0]
        return {"provider": requested, "authority_mode": "hybrid" if requested in {"openspec", "bmad"} else "orchestrator", "native_state_ref": None, "reason": "explicit selection without detected native state"}
    return sdd.get("preferred")


def _host_names(discovery: dict[str, Any], requested: str) -> list[str]:
    if requested == "none":
        return []
    if requested == "all":
        return ["claude-code", "codex", "pi"]
    if requested != "auto":
        return [requested]
    detected = (discovery.get("hosts") or {}).get("detected") or []
    # Safe-auto installs only high-confidence repository-local host markers. A binary-only
    # signal is advisory because generic executable names (especially "pi") can collide.
    return [x.get("host") for x in detected if x.get("confidence") == "high" and x.get("host")]


def _install_hosts(repo: Path, hosts: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    install_host_adapter.ensure_enforcement_config(repo, True)
    install_host_adapter.ensure_session_context_config(repo, True)
    for host in hosts:
        if host == "claude-code":
            frag = install_host_adapter.load_template(ROOT / "hosts" / "claude-code" / "hooks.template.json")
            target = repo / ".claude" / "settings.json"
            install_host_adapter.merge_hooks(target, frag, True)
            out[host] = str(target)
        elif host == "codex":
            frag = install_host_adapter.load_template(ROOT / "hosts" / "codex" / "hooks.template.json")
            target = repo / ".codex" / "hooks.json"
            install_host_adapter.merge_hooks(target, frag, True)
            out[host] = str(target)
        elif host == "pi":
            out[host] = install_host_adapter.install_pi(repo, True)
    return out


def initialize(repo: Path, *, sdd: str = "auto", host: str = "auto", architecture: str = "auto", force: bool = False, ci: bool = False) -> dict[str, Any]:
    repo = repo.resolve()
    repo.mkdir(parents=True, exist_ok=True)
    discovery = project_discovery.discover(repo)
    orch = repo / ".orchestrator"
    orch.mkdir(parents=True, exist_ok=True)
    for name in ["intake", "context", "session", "evidence"]:
        (orch / name).mkdir(parents=True, exist_ok=True)

    selected_sdd = _resolve_sdd(discovery, sdd)
    unresolved: list[dict[str, Any]] = []
    if selected_sdd is None:
        unresolved.append({
            "code": "SDD_AUTHORITY_REQUIRED",
            "detail": "multiple SDD providers are present; rerun init with --sdd openspec|bmad|generic",
            "candidates": [x.get("provider") for x in (discovery.get("sdd") or {}).get("candidates", [])],
        })

    policies = _install_policies(repo, discovery, architecture=architecture, force=force)
    enforcement = repo / ".orchestrator" / "enforcement.yaml"
    session_cfg = repo / ".orchestrator" / "session-context.yaml"
    if not enforcement.exists() or force:
        shutil.copy2(ROOT / "examples" / "enforcement.yaml", enforcement)
    if not session_cfg.exists() or force:
        shutil.copy2(ROOT / "examples" / "session-context.yaml", session_cfg)

    hosts = _host_names(discovery, host)
    installed_hosts = _install_hosts(repo, hosts)

    config_path = orch / "config.yaml"
    config_existed = config_path.exists()
    if not config_existed or force:
        config = {
            "version": "6.5",
            "orchestrator": {
                "bootstrap": {
                    "mode": "ci" if ci else "safe-auto",
                    "discovery_status": discovery.get("status"),
                    "unresolved": unresolved,
                },
                "project": {
                    "name": repo.name,
                    "technology": discovery.get("technology"),
                    "architecture": discovery.get("architecture"),
                },
                "sdd": selected_sdd or {"provider": None, "authority_mode": None, "native_state_ref": None},
                "code_intelligence": discovery.get("code_intelligence"),
                "hosts": {"installed": sorted(installed_hosts), "detected": (discovery.get("hosts") or {}).get("names", [])},
                "engineering_policy": {"manifest": ".orchestrator/policies/manifest.yaml", "spring_layered_enabled": policies.get("spring_layered_enabled")},
                "runtime": {
                    "execution_state": ".orchestrator/execution-state.yaml",
                    "intake_dir": ".orchestrator/intake",
                    "session_dir": ".orchestrator/session",
                },
            },
        }
        _dump_yaml(config_path, config)
        config_status = "updated" if config_existed else "created"
    else:
        config_status = "preserved"

    bootstrap = session_context.build_bootstrap(repo, role="implementer", session_type="auto", host=hosts[0] if len(hosts) == 1 else None)
    bootstrap_json, bootstrap_md = session_context.persist_bootstrap(repo, bootstrap)

    warnings: list[dict[str, Any]] = []
    if not (discovery.get("code_intelligence") or {}).get("available"):
        warnings.append({"code": "CBM_NOT_AVAILABLE", "detail": "codebase-memory-mcp was not found; semantic intake will fail closed unless degraded mode is explicitly requested"})
    if not hosts:
        warnings.append({"code": "NO_HOST_DETECTED", "detail": "no Claude Code/Codex/Pi host was detected; install later with 'coding-orchestrator host install <host>'"})
    arch = discovery.get("architecture") or {}
    if "spring-boot" in ((discovery.get("technology") or {}).get("frameworks") or []) and not policies.get("spring_layered_enabled"):
        warnings.append({"code": "SPRING_ARCHITECTURE_NOT_PROVEN", "detail": arch.get("reason")})

    status = "ACTION_REQUIRED" if unresolved else "READY_FOR_INTAKE"
    report = {
        "status": status,
        "repo": str(repo),
        "config": {"path": str(config_path), "status": config_status},
        "sdd": selected_sdd,
        "policies": policies,
        "hosts": {"installed": installed_hosts, "detected": (discovery.get("hosts") or {}).get("names", [])},
        "code_intelligence": discovery.get("code_intelligence"),
        "bootstrap": {"json": str(bootstrap_json), "markdown": str(bootstrap_md), "status": bootstrap.get("status")},
        "warnings": warnings,
        "unresolved": unresolved,
        "next_action": "resolve_init_ambiguity" if unresolved else "run_intake_for_first_work_item",
    }
    (orch / "bootstrap-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
