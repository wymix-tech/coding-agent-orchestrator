#!/usr/bin/env python3
"""Safe, idempotent project bootstrap for Coding Agent Orchestrator."""
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
import host_runtime
import project_discovery
import project_activation
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


def _host_selection(requested: str) -> dict[str, Any]:
    """Select host adapters from the current Agent runtime, not stale repo markers."""
    return host_runtime.select_hosts(requested)


def _host_names(discovery: dict[str, Any], requested: str) -> list[str]:
    # Kept as a compatibility helper for callers/tests. Discovery markers are deliberately not
    # authoritative for auto selection; they describe what exists in the repo, not who is running.
    return list(_host_selection(requested).get("hosts") or [])


def _persist_host_runtime(repo: Path, selection: dict[str, Any], installed_now: dict[str, Any]) -> None:
    """Persist host reconciliation as runtime metadata, never as project-governance truth."""
    path = repo / ".orchestrator" / "runtime" / "host-selection.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "selection": selection,
        "installed_now": sorted(installed_now),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def ensure_selected_host(repo: Path, requested: str = "auto", *, activation: bool = True) -> dict[str, Any]:
    """Ensure only the selected/current host adapter exists, without reinitializing project state."""
    selection = _host_selection(requested)
    hosts = list(selection.get("hosts") or [])
    missing = [h for h in hosts if not install_host_adapter.is_installed(repo, h)]
    installed_now = _install_hosts(repo, missing) if missing else {}
    activation_result = project_activation.install(repo, hosts, apply=True) if activation and hosts else {"status": "disabled", "hosts": {}}
    _persist_host_runtime(repo, selection, installed_now)
    return {
        "selection": selection,
        "hosts": hosts,
        "missing_before": missing,
        "installed_now": installed_now,
        "performed": bool(installed_now),
        "activation": activation_result,
    }


def _install_hosts(repo: Path, hosts: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    install_host_adapter.ensure_enforcement_config(repo, True)
    install_host_adapter.ensure_session_context_config(repo, True)
    for host in hosts:
        if host == "claude-code":
            frag = install_host_adapter.load_template(ROOT / "hosts" / "claude-code" / "hooks.template.json", repo)
            target = repo / ".claude" / "settings.json"
            install_host_adapter.merge_hooks(target, frag, True)
            out[host] = str(target)
        elif host == "codex":
            frag = install_host_adapter.load_template(ROOT / "hosts" / "codex" / "hooks.template.json", repo)
            target = repo / ".codex" / "hooks.json"
            install_host_adapter.merge_hooks(target, frag, True)
            out[host] = str(target)
        elif host == "pi":
            out[host] = install_host_adapter.install_pi(repo, True)
    return out


def initialize(repo: Path, *, sdd: str = "auto", host: str = "auto", architecture: str = "auto", force: bool = False, ci: bool = False, activation: bool = True) -> dict[str, Any]:
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

    host_selection = _host_selection(host)
    hosts = list(host_selection.get("hosts") or [])
    installed_hosts = _install_hosts(repo, hosts)
    activation_result = project_activation.install(repo, hosts, apply=True) if activation else {"status": "disabled", "hosts": {}}

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
                "hosts": {
                    "installed": sorted(installed_hosts),
                    "detected": (discovery.get("hosts") or {}).get("names", []),
                    "current": host_selection,
                },
                "activation": {
                    "enabled": bool(activation),
                    "agents_file": "AGENTS.md" if activation else None,
                    "claude_file": "CLAUDE.md" if activation and "claude-code" in hosts else None,
                    "skill_ref": activation_result.get("skill_ref") if isinstance(activation_result, dict) else None,
                },
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
        # An explicit SDD choice is a field-level authority resolution, not a request
        # to overwrite project configuration. Persist it even when config already
        # exists and --force was not supplied.
        if sdd != "auto":
            existing = _load_yaml(config_path)
            orch_cfg = existing.setdefault("orchestrator", {})
            current_sdd = orch_cfg.get("sdd") or {}
            active_state = repo / ".orchestrator" / "execution-state.yaml"
            if active_state.exists() and current_sdd.get("provider") not in {None, sdd}:
                unresolved.append({
                    "code": "ACTIVE_NATIVE_AUTHORITY_CONFLICT",
                    "detail": f"active work item belongs to {current_sdd.get('provider')}; explicit switch to {sdd} requires closing/switching the work item",
                })
            elif selected_sdd is not None:
                orch_cfg["sdd"] = selected_sdd
                bootstrap_cfg = orch_cfg.setdefault("bootstrap", {})
                old_unresolved = bootstrap_cfg.get("unresolved") or []
                bootstrap_cfg["unresolved"] = [
                    item for item in old_unresolved
                    if (item or {}).get("code") != "SDD_AUTHORITY_REQUIRED"
                ]
                _dump_yaml(config_path, existing)
                config_status = "merged"
                unresolved = [item for item in unresolved if item.get("code") != "SDD_AUTHORITY_REQUIRED"]

    bootstrap = session_context.build_bootstrap(repo, role="implementer", session_type="auto", host=hosts[0] if len(hosts) == 1 else None)
    bootstrap_json, bootstrap_md = session_context.persist_bootstrap(repo, bootstrap)

    warnings: list[dict[str, Any]] = []
    if not (discovery.get("code_intelligence") or {}).get("available"):
        warnings.append({"code": "CBM_NOT_AVAILABLE", "detail": "codebase-memory-mcp was not found; semantic intake will fail closed unless degraded mode is explicitly requested"})
    if host_selection.get("fallback"):
        warnings.append({
            "code": "HOST_RUNTIME_FALLBACK",
            "detail": "current Agent host could not be proven; Claude Code adapter was installed as the deterministic fallback",
        })
    arch = discovery.get("architecture") or {}
    if "spring-boot" in ((discovery.get("technology") or {}).get("frameworks") or []) and not policies.get("spring_layered_enabled"):
        warnings.append({"code": "SPRING_ARCHITECTURE_NOT_PROVEN", "detail": arch.get("reason")})

    # Re-read persisted bootstrap ambiguities so report and disk cannot disagree.
    persisted = _load_yaml(config_path) if config_path.exists() else {}
    persisted_orch = (persisted.get("orchestrator") or {}) if isinstance(persisted, dict) else {}
    persisted_unresolved = ((persisted_orch.get("bootstrap") or {}).get("unresolved") or [])
    persisted_sdd = persisted_orch.get("sdd") if isinstance(persisted_orch.get("sdd"), dict) else None
    effective_unresolved = unresolved or persisted_unresolved
    status = "ACTION_REQUIRED" if effective_unresolved else "READY_FOR_INTAKE"
    report = {
        "status": status,
        "repo": str(repo),
        "config": {"path": str(config_path), "status": config_status},
        # Report what is actually persisted as authority. A conflicting explicit request is
        # exposed separately and must never make stdout disagree with durable config.
        "sdd": persisted_sdd or selected_sdd,
        "requested_sdd": selected_sdd if sdd != "auto" else None,
        "policies": policies,
        "hosts": {
            "installed": installed_hosts,
            "detected": (discovery.get("hosts") or {}).get("names", []),
            "current": host_selection,
        },
        "activation": activation_result,
        "code_intelligence": discovery.get("code_intelligence"),
        "bootstrap": {"json": str(bootstrap_json), "markdown": str(bootstrap_md), "status": bootstrap.get("status")},
        "warnings": warnings,
        "unresolved": effective_unresolved,
        "next_action": "resolve_init_ambiguity" if effective_unresolved else "run_intake_for_first_work_item",
    }
    (orch / "bootstrap-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
