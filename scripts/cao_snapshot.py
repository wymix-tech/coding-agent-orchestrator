"""Deterministic source, rules, and verification snapshots."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from scripts.cao_runtime import config, rel, sha256_file, verification_scripts
from scripts.completion_policy import acceptance_hash, anchor_acceptance, normalize_timeout
from scripts.native_obligations import digest as native_digest
from scripts.snapshot_scope import (
    SNAPSHOT_FORMAT, excluded_input, git_inputs, path_key, scope_config, validate_scope,
)


def code_snapshot(repo: Path, excluded: set[str] | None = None, additional_ignore_dirs: set[str] | None = None, *, include_roots=None, manifest=None) -> str:
    """Versioned content, execution bits and link topology; bounded internal traversal."""
    repo = repo.resolve()
    excluded = excluded or set()
    tracked = git_inputs(repo)
    cfg = config(repo)
    scope = validate_scope(repo, cfg, tracked, protected=excluded)
    tracked_keys = {path_key(name) for name in tracked}
    tracked_dirs = {path_key(parent.as_posix()) for name in tracked for parent in Path(name).parents}
    extra = {path_key(name.rstrip("/")) for name in (additional_ignore_dirs or set())}

    roots = None
    if include_roots is not None:
        from scripts.project_context import relative
        roots = [relative(repo, value) for value in include_roots]
        if not roots:
            raise ValueError('SNAPSHOT_SCOPE_REQUIRED')
        if '.' in roots:
            roots = None
    def selected(name, directory=False):
        return roots is None or any(name == root or name.startswith(root + '/') or directory and root.startswith(name + '/') for root in roots)
    if roots is not None:
        for key in ('exclude_paths', 'outputs'):
            scope[key] = [value for value in scope[key] if selected(value.rstrip('/'))]
        if scope.get('generated_output_suffixes'):
            scope['generated_output_suffixes'] = {p: s for p, s in scope['generated_output_suffixes'].items() if selected(p.rstrip('/'))}

    def skip(relative, directory=False):
        return not selected(relative, directory) or excluded_input(relative, scope, tracked_keys,
                              tracked_dirs if directory else (), extra)

    h = hashlib.sha256()
    records = []
    h.update(json.dumps(scope, sort_keys=True).encode())
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
            if str(exc).startswith("SNAPSHOT_"):
                raise
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
        dirs[:] = sorted(name for name in dirs if not skip((directory / name).relative_to(repo).as_posix(), True))
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
            if skip(relative) or relative in excluded:
                continue
            relative, meta = identity(path)
            if meta["type"] != "file":
                raise RuntimeError("SNAPSHOT_UNSUPPORTED_INPUT: " + relative)
            try:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                content = digest.digest()
            except OSError as exc:
                raise RuntimeError("SNAPSHOT_READ_FAILED: " + relative) from exc
            records.append((relative, meta, content))
    for relative, meta, content in sorted(records):
        if manifest is not None:
            manifest.append({'path': relative, **meta, 'sha256': content.hex() if content else None})
        h.update(relative.encode())
        h.update(b"\0")
        h.update(json.dumps(meta, sort_keys=True).encode())
        h.update(b"\0")
        h.update(content)
        h.update(b"\0")
    return f"v{SNAPSHOT_FORMAT}:" + h.hexdigest()


def verification_timeout(cfg: dict[str, Any]) -> float:
    return normalize_timeout((cfg.get("verification") or {}).get("timeout_seconds", 300.0))


def verification_snapshot(cfg: dict[str, Any], repo: Path | None = None, *, managed_scopes=None) -> str:
    from scripts.cao_provider_workflows import workflow_command_identities

    scope = validate_scope(repo.resolve(), cfg) if repo is not None else scope_config(cfg)
    config_hash = native_digest({"scripts": acceptance_hash(verification_scripts(cfg), verification_timeout(cfg)),
                                 "scope": scope})
    metadata = {key: cfg["verification"][key] for key in ("environment_variables", "maximum_evidence_age_seconds")
                if key in (cfg.get("verification") or {})}
    if metadata:
        config_hash = native_digest({"standard": config_hash, "environment_policy": metadata})
    files = {}
    if repo is not None:
        from scripts.project_baseline import script_paths
        sources = (cfg.get('verification') or {}).get('materialized_commands') or {}
        commands = [script['command'] for script in verification_scripts(cfg)]
        bound_sources = {name: sources[name] for command in commands
                         for name in script_paths(command, repo) if name in sources}
        if bound_sources:
            config_hash = native_digest({'standard': config_hash, 'materialized_commands': bound_sources})
            commands.extend(bound_sources.values())
        for command in commands:
            for identity in workflow_command_identities(command):
                path = Path(identity)
                path = path if path.is_absolute() else repo / path
                # Executable wrappers are standards; their test data and business
                # dependencies remain covered by the ordinary code snapshot.
                if path.is_file() and path.resolve().is_relative_to(repo.resolve()):
                    files[path.relative_to(repo).as_posix()] = sha256_file(path)
                elif path.suffix in {".sh", ".py", ".ps1", ".cmd", ".bat"} and path.is_relative_to(repo):
                    files[path.relative_to(repo).as_posix()] = "missing"
    if repo is not None:
        from scripts.managed_verification import input_hashes
        managed = input_hashes(repo, managed_scopes)
        if managed:
            return native_digest({"config": config_hash, "wrappers": files, "managed_inputs": managed})
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
