"""Material repository fingerprint shared by analysis and runtime authorization."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

GENERATED_PREFIXES = tuple(f".orchestrator/{p}/" for p in (
    "intake", "runtime", "session", "context", "evidence", "archive", "work-items", "requirements", "providers",
))
GENERATED_FILES = {
    ".orchestrator/execution-state.yaml", ".orchestrator/execution-state.yaml.tmp",
    ".orchestrator/execution-history.jsonl", ".orchestrator/bootstrap-report.json",
    ".orchestrator/config.yaml", ".orchestrator/enforcement.yaml", ".orchestrator/session-context.yaml",
}
IGNORED_DIRS = {".git", ".claude", ".codex", ".pi", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
IGNORED_PREFIXES = (".agents/skills/coding-agent-orchestrator/",)
ACTIVATION_START = "<!-- coding-agent-orchestrator:start -->"
ACTIVATION_END = "<!-- coding-agent-orchestrator:end -->"


def is_generated(path: str) -> bool:
    rel = path.replace("\\", "/")
    if rel in GENERATED_FILES or rel.startswith(GENERATED_PREFIXES):
        return True
    # Durable state transaction/lock artifacts are runtime control-plane material.
    return rel.startswith(".orchestrator/.execution-state.yaml.") or rel.startswith(".orchestrator/.execution-state.yaml") and ("transaction" in rel or rel.endswith(".lock"))


def repo_from_state(path: Path) -> Path:
    path = path.resolve()
    return path.parent.parent if path.parent.name == ".orchestrator" else path.parent


def digest_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        rows = [(p.relative_to(path).as_posix(), digest_path(p), p.stat().st_size)
                for p in sorted(path.rglob("*")) if p.is_file()]
        return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    raise FileNotFoundError(str(path))




def _strip_activation_block(text: str) -> str:
    if ACTIVATION_START not in text or ACTIVATION_END not in text:
        return text
    before, rest = text.split(ACTIVATION_START, 1)
    _, after = rest.split(ACTIVATION_END, 1)
    return (before.rstrip() + "\n" + after.lstrip()).strip()


def _material_file_digest(rel: str, path: Path) -> str | None:
    if rel in {"AGENTS.md", "CLAUDE.md"}:
        try:
            stripped = _strip_activation_block(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return digest_path(path)
        if not stripped:
            return None
        return hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    return digest_path(path)

def fingerprint(repo: Path) -> str:
    repo = repo.resolve()
    head = ""
    try:
        raw = subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo, stderr=subprocess.DEVNULL,
        )
        names = {os.fsdecode(x) for x in raw.split(b"\0") if x}
        try:
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                           stderr=subprocess.DEVNULL, text=True).strip()
        except subprocess.CalledProcessError:
            pass  # unborn branch
    except (OSError, subprocess.CalledProcessError):
        names = set()
        for directory, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS
                       and not is_generated((Path(directory) / d).relative_to(repo).as_posix() + "/")]
            names.update((Path(directory) / name).relative_to(repo).as_posix() for name in files)
    rows = []
    for rel in sorted(names):
        if is_generated(rel) or rel.startswith(IGNORED_PREFIXES) or any(p in IGNORED_DIRS for p in Path(rel).parts):
            continue
        path = repo / rel
        if path.is_symlink():
            rows.append((rel, "symlink", os.readlink(path)))
        elif path.is_file():
            digest = _material_file_digest(rel, path)
            if digest is None:
                continue
            rows.append((rel, digest, bool(path.stat().st_mode & 0o111)))
        else:
            rows.append((rel, "missing", None))
    payload = json.dumps({"head": head, "files": rows}, sort_keys=True, separators=(",", ":")).encode()
    return "worktree:" + hashlib.sha256(payload).hexdigest()
