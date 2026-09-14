#!/usr/bin/env python3
"""Project-level activation stubs for Coding Agent Orchestrator.

The Skill itself is intentionally lazy-loaded. These tiny, host-visible project instruction
blocks solve the cold-start activation problem for vague prompts such as "开始", "继续",
"start", "continue", or "resume" without copying the whole Skill into host config.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import skill_runtime  # noqa: E402  (needs the path bootstrap above)
START = "<!-- coding-agent-orchestrator:start -->"
END = "<!-- coding-agent-orchestrator:end -->"


def _find_installed(repo: Path, filename: str) -> Path | None:
    """Locate a Skill file inside the repo when this package is not already there.

    The install directory name is a deployment choice, so scan for the file instead of
    assuming a name. Only directories that actually look like this Skill qualify.
    """
    skills = repo / ".agents" / "skills"
    if not skills.is_dir():
        return None
    for candidate in sorted(skills.iterdir()):
        if not candidate.is_dir():
            continue
        target = candidate / filename
        if target.is_file() and (candidate / "scripts" / "coding_orchestrator.py").is_file():
            return target
    return None


def skill_ref(repo: Path) -> str:
    repo = repo.resolve()
    skill = (ROOT / "SKILL.md").resolve()
    try:
        return skill.relative_to(repo).as_posix()
    except ValueError:
        installed = _find_installed(repo, "SKILL.md")
        if installed is not None:
            return installed.relative_to(repo).as_posix()
        return skill.as_posix()


def cli_ref(repo: Path) -> str:
    repo = repo.resolve()
    cli = (ROOT / "coding-orchestrator").resolve()
    try:
        return cli.relative_to(repo).as_posix()
    except ValueError:
        installed = _find_installed(repo, "coding-orchestrator")
        if installed is not None:
            return installed.relative_to(repo).as_posix()
        return cli.as_posix()


def _block(repo: Path, host: str) -> str:
    host_note = {
        "claude-code": "This project also uses Claude Code lifecycle hooks when installed.",
        "codex": "This project also uses Codex lifecycle hooks when installed.",
        "generic": "Use the repository's available host integration when present.",
    }.get(host, "")
    if skill_runtime.is_in_repo(repo):
        load_line = (f"For repository coding work, **load and follow `{skill_ref(repo)}` before acting**."
                     " This includes starting, resuming, continuing, implementing, debugging, reviewing,"
                     " and verifying work.")
        start_line = (f"run `{cli_ref(repo)} --repo . start` and follow its deterministic route"
                      " instead of guessing what \"start\" means.")
        scope_note = ""
    else:
        # User-level install. The block is written into a file the repository usually commits,
        # so it must stay free of machine-specific absolute paths.
        load_line = ("For repository coding work, **load and follow the Coding Agent Orchestrator Skill"
                     " before acting**. This includes starting, resuming, continuing, implementing,"
                     " debugging, reviewing, and verifying work.")
        start_line = ("run the Skill's `start` route (`coding-orchestrator --repo . start`, resolved"
                      " through your installed Skill) instead of guessing what \"start\" means.")
        scope_note = ("- This Skill is installed at user level, outside this repository, so no"
                      " repository-relative path is recorded here on purpose: an absolute path would be"
                      " machine-specific and would not work for teammates. Resolve the front controller"
                      " through your Skill installation; `where` prints it for this machine.\n")
    continuation = ("When the user gives a short continuation prompt such as `开始`, `继续`, `接着做`,"
                    " `start`, `continue`, or `resume`, and no more specific intent conflicts, ")
    return f"""{START}
## Coding Agent Orchestrator activation

{load_line}

{continuation}{start_line}

- `start` performs the Bootstrap Guard automatically. If `.orchestrator/config.yaml` is missing, it runs Safe Auto init once, then continue the same user request without requiring a restart.
- `start` resumes an active work item, surfaces blockers, discovers a single high-confidence requirement for intake, asks for a requirement when none exists, and refuses to pick randomly when multiple candidates exist.
- Never create production code or a fake work item merely because the user said "start".
- Do **not** preload all Skill references. Follow the Skill's Lazy Reference Loading Contract and normally read only the current-stage reference(s).
- Explicit user instructions take precedence over this project workflow.
{scope_note}{host_note}
{END}""".strip()


def merge_managed_block(path: Path, block: str, apply: bool = True) -> dict[str, Any]:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if START in existing and END in existing:
        before, rest = existing.split(START, 1)
        _, after = rest.split(END, 1)
        merged = before.rstrip() + ("\n\n" if before.strip() else "") + block + after
        status = "updated" if merged != existing else "preserved"
    else:
        sep = "\n\n" if existing.strip() else ""
        merged = existing.rstrip() + sep + block + "\n"
        status = "updated" if path.exists() else "created"
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8")
    return {"path": str(path), "status": status, "managed": True}


def install(repo: Path, hosts: list[str] | None = None, apply: bool = True) -> dict[str, Any]:
    repo = repo.resolve()
    hosts = hosts or []
    result: dict[str, Any] = {
        "skill_ref": skill_ref(repo),
        "generic": merge_managed_block(repo / "AGENTS.md", _block(repo, "generic"), apply),
        "hosts": {},
    }
    if "claude-code" in hosts:
        result["hosts"]["claude-code"] = merge_managed_block(repo / "CLAUDE.md", _block(repo, "claude-code"), apply)
    if "codex" in hosts:
        # Codex consumes the generic AGENTS.md activation block; hooks are installed separately.
        result["hosts"]["codex"] = {"path": str(repo / "AGENTS.md"), "status": result["generic"]["status"], "managed": True}
    if "pi" in hosts:
        # Pi activation is handled at session_start by the extension; AGENTS.md remains a portable fallback.
        result["hosts"]["pi"] = {"path": str(repo / "AGENTS.md"), "status": result["generic"]["status"], "managed": True, "session_extension": True}
    return result


def has_managed_block(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return START in text and END in text
