"""Translate host payloads into shared actions; this module grants no permission."""
from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shlex

import yaml
import skill_runtime

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


def _mapping(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _absolute(base: Path, path: str | Path) -> Path:
    # Keep '..' until resolve() has followed intermediate symlinks. Collapsing it
    # first can turn link/../policy.yaml into a different filesystem target.
    return base.absolute() / str(path).replace("\\", "/")


def governance_targets(repo: Path) -> list[tuple[Path, bool]]:
    """Configured and currently analyzed authority inputs, including external packs."""
    repo = repo.resolve()
    targets = {(_absolute(repo, p), False) for p in GOVERNANCE_FILES}
    targets.update((_absolute(repo, p), True) for p in GOVERNANCE_DIRS)
    manifests = {repo / ".orchestrator/policies/manifest.yaml"}
    orch = _mapping(repo / ".orchestrator/config.yaml").get("orchestrator") or {}
    if not isinstance(orch, dict):
        orch = {}
    policy = orch.get("engineering_policy") or {}
    if isinstance(policy, dict) and policy.get("manifest"):
        manifests.add(_absolute(repo, policy["manifest"]))
    state_paths = {repo / ".orchestrator/execution-state.yaml"}
    for section, key in (("runtime", "execution_state"), ("execution_state", "state_file")):
        config = orch.get(section) or {}
        if isinstance(config, dict) and config.get(key):
            state_paths.add(_absolute(repo, config[key]))
    for state_path in state_paths:
        targets.add((state_path, False))
        analysis = _mapping(state_path).get("analysis") or {}
        if not isinstance(analysis, dict) or not analysis.get("policy_plan_ref"):
            continue
        plan = _mapping(_absolute(repo, analysis["policy_plan_ref"]))
        if plan.get("manifest"):
            manifest = _absolute(repo, plan["manifest"])
            manifests.add(manifest)
            # Keep protecting the last analyzed sources even if the live manifest
            # has changed and a replacement analysis has not yet been attached.
            for source in plan.get("policy_sources") or []:
                if isinstance(source, dict) and source.get("ref"):
                    targets.add((_absolute(manifest.parent, source["ref"]), False))
    manifests.update(path.resolve() for path in list(manifests))
    for manifest in manifests:
        targets.add((manifest, False))
        for pack in _mapping(manifest).get("packs") or []:
            if isinstance(pack, dict) and pack.get("path") and pack.get("enabled", True):
                targets.add((_absolute(manifest.parent, pack["path"]), False))
    # Resolve aliases on both sides, including a protected file that is itself a symlink.
    normalized = {(Path(os.path.abspath(path)), directory) for path, directory in targets}
    normalized.update((path.resolve(), directory) for path, directory in targets)
    return sorted(normalized, key=lambda row: (str(row[0]), row[1]))


def governance_class(repo: Path, path: str, *, cwd: Path | None = None,
                     ancestors: bool = False, targets=None) -> str | None:
    """Classify a literal target without granting a preparation exemption.

    Directory removal/move operations also protect ancestors of authority inputs.
    Ordinary SDD documents and generated projections remain preparation material.
    """
    try:
        target = _absolute(cwd or repo, path)
        candidates = {Path(os.path.abspath(target)), target.resolve()}
        for protected, directory in targets if targets is not None else governance_targets(repo):
            for candidate in candidates:
                if (candidate == protected or directory and protected in candidate.parents
                        or ancestors and candidate in protected.parents):
                    try:
                        return protected.relative_to(repo.resolve()).as_posix()
                    except ValueError:
                        return str(protected)
    except (ValueError, OSError):
        return None  # unresolvable targets fall through to existing conservative handling
    return None


def shell_words(command: str, *, windows: bool | None = None) -> list[str]:
    windows = os.name == "nt" if windows is None else windows
    lexer = shlex.shlex(command, posix=not windows, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    words = list(lexer)
    if windows:
        words = [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'" else w for w in words]
    return words


def shell_governance_paths(command: str, repo: Path, cwd: Path | None = None) -> list[str]:
    targets = governance_targets(repo)
    try:
        words = shell_words(command)
    except ValueError:
        words = []
    # Also inspect literal filenames inside script arguments and option=value forms.
    literals = re.findall(r"[^\s\"'`=;|<>()\[\]{},]+", command)
    bases = {cwd or repo}
    for index, word in enumerate(words[:-1]):
        if word in {"cd", "pushd"}:
            bases.update(_absolute(base, words[index + 1]) for base in list(bases))
    parent_operation = any(Path(w).name.lower() in {
        "rm", "rmdir", "mv", "move", "rename", "ren", "remove-item", "move-item", "rename-item",
    } for w in words)
    found = set()
    for word in words[1:] + literals:
        if not word or word.startswith("-") or word in {";", "&&", "||", "|", ">", ">>"}:
            continue
        # A glob targeting an authority directory must not hide its literal prefix.
        literal = re.split(r"[*?\[]", word, maxsplit=1)[0] if parent_operation else word
        if not literal:
            continue
        for base in bases:
            match = governance_class(repo, literal, cwd=base, ancestors=parent_operation, targets=targets)
            if match:
                found.add(match)
    return sorted(found)


def shell_action(command: str, repo: Path, cwd: Path | None = None) -> str:
    action = _shell_action_base(command, cwd or repo)
    # A mutating shell command that names a governance input is escalated, never downgraded.
    # Read-only commands (cat/grep) and packaged CLI invocations keep their own classification.
    if action == "mutate_code" and shell_governance_paths(command, repo, cwd):
        return "mutate_governance"
    return action


def _shell_action_base(command: str, repo: Path) -> str:
    if not command.strip():
        return "mutate_code"
    if "$" in command or "`" in command or "\n" in command:
        return "mutate_code"
    try:
        words = shell_words(command)
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
    if entry.resolve() in skill_runtime.front_controllers():
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
    working_dir = (inp.get("workdir") or inp.get("cwd")) if isinstance(inp, dict) else None
    cwd = _absolute(repo, working_dir or raw.get("cwd") or repo)
    paths: list[str] = []
    governance: list[str] = []
    direct = low in {"write", "edit", "apply_patch", "applypatch"} or "write_file" in low or "edit_file" in low
    if direct:
        if isinstance(inp, dict):
            paths = [str(inp[k]) for k in ("file_path", "path", "filename") if inp.get(k)]
        if low in {"apply_patch", "applypatch"}:
            patch = inp if isinstance(inp, str) else str(inp.get("command") or inp.get("patch") or inp.get("input") or "")
            paths += re.findall(r"(?:\+\+\+ b/|--- a/|\*\*\* (?:Update|Add|Delete) File: |\*\*\* Move to: )([^\n]+)", patch)
        targets = governance_targets(repo)
        governance = [g for g in (governance_class(repo, path, cwd=cwd, targets=targets) for path in paths) if g]
        if governance:
            action = "mutate_governance"
        else:
            action = "prepare" if paths and all(not is_code(repo, str(_absolute(cwd, path))) for path in paths) else "mutate_code"
    elif low in {"bash", "shell", "exec_command"}:
        command = str(inp.get("command") or inp.get("cmd") or "") if isinstance(inp, dict) else str(inp)
        action = shell_action(command, repo, cwd)
        governance = shell_governance_paths(command, repo, cwd) if action == "mutate_governance" else []
    elif low in {"read", "read_file", "view_image", "list", "glob", "grep"}:
        action = "read"
    elif low == "write_stdin" and isinstance(inp, dict) and not inp.get("chars"):
        action = "read"
    else:
        action = "mutate_code"  # adapters must explicitly identify read-only custom tools
    return {"tool": name, "input": inp, "paths": paths, "direct_file": direct,
            "action": action, "mutating": action != "read",
            "governance_paths": list(dict.fromkeys(governance))}
