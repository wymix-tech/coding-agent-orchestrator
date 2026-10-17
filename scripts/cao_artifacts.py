"""Work Item artifact references, confirmation, and refresh commands."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from scripts.cao_runtime import (
    ensure_initialized,
    locked_command,
    locked_repo,
    repo_root,
    sha256_file,
)
from scripts.cao_work_store import _refresh_work, resolve_work, save_work, work_summary
from scripts.path_guard import repo_file


def resolve_artifact_path(repo: Path, value: str) -> tuple[Path, str]:
    resolved = repo_file(repo, value)
    return resolved, resolved.relative_to(repo.resolve()).as_posix()


def artifact_reference(repo: Path, value: str, role: str | None = None, native_type: str | None = None, native_ref: str | None = None) -> dict[str, Any]:
    path, relative = resolve_artifact_path(repo, value)
    if not path.is_file():
        raise ValueError(f"Artifact file not found: {relative}")
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "role": role,
        "native_type": native_type,
        "native_ref": native_ref,
    }


@locked_command
def cmd_artifact_add(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    try:
        ref = artifact_reference(repo, args.path, args.role, args.native_type, args.native_ref)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "ARTIFACT_INVALID", "reason": str(exc)}, ensure_ascii=False, indent=2)); return 2
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    artifacts[:] = [a for a in artifacts if a.get("path") != ref["path"]]
    artifacts.append(ref)
    save_work(repo, work)
    print(json.dumps({"ok": True, "artifact": ref, "work_item": work_summary(work)}, ensure_ascii=False, indent=2)); return 0


@locked_repo
def mark_artifact_confirmed(repo: Path, work: dict[str, Any], value: str, confirmed_by: str = "user") -> dict[str, Any]:
    """Record explicit confirmation for the current content hash of a native artifact."""
    _refresh_work(repo, work)
    sdd = work.setdefault("sdd", {})
    artifacts = sdd.setdefault("artifacts", [])
    role = None
    native_type = None
    native_ref = sdd.get("native_ref")
    for existing in artifacts:
        if existing.get("path") == resolve_artifact_path(repo, value)[1]:
            role = existing.get("role")
            native_type = existing.get("native_type")
            native_ref = existing.get("native_ref") or native_ref
            break
    ref = artifact_reference(repo, value, role=role, native_type=native_type, native_ref=native_ref)
    ref.update({"confirmed_at": int(time.time()), "confirmed_by": confirmed_by, "status": "confirmed"})
    artifacts[:] = [existing for existing in artifacts if existing.get("path") != ref["path"]]
    artifacts.append(ref)
    save_work(repo, work)
    return ref


@locked_command
def cmd_artifact_confirm(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    try:
        ref = mark_artifact_confirmed(repo, work, args.path, args.confirmed_by)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "ARTIFACT_INVALID", "reason": str(exc)}, ensure_ascii=False, indent=2)); return 2
    print(json.dumps({"ok": True, "artifact": ref, "work_item": work_summary(work)}, ensure_ascii=False, indent=2)); return 0


def cmd_artifact_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    print(json.dumps({"ok": True, "work_item": work["id"], "artifacts": work.get("sdd", {}).get("artifacts", [])}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_artifact_remove(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    _, relative = resolve_artifact_path(repo, args.path)
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    before = len(artifacts)
    work["sdd"]["artifacts"] = [a for a in artifacts if a.get("path") != relative]
    save_work(repo, work)
    print(json.dumps({"ok": True, "removed": before - len(work["sdd"]["artifacts"]), "artifacts": work["sdd"]["artifacts"]}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_artifact_refresh(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    target = args.path
    refreshed = []
    for item in artifacts:
        if target:
            _, relative = resolve_artifact_path(repo, target)
            if item.get("path") != relative:
                continue
        p = repo / item["path"]
        if p.is_file():
            previous_sha = item.get("sha256")
            current_sha = sha256_file(p)
            item["sha256"] = current_sha
            if previous_sha and previous_sha != current_sha:
                item.pop("confirmed_at", None)
                item.pop("confirmed_by", None)
                item["status"] = "generated"
            refreshed.append(item)
    if target and not refreshed:
        print(json.dumps({"ok": False, "error": "ARTIFACT_NOT_FOUND", "path": target}, ensure_ascii=False, indent=2)); return 2
    save_work(repo, work)
    print(json.dumps({"ok": True, "refreshed": refreshed, "artifacts": artifacts}, ensure_ascii=False, indent=2)); return 0
