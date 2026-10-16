"""Agent-authored route inputs, separate from native artifacts and CAO decisions."""
import hashlib
import json
import os
from pathlib import Path

from scripts.native_route import rule_source, rule_text, RouteValidationError
from scripts.path_guard import repo_file
from scripts.state_store import atomic_json


def declaration_path(work):
    wid = work["id"]
    if not isinstance(wid, str) or not wid or wid in {".", ".."} or any(c in wid for c in "/\\\x00"):
        raise ValueError("NATIVE_ROUTE_WORK_ID_INVALID")
    return f".orchestrator/inputs/{wid}/route.json"


def input_path(repo, work):
    """Do not follow links into managed state, native outputs or another Work."""
    current = repo.resolve()
    for part in Path(declaration_path(work)).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("NATIVE_ROUTE_INPUT_SYMLINK")
    if current.exists() and (not current.is_file() or current.stat().st_nlink != 1):
        raise ValueError("NATIVE_ROUTE_INPUT_PATH_INVALID")
    return current


def writable_input(repo, value, work):
    if not work or work.get("status") != "active":
        return False
    try:
        lexical = Path(os.path.abspath(value if Path(value).is_absolute() else repo / value))
        state = next((p for p in lexical.parents if p.name == ".orchestrator" and p.parent.resolve() == repo.resolve()), None)
        expected = input_path(repo, work)
        return (state is not None and lexical == state / "inputs" / work["id"] / "route.json"
                and lexical.resolve() == expected)
    except (ValueError, OSError, RuntimeError):
        return False


def read_declaration(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("NATIVE_ROUTE_DECLARATION_TOO_LARGE")
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("NATIVE_ROUTE_DECLARATION_INVALID")
    return raw


def prepare(repo, work, draft, source=None):
    path = input_path(repo, work)
    if path.exists():
        return path, read_declaration(path), False
    if source is not None:
        draft = read_declaration(source)
        if (draft.get("provider") != work["sdd"]["provider"]
                or draft.get("requirement_sha256") != work["requirement"]["sha256"]):
            raise ValueError("NATIVE_ROUTE_IDENTITY_MISMATCH")
    atomic_json(path, draft)
    return path, draft, True


def add_rule(repo, work, source, first, last, skills, index=None):
    path = input_path(repo, work)
    if not path.is_file():
        raise ValueError("NATIVE_ROUTE_INPUT_PREPARE_REQUIRED")
    raw = read_declaration(path)
    if (raw.get("provider") != work["sdd"]["provider"]
            or raw.get("requirement_sha256") != work["requirement"]["sha256"]):
        raise ValueError("NATIVE_ROUTE_IDENTITY_MISMATCH")
    source = rule_source(repo, source, work["sdd"]["provider"], skills)
    content = source.read_bytes()
    lines = rule_text(content).splitlines(keepends=True)
    if first < 1 or last < first or last > len(lines) or not "".join(lines[first - 1:last]).strip():
        raise RouteValidationError([{"code": "NATIVE_ROUTE_RULE_LINE_RANGE_INVALID", "field": "line_range",
                                     "path": source.relative_to(repo.resolve()).as_posix(),
                                     "expected": f"a nonempty excerpt with 1 <= start <= end <= {len(lines)}"}])
    rule = {"path": source.relative_to(repo.resolve()).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(), "quote": "".join(lines[first - 1:last]).strip()}
    rules = raw.setdefault("rules", [])
    if not isinstance(rules, list):
        raise ValueError("NATIVE_ROUTE_RULES_REQUIRED")
    if index is None:
        index = len(rules)
        for i, existing in enumerate(rules):
            try:
                if isinstance(existing, dict) and repo_file(repo, existing.get("path")) == source:
                    index = i
                    break
            except (ValueError, OSError, TypeError, RuntimeError):
                continue
    if index < 0 or index > len(rules):
        raise ValueError("NATIVE_ROUTE_RULE_INDEX_INVALID")
    if index == len(rules):
        rules.append(rule)
    else:
        rules[index] = rule
    atomic_json(path, raw)
    return path, rule, index
