#!/usr/bin/env python3
"""Detect the *currently running* coding-agent host for Safe Auto bootstrap.

Host selection is runtime-oriented, not repository-marker-oriented. A repository may retain
configuration for several agents over time; that must not cause Self-Bootstrap to install all
of them or pick a stale one.

Priority for ``auto`` selection:
  1. explicit orchestrator override environment (for embedding/tests),
  2. nearest recognized process in the current process ancestry,
  3. host-specific runtime environment signals,
  4. Claude Code fallback.

The fallback is deliberate product policy: if the current host cannot be proven, install the
Claude Code adapter rather than guessing from binaries or repository marker directories.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

HOSTS = {"claude-code", "codex", "pi"}
TRUTHY = {"1", "true", "yes", "on"}
OVERRIDE_KEYS = ("CODING_ORCHESTRATOR_HOST", "ORCHESTRATOR_HOST")


def _normalize_host(value: str | None) -> str | None:
    if not value:
        return None
    low = value.strip().lower()
    aliases = {
        "claude": "claude-code",
        "claude_code": "claude-code",
        "claude-code": "claude-code",
        "codex": "codex",
        "openai-codex": "codex",
        "pi": "pi",
        "pi-agent": "pi",
        "pi-coding-agent": "pi",
    }
    return aliases.get(low)


def _process_chain(max_depth: int = 10) -> list[dict[str, Any]]:
    """Return nearest-parent-first process commands when the platform exposes ``ps``.

    Failure is intentionally silent: environment signals and the Claude fallback still provide
    deterministic behavior on platforms where process ancestry is unavailable.
    """
    chain: list[dict[str, Any]] = []
    pid = os.getppid()
    seen: set[int] = set()
    for _ in range(max_depth):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)
        try:
            out = subprocess.check_output(
                ["ps", "-o", "ppid=", "-o", "command=", "-p", str(pid)],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            break
        if not out:
            break
        parts = out.split(None, 1)
        try:
            ppid = int(parts[0])
        except Exception:
            break
        command = parts[1] if len(parts) > 1 else ""
        chain.append({"pid": pid, "ppid": ppid, "command": command})
        pid = ppid
    return chain


def _host_from_command(command: str) -> str | None:
    """Recognize an Agent process without matching arbitrary shell-script text."""
    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except Exception:
        tokens = command.split()
    if not tokens:
        return None
    exe = Path(tokens[0]).name.lower()
    exe = re.sub(r"\.(exe|cmd|bat)$", "", exe)

    if exe == "pi":
        return "pi"
    if exe == "codex":
        return "codex"
    if exe == "claude":
        return "claude-code"

    # JS runtimes commonly host the actual coding-agent CLI. Inspect only their immediate
    # argv, never a generic shell's full script string.
    if exe in {"node", "nodejs", "bun", "deno"}:
        args = " ".join(tokens[1:5]).lower()
        if "pi-coding-agent" in args or "@mariozechner/pi-coding-agent" in args:
            return "pi"
        if "openai/codex" in args or re.search(r"(?:^|[\/])codex(?:[\/]|$)", args):
            return "codex"
        if "claude-code" in args or "@anthropic-ai/claude-code" in args:
            return "claude-code"
    return None


def detect_current_host(
    *,
    env: Mapping[str, str] | None = None,
    process_chain: Sequence[Mapping[str, Any] | str] | None = None,
) -> dict[str, Any]:
    """Detect the current Agent host with evidence and a deterministic Claude fallback."""
    env = env if env is not None else os.environ

    for key in OVERRIDE_KEYS:
        host = _normalize_host(env.get(key))
        if host:
            return {
                "host": host,
                "confidence": "explicit",
                "source": f"env:{key}",
                "evidence": [f"{key}={env.get(key)}"],
                "fallback": False,
            }

    chain = list(process_chain) if process_chain is not None else _process_chain()
    for idx, entry in enumerate(chain):
        command = str(entry.get("command", "")) if isinstance(entry, Mapping) else str(entry)
        host = _host_from_command(command)
        if host:
            return {
                "host": host,
                "confidence": "high",
                "source": "process_ancestry",
                "evidence": [f"depth={idx + 1}", command[:500]],
                "fallback": False,
            }

    # Codex exposes CODEX_THREAD_ID to shell/tool executions. Check it before PI_CODING_AGENT
    # for the rare case where a nested Codex process inherits a parent Pi environment.
    if str(env.get("CODEX_THREAD_ID", "")).strip():
        return {
            "host": "codex",
            "confidence": "high",
            "source": "env:CODEX_THREAD_ID",
            "evidence": ["CODEX_THREAD_ID is set"],
            "fallback": False,
        }

    if str(env.get("PI_CODING_AGENT", "")).strip().lower() in TRUTHY:
        return {
            "host": "pi",
            "confidence": "high",
            "source": "env:PI_CODING_AGENT",
            "evidence": [f"PI_CODING_AGENT={env.get('PI_CODING_AGENT')}"],
            "fallback": False,
        }

    # Some Claude Code launchers expose one or more CLAUDE_CODE_* variables. These are useful
    # positive evidence, but no Claude-specific signal is required because Claude is the fallback.
    claude_keys = sorted(k for k, v in env.items() if k.startswith("CLAUDE_CODE_") and str(v).strip())
    if claude_keys:
        return {
            "host": "claude-code",
            "confidence": "medium",
            "source": "env:CLAUDE_CODE_*",
            "evidence": claude_keys[:8],
            "fallback": False,
        }

    return {
        "host": "claude-code",
        "confidence": "fallback",
        "source": "default_fallback",
        "evidence": ["no reliable current-agent runtime signal detected"],
        "fallback": True,
    }


def select_hosts(requested: str = "auto", *, env: Mapping[str, str] | None = None, process_chain: Sequence[Mapping[str, Any] | str] | None = None) -> dict[str, Any]:
    """Resolve bootstrap host selection while preserving explicit CLI semantics."""
    requested = requested or "auto"
    if requested == "none":
        return {"requested": requested, "hosts": [], "source": "explicit", "confidence": "explicit", "fallback": False, "evidence": ["--host none"]}
    if requested == "all":
        return {"requested": requested, "hosts": ["claude-code", "codex", "pi"], "source": "explicit", "confidence": "explicit", "fallback": False, "evidence": ["--host all"]}
    if requested != "auto":
        host = _normalize_host(requested)
        if host not in HOSTS:
            raise ValueError(f"unsupported host: {requested}")
        return {"requested": requested, "hosts": [host], "source": "explicit", "confidence": "explicit", "fallback": False, "evidence": [f"--host {requested}"]}

    detected = detect_current_host(env=env, process_chain=process_chain)
    return {"requested": "auto", "hosts": [detected["host"]], **detected}


def main() -> int:
    import argparse, json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--requested", default="auto", choices=["auto", "claude-code", "codex", "pi", "all", "none"])
    args = p.parse_args()
    print(json.dumps(select_hosts(args.requested), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
