"""SDD discovery, prerequisites, installation, and host integration selection."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.cao_defaults import CONFIG_REL, SDD_CATALOG
from scripts.cao_language import apply_provider_language
from scripts.cao_runtime import (
    action,
    command_exists,
    config,
    ensure_initialized,
    locked_command,
    locked_repo,
    now,
    rel,
    repo_root,
    save_json,
)
from scripts.provider_restart import require_restart, restart_state


def command_output(argv: list[str], timeout: float = 3.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)


def version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def version_at_least(actual: tuple[int, ...] | None, required: tuple[int, ...]) -> bool:
    if actual is None:
        return False
    a = actual + (0,) * (len(required) - len(actual))
    return a[: len(required)] >= required


def find_sdd_project_markers(repo: Path, provider: str) -> list[str]:
    meta = SDD_CATALOG.get(provider) or {}
    found: list[str] = []
    for marker in meta.get("project_markers") or []:
        p = repo / marker
        if p.exists():
            found.append(marker)
    return found


def detect_sdd(repo: Path, provider: str, include_cli_version: bool = False) -> dict[str, Any]:
    meta = SDD_CATALOG.get(provider) or {}
    markers = find_sdd_project_markers(repo, provider)
    cli = str(meta.get("cli") or provider)
    cli_installed = command_exists(cli)
    cli_version = None
    if cli_installed and include_cli_version:
        rc, out, err = command_output([cli, "--version"])
        if rc == 0:
            cli_version = out or err
    health = {}
    if provider == 'bmad':
        from scripts.bmad_project import health as bmad_health
        health = bmad_health(repo)
    return {
        "provider": provider,
        "name": meta.get("name", provider),
        "description": meta.get("description", ""),
        "detected": bool(markers),
        "installed": bool(markers) and health.get('healthy', True),
        "initialized": bool(markers) and health.get('healthy', True),
        **health,
        "project_markers": markers,
        "cli_installed": cli_installed,
        "cli_version": cli_version,
    }


def sdd_status(repo: Path) -> dict[str, Any]:
    cfg = config(repo)
    selected = str((cfg.get("sdd") or {}).get("provider") or "auto")
    providers = [detect_sdd(repo, name) for name in SDD_CATALOG]
    installed = [x for x in providers if x["installed"]]
    selected_status = None
    if selected in SDD_CATALOG:
        selected_status = detect_sdd(repo, selected)
    return {
        "selected": selected,
        "selected_status": selected_status,
        "installed": installed,
        "providers": providers,
        "selection_required": not installed or (selected == "auto" and len(installed) > 1),
    }


def sdd_options(status: dict[str, Any]) -> list[dict[str, Any]]:
    options = []
    for item in status["providers"]:
        options.append({
            "provider": item["provider"],
            "name": item["name"],
            "description": item["description"],
            "installed": item["installed"],
            "cli_installed": item["cli_installed"],
            "project_markers": item["project_markers"],
            **{key: item[key] for key in ('detected', 'healthy', 'compatible', 'reason', 'missing') if key in item},
        })
    return options


@locked_repo
def select_sdd_provider(repo: Path, provider: str) -> None:
    if provider not in SDD_CATALOG:
        raise ValueError(f"Unsupported SDD provider: {provider}")
    cfg_path = repo / CONFIG_REL
    cfg = config(repo)
    cfg["sdd"]["provider"] = provider
    cfg["sdd"]["selected_at"] = now()
    save_json(cfg_path, cfg)


def find_python_runtime(required: tuple[int, ...]) -> dict[str, Any]:
    """Find any usable Python interpreter meeting the minimum version.

    Do not require the generic `python3` alias: macOS/Linux environments often
    expose a versioned executable such as `python3.11` instead.
    """
    candidates = ["python3"] + [f"python3.{minor}" for minor in range(20, 9, -1)] + ["python"]
    if sys.executable:
        candidates.append(sys.executable)

    seen: set[str] = set()
    available: list[dict[str, Any]] = []
    selected: str | None = None
    selected_version: str | None = None

    for name in candidates:
        if name in seen or not command_exists(name):
            continue
        seen.add(name)
        rc, out, err = command_output([name, "--version"])
        version_text = out or err
        actual = version_tuple(version_text)
        ok = rc == 0 and version_at_least(actual, required)
        available.append({"name": name, "ok": ok, "version": version_text})
        if ok and selected is None:
            selected = name
            selected_version = version_text

    if selected:
        return {
            "name": "python",
            "ok": True,
            "selected": selected,
            "version": selected_version,
            "required": ".".join(map(str, required)) + "+",
            "candidates": available,
        }

    return {
        "name": "python",
        "ok": False,
        "reason": "Python >= 3.10 is not installed or no compatible executable was found",
        "required": ".".join(map(str, required)) + "+",
        "candidates": available,
    }


def sdd_prerequisite_check(provider: str) -> dict[str, Any]:
    meta = SDD_CATALOG[provider]
    checks: list[dict[str, Any]] = []
    if provider == "openspec":
        if not command_exists("node"):
            checks.append({"name": "node", "ok": False, "reason": "Node.js is not installed", "required": ">= 20.19.0"})
        else:
            rc, out, err = command_output(["node", "--version"])
            actual = version_tuple(out or err)
            checks.append({"name": "node", "ok": rc == 0 and version_at_least(actual, (20, 19, 0)), "version": out or err, "required": ">= 20.19.0"})
    elif provider == "bmad":
        if not command_exists("node"):
            checks.append({"name": "node", "ok": False, "reason": "node is not installed", "required": ">= 20.12.0"})
        else:
            rc, out, err = command_output(["node", "--version"])
            actual = version_tuple(out or err)
            checks.append({"name": "node", "ok": rc == 0 and version_at_least(actual, (20, 12, 0)), "version": out or err, "required": "20.12.0+"})

        python_check = find_python_runtime((3, 10, 0))
        checks.append(python_check)
        uv_ok = command_exists("uv")
        checks.append({"name": "uv", "ok": uv_ok, "reason": "uv is not installed" if not uv_ok else None})
    elif provider == "speckit":
        checks.append({"name": "uv", "ok": command_exists("uv"), "reason": "uv is not installed" if not command_exists("uv") else None})
    return {"provider": provider, "ok": all(c["ok"] for c in checks), "checks": checks, "declared": meta["install"].get("prerequisite")}


def choose_external_install_command(provider: str, upgrade: bool = False) -> list[str] | None:
    if provider == "openspec" and not upgrade and command_exists("openspec"):
        rc, out, err = command_output(["openspec", "--version"])
        if rc == 0 and version_tuple(out or err):
            return None
    for candidate in SDD_CATALOG[provider]["install"].get("external_commands") or []:
        if candidate and command_exists(candidate[0]):
            return candidate
    return None


def run_install_command(argv: list[str], repo: Path) -> dict[str, Any]:
    from scripts.provider_execution import run
    timeout = (config(repo).get('sdd') or {}).get('install_timeout_seconds', 900)
    return run(repo, argv, kind='provider_install', timeout=timeout)


def infer_sdd_provider(repo: Path, requirement: Path, cfg: dict[str, Any]) -> str:
    p = rel(requirement, repo).lower()
    configured = str((cfg.get("sdd") or {}).get("provider") or "auto")
    if configured != "auto":
        return configured
    if p.startswith("openspec/"):
        return "openspec"
    if p.startswith("_bmad/") or p.startswith("bmad/"):
        return "bmad"
    if "/specs/" in f"/{p}" or p.startswith("specs/"):
        if (repo / ".specify").exists():
            return "speckit"
        return "generic-sdd"
    return "generic"


def run_sdd_command(repo: Path, name: str, extra: list[str]) -> dict[str, Any] | None:
    command = str((((config(repo).get("sdd") or {}).get("command") or {}).get(name) or "")).strip()
    if not command:
        return None
    env = os.environ.copy()
    env.update({"CAO_REPO": str(repo), "CAO_COMMAND": name})
    from scripts.provider_execution import run
    timeout = (config(repo).get('sdd') or {}).get('command_timeout_seconds', 300)
    result = run(repo, shlex.split(command) + extra, kind='provider_' + name, timeout=timeout, env=env)
    return {**result, 'command': command, 'args': extra}


def cmd_sdd_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    if (repo / CONFIG_REL).exists():
        status = sdd_status(repo)
        status["providers"] = [detect_sdd(repo, name, include_cli_version=True) for name in SDD_CATALOG]
        status["installed"] = [x for x in status["providers"] if x["installed"]]
        selected = status["selected"]
        status["selected_status"] = detect_sdd(repo, selected, include_cli_version=True) if selected in SDD_CATALOG else None
    else:
        providers = [detect_sdd(repo, name, include_cli_version=True) for name in SDD_CATALOG]
        status = {
            "selected": "auto",
            "selected_status": None,
            "installed": [x for x in providers if x["installed"]],
            "providers": providers,
            "selection_required": True,
        }
    print(json.dumps({"ok": True, **status}, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_sdd_select(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    detected = detect_sdd(repo, args.provider)
    if detected.get('detected') and not detected.get('healthy', True):
        print(json.dumps({'ok': False, 'error': 'SDD_INSTALL_UNHEALTHY', 'status': detected,
                          'next': 'Repair the stable BMAD installation with `cao sdd install --provider bmad --host <host> --yes`; preserve project config and current Work.'})); return 2
    if not detected["installed"]:
        print(json.dumps({
            "ok": False,
            "error": "SDD_NOT_INSTALLED",
            "provider": args.provider,
            "status": detected,
            "next": f"cao sdd install --provider {args.provider} --yes",
        }, ensure_ascii=False, indent=2))
        return 2
    select_sdd_provider(repo, args.provider)
    try:
        language_configuration = apply_provider_language(repo, args.provider)
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": args.provider, "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "selected": args.provider, "status": detect_sdd(repo, args.provider), "language_configuration": language_configuration}, ensure_ascii=False, indent=2))
    return 0


def detect_current_host(environ: dict[str, str] | None = None) -> str | None:
    """Detect the active coding-agent host from its runtime environment.

    Explicit CLI arguments take precedence at call sites. Detection is
    intentionally conservative: if multiple hosts appear active, return None
    instead of installing integrations for all of them.
    """
    env = os.environ if environ is None else environ
    declared = str(env.get("CAO_HOST") or "").strip().lower()
    aliases = {
        "claude": "claude-code", "claude-code": "claude-code",
        "codex": "codex", "pi": "pi",
    }
    if declared in aliases:
        return aliases[declared]

    matches: set[str] = set()
    if any(env.get(key) for key in ("CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")):
        matches.add("claude-code")
    if any(env.get(key) for key in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME")):
        matches.add("codex")
    if any(env.get(key) for key in ("PI_CODING_AGENT", "PI_SESSION_ID")):
        matches.add("pi")
    return next(iter(matches)) if len(matches) == 1 else None


HOST_SESSION_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "claude-code": ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"),
    "codex": ("CODEX_THREAD_ID",),
    "pi": ("PI_SESSION_ID",),
}


def host_session_identity(host: str | None, environ: dict[str, str] | None = None) -> tuple[str, str] | None:
    """Resolve (host, native session id) when the runtime exposes both.

    Host adapters are supposed to register the Session through `host-event`, but
    that only happens when the adapter is actually installed. Returning None
    here keeps CAO from inventing an `unknown` Session for every command.
    """
    env = os.environ if environ is None else environ
    resolved = host or detect_current_host(env)
    if not resolved:
        return None
    for key in HOST_SESSION_ENV_KEYS.get(resolved, ()):
        value = str(env.get(key) or "").strip()
        if value:
            return resolved, value
    return None


def openspec_tool_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return str((SDD_CATALOG["openspec"]["install"].get("tool_by_host") or {}).get(host) or "") or None


def build_openspec_project_command(args: argparse.Namespace) -> list[str]:
    tools = args.tools
    if not tools:
        host = args.host or detect_current_host()
        tools = openspec_tool_for_host(host)
    if not tools:
        raise ValueError(
            "OpenSpec installation requires a detectable current host or explicit "
            "--host <claude-code|codex|pi> / --tools <tool-id>. Refusing --tools all "
            "because it installs integrations for unrelated hosts."
        )
    return ["openspec", "init", "--tools", tools]


def speckit_integration_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return str((SDD_CATALOG["speckit"]["install"].get("integration_by_host") or {}).get(host) or "") or None


def bmad_tool_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return str((SDD_CATALOG["bmad"]["install"].get("tool_by_host") or {}).get(host) or "") or None


def sdd_install_instruction(provider: str, host: str | None = None, tools: str | None = None) -> str:
    if provider == "speckit":
        if host:
            return f"cao sdd install --provider speckit --host {host} --yes"
        return "cao sdd install --provider speckit --host <claude-code|codex|pi> --yes"
    if provider == "bmad":
        if tools:
            return f"cao sdd install --provider bmad --tools {tools} --yes"
        if host:
            return f"cao sdd install --provider bmad --host {host} --yes"
        return "cao sdd install --provider bmad --host <claude-code|codex|pi> --yes"
    return f"cao sdd install --provider {provider} --yes"


def build_bmad_project_command(args: argparse.Namespace, repo: Path) -> list[str]:
    tools = args.tools or bmad_tool_for_host(args.host or detect_current_host())
    if not tools:
        raise ValueError("BMad installation requires --host <claude-code|codex|pi> or explicit --tools <tool-id>")
    template = list(SDD_CATALOG["bmad"]["install"].get("project_command") or [])
    if not template:
        raise ValueError("BMad installation command template is not configured")
    replacements = {"{repo}": str(repo), "{tool}": tools}
    return [replacements.get(part, part) for part in template]


def build_speckit_project_command(args: argparse.Namespace) -> list[str]:
    integration = args.integration or speckit_integration_for_host(args.host or detect_current_host())
    if not integration:
        raise ValueError("Spec Kit installation requires --host <claude-code|codex|pi> or --integration <spec-kit integration>")
    command = ["specify", "init", "--here", "--force", "--non-interactive", "--integration", integration, "--ignore-agent-tools"]
    if args.integration_options:
        command.append(f"--integration-options={args.integration_options}")
    return command


def install_restart_hosts(args: argparse.Namespace, provider: str) -> list[str]:
    explicit = getattr(args, "tools", None) or getattr(args, "integration", None)
    if explicit:
        meta = SDD_CATALOG[provider]["install"]
        mapping = meta.get("integration_by_host") if provider == "speckit" else meta.get("tool_by_host")
        targets = {value.strip() for value in explicit.split(",")}
        return sorted(host for host, tool in (mapping or {}).items() if tool in targets)
    host = getattr(args, "host", None) or detect_current_host()
    return [host] if host else []


def install_restart_result(repo: Path) -> dict:
    from scripts.cao_guidance import agent_guidance

    restart = restart_state(repo)
    return {"restart_required": not restart["ready"], "restart": restart,
            **({"next": action("human", restart["next"], reason=restart["reason"]),
                "guidance": agent_guidance(repo, None, "host_restart_required")} if not restart["ready"] else {})}


def cmd_sdd_install(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    provider = args.provider
    if provider not in SDD_CATALOG:
        print(json.dumps({"ok": False, "error": "SDD_PROVIDER_REQUIRED", "options": sdd_options(sdd_status(repo))}, ensure_ascii=False, indent=2))
        return 2

    detected = detect_sdd(repo, provider)
    if detected["installed"] and detected.get('healthy', True) and not getattr(args, "upgrade", False):
        select_sdd_provider(repo, provider)
        try:
            language_configuration = apply_provider_language(repo, provider)
        except (OSError, UnicodeError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": provider, "reason": str(exc), **install_restart_result(repo)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, "status": "already_installed", "selected": provider, "sdd": detected, "language_configuration": language_configuration, **install_restart_result(repo)}, ensure_ascii=False, indent=2))
        return 0

    prereq = sdd_prerequisite_check(provider)

    meta = SDD_CATALOG[provider]["install"]
    external = choose_external_install_command(provider, getattr(args, "upgrade", False))
    plan = []
    if external:
        plan.append({"scope": "machine", "command": shlex.join(external)})
    project_command = list(meta.get("project_command") or [])
    if provider == "openspec":
        try:
            project_command = build_openspec_project_command(args)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": "SDD_INSTALL_ARGUMENTS_REQUIRED", "provider": provider, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    elif provider == "bmad":
        try:
            project_command = build_bmad_project_command(args, repo)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": "SDD_INSTALL_ARGUMENTS_REQUIRED", "provider": provider, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    elif provider == "speckit":
        project_command = build_speckit_project_command(args)
    if project_command:
        plan.append({"scope": "project", "command": shlex.join(project_command)})

    if not args.yes:
        print(json.dumps({
            "ok": True,
            "status": "confirmation_required",
            "provider": provider,
            "sdd": detected,
            "prerequisites": prereq,
            "install_plan": plan,
            "next": f"Review the install plan and rerun `cao sdd install --provider {provider} --yes` after the user confirms.",
        }, ensure_ascii=False, indent=2))
        return 0

    # A plan is inspectable even when a tool is missing; actual installation
    # remains blocked before any machine/project command runs.
    if not prereq["ok"]:
        print(json.dumps({"ok": False, "error": "SDD_PREREQUISITE_FAILED", "provider": provider, "prerequisites": prereq}, ensure_ascii=False, indent=2))
        return 2
    results = []
    if external:
        result = run_install_command(external, repo); results.append(result)
        if result["exit_code"] != 0:
            print(json.dumps({"ok": False, "error": "SDD_MACHINE_INSTALL_FAILED", "provider": provider, "results": results}, ensure_ascii=False, indent=2))
            return 1

    # Provider project installers are invoked only after explicit user
    # confirmation via --yes. Built-in commands are constructed in non-
    # interactive form so Bootstrap never gets trapped inside a provider prompt.
    if project_command:
        result = run_install_command(project_command, repo)
        results.append(result)
        if result['exit_code'] != 0:
            print(json.dumps({'ok': False, 'error': 'SDD_PROJECT_INSTALL_FAILED', 'provider': provider, 'results': results}, ensure_ascii=False, indent=2))
            return 1

    final = detect_sdd(repo, provider)
    if not final["installed"] or not final.get('healthy', True):
        print(json.dumps({"ok": False, "error": "SDD_INSTALL_NOT_DETECTED", "provider": provider, "results": results, "status": final}, ensure_ascii=False, indent=2))
        return 1
    select_sdd_provider(repo, provider)
    # Persist before language configuration: an installed provider still needs
    # its host resources reloaded if the language update subsequently fails.
    require_restart(repo, provider, install_restart_hosts(args, provider))
    try:
        language_configuration = apply_provider_language(repo, provider)
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": provider, "reason": str(exc), "sdd": final, "results": results, **install_restart_result(repo)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "status": "installed", "selected": provider, "sdd": final, "results": results, "language_configuration": language_configuration, **install_restart_result(repo)}, ensure_ascii=False, indent=2))
    return 0
