"""Translate host payloads into shared actions; this module grants no permission."""
from __future__ import annotations

from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parents[1]
CODE_SUFFIXES = {".java", ".kt", ".kts", ".py", ".go", ".rs", ".ts", ".tsx", ".js", ".jsx", ".cs", ".cpp", ".c", ".h", ".sql", ".sh"}
SOURCE_ROOTS = {"src", "app", "lib", "packages", "services", "modules"}
GOVERNANCE_SCRIPTS = {"coding_orchestrator.py", "execution_state_manager.py", "context_plane.py",
                      "session_context.py", "semantic_intake_pipeline.py", "fact_extractor.py",
                      "fact_resolver.py", "decision_engine.py", "policy_engine.py", "verification_planner.py"}

# Governance inputs define what is authoritative and must not be self-editable by an agent.
# SDD artifacts under docs/ and openspec/ stay writable on purpose: authoring requirements is
# legitimate work. Authority config, policy sources, execution state, and requirement identity
# are what *grant* authority, so rewriting them would let an agent grant itself authority.
GOVERNANCE_FILES = {
    ".orchestrator/config.yaml",
    ".orchestrator/enforcement.yaml",
    ".orchestrator/execution-state.yaml",
}
GOVERNANCE_DIRS = {
    ".orchestrator/policies",
    ".orchestrator/requirements",
}
GOVERNANCE_TOKENS = tuple(sorted(GOVERNANCE_FILES | GOVERNANCE_DIRS))


def extract_tool(raw: dict) -> tuple[str, object]:
    name = str(raw.get("tool_name") or raw.get("toolName") or raw.get("tool") or "")
    for key in ("tool_input", "input", "args"):
        if raw.get(key) is not None:
            return name, raw[key]
    return name, {}


def is_code(repo: Path, path: str) -> bool:
    try:
        target = Path(path)
        target = target.resolve() if target.is_absolute() else (repo / target).resolve()
        rel = target.relative_to(repo.resolve())
    except ValueError:
        return True  # unresolved or outside-repository targets get no preparation exemption
    if rel.parts and rel.parts[0] in SOURCE_ROOTS:
        return True
    if rel.parts and rel.parts[0] in {".orchestrator", "openspec", ".openspec", "docs"} and rel.suffix.lower() not in CODE_SUFFIXES:
        return False
    if rel.suffix.lower() in {".md", ".txt", ".rst"}:
        return False
    return True  # unknown formats may change production behavior, including build/config files


def governance_class(repo: Path, path: str) -> str | None:
    """Return the governance area for a path, or None when the path is not a governance input.

    Only files/directories that *grant* authority are governed. Generated outputs such as
    work-items, evidence, session, and context projections are not, and neither are SDD
    artifacts under docs/ or openspec/, which agents are expected to author.
    """
    try:
        target = Path(path)
        target = target.resolve() if target.is_absolute() else (repo / target).resolve()
        rel = target.relative_to(repo.resolve())
    except (ValueError, OSError):
        return None  # unresolvable targets fall through to existing conservative handling
    rel_posix = rel.as_posix()
    if rel_posix in GOVERNANCE_FILES:
        return rel_posix
    if len(rel.parts) >= 2 and f"{rel.parts[0]}/{rel.parts[1]}" in GOVERNANCE_DIRS:
        return f"{rel.parts[0]}/{rel.parts[1]}"
    return None


def mentions_governance(command: str) -> bool:
    return any(token in command for token in GOVERNANCE_TOKENS)


def shell_action(command: str, repo: Path) -> str:
    action = _shell_action_base(command, repo)
    # A mutating shell command that names a governance input is escalated, never downgraded.
    # Read-only commands (cat/grep) and packaged CLI invocations keep their own classification.
    if action == "mutate_code" and mentions_governance(command):
        return "mutate_governance"
    return action


def _shell_action_base(command: str, repo: Path) -> str:
    if not command.strip():
        return "mutate_code"
    if "$" in command or "`" in command or "\n" in command:
        return "mutate_code"
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return "mutate_code"
    if not words or any(w and all(c in ";&|<>()" for c in w) for w in words):
        return "mutate_code"
    binary = Path(words[0]).name.lower()
    if binary in {"python", "python3", "python.exe", "python3.exe"} and len(words) > 1:
        script = Path(words[1])
        script = script if script.is_absolute() else repo / script
        if script.resolve() == ROOT / "coding-orchestrator":
            return "prepare"
        if script.resolve().parent == ROOT / "scripts" and script.name in GOVERNANCE_SCRIPTS:
            return "prepare"
        if words[1:3] == ["-m", "pytest"]:
            return "read"
    entry = Path(words[0])
    entry = entry if entry.is_absolute() else repo / entry
    if entry.resolve() == ROOT / "coding-orchestrator":
        return "prepare"
    if binary in {"cat", "head", "tail", "less", "more", "rg", "grep", "ls", "pwd", "echo", "printf", "pytest"}:
        return "read"
    if binary == "git" and len(words) > 1 and words[1] in {"status", "diff", "log", "show", "ls-files", "rev-parse"}:
        if not any(w.startswith("--output") for w in words):
            return "read"
    if binary in {"mvn", "gradle", "gradlew"} and any(w in {"test", "verify", "check"} for w in words[1:]):
        return "read"
    if binary in {"npm", "pnpm", "yarn"} and (words[1:2] == ["test"] or words[1:3] == ["run", "test"]):
        return "read"
    return "mutate_code"


def describe(raw: dict, repo: Path) -> dict:
    name, inp = extract_tool(raw)
    low = name.lower().split(".")[-1]
    paths: list[str] = []
    governance: list[str] = []
    direct = low in {"write", "edit", "apply_patch", "applypatch"} or "write_file" in low or "edit_file" in low
    if direct:
        if isinstance(inp, dict):
            paths = [str(inp[k]) for k in ("file_path", "path", "filename") if inp.get(k)]
        if low in {"apply_patch", "applypatch"}:
            patch = inp if isinstance(inp, str) else str(inp.get("command") or inp.get("patch") or inp.get("input") or "")
            paths += re.findall(r"(?:\+\+\+ b/|--- a/|\*\*\* (?:Update|Add|Delete) File: |\*\*\* Move to: )([^\n]+)", patch)
        governance = [g for g in (governance_class(repo, path) for path in paths) if g]
        if governance:
            action = "mutate_governance"
        else:
            action = "prepare" if paths and all(not is_code(repo, path) for path in paths) else "mutate_code"
    elif low in {"bash", "shell", "exec_command"}:
        command = str(inp.get("command") or inp.get("cmd") or "") if isinstance(inp, dict) else str(inp)
        action = shell_action(command, repo)
        governance = [token for token in GOVERNANCE_TOKENS if token in command] if action == "mutate_governance" else []
    elif low in {"read", "read_file", "view_image", "list", "glob", "grep"}:
        action = "read"
    elif low == "write_stdin" and isinstance(inp, dict) and not inp.get("chars"):
        action = "read"
    else:
        action = "mutate_code"  # adapters must explicitly identify read-only custom tools
    return {"tool": name, "input": inp, "paths": paths, "direct_file": direct,
            "action": action, "mutating": action != "read",
            "governance_paths": list(dict.fromkeys(governance))}
