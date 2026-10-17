"""Deterministic source, rules, and verification snapshots."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from scripts.cao_defaults import SNAPSHOT_IGNORE_DIRS
from scripts.cao_runtime import rel, sha256_file, verification_scripts
from scripts.completion_policy import acceptance_hash, anchor_acceptance, normalize_timeout
from scripts.native_obligations import digest as native_digest


SNAPSHOT_FORMAT = 3


def code_snapshot(repo: Path, excluded: set[str] | None = None, additional_ignore_dirs: set[str] | None = None) -> str:
    """Versioned content, execution bits and link topology; bounded internal traversal."""
    repo = repo.resolve()
    excluded = excluded or set()
    ignored = SNAPSHOT_IGNORE_DIRS | (additional_ignore_dirs or set())
    h = hashlib.sha256()
    records = []
    visited_entries = 0
    def walk_error(exc):
        raise RuntimeError("SNAPSHOT_READ_FAILED: " + str(exc.filename or repo)) from exc
    def identity(path):
        relative = path.relative_to(repo).as_posix()
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(repo):
                raise RuntimeError("SNAPSHOT_EXTERNAL_SYMLINK: " + relative)
            info = path.lstat()
            target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
            mode = path.stat().st_mode
            kind = "directory" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "special"
            bits = mode & 0o111 if os.name != "nt" else 0
            return relative, {"type": kind, "executable": bits, "link": target}
        except RuntimeError as exc:
            if str(exc).startswith("SNAPSHOT_"): raise
            raise RuntimeError("SNAPSHOT_SYMLINK_CYCLE: " + relative) from exc
        except (OSError, ValueError) as exc:
            raise RuntimeError("SNAPSHOT_READ_FAILED: " + relative) from exc
    for base, dirs, names in os.walk(repo, topdown=True, followlinks=True, onerror=walk_error):
        directory = Path(base)
        try:
            real = directory.resolve(strict=True)
            ancestors = [parent.resolve(strict=True) for parent in directory.parents if parent.is_relative_to(repo)]
            if real in ancestors:
                raise RuntimeError("SNAPSHOT_SYMLINK_CYCLE: " + directory.relative_to(repo).as_posix())
        except OSError as exc:
            raise RuntimeError("SNAPSHOT_READ_FAILED: " + str(directory)) from exc
        dirs[:] = sorted(name for name in dirs if name not in ignored)
        visited_entries += len(dirs) + len(names)
        if visited_entries > 100000 or len(directory.relative_to(repo).parts) > 100:
            raise RuntimeError("SNAPSHOT_SCAN_LIMIT: " + str(directory))
        for name in dirs:
            path = directory / name
            if path.is_symlink():
                relative, meta = identity(path)
                records.append((relative, meta, b""))
        for name in names:
            path = directory / name
            relative = path.relative_to(repo).as_posix()
            if name in ignored or relative in excluded: continue
            relative, meta = identity(path)
            if meta["type"] != "file":
                raise RuntimeError("SNAPSHOT_UNSUPPORTED_INPUT: " + relative)
            try: content = hashlib.sha256(path.read_bytes()).digest()
            except OSError as exc: raise RuntimeError("SNAPSHOT_READ_FAILED: " + relative) from exc
            records.append((relative, meta, content))
    for relative, meta, content in sorted(records):
        h.update(relative.encode()); h.update(b"\0")
        h.update(json.dumps(meta, sort_keys=True).encode()); h.update(b"\0")
        h.update(content); h.update(b"\0")
    return f"v{SNAPSHOT_FORMAT}:" + h.hexdigest()


def verification_timeout(cfg: dict[str, Any]) -> float:
    return normalize_timeout((cfg.get("verification") or {}).get("timeout_seconds", 300.0))


def verification_snapshot(cfg: dict[str, Any], repo: Path | None = None) -> str:
    from scripts.cao_provider_workflows import workflow_command_identities

    config_hash = acceptance_hash(verification_scripts(cfg), verification_timeout(cfg))
    files = {}
    if repo is not None:
        for script in verification_scripts(cfg):
            for identity in workflow_command_identities(script["command"]):
                path = Path(identity)
                path = path if path.is_absolute() else repo / path
                # Executable wrappers are standards; their test data and business
                # dependencies remain covered by the ordinary code snapshot.
                if path.is_file() and path.resolve().is_relative_to(repo.resolve()):
                    files[path.relative_to(repo).as_posix()] = sha256_file(path)
                elif path.suffix in {".sh", ".py", ".ps1", ".cmd", ".bat"} and path.is_relative_to(repo):
                    files[path.relative_to(repo).as_posix()] = "missing"
    return native_digest({"config": config_hash, "wrappers": files}) if files else config_hash


def anchor_verification(repo: Path, work: dict, cfg: dict) -> None:
    anchor_acceptance(work, verification_scripts(cfg), verification_timeout(cfg))
    work["acceptance"]["config_sha256"] = verification_snapshot(cfg, repo)


def rules_snapshot(repo: Path, cfg: dict[str, Any]) -> str:
    files: set[Path] = set()
    for pattern in (cfg.get("rules") or {}).get("files") or []:
        for item in glob.glob(str(repo / pattern), recursive=True):
            p = Path(item)
            if not p.resolve().is_relative_to(repo.resolve()):
                raise RuntimeError("SNAPSHOT_EXTERNAL_SYMLINK: " + str(p))
            try:
                if stat.S_ISREG(p.stat().st_mode):
                    files.add(p.resolve())
            except OSError as exc:
                raise RuntimeError("SNAPSHOT_READ_FAILED: " + str(p)) from exc
    h = hashlib.sha256()
    for p in sorted(files):
        rp = rel(p, repo)
        try:
            digest = sha256_file(p)
        except OSError as exc:
            raise RuntimeError("SNAPSHOT_READ_FAILED: " + rp) from exc
        h.update(rp.encode()); h.update(b"\0"); h.update(digest.encode()); h.update(b"\0")
    return h.hexdigest()
