#!/usr/bin/env python3
"""Locate this Skill's runtime, wherever it happens to be installed.

The Skill's *directory name* is a deployment choice and carries no meaning: the same
package installs as `.agents/skills/coding-agent-orchestrator/` in one project and
`.agents/skills/orchestrating-sdd-coding/` in another. Nothing may hardcode it, because a
hardcoded path that does not exist forces the agent to guess and retry.

Everything that needs to name the Skill, the front controller, or a ready-to-run command
resolves it from this module, which derives the location from its own file path.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_FILE = "SKILL.md"
POSIX_CLI = "coding-orchestrator"
WINDOWS_CLI = "coding-orchestrator.cmd"
PYTHON_ENTRY = Path("scripts") / "coding_orchestrator.py"

SUBCOMMANDS = (
    "init", "discover", "doctor", "status", "start", "intake", "resume", "check", "verify", "where",
    "readiness", "progress", "transition", "native-sync",
)


def skill_root() -> Path:
    return SKILL_ROOT


def skill_file() -> Path:
    return SKILL_ROOT / SKILL_FILE


def cli_path() -> Path:
    return SKILL_ROOT / POSIX_CLI


def windows_cli_path() -> Path:
    return SKILL_ROOT / WINDOWS_CLI


def front_controllers() -> tuple[Path, Path]:
    """Packaged entry points shared by launcher selection and tool classification."""
    return cli_path(), windows_cli_path()


def python_entry() -> Path:
    return SKILL_ROOT / PYTHON_ENTRY


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and (path.stat().st_mode & 0o111) != 0
    except OSError:
        return False


def ref(repo: Path, target: Path) -> str:
    """Repo-relative path when the target lives inside the repo, otherwise absolute."""
    try:
        return target.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(target.resolve())


def is_in_repo(repo: Path) -> bool:
    """Whether this Skill package is installed inside the repository.

    A user-level install lives outside the repo, so no repository-relative path exists and
    absolute paths must not be written into files the repository shares with a team.
    """
    try:
        SKILL_ROOT.relative_to(repo.resolve())
        return True
    except ValueError:
        return False


def quote(text: str) -> str:
    """Quote a path for a shell command, but only when it actually needs quoting.

    User-level installs routinely land in directories with spaces (`C:\\Users\\Jane Doe\\...`),
    and an unquoted path makes a printed command fail the moment it is pasted.
    """
    if not any(ch.isspace() or ch in "\"'\\$&|;<>()*?[]{}~!#^" for ch in text):
        return text
    if os.name == "nt":
        return f'"{text}"'
    return "'" + text.replace("'", "'\\''") + "'"


def launcher(repo: Path) -> tuple[str, Path]:
    """Pick the runnable front controller for this platform, with a Python fallback.

    Returns `(invocation_prefix, path)` where the prefix is normalized to a repo-relative or
    absolute path and quoted when needed, so it can be pasted into a shell unchanged.
    """
    if os.name == "nt":
        windows_cli = windows_cli_path()
        if windows_cli.is_file():
            return quote(ref(repo, windows_cli)), windows_cli
    else:
        cli = cli_path()
        if _is_executable(cli):
            return quote(ref(repo, cli)), cli
    return f"python3 {quote(ref(repo, python_entry()))}", python_entry()


def command(repo: Path, subcommand: str) -> str:
    prefix, _ = launcher(repo)
    return f"{prefix} --repo . {subcommand}".strip()


def recovery_command(repo: Path, subcommand: str) -> str:
    """Render a pasteable command for a concrete project from any caller cwd.

    Recovery output is runtime text, not shared project configuration, so an
    absolute packaged entrypoint is intentional. It avoids assuming PATH or a
    repository-root working directory while retaining platform-specific launchers.
    """
    repo = Path(repo).resolve()
    if os.name == "nt" and windows_cli_path().is_file():
        prefix = quote(str(windows_cli_path()))
    elif _is_executable(cli_path()):
        prefix = quote(str(cli_path()))
    else:
        prefix = f"python3 {quote(str(python_entry()))}"
    return f"{prefix} --repo {quote(str(repo))} {subcommand}".strip()


def describe(repo: Path | None = None) -> dict[str, Any]:
    """Everything an agent needs in order to stop searching for the runtime."""
    cli = cli_path()
    info: dict[str, Any] = {
        "skill_root": str(SKILL_ROOT),
        "skill_dir_name": SKILL_ROOT.name,
        "skill_file": str(skill_file()),
        "cli": str(cli),
        "cli_executable": _is_executable(cli),
        "windows_cli": str(windows_cli_path()),
        "python_entry": str(python_entry()),
    }
    if repo is not None:
        repo = repo.resolve()
        prefix, _ = launcher(repo)
        info["repo"] = str(repo)
        info["in_repo"] = is_in_repo(repo)
        info["skill_ref"] = ref(repo, skill_file())
        info["cli_ref"] = ref(repo, cli)
        info["prefix"] = prefix
        info["commands"] = {name: command(repo, name) for name in SUBCOMMANDS}
    return info
