"""Validated root-relative snapshot exclusions and verification output declarations."""
from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path, PurePosixPath

from scripts.cao_defaults import DEFAULT_CONFIG, SDD_CATALOG, SNAPSHOT_CACHE_DIRS, SNAPSHOT_INTERNAL_ROOTS
from scripts.verification_setup import SCAFFOLD_MANIFESTS
from scripts.snapshot_version import SNAPSHOT_FORMAT

# Metadata is bound separately; caches are pruned unless the Git index owns inputs inside.
INTERNAL_ROOTS = SNAPSHOT_INTERNAL_ROOTS
CACHE_NAMES = SNAPSHOT_CACHE_DIRS
SOURCE_ROOTS = {"src", "source", "lib", "app", "apps", "packages", "test", "tests", "acceptance-tests",
                "scripts", "requirements", "specs", "openspec", ".specify", ".agents", ".pi"}
SOURCE_SUFFIXES = {".py", ".pyi", ".java", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".c", ".h",
                   ".cpp", ".cs", ".rb", ".sh", ".ps1", ".cmd", ".bat", ".md", ".yml", ".yaml"}
MANIFEST_NAMES = {name.casefold() for name in SCAFFOLD_MANIFESTS}
SOURCE_ROOTS.update(root.split("/", 1)[0] for meta in SDD_CATALOG.values()
                    for root in meta.get("output_dirs", []))


def paths(raw: object, label: str) -> list[str]:
    """No glob grammar: files are exact, trailing / means that root-relative subtree."""
    if not isinstance(raw, list):
        raise ValueError(f"SNAPSHOT_SCOPE_INVALID: {label} must be a list")
    result = set()
    for value in raw:
        if (not isinstance(value, str) or not value or value != value.strip()
                or any(c in value for c in "\\:*?[]\x00\n\r")
                or value.startswith("/") or "//" in value):
            raise ValueError(f"SNAPSHOT_SCOPE_INVALID: invalid {label} path {value!r}")
        body = value.rstrip("/")
        if not body or any(part in {".", ".."} for part in body.split("/")):
            raise ValueError(f"SNAPSHOT_SCOPE_INVALID: invalid {label} path {value!r}")
        result.add(value)
    return sorted(result)


def scope_config(cfg: dict) -> dict:
    snapshot = cfg.get("snapshot", {})
    verification = cfg.get("verification") or {}
    if not isinstance(snapshot, dict) or not isinstance(verification, dict):
        raise ValueError("SNAPSHOT_SCOPE_INVALID: snapshot and verification must be objects")
    return {"format": SNAPSHOT_FORMAT,
            "exclude_paths": paths(snapshot.get("exclude_paths", []), "snapshot.exclude_paths"),
            "outputs": paths(verification.get("outputs", []), "verification.outputs")}


def path_key(relative: str) -> str:
    """Use native case semantics for Git-index and declaration comparisons."""
    return os.path.normcase(relative).replace(os.sep, "/")


def covers(pattern: str, relative: str) -> bool:
    pattern, relative = path_key(pattern), path_key(relative)
    return relative == pattern.rstrip("/") or (pattern.endswith("/") and relative.startswith(pattern))


def excluded_input(relative: str, scope: dict, tracked_keys: set[str],
                   tracked_dirs=(), additional_roots=()) -> bool:
    """One source boundary for snapshots and native commit change filtering."""
    relative = path_key(relative)
    if relative.split("/", 1)[0] in INTERNAL_ROOTS:
        return True
    # Native metadata has separate SPEC/instruction bindings in review inputs.
    if any(relative == root or relative.startswith(root + "/") for root in additional_roots):
        return True
    if relative in tracked_keys or relative in tracked_dirs:
        return False
    return (any(part in CACHE_NAMES for part in relative.split("/"))
            or any(covers(value, relative) for value in scope["exclude_paths"] + scope["outputs"]))


def git_inputs(repo: Path) -> set[str]:
    if not (repo / ".git").exists():
        return set()
    try:
        result = subprocess.run(["git", "ls-files", "--cached", "-z"], cwd=repo,
                                capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("SNAPSHOT_GIT_INDEX_FAILED") from exc
    if result.returncode:
        raise RuntimeError("SNAPSHOT_GIT_INDEX_FAILED: " + result.stderr.decode(errors="replace")[-1000:])
    return {os.fsdecode(item) for item in result.stdout.split(b"\0") if item}


def _protected(cfg: dict, relative: str) -> bool:
    path = PurePosixPath(relative)
    if (path.parts[0].casefold() in INTERNAL_ROOTS | SOURCE_ROOTS
            or path.name.casefold() in MANIFEST_NAMES
            or path.name.casefold() in {"agents.md", "claude.md", "cao", "makefile", "dockerfile"}
            or path.suffix.lower() in SOURCE_SUFFIXES):
        return True
    rules = (cfg.get("rules") or {}).get("files") or []
    requirements = (cfg.get("sdd") or {}).get("requirement_globs",
                                              DEFAULT_CONFIG["sdd"]["requirement_globs"])
    return any(fnmatch.fnmatchcase(path_key(relative), path_key(pattern)) for pattern in [*rules, *requirements])


def validate_scope(repo: Path, cfg: dict, tracked: set[str] | None = None, protected=()) -> dict:
    """Reject unsafe declarations before pruning; tracked inputs override exclusions."""
    declaration = scope_config(cfg)
    tracked = git_inputs(repo) if tracked is None else tracked
    from scripts.cao_provider_workflows import workflow_command_identities
    from scripts.cao_runtime import verification_scripts

    protected = set(protected)
    for script in verification_scripts(cfg):
        for identity in workflow_command_identities(script["command"]):
            path = Path(identity)
            path = path if path.is_absolute() else repo / path
            lexical = Path(os.path.abspath(path))
            if lexical.is_relative_to(repo):
                protected.add(lexical.relative_to(repo).as_posix())
            resolved = path.resolve()
            if resolved.is_relative_to(repo):
                protected.add(resolved.relative_to(repo).as_posix())
    for kind in ("exclude_paths", "outputs"):
        for value in declaration[kind]:
            relative = value.rstrip("/")
            path = repo / relative
            # A declaration must not prune a link before the normal link guard sees it.
            for part in (path, *path.parents):
                if part == repo:
                    break
                if part.is_symlink():
                    raise ValueError("SNAPSHOT_SCOPE_SYMLINK: " + value)
            if not path.resolve().is_relative_to(repo):
                raise ValueError("SNAPSHOT_SCOPE_EXTERNAL: " + value)
            if path.is_dir() and not value.endswith("/"):
                raise ValueError("SNAPSHOT_SCOPE_INVALID: directories require trailing /: " + value)
            if _protected(cfg, relative) or any(covers(value, item) for item in protected):
                raise ValueError("SNAPSHOT_SCOPE_PROTECTED: " + value)
            # Validate existing untracked inputs in declared output trees too. Limit traversal.
            if kind == "outputs" and value.endswith("/") and path.is_dir():
                count = 0
                def fail(exc):
                    raise RuntimeError("SNAPSHOT_READ_FAILED: " + str(exc.filename)) from exc
                for base, dirs, names in os.walk(path, followlinks=False, onerror=fail):
                    for name in dirs + names:
                        candidate = Path(base) / name
                        count += 1
                        if count > 100000 or len(candidate.relative_to(repo).parts) > 100:
                            raise RuntimeError("SNAPSHOT_SCAN_LIMIT: " + value)
                        if candidate.is_symlink():
                            raise ValueError("SNAPSHOT_SCOPE_SYMLINK: " + str(candidate))
                        if _protected(cfg, candidate.relative_to(repo).as_posix()):
                            raise ValueError("SNAPSHOT_SCOPE_PROTECTED: " + str(candidate))
            if kind == "outputs" and any(covers(value, item) for item in tracked):
                raise ValueError("SNAPSHOT_OUTPUT_TRACKED_INPUT: " + value)
    return declaration
