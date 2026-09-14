#!/usr/bin/env python3
"""Project-level activation stubs for Coding Agent Orchestrator.

The Skill itself is intentionally lazy-loaded. These tiny, host-visible project instruction
blocks solve the cold-start activation problem for vague prompts such as "开始", "继续",
"start", "continue", or "resume" without copying the whole Skill into host config.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- coding-agent-orchestrator:start -->"
END = "<!-- coding-agent-orchestrator:end -->"


def skill_ref(repo: Path) -> str:
    repo = repo.resolve()
    skill = (ROOT / "SKILL.md").resolve()
    try:
        return skill.relative_to(repo).as_posix()
    except ValueError:
        canonical = repo / ".agents" / "skills" / "coding-agent-orchestrator" / "SKILL.md"
        if canonical.exists():
            return canonical.relative_to(repo).as_posix()
        return skill.as_posix()


def cli_ref(repo: Path) -> str:
    repo = repo.resolve()
    cli = (ROOT / "coding-orchestrator").resolve()
    try:
        return cli.relative_to(repo).as_posix()
    except ValueError:
        canonical = repo / ".agents" / "skills" / "coding-agent-orchestrator" / "coding-orchestrator"
        if canonical.exists():
            return canonical.relative_to(repo).as_posix()
        return cli.as_posix()


def _block(repo: Path, host: str) -> str:
    skill = skill_ref(repo)
    cli = cli_ref(repo)
    host_note = {
        "claude-code": "This project also uses Claude Code lifecycle hooks when installed.",
        "codex": "This project also uses Codex lifecycle hooks when installed.",
        "generic": "Use the repository's available host integration when present.",
    }.get(host, "")
    return f"""{START}
## Coding Agent Orchestrator activation

For repository coding work, **load and follow `{skill}` before acting**. This includes starting, resuming, continuing, implementing, debugging, reviewing, and verifying work.

When the user gives a short continuation prompt such as `开始`, `继续`, `接着做`, `start`, `continue`, or `resume`, and no more specific intent conflicts, run `{cli} --repo . start` and follow its deterministic route instead of guessing what "start" means.

- `start` performs the Bootstrap Guard automatically. If `.orchestrator/config.yaml` is missing, it runs Safe Auto init once, then continue the same user request without requiring a restart.
- `start` resumes an active work item, surfaces blockers, discovers a single high-confidence requirement for intake, asks for a requirement when none exists, and refuses to pick randomly when multiple candidates exist.
- Never create production code or a fake work item merely because the user said "start".
- Do **not** preload all Skill references. Follow the Skill's Lazy Reference Loading Contract and normally read only the current-stage reference(s).
- Explicit user instructions take precedence over this project workflow.
{host_note}
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
