"""Tool effects, shell classification, and protected-path write policy."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scripts.cao_defaults import ORCH, REQUIREMENT_DIR, SDD_CATALOG
from scripts.cao_instructions import cao_invocation, package_root_dir
from scripts.cao_provider_workflows import workflow_command_identities
from scripts.cao_providers import detect_sdd
from scripts.cao_runtime import config
from scripts.cao_snapshot import verification_snapshot
from scripts.path_guard import inside, patch_targets, repo_file
from scripts.provider_preparation import bmad_preparation
from scripts.route_inputs import writable_input
from scripts.verification_setup import (
    SCAFFOLD_MANIFESTS,
    SCAFFOLD_ROOTS,
    setup_instruction,
    setup_state,
)


def native_output_dirs() -> list[str]:
    """Every provider-owned SDD output directory across the catalog."""
    dirs: list[str] = []
    for meta in SDD_CATALOG.values():
        for out in meta.get("output_dirs") or []:
            if out not in dirs:
                dirs.append(out)
    return dirs


# Compared against lower-cased keys, so every entry must be lower-case too.
WRITE_TOOL_PATH_KEYS = ("path", "file_path", "filepath", "filename", "notebook_path",
                       "destination", "destination_path", "dest", "new_path", "newpath",
                       "target_path", "targetpath", "source_path", "old_path", "oldpath")


# Tools whose target lives in a command string rather than a path argument.
SHELL_TOOLS = frozenset({"bash", "shell", "exec", "command", "exec_command", "shell_command", "run_shell_command", "run_command"})


def _collect_path_strings(node: Any, depth: int = 0) -> list[str]:
    """Recursively collect path-like string values.

    Hosts disagree on where a tool call's target path lives: `tool_input.path`,
    `input.filePath`, `params.path`, or nested further. Reading only the first
    level silently disabled the intake exemption in Pi, whose event shape does
    not match any single-level guess, and that reproduced the intake deadlock.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            k = str(key).lower()
            if isinstance(value, str):
                if k in WRITE_TOOL_PATH_KEYS and value.strip():
                    found.append(value.strip())
            elif isinstance(value, (dict, list)):
                found.extend(_collect_path_strings(value, depth + 1))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_path_strings(item, depth + 1))
    return found


def write_targets_protected_state(repo: Path, raw: dict[str, Any], work: dict | None = None) -> list[str]:
    """Protect both the lexical state link and its resolved destination."""
    repo = repo.resolve()
    state, requirements = repo / ORCH, repo / REQUIREMENT_DIR
    hits = []
    for target in tool_write_targets(raw, repo):
        value = Path(target)
        lexical = Path(os.path.abspath(value if value.is_absolute() else repo / value))
        resolved = lexical.resolve()
        # Resolve repository aliases (e.g. macOS /var) without dereferencing a state link.
        lexical_state = next((ancestor for ancestor in (lexical, *lexical.parents)
                              if ancestor.name == ORCH.name and ancestor.parent.resolve() == repo), None)
        state_target = lexical_state is not None or inside(resolved, state)
        writable = (inside(resolved, repo) and inside(resolved, requirements)
                    and inside(resolved, state)
                    and (lexical_state is None or lexical.is_relative_to(lexical_state / "requirements")))
        writable = writable or writable_input(repo, target, work)
        if state_target and not writable and target not in hits:
            hits.append(target)
    return hits


def protected_state_advice(targets: list[str]) -> str:
    """Point at the command that owns this state instead of a hand edit."""
    joined = " ".join(targets).lower()
    if "requirements" in joined:
        return "Requirement documents are Agent-authored; use `cao requirement next-id` to create one."
    if "/work/" in joined:
        return ("Work Item state is CAO-derived. Use its commands instead: `cao checkpoint --summary ...`, "
                "`cao decision --decision ...`, `cao path assess ...`, `cao sdd workflow <select|execute|complete>`, "
                "`cao verify`, `cao done`.")
    if "config.json" in joined:
        return "Project config is written by `cao verification configure`, `cao init`, `cao language set`, and provider install commands."
    if "index.json" in joined:
        return "`index.json` is regenerated from `work/*.json` after every Work Item mutation; it never needs editing."
    if "sessions/" in joined or "handoffs/" in joined:
        return "Session and Handoff records are written by the host hooks automatically."
    if "lifecycle/" in joined:
        return "`lifecycle/events.jsonl` is an append-only audit log written by CAO."
    return "CAO state is derived from its own commands; hand edits would be overwritten."


def _event_key(raw: Any, *keys: str) -> str:
    """Read a top-level string key case-insensitively.

    Hosts spell the tool name differently (`tool_name`, `toolName`, `tool`), and
    `dict.get("toolname")` misses `toolName`, which silently made every Pi shell
    call look targetless.
    """
    if not isinstance(raw, dict):
        return ""
    lowered = {str(key).lower(): value for key, value in raw.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def tool_cwd(raw: dict, repo: Path | None = None) -> Path | None:
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments") or raw.get("params") or {}
    session_value = _event_key(raw, "cwd", "workdir", "working_directory")
    session = Path(session_value) if session_value else repo
    if session is not None and not session.is_absolute() and repo is not None: session = repo / session
    value = _event_key(payload, "workdir", "cwd", "working_directory")
    if value:
        path = Path(value)
        if path.is_absolute(): return path
        return session / path if session is not None else None
    return session


def tool_effect(raw: dict, repo: Path | None = None) -> dict:
    """One bounded analysis supplies mutation classification and write targets."""
    tool = _event_key(raw, "tool_name", "toolname", "tool", "name").lower()
    if not tool:
        tool = next(iter(_collect_values_for_keys(raw, TOOL_NAME_KEYS)), "").lower()
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments") or raw.get("params") or raw
    cwd = tool_cwd(raw, repo)
    if cwd is not None and cwd.is_absolute(): cwd = cwd.resolve()
    targets, diagnostics = [], []
    effect = "write" if tool in MUTATING_TOOLS or _collect_values_for_keys(payload, MUTATION_PAYLOAD_KEYS) else "read"
    if tool not in SHELL_TOOLS:
        targets = _collect_path_strings(raw)
        patches = _collect_values_for_keys(raw, {"patch", "patch_text", "input"})
        if tool in {"apply_patch", "patch"}:
            patches.extend(_collect_values_for_keys(raw, {"command", "cmd"}))
        if isinstance(payload, str):
            if tool in {"apply_patch", "patch"}: patches.append(payload)
            else: targets.append(payload)
        for patch in patches: targets.extend(patch_targets(patch))
    else:
        commands = _collect_values_for_keys(raw, {"command", "cmd"})
        if isinstance(payload, str): commands.append(payload)
        effect = "read" if commands else "unknown"
        for command in commands:
            directory = cwd or Path(".")
            try:
                segments = _shell_segments(command)
            except ValueError:
                effect = "unknown" if effect != "write" else effect
                diagnostics.append("SHELL_PARSE_UNKNOWN")
                continue
            for tokens in segments:
                if tokens[0] == "cd" and len(tokens) == 2 and not any(c in tokens[1] for c in "$`~"):
                    path = Path(tokens[1]); directory = path if path.is_absolute() else directory / path
                    if directory.is_absolute(): directory = directory.resolve()
                    continue
                if _readonly_tokens(tokens, directory): continue
                operands = shell_write_operands(tokens)
                redirects = any(token in {">", ">>", "&>", ">|", "<>"} for token in tokens)
                known_command = tokens[0] in {"rm", "mv", "cp", "touch", "mkdir", "rmdir", "chmod", "tee", "find", "sed", "git", "diff", "echo", "printf", "cat", "head", "tail", "ls", "grep", "rg"}
                known = known_command and (bool(operands) or redirects)
                if known: effect = "write"
                elif effect != "write": effect = "unknown"
                if not known_command or (tokens[0] == "sed" and sed_effect(tokens)[0] == "unknown"):
                    diagnostics.append("SHELL_EFFECT_UNKNOWN")
                if tokens[0] == "find":
                    for flag in ("-exec", "-execdir", "-ok", "-okdir"):
                        if flag in tokens:
                            nested = [t for t in tokens[tokens.index(flag) + 1:] if t not in {"{}", ";", "+"}]
                            if nested and not _readonly_tokens(nested):
                                if nested[0] not in {"rm", "mv", "cp", "touch", "mkdir", "rmdir", "chmod", "tee", "sed"} or (nested[0] == "sed" and sed_effect(nested)[0] == "unknown"):
                                    diagnostics.append("SHELL_EFFECT_UNKNOWN")
                if any(c in operand for operand in operands for c in "$`~"):
                    diagnostics.append("SHELL_TARGET_UNKNOWN")
                target_cwd = directory
                if tokens[0] == "git":
                    for i, token in enumerate(tokens[:-1]):
                        if token == "-C":
                            path = Path(tokens[i + 1]); target_cwd = path if path.is_absolute() else target_cwd / path
                for operand in operands:
                    path = Path(operand)
                    targets.append(str(path if path.is_absolute() else target_cwd / path))
    # Retain lexical paths as well as resolving links in the protection layer.
    if tool not in SHELL_TOOLS:
        targets = [str(Path(value) if Path(value).is_absolute() or cwd is None else cwd / value) for value in targets]
    if cwd is None and any(not Path(value).is_absolute() for value in targets):
        diagnostics.append("TOOL_CWD_UNKNOWN")
    if repo is not None:
        # Preserve the lexical target for state-link protection, and include the
        # actual target resolved before lexical .. collapses a directory alias.
        actual = [str(Path(value).resolve()) for value in targets]
        targets = actual + [os.path.abspath(value) for value in targets]
    return {"effect": effect, "targets": list(dict.fromkeys(targets)),
            "cwd": str(cwd) if cwd is not None else None, "diagnostics": diagnostics}


def tool_write_targets(raw: dict[str, Any], repo: Path | None = None) -> list[str]:
    return tool_effect(raw, repo)["targets"]


def sed_effect(tokens: list[str]) -> tuple[str, list[str]]:
    expressions, files = [], []
    explicit, unknown, skip = False, False, False
    for i, token in enumerate(tokens[1:], 1):
        if skip: skip = False; continue
        if token in {"-e", "--expression"} and i + 1 < len(tokens):
            expressions.append(tokens[i + 1]); explicit = skip = True
        elif token in {"-f", "--file"}:
            unknown = explicit = skip = True
        elif token.startswith(("--expression=", "-e")):
            expressions.append(token.split("=", 1)[1] if "=" in token else token[2:]); explicit = True
        elif token.startswith(("--file=", "-f")):
            unknown = explicit = True
        elif not token.startswith("-"):
            files.append(token)
    if not explicit and files: expressions.append(files.pop(0))
    targets = []
    if any(token.startswith(("-i", "--in-place")) for token in tokens[1:]): targets.extend(files)
    for expression in expressions:
        # w/W commands, address-prefixed w, and substitution w flags.
        for match in re.finditer(r"(?:^|[;}]|/[gIp0-9]*)\s*(?:[0-9,$!/]+\s*)?[wW]\s+([^;\n]+)", expression):
            targets.append(match.group(1).strip())
        execution = re.search(r"(?:^|;)\s*(?:[0-9,$!/]+\s*)?e(?:\s+([^;\n]+)|$)", expression)
        if execution:
            unknown = True
            if execution.group(1):
                try:
                    for segment in _shell_segments(execution.group(1)):
                        targets.extend(shell_write_operands(segment))
                except ValueError: pass
        if re.search(r"/[^;]*e(?:\s|$)", expression): unknown = True
        # Unsupported expression grammar is unknown rather than proven read.
        if not re.match(r"^(?:[0-9,$!/]+\s*)?(?:[spdqnaic=hrwWyY]|$)", expression): unknown = True
    return ("unknown" if unknown else "write" if targets else "read"), targets


def git_output_targets(tokens: list[str]) -> list[str]:
    targets = []
    for i, token in enumerate(tokens[1:], 1):
        if token in {"--output", "-o"} and i + 1 < len(tokens): targets.append(tokens[i + 1])
        elif token.startswith("--output="): targets.append(token.split("=", 1)[1])
    return targets


def shell_write_operands(tokens: list[str]) -> list[str]:
    """Keep read inputs and command data out of native-output exemptions."""
    redirects = {">", ">>", "&>", ">|", "<>"}
    outputs = [tokens[i + 1] for i, token in enumerate(tokens[:-1]) if token in redirects]
    if tokens[0] in {"echo", "printf", "cat", "head", "tail", "ls", "grep", "rg", "diff"}:
        return outputs
    operands = []
    skip = False
    for token in tokens[1:]:
        if skip:
            skip = False
            continue
        if token in redirects | {"<", "<<", "<<<", "<&", ">&"}:
            skip = True
            continue
        if token.startswith("-") or token in {"2", "&"}:
            continue
        operands.append(token)
    if tokens[0] == "chmod":
        # The symbolic/numeric mode is command data, not a write destination.
        # Reference forms remain conservative; setup allows only literal modes.
        mode = next((t for t in tokens[1:] if re.fullmatch(r"(?:[0-7]{3,4}|[ugoa]*[+=-][rwxXstugo]*(?:,[ugoa]*[+=-][rwxXstugo]*)*)", t)), None)
        if not any(t.startswith("--reference") for t in tokens) and operands and operands[0] == mode:
            operands = operands[1:]
        return outputs + operands
    if tokens[0] == "cp":
        target_dir = next((tokens[i + 1] for i, token in enumerate(tokens[:-1])
                           if token in {"-t", "--target-directory"}), None)
        target_dir = target_dir or next((token.split("=", 1)[1] for token in tokens
                                        if token.startswith("--target-directory=")), None)
        return outputs + ([target_dir] if target_dir else operands[-1:])
    if tokens[0] == "find":
        roots = []
        for token in tokens[1:]:
            if token.startswith("-") or token in {"(", ")", "!"}: break
            roots.append(token)
        if "-delete" in tokens: outputs.extend(roots or ["."])
        for i, token in enumerate(tokens[:-1]):
            if token in {"-fprint", "-fprint0", "-fprintf"}: outputs.append(tokens[i + 1])
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in tokens:
                nested = tokens[tokens.index(flag) + 1:]
                substitutes = "{}" in nested
                nested = [token for token in nested if token not in {"{}", ";", "+"}]
                if nested:
                    outputs.extend(shell_write_operands(nested))
                    if substitutes and not _readonly_tokens(nested): outputs.extend(roots or ["."])
        return outputs
    if tokens[0] == "git": return outputs + git_output_targets(tokens)
    if tokens[0] == "sed": return outputs + sed_effect(tokens)[1]
    return outputs + operands


def verification_baseline_state(repo: Path, work: dict) -> dict:
    cfg = config(repo)
    from scripts.project_verification import project_prerequisite
    project = project_prerequisite(repo, work)
    if project:
        return {"ready": False, "reason": project["reason"], "next": project["instruction"]}
    state = setup_state(cfg)
    if not state["ready"]:
        return {"ready": False, "reason": "VERIFICATION_BASELINE_REQUIRED", "next": setup_instruction(cao_invocation()["command"])}
    anchor = work.get("acceptance") or {}
    if anchor.get("version") != 2 or anchor.get("config_sha256") != verification_snapshot(cfg, repo):
        review = work.get('verification_review') or {}
        if review.get('status') == 'pending' and review.get('config_sha256') == verification_snapshot(cfg, repo):
            return {'ready': False, 'reason': 'VERIFICATION_REVIEW_REQUIRED', 'approval_id': review['id'],
                    'next': f'Present the changed verification baseline for request {review["id"]}. '
                    'Ask once through a bound ask_user_question or a direct user reply (批准验证基线 / approve verification baseline). '
                    f'After approval run `cao reopen --work {work["id"]} --reanchor --reason "Reviewed verification baseline"`.'}
        return {"ready": False, "reason": "VERIFICATION_STANDARD_CHANGED",
                "next": f'Review the configured scripts, then `cao reopen --work {work["id"]} --reanchor --reason "Reviewed verification baseline"` before feature implementation. Tests need not pass yet.'}
    return {"ready": True}


def verification_capability_state(repo: Path, work: dict) -> dict:
    required = (work.get('path_assessment') or {}).get('required_gates', [])
    scripts = setup_state(config(repo))['scripts']
    if 'security_verification' in required and not any(
            script['required'] and 'security_verification' in script['gates'] for script in scripts):
        return {'ready': False, 'reason': 'VERIFICATION_GATE_UNBOUND',
                'next': 'The selected risk requires security verification before implementation. '
                'Configure a real security check with `cao verification configure --security-script <wrapper>`; '
                'preserve existing test/general declarations with --replace when needed. The same test wrapper may declare both capabilities. '
                'Review and reanchor a changed baseline, then continue the existing native workflow.'}
    return {'ready': True}


def verification_scaffold_write(repo: Path, raw: dict) -> bool:
    analysis = tool_effect(raw, repo)
    targets = [] if analysis["diagnostics"] else analysis["targets"]
    if _event_key(raw, "tool_name", "toolname", "tool", "name").lower() in SHELL_TOOLS:
        try:
            commands = _collect_values_for_keys(raw, {"command", "cmd"})
            payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments")
            if isinstance(payload, str):
                commands.append(payload)
            for command in commands:
                for tokens in _shell_segments(command):
                    if tokens[0] == "chmod":
                        if (len(tokens) < 3 or tokens[1] not in {"+x", "u+x", "a+x", "ug+x", "700", "750", "755"}
                                or any(t.startswith("-") and t != "--" for t in tokens[2:])):
                            return False
                        if not all(repo_file(repo, p).is_file() for p in targets):
                            return False
        except (ValueError, OSError, RuntimeError):
            return False
    if targets:
        valid = True
        for value in targets:
            try:
                path = repo_file(repo, value)
                relative = path.relative_to(repo.resolve()).as_posix()
                allowed = relative in SCAFFOLD_MANIFESTS or any(inside(path, repo.resolve() / root) and
                          (repo.resolve() / root).resolve() == repo.resolve() / root for root in SCAFFOLD_ROOTS)
                valid = valid and allowed
            except (ValueError, OSError, RuntimeError):
                valid = False
        if valid:
            return True
    if _event_key(raw, "tool_name", "toolname", "tool", "name").lower() in SHELL_TOOLS:
        commands = _collect_values_for_keys(raw, {"command", "cmd"})
        try:
            for command in commands:
                for tokens in _shell_segments(command):
                    if _readonly_tokens(tokens): continue
                    if any(char in shlex.join(tokens) for char in "$`") or any(t in {">", ">>", "<<", "&>"} for t in tokens): return False
                    name = Path(tokens[0]).name
                    if tokens[:2] == ["git", "init"]: continue
                    if tokens[:2] in (["uv", "venv"], ["uv", "run"]):
                        if tokens[:2] == ["uv", "run"]:
                            runner = tokens[2:]
                            while runner and runner[0].startswith("--"):
                                runner = runner[1:]
                            if not runner or not (Path(runner[0]).name == "pytest" or
                                    (Path(runner[0]).name.startswith("python") and runner[1:3] in (["-m", "pytest"], ["-m", "unittest"]))): return False
                        continue
                    if tokens[:3] == ["uv", "pip", "install"] or (name in {"pip", "pip3", "npm"} and "install" in tokens): continue
                    if name.startswith("python") and tokens[1:3] == ["-m", "pip"] and "install" in tokens: continue
                    if name.startswith("python") and tokens[1:3] in (["-m", "pytest"], ["-m", "unittest"], ["-m", "venv"]): continue
                    return False
            return bool(commands)
        except ValueError:
            return False
    return False


def intake_writable_dirs() -> list[str]:
    """Directories that stay writable before a Work Item exists.

    - provider-owned SDD output dirs: requirement/PRD/spec artifacts, which are
      also the only places native workflow artifacts may live;
    - `.orchestrator/requirements/`: CAO's own numbered requirement documents.

    Blocking writes to either deadlocks intake: the hook demands an active Work
    Item, `cao start` demands a requirement file, and the file can never be
    written. Only `.orchestrator/requirements/` is exempt — the rest of
    `.orchestrator/` holds CAO state the Agent must not hand-edit.
    """
    return native_output_dirs() + [f"{REQUIREMENT_DIR.as_posix()}/"]


def write_targets_native_sdd_output(repo: Path, raw: dict[str, Any]) -> bool:
    """Only an all-native write is exempt from the no-Work gate and reminders."""
    analysis = tool_effect(raw, repo)
    # A literal-looking operand is not proof of the effects of an unknown
    # program, or of an expanded variable target inside a native directory.
    if analysis["diagnostics"]: return False
    targets = analysis["targets"]
    if not targets:
        return False
    for target in targets:
        try:
            path = repo_file(repo, target)
        except ValueError:
            return False
        if not any((repo.resolve() / directory).resolve() == repo.resolve() / directory
                   and inside(path, repo.resolve() / directory) for directory in intake_writable_dirs()):
            return False
    return True


def native_intake_shell(repo: Path, raw: dict[str, Any]) -> bool:
    """Trust native metadata commands, not general implementation scripts."""
    if _event_key(raw, "tool_name", "toolname", "tool", "name").lower() not in SHELL_TOOLS:
        return False
    commands = _collect_values_for_keys(raw, {"command", "cmd"})
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments")
    if isinstance(payload, str):
        commands.append(payload)
    if not commands:
        return False
    for command in commands:
        try:
            segments = _shell_segments(command)
        except ValueError:
            return False
        for tokens in segments:
            if _readonly_tokens(tokens):
                continue
            if any(any(char in token for char in "$`") or token in {">", ">>", "&>", "<", "<<", "(", ")"} for token in tokens):
                return False
            for i, token in enumerate(tokens):
                if token in {"--output", "--output-dir", "--out", "-o"} and i + 1 < len(tokens):
                    output = tokens[i + 1]
                elif token.startswith(("--output=", "--output-dir=", "--out=")):
                    output = token.split("=", 1)[1]
                else:
                    continue
                if not write_targets_native_sdd_output(repo, {"tool_name": "write", "tool_input": {"path": output}}):
                    return False
            if tokens[:2] in (["openspec", "status"], ["openspec", "instructions"], ["openspec", "list"], ["openspec", "show"], ["openspec", "validate"]):
                continue
            if bmad_preparation(repo, tokens):
                continue
            if tokens[:3] == ["openspec", "new", "change"] and detect_sdd(repo, "openspec")["installed"]:
                continue
            if (tokens[:2] == ["openspec", "archive"]
                    and (repo / "openspec").is_dir()
                    and (repo / "openspec").resolve() == repo.resolve() / "openspec"
                    and len([arg for arg in tokens[2:] if not arg.startswith("-")]) == 1
                    and all(arg in {"--yes", "-y", "--json", "--skip-specs"}
                            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", arg) for arg in tokens[2:])):
                continue
            identities = workflow_command_identities(shlex.join(tokens))
            if Path(tokens[0]).stem.lower() in {"pwsh", "powershell"} and len(tokens) > 2 and tokens[1].lower() == "-file":
                identities.append(tokens[2])
            helpers = [repo / "_bmad/scripts/render_skill.py"] + [
                repo / ".specify/scripts" / directory / (name + suffix)
                for directory, suffix in (("bash", ".sh"), ("powershell", ".ps1"))
                for name in ("check-prerequisites", "create-new-feature", "setup-plan")
            ]
            if any(helper.is_file() and helper.parent.resolve() == repo.resolve() / helper.parent.relative_to(repo)
                   and inside(helper.resolve(), helper.parent)
                   and any((Path(identity) if Path(identity).is_absolute() else repo / identity).resolve() == helper.resolve()
                           for identity in identities) for helper in helpers):
                continue
            return False
    return True


MUTATING_TOOLS = {"write", "edit", "apply_patch", "str_replace", "strreplace", "insert", "delete", "mv", "move", "write_file", "create_file", "patch"}


# `command`/`cmd` are deliberately absent: shell calls are judged by
# `readonly_shell` below, and treating any command as a mutation would block
# read-only shell usage entirely.
MUTATION_PAYLOAD_KEYS = {"new_string", "newstring", "old_string", "patch", "content"}


TOOL_NAME_KEYS = {"tool_name", "toolname", "tool", "name"}


def _collect_values_for_keys(node: Any, keys: frozenset[str] | set[str], depth: int = 0) -> list[str]:
    """Collect string values stored under `keys`, without silently truncating targets."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            k = str(key).lower()
            if isinstance(value, str):
                if k in keys and value.strip():
                    found.append(value.strip())
            elif isinstance(value, (dict, list)):
                found.extend(_collect_values_for_keys(value, keys, depth + 1))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_values_for_keys(item, keys, depth + 1))
    return found


def tool_is_mutating(raw: dict[str, Any]) -> bool:
    return tool_effect(raw)["effect"] != "read"


def _shell_segments(command: str) -> list[list[str]]:
    # Heredoc bodies are input data, not additional mutating commands. Keep
    # subsequent commands after the terminator visible to the gate.
    lines = []
    pending: list[tuple[str, bool]] = []
    for line in command.splitlines(keepends=True):
        if pending:
            delimiter, quoted = pending[0]
            if line.rstrip("\r\n") == delimiter:
                pending.pop(0)
            elif not quoted and any(char in line for char in "$`"):
                # Unquoted heredocs execute command substitutions. Preserve
                # those bodies for target checking instead of treating them
                # as literal native document content.
                lines = None
                break
            continue
        lines.append(line)
        try:
            header = shlex.shlex(line, posix=False, punctuation_chars=";&|<>()")
            header.whitespace_split = True
            tokens = list(header)
        except ValueError:
            # A quoted string may span lines; do not interpret any of its
            # later lines as a heredoc operator.
            lines = None
            break
        for i, token in enumerate(tokens[:-1]):
            if token == "<<":
                marker = tokens[i + 1]
                quoted = re.fullmatch(r"(['\"])([A-Za-z_][A-Za-z0-9_-]*)\1", marker)
                if quoted:
                    pending.append((quoted.group(2), True))
                elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", marker):
                    pending.append((marker, False))
                else:
                    # Unsupported delimiters must not hide later commands.
                    lines = None
                    break
        if lines is None:
            break
    if lines is not None:
        command = "".join(lines)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"  # Preserve unquoted newlines as command boundaries.
    segments = [[]]
    for token in lexer:
        if token and all(char in ";&|\n" for char in token):
            if segments[-1]:
                segments.append([])
        else:
            segments[-1].append(token)
    return [tokens for tokens in segments if tokens]


def readonly_shell(command: str, cwd: Path | None = None) -> bool:
    try:
        directory = cwd or Path.cwd()
        for tokens in _shell_segments(command):
            if not _readonly_tokens(tokens, directory):
                return False
            if tokens[0] == 'cd' and len(tokens) == 2:
                directory = (directory / tokens[1]).resolve()
        return True
    except ValueError:
        return False


def diagnostic_shell_write(repo: Path, raw: dict) -> bool:
    """Allow literal diff artifacts in the system temp directory, not product writes."""
    if _event_key(raw, 'tool_name', 'toolname', 'tool', 'name').lower() not in SHELL_TOOLS:
        return False
    analysis = tool_effect(raw, repo)
    if analysis['effect'] != 'write' or analysis['diagnostics'] or not analysis['targets']:
        return False
    root = Path(tempfile.gettempdir()).resolve()
    if any(not inside(Path(target).resolve(), root) or inside(Path(target).resolve(), repo.resolve())
           or Path(target).resolve() == root for target in analysis['targets']):
        return False
    commands = _collect_values_for_keys(raw, {'command', 'cmd'})
    if not commands:
        return False
    for command in commands:
        for tokens in _shell_segments(command):
            if _readonly_tokens(tokens, tool_cwd(raw, repo)):
                continue
            if tokens[0] == 'mkdir' and all(t == '-p' or not t.startswith('-') for t in tokens[1:]):
                continue
            if tokens[0] != 'diff':
                return False
            stripped = list(tokens)
            for i in range(len(stripped) - 2, 0, -1):
                if stripped[i] in {'>', '>>'}:
                    del stripped[i:i + 2]
            if not _readonly_tokens(stripped, tool_cwd(raw, repo)):
                return False
    return True


def _readonly_checksum_tokens(tokens: list[str]) -> bool:
    """Recognize literal SHA probes without accepting arbitrary runner flags."""
    algorithms = {"1", "224", "256", "384", "512", "512224", "512256"}
    options = {"-b", "--binary", "-t", "--text", "--tag", "-z", "--zero", "--help", "--version"}
    shasum = Path(tokens[0]).name.lower() == "shasum"
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            return True  # Remaining literal arguments are filenames.
        if shasum and token in {"-a", "--algorithm"}:
            i += 1
            if i >= len(tokens) or tokens[i] not in algorithms:
                return False
        elif shasum and token.startswith("--algorithm="):
            if token.partition("=")[2] not in algorithms:
                return False
        elif token.startswith("-") and token not in options:
            return False
        i += 1
    return True


def _readonly_tokens(tokens: list[str], cwd: Path | None = None) -> bool:
    tokens = list(tokens)
    # Descriptor duplication has no filesystem target. Keep arbitrary redirects
    # and substitutions visible to the write-target guard.
    index = 1
    while index + 2 < len(tokens):
        if tokens[index:index + 3] in (["2", ">&", "1"], ["1", ">&", "2"]):
            del tokens[index:index + 3]
        else:
            index += 1
    # Exit status expansion is data, not command substitution. Restrict it to
    # diagnostic echo; do not broadly exempt dollar expansions.
    if tokens and tokens[0] == "echo":
        tokens = [token.replace("$?", "STATUS") for token in tokens]
    # Literal output to the real null device cannot modify project files. Keep
    # every other redirection visible, and never remove the command itself.
    if Path("/dev/null").is_char_device():
        index = 1
        while index + 1 < len(tokens):
            if tokens[index] in {">", ">>"} and tokens[index + 1] == "/dev/null":
                start = index - 1 if index > 1 and tokens[index - 1] in {"1", "2"} else index
                del tokens[start:index + 2]
                index = max(1, start)
            else:
                index += 1
    operators = {">", ">>", "<", "<<", "<>", ">|", "&>", ">&", "<&", ";", "&", "|", "&&", "||", "(", ")", "$", "`"}
    if not tokens or any(token in operators for token in tokens):
        return False
    name = tokens[0].lower()
    if any(char in token for token in tokens for char in "$`"): return False
    if re.fullmatch(r"NPM_CONFIG_PREFIX=.+", tokens[0]):
        # Only a literal npm prefix on the real CAO installer is exempt. Never
        # accept interpreter/preload/PATH overrides or arbitrary assigned commands.
        rest = tokens[1:]
        return bool(rest and rest[1:3] == ["sdd", "install"] and _readonly_tokens(rest))
    if tokens in (["true"], ["false"]):
        return True
    if tokens in (["set", "-o", "pipefail"], ["set", "-e"], ["set", "-u"], ["set", "-euo", "pipefail"]):
        return True
    if name == "sort":
        safe = {"--", "-r", "-n", "-u", "-f", "-b", "-R", "-V", "-h", "--reverse",
                "--numeric-sort", "--unique", "--ignore-case"}
        return all(not token.startswith("-") or token in safe for token in tokens[1:])
    if name == "npm" and (tokens[1:] in (["root"], ["root", "-g"], ["root", "--global"])
                          or (tokens[1:3] == ["config", "get"] and len(tokens) == 4
                              and tokens[3] in {"prefix", "cache", "registry"})):
        return True
    if name == "openspec":
        if tokens[1:] in (["--help"], ["-h"], ["--version"], ["-V"]):
            return True
        if len(tokens) == 3 and tokens[1] in {"archive", "init", "new", "status", "instructions", "validate", "list", "show"} and tokens[2] in {"--help", "-h"}:
            return True
        if (len(tokens) > 1 and tokens[1] in {"status", "instructions", "list", "show", "validate", "context", "schemas"}
                and not any(token in {"--output", "--out", "--output-dir", "-o"}
                            or token.startswith(("--output=", "--out=", "--output-dir=")) for token in tokens)):
            return True
    if name == "find":
        return not any(token in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf"} for token in tokens)
    if name == 'date':
        return all(token in {'-u', '--utc', '--universal', '-I', '--iso-8601', '--rfc-3339=seconds'}
                   or token.startswith('+') for token in tokens[1:])
    if name in {'diff', 'printenv'}:
        return True
    if name in {"ls", "dir", "cat", "type", "pwd", "grep", "rg", "head", "tail", "echo", "printf", "cd", "exit", "which", "file", "stat", "wc"}:
        return True
    if name == "command" and len(tokens) > 1 and tokens[1] in {"-v", "-V"}:
        return True
    probe_name = Path(tokens[0]).name.lower()
    python_probe = re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", probe_name)
    if len(tokens) == 2:
        if python_probe and tokens[1] in {"--version", "-V"}:
            return True
        if probe_name in {"java", "javac", "mvn", "gradle", "node", "npm", "uv"} and tokens[1] in {"--version", "-version", "-v"}:
            return True
    if probe_name in {"sha256sum", "shasum"}:
        return _readonly_checksum_tokens(tokens)
    if name == "git":
        args = tokens[1:]
        while len(args) >= 2 and args[0] == "-C": args = args[2:]
        if args and args[0] == "rev-parse":
            return bool(args[1:]) and all(token in {"--show-toplevel", "--is-inside-work-tree", "--git-dir", "--show-prefix", "--verify", "HEAD"} for token in args[1:])
        if args and args[0] in {"status", "diff", "log", "show", "branch", "ls-files"}:
            unsafe = {"--ext-diff", "--textconv", "-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy"}
            return not git_output_targets(tokens) and not any(token in unsafe for token in args[1:]) and (args[0] != "branch" or all(token.startswith("-") for token in args[1:]))
    if name == "sed":
        return sed_effect(tokens)[0] == "read"
    if name == "pytest" or tokens[:3] in (["python", "-m", "unittest"],):
        return True
    if tokens[:2] in (["mvn", "test"], ["gradle", "test"], ["npm", "test"]):
        return True
    # Standalone CAO commands own their state mutations; their arguments are not hand edits.
    command_tokens = tokens
    if python_probe:
        runner = tokens[1:]
        while runner and runner[0] in {"-u", "-B", "-E", "-I", "-s", "-S"}:
            runner = runner[1:]
        if len(runner) < 2:
            return False
        try:
            if Path(runner[0]).resolve() != Path(__file__).with_name("cao.py").resolve():
                return False
        except (OSError, ValueError, RuntimeError):
            return False
        command_tokens = runner[1:]
    elif tokens[0] == "cao":
        command_tokens = tokens[1:]
    else:
        # Templates and the Pi fallback use this package's absolute launcher.
        # A matching basename alone must not exempt an unrelated script.
        index = 1 if tokens[0] == "sh" else 0
        if len(tokens) <= index:
            return False
        root = package_root_dir()
        launchers = {str((root / "cao").resolve())} if root else set()
        from scripts.registered_launchers import project_launchers
        launchers.update(project_launchers(cwd or Path.cwd()))
        console = shutil.which("cao")
        if console:
            launchers.add(str(Path(console).resolve()))
        try:
            target = str(((cwd or Path.cwd()) / tokens[index]).resolve())
        except (OSError, RuntimeError):
            return False
        if target not in launchers:
            return False
        command_tokens = tokens[index + 1:]
    safe_control_commands = {
        "init", "bootstrap", "sdd", "start", "path", "list", "status", "next", "resume",
        "checkpoint", "decision", "block", "unblock", "handoff", "sessions", "verify", "done",
        "project", "acceptance", "runtime", "context", "artifact", "host", "host-event", "requirement", "language", "gate", "reopen", "verification", "instructions",
    }
    return bool(command_tokens and (command_tokens[0] in safe_control_commands or command_tokens in (["--help"], ["-h"])))
