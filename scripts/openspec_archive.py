"""Recognize unique, byte-preserving OpenSpec moves; never certify execution."""
from copy import deepcopy
import hashlib
from pathlib import Path, PurePosixPath
import re

from scripts.sdd_acceptance_policy import digest, normalize_declaration


def _root(value):
    if not isinstance(value, str):
        return None
    parts = PurePosixPath(value).parts
    if (len(parts) < 3 or parts[:2] != ("openspec", "changes") or parts[2] == "archive"
            or "\\" in value or any(part in {"", ".", ".."} for part in value.split("/"))):
        return None
    return "/".join(parts[:3])


def _unlinked(repo: Path, path: Path) -> bool:
    if not path.is_relative_to(repo):
        return False
    while path != repo:
        if path.is_symlink():
            return False
        path = path.parent
    return True


def _references(sdd):
    """Later explicit bindings take precedence over earlier discovery hashes."""
    refs = {item["path"]: item["sha256"] for item in sdd.get("artifacts", [])
            if isinstance(item, dict) and item.get("path") and item.get("sha256")}
    refs.update((sdd.get("workflow") or {}).get("artifact_hashes") or {})
    refs.update({item["path"]: item["sha256"] for item in (sdd.get("plan_scope") or {}).get("plans", [])
                 if item.get("path") and item.get("sha256")})
    for field in ("spec", "criteria"):
        ref = (sdd.get("spec_binding") or {}).get(field) or {}
        if ref.get("path") and ref.get("sha256"):
            refs[ref["path"]] = ref["sha256"]
    refs.update((sdd.get("acceptance") or {}).get("anchor_sources") or {})
    return refs


def relocate_acceptance_sources(sdd: dict) -> bool:
    """Apply recorded locations when an explicit reopen creates active inputs."""
    record = sdd.get("acceptance") or {}
    if not record.get("declaration"):
        return False
    declaration = deepcopy(record["declaration"])
    for entry in sdd.get("archive_relocations", []):
        source, destination = entry["source_root"], entry["archive_root"]

        def moved(value):
            return destination + value[len(source):] if value == source or value.startswith(source + "/") else value

        declaration["spec"] = moved(declaration["spec"])
        declaration["sources"] = [moved(value) for value in declaration.get("sources", [])]
        for criterion in declaration["criteria"]:
            criterion["source"] = moved(criterion["source"])
    declaration = normalize_declaration(declaration)
    if declaration == record["declaration"]:
        return False
    record["declaration"] = declaration
    record["declaration_sha256"] = digest(declaration)
    return True


def relocate(repo: Path, work: dict, occurred_at: int) -> list[dict]:
    """Mutate only current references after matching every known bound file.

    Frozen verification and acceptance runs remain untouched. Active acceptance
    declarations get new source locations and therefore require an explicit
    reanchor and fresh checks. Completed acceptance is immutable history.
    """
    sdd = work.get("sdd") or {}
    if sdd.get("provider") != "openspec":
        return []
    repo = repo.resolve()
    archive = repo / "openspec/changes/archive"
    if not _unlinked(repo, archive) or not archive.is_dir():
        return []
    refs = _references(sdd)
    roots = sorted({root for value in refs if (root := _root(value))})
    migrations = []
    for source in roots:
        if any(entry.get("source_root") == source for entry in sdd.get("archive_relocations", [])):
            continue
        original = repo / source
        if original.exists() or original.is_symlink():
            continue
        name = PurePosixPath(source).name
        candidates = [path for path in archive.iterdir()
                      if path.name == name or re.fullmatch(r"\d{4}-\d{2}-\d{2}-" + re.escape(name), path.name)]
        # Multiple matching names are ambiguous even if only one has matching bytes.
        if len(candidates) != 1:
            continue
        target = candidates[0]
        expected = {value[len(source) + 1:]: sha for value, sha in refs.items()
                    if value.startswith(source + "/")}
        if not expected or not _unlinked(repo, target) or not target.is_dir():
            continue
        try:
            if not all(_unlinked(repo, target / suffix) and (target / suffix).is_file()
                       and hashlib.sha256((target / suffix).read_bytes()).hexdigest() == sha
                       for suffix, sha in expected.items()):
                continue
        except (OSError, ValueError, RuntimeError):
            continue
        destination = target.relative_to(repo).as_posix()

        def moved(value):
            if isinstance(value, str) and (value == source or value.startswith(source + "/")):
                return destination + value[len(source):]
            return value

        sdd["native_ref"] = moved(sdd.get("native_ref"))
        for item in sdd.get("artifacts", []):
            item["path"] = moved(item["path"])
        workflow = sdd.get("workflow") or {}
        if workflow:
            workflow["artifacts"] = [moved(value) for value in workflow.get("artifacts", [])]
            workflow["artifact_hashes"] = {moved(key): value for key, value in workflow.get("artifact_hashes", {}).items()}
        binding = sdd.get("spec_binding") or {}
        for field in ("spec", "criteria"):
            if field in binding:
                binding[field]["path"] = moved(binding[field]["path"])
        scope = sdd.get("plan_scope") or {}
        for plan in scope.get("plans", []):
            plan["path"] = moved(plan["path"])
        if "patterns" in scope:
            scope["patterns"] = [moved(value) for value in scope["patterns"]]
        state = sdd.get("native_state") or {}
        if state.get("source"):
            state["source"] = ", ".join(moved(value) for value in state["source"].split(", "))
        entry = {"source_root": source, "archive_root": destination,
                 "verified_hashes": expected, "occurred_at": occurred_at}
        sdd.setdefault("archive_relocations", []).append(entry)
        migrations.append(entry)
    if migrations and work.get("status") != "completed":
        relocate_acceptance_sources(sdd)
    return migrations
