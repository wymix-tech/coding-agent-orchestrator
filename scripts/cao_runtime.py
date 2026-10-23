"""Repository configuration, JSON helpers, identifiers, and transaction decorators."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import subprocess
import time
import uuid
from functools import wraps
from pathlib import Path
from typing import Any

from scripts.cao_defaults import ACTION_TYPES, CONFIG_REL, DEFAULT_CONFIG
from scripts.state_store import atomic_json, repo_transaction
from scripts.verification_setup import configured_scripts


def locked_command(fn):
    @wraps(fn)
    def call(args):
        with repo_transaction(repo_root(args.repo)):
            return fn(args)
    return call


def locked_repo(fn):
    @wraps(fn)
    def call(repo, *args, **kwargs):
        with repo_transaction(repo):
            return fn(repo, *args, **kwargs)
    return call


def command_exists(command: str) -> bool:
    from shutil import which
    return which(command) is not None


def repo_root(path: str | Path = ".") -> Path:
    p = Path(path).resolve()
    try:
        out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=p, text=True, stderr=subprocess.DEVNULL).strip()
        return Path(out).resolve()
    except Exception:
        return p


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    atomic_json(path, value)


def deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v


def config(repo: Path) -> dict[str, Any]:
    raw = load_json(repo / CONFIG_REL, {})
    if not isinstance(raw, dict):
        raise ValueError("CONFIG_INVALID: restore the project configuration; do not initialize over it")
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if raw:
        deep_merge(merged, raw)

    # Migrate the previous command-list model only when the persisted config
    # does not explicitly contain the new `scripts` key. This distinction is
    # important because DEFAULT_CONFIG itself supplies an empty scripts list.
    raw_verification = raw.get("verification") if isinstance(raw, dict) else None
    if isinstance(raw_verification, dict) and "scripts" not in raw_verification:
        verification = merged.setdefault("verification", {})
        old_default = "python -m unittest discover -s tests -p 'test_*.py'"
        legacy_command = raw_verification.get("command")
        legacy_commands = raw_verification.get("commands")
        if isinstance(legacy_commands, list) and legacy_commands:
            verification["scripts"] = legacy_commands
        elif legacy_command and legacy_command != old_default:
            verification["scripts"] = [{"name": "default", "command": str(legacy_command), "required": True}]
        else:
            verification["scripts"] = []
    return merged


def verification_scripts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return configured_scripts(cfg)


# Compatibility alias for internal callers and older integrations.
verification_commands = verification_scripts


def now() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}-{uuid.uuid4().hex[:6]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def rel(path: Path, repo: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path, f"*/{p}") for p in patterns)


def action(kind: str, instruction: str, **extra: Any) -> dict[str, Any]:
    if kind not in ACTION_TYPES:
        raise ValueError(f"unsupported action type: {kind}")
    return {"type": kind, "instruction": instruction, **extra}


def ensure_initialized(repo: Path) -> None:
    if not (repo / CONFIG_REL).exists():
        raise RuntimeError("Project is not initialized. Run `cao init` first.")
