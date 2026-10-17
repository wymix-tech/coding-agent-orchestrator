"""Package resources, project instructions, and host adapter installation."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sysconfig
from pathlib import Path
from typing import Any

from scripts.cao_runtime import command_exists, load_json, locked_command, repo_root, save_json
from scripts.instruction_updates import update_instructions


# Files unknown hosts might read. When the host is not known, install every
# template rather than guessing which one applies.
ALL_INSTRUCTION_TEMPLATES = ("AGENTS.md", "CLAUDE.md")


# Which instruction file each host actually reads. Only Claude Code reads
# `CLAUDE.md`; Codex and Pi read `AGENTS.md`. Installing both meant a Pi project
# carried a `CLAUDE.md` telling it to run `bootstrap --host claude-code`.
INSTRUCTION_TEMPLATES_BY_HOST = {
    "claude-code": ("CLAUDE.md",),
    "codex": ("AGENTS.md",),
    "pi": ("AGENTS.md",),
}


def instruction_templates_for_host(host: str | None) -> tuple[str, ...]:
    return INSTRUCTION_TEMPLATES_BY_HOST.get(host or "", ALL_INSTRUCTION_TEMPLATES)


def instruction_template_dir() -> Path | None:
    """Locate project-level instruction templates in a checkout or installed wheel."""
    source_root = Path(__file__).resolve().parents[1]
    candidates = [
        source_root / "templates",
        Path(sysconfig.get_path("data")) / "share" / "coding-agent-orchestrator" / "templates",
        Path(sysconfig.get_path("data")) / "templates",
    ]
    required = list(ALL_INSTRUCTION_TEMPLATES)
    for candidate in candidates:
        if all((candidate / name).is_file() for name in required):
            return candidate
    return None


def cao_invocation(root: Path | None = None) -> dict[str, str]:
    """Resolve exactly how the Agent should invoke CAO in this installation.

    The skill directory ships a `cao` launcher at its root; the installed package
    root *is* the skill directory. Agents must call that launcher and must never
    be pointed at `scripts/cao.py`, so the implementation stays out of the
    Agent's context and cannot be read around or reasoned about instead of run.

    Guessing produced failures such as `cao: command not found` followed by
    invented paths like `.agents/skills/.../scripts/cao.py`, so the resolved
    invocation is rendered into the project instruction files instead.
    """
    if command_exists("cao"):
        return {"command": "cao", "source": "console-script"}
    root = root or package_root_dir()
    if root:
        launcher = root / "cao"
        if launcher.is_file():
            # Wheel installs can drop the executable bit, so fall back to `sh`.
            usable = os.access(launcher, os.X_OK)
            return {
                "command": str(launcher) if usable else f"sh {launcher}",
                "source": "skill-launcher",
                "path": str(launcher),
                "executable_bit": usable,
            }
        # Last resort only: the launcher is missing from this installation.
        script = root / "scripts" / "cao.py"
        if script.is_file():
            return {"command": f"python3 {script}", "source": "checkout-script", "path": str(script)}
    return {"command": "cao", "source": "unresolved"}


def install_project_instruction_templates(repo: Path, host: str | None = None) -> dict[str, Any]:
    """Install missing project bootstrap instructions without overwriting user files.

    Instruction files are read when a session starts, so like the host hooks they
    belong to provisioning. They are installed from `cao init` and re-checked
    from `cao bootstrap`, which is idempotent and never overwrites. Only the file
    the provisioned host reads is installed, so a project is not left carrying
    instructions for a different host.
    """
    templates = instruction_templates_for_host(host)
    template_dir = instruction_template_dir()
    result: dict[str, Any] = {
        "status": "installed",
        "installed": [],
        "skipped_existing": [],
        "missing_templates": [],
    }
    if template_dir is None:
        result["status"] = "templates_unavailable"
        result["missing_templates"] = list(templates)
        return result

    for name in templates:
        target = repo / name
        source = template_dir / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(render_project_instruction(source, host))
            result["installed"].append(name)
        except FileExistsError:
            result["skipped_existing"].append(name)
        except OSError as exc:
            result["status"] = "partial"
            result.setdefault("errors", []).append({"file": name, "reason": str(exc)})
    if result.get("status") != "partial":
        if result["installed"]:
            result["status"] = "installed"
        elif result["skipped_existing"]:
            result["status"] = "already_present"
    if result["skipped_existing"]:
        rendered = {name: render_project_instruction(template_dir / name, host)
                    for name in result["skipped_existing"]}
        preview = update_instructions(repo, rendered)
        updates = [item["file"] for item in preview["files"] if item["status"] == "preview"]
        if updates:
            result["upgrade_available"] = updates
            host_arg = f" --host {host}" if host else ""
            result["upgrade_command"] = f"{cao_invocation()['command']} instructions update{host_arg}"
    return result


def render_project_instruction(source: Path, host: str | None) -> str:
    content = source.read_text(encoding="utf-8")
    if host:
        content = content.replace("--host <host>", f"--host {host}")
    return content.replace("{{CAO_COMMAND}}", cao_invocation()["command"])


@locked_command
def cmd_instructions_update(args: argparse.Namespace) -> int:
    from scripts.cao_providers import detect_current_host

    template_dir = instruction_template_dir()
    if template_dir is None:
        raise RuntimeError("INSTRUCTION_TEMPLATES_UNAVAILABLE")
    host = args.host or detect_current_host()
    rendered = {name: render_project_instruction(template_dir / name, host)
                for name in instruction_templates_for_host(host)}
    out = update_instructions(repo_root(args.repo), rendered, args.apply)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# Host adapters are process-startup integrations: Claude Code reads its settings
# file and Pi loads its extensions when the host process starts, not when a
# command inside the session runs. They must therefore be wired during project
# provisioning (`cao init`), never from `cao bootstrap` — by then the current
# session's SessionStart hook has already been missed.
HOST_ADAPTER_META: dict[str, dict[str, Any]] = {
    "claude-code": {
        "fragment": "hosts/claude-code/settings.fragment.json",
        # Local, usually git-ignored: only affects this machine, so a team member
        # who does not use CAO is never forced to load its hooks.
        "target": ".claude/settings.local.json",
    },
    "codex": {
        "fragment": "hosts/codex/hooks.json",
        "target": ".codex/hooks.json",
    },
    "pi": {
        # Pi registers extensions as packages, not as a JSON fragment.
        "target": ".pi/settings.json",
        "install": "pi-cli",
    },
}


# Placeholders inside the shipped fragments that must be resolved to the real
# install location. `${CLAUDE_PROJECT_DIR}` is deliberately absent: it is
# expanded by the host at runtime.
HOST_FRAGMENT_PLACEHOLDERS = ("/PATH/TO/coding-agent-orchestrator", "${PLUGIN_ROOT}")


def package_root_dir() -> Path | None:
    """Locate the installed CAO package root that owns the `hosts/` adapters."""
    source_root = Path(__file__).resolve().parents[1]
    candidates = [
        source_root,
        Path(sysconfig.get_path("data")) / "share" / "coding-agent-orchestrator",
    ]
    for candidate in candidates:
        if (candidate / "hosts").is_dir():
            return candidate
    return None


def render_host_fragment(fragment: dict[str, Any], root: Path) -> dict[str, Any]:
    """Resolve install-location placeholders inside a host fragment."""
    text = json.dumps(fragment)
    for token in HOST_FRAGMENT_PLACEHOLDERS:
        text = text.replace(token, str(root))
    return json.loads(text)


def merge_hook_config(existing: dict[str, Any], incoming: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Deep-merge incoming host hooks into an existing host settings file.

    Existing entries are never removed or rewritten. An incoming command that is
    already registered for the same event is skipped, so the merge is idempotent
    and safe to re-run.
    """
    merged = json.loads(json.dumps(existing)) if isinstance(existing, dict) else {}
    changed = False
    for event, entries in (incoming.get("hooks") or {}).items():
        if not isinstance(entries, list):
            continue
        bucket = merged.setdefault("hooks", {}).setdefault(event, [])
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for inner in entry.get("hooks") or []:
                if not isinstance(inner, dict):
                    continue
                if any(inner.get("command") == h.get("command") for e in bucket for h in (e.get("hooks") or [])):
                    continue
                matcher = entry.get("matcher")
                target = next((e for e in bucket if e.get("matcher") == matcher), None)
                if target is None:
                    target = {"hooks": []}
                    if matcher:
                        target["matcher"] = matcher
                    bucket.append(target)
                target.setdefault("hooks", []).append(json.loads(json.dumps(inner)))
                changed = True
    return merged, changed


def host_adapter_status(repo: Path, host: str, root: Path | None = None) -> dict[str, Any]:
    """Report whether a host adapter is wired into the project."""
    meta = HOST_ADAPTER_META.get(host)
    if not meta:
        return {"host": host, "installed": False, "reason": "unsupported host"}
    info = {"host": host, "session_end_supported": host != "codex"}
    if host == "codex":
        info["limitations"] = ["No verified native SessionEnd hook; Stop records turn end only. Use `cao handoff --end-session` on actual exit."]
    root = root or package_root_dir()
    if meta.get("install") == "pi-cli":
        target = repo / meta["target"]
        try:
            settings = load_json(target, {}) or {}
        except (OSError, ValueError):
            settings = {}
        entries = settings.get("packages") or []
        packages = [str(p) for p in entries]
        sources = [p if isinstance(p, str) else str(p.get("source", ""))
                   for p in entries if isinstance(p, (str, dict))]
        installed = any("coding-agent-orchestrator" in source for source in sources)
        # A local Pi package records its checkout path, whose folder need not
        # share the package name (for example a checkout named `cao`).
        if root and not installed:
            for source in sources:
                try:
                    candidate = Path(source).expanduser()
                    if not candidate.is_absolute():
                        # Pi resolves local package entries relative to .pi/.
                        candidate = target.parent / candidate
                    installed = candidate.resolve() == root.resolve()
                except (OSError, ValueError, RuntimeError):
                    continue
                if installed:
                    break
        subagents = settings.get('subagents') or {}
        child_extensions = subagents.get('defaultSubagentOnlyExtensions', []) if isinstance(subagents, dict) else []
        child_hook = str(root / 'hosts/pi/coding-orchestrator.ts') if root else ''
        child_ready = isinstance(child_extensions, list) and child_hook in child_extensions
        return {**info, "installed": installed, "target": meta["target"], "packages": packages,
                'child_hooks_installed': child_ready,
                'child_hooks_next': None if child_ready else 'Run cao host install --host pi before restarting; foreground children need the CAO adapter explicitly.'}
    if root is None:
        return {**info, "installed": False, "target": meta["target"], "reason": "adapter files unavailable"}
    fragment_path = root / meta["fragment"]
    if not fragment_path.is_file():
        return {**info, "installed": False, "target": meta["target"], "reason": "fragment missing"}
    target = repo / meta["target"]
    try:
        fragment = render_host_fragment(json.loads(fragment_path.read_text(encoding="utf-8")), root)
    except (OSError, ValueError) as exc:
        return {**info, "installed": False, "target": meta["target"], "reason": str(exc)}
    if not target.is_file():
        return {**info, "installed": False, "target": str(target), "reason": "host settings file missing"}
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {**info, "installed": False, "target": str(target), "reason": str(exc)}
    _, changed = merge_hook_config(existing, fragment)
    return {**info, "installed": not changed, "target": str(target)}


def install_host_adapter(repo: Path, host: str) -> dict[str, Any]:
    """Wire one host adapter into the project. Idempotent and non-destructive."""
    meta = HOST_ADAPTER_META.get(host)
    if not meta:
        return {"host": host, "status": "error", "reason": f"unsupported host: {host}"}
    root = package_root_dir()
    if root is None:
        return {"host": host, "status": "error", "reason": "CAO host adapter files are unavailable in this installation"}

    if meta.get("install") == "pi-cli":
        if not command_exists("pi"):
            return {
                "host": host,
                "status": "skipped",
                "reason": "`pi` CLI not found on PATH",
                "next": "pi install --local <path-to-coding-agent-orchestrator> --approve",
            }
        result = subprocess.run(
            # `host install` explicitly requests this local adapter registration.
            # Pi 1.1 needs command-scoped approval to write local package config;
            # this does not persist global project trust.
            ["pi", "install", "--local", str(root), "--approve"],
            cwd=repo, text=True, capture_output=True, timeout=120,
        )
        if result.returncode == 0:
            target = repo / meta['target']
            try:
                settings = load_json(target, {})
                if not isinstance(settings, dict):
                    raise ValueError('Pi settings must be an object')
                subagents = settings.setdefault('subagents', {})
                if not isinstance(subagents, dict):
                    raise ValueError('Pi subagents settings must be an object')
                if 'defaultSubagentOnlyExtensions' not in subagents:
                    # A new project-level key shadows the user's global default.
                    # Carry the effective inherited list forward before appending CAO.
                    agent_dir = Path(os.environ.get('PI_CODING_AGENT_DIR') or Path.home() / '.pi/agent').expanduser()
                    user_settings = load_json(agent_dir / 'settings.json', {})
                    inherited = user_settings.get('subagents', {}) if isinstance(user_settings, dict) else {}
                    inherited = inherited.get('defaultSubagentOnlyExtensions', []) if isinstance(inherited, dict) else []
                    subagents['defaultSubagentOnlyExtensions'] = inherited
                extensions = subagents.setdefault('defaultSubagentOnlyExtensions', [])
                if not isinstance(extensions, list) or any(not isinstance(p, str) for p in extensions):
                    raise ValueError('Pi child extension defaults must be a list of paths')
                adapter = str(root / 'hosts/pi/coding-orchestrator.ts')
                if adapter not in extensions:
                    extensions.append(adapter)
                    save_json(target, settings)
            except (OSError, ValueError, TypeError) as exc:
                return {'host': host, 'status': 'error', 'target': meta['target'],
                        'reason': 'Cannot configure CAO child hooks: ' + str(exc)}
        return {
            "host": host,
            "status": "installed" if result.returncode == 0 else "error",
            "target": meta["target"],
            "command": f"pi install --local {shlex.quote(str(root))} --approve",
            "exit_code": result.returncode,
            "stderr": result.stderr.strip()[-500:] or None,
        }

    fragment_path = root / meta["fragment"]
    target = repo / meta["target"]
    try:
        fragment = render_host_fragment(json.loads(fragment_path.read_text(encoding="utf-8")), root)
    except (OSError, ValueError) as exc:
        return {"host": host, "status": "error", "reason": str(exc)}

    try:
        existing = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    except (OSError, ValueError) as exc:
        return {"host": host, "status": "error", "target": str(target), "reason": f"cannot read existing host settings: {exc}"}
    if not isinstance(existing, dict):
        return {"host": host, "status": "error", "target": str(target), "reason": "existing host settings is not an object"}

    merged, changed = merge_hook_config(existing, fragment)
    if not changed:
        return {"host": host, "status": "already_present", "target": str(target)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        save_json(target, merged)
    except OSError as exc:
        return {"host": host, "status": "error", "target": str(target), "reason": str(exc)}
    return {"host": host, "status": "installed", "target": str(target)}


def install_host_adapters(repo: Path, hosts: list[str]) -> dict[str, Any]:
    results = [install_host_adapter(repo, host) for host in hosts]
    return {
        "hosts": results,
        "installed": [r["host"] for r in results if r["status"] == "installed"],
        "already_present": [r["host"] for r in results if r["status"] == "already_present"],
        "failed": [r["host"] for r in results if r["status"] in {"error", "skipped"}],
    }
