"""Requirement discovery, evidence, numbered documents, and requirement commands."""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from scripts.cao_defaults import REQUIREMENT_DIR
from scripts.cao_runtime import (
    config,
    ensure_initialized,
    locked_command,
    matches_any,
    rel,
    repo_root,
    sha256_file,
)


def requirement_slug(title: str) -> str:
    """Turn a requirement title into a readable filename stem.

    Chinese titles keep their characters; everything unsafe becomes a hyphen.
    """
    slug = re.sub(r"[^\w-]+", "-", str(title).strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:60]


def requirement_filename(number: int, slug: str, date: str) -> str:
    stem = f"{number:03d}-requirement"
    if slug:
        stem = f"{stem}-{slug}"
    return f"{stem}-{date}.md"


def next_requirement_number(repo: Path) -> int:
    """Next requirement number, derived from existing files so it never collides."""
    directory = repo / REQUIREMENT_DIR
    if not directory.is_dir():
        return 1
    highest = 0
    for path in directory.glob("*.md"):
        match = re.match(r"^(\d+)-", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def new_requirement_path(repo: Path, title: str, date: str | None = None) -> Path:
    """Allocate the numbered, dated path for a new requirement document."""
    day = date or time.strftime("%Y-%m-%d", time.localtime())
    return repo / REQUIREMENT_DIR / requirement_filename(next_requirement_number(repo), requirement_slug(title), day)


def requirement_skeleton(title: str, number: str, date: str | None, cfg: dict[str, Any]) -> str:
    """Starter content for a newly allocated requirement document."""
    day = date or time.strftime("%Y-%m-%d", time.localtime())
    language = str((cfg.get("language") or {}).get("user") or "zh-CN")
    if language.lower().startswith("zh"):
        return (
            f"# {title}\n\n"
            f"- 需求编号: {number}\n"
            f"- 创建日期: {day}\n"
            f"- 状态: draft\n\n"
            f"## 需求描述\n\n<!-- 在此描述需求。 -->\n\n"
            "<!-- 技术选择只记录用户指定、仓库既有事实或用户明确授权代选的内容，并注明来源。\n"
            "未明确的技术栈标记为待确认，先询问用户；模型建议不能写成用户已确认。 -->\n\n"
            f"## 验收标准\n\n<!-- 完成时必须满足的条件。 -->\n\n"
            f"## 范围外\n\n<!-- 明确不属于本次需求的内容（可选）。 -->\n"
        )
    return (
        f"# {title}\n\n"
        f"- Requirement ID: {number}\n"
        f"- Created: {day}\n"
        f"- Status: draft\n\n"
        f"## Description\n\n<!-- Describe the requirement here. -->\n\n"
        "<!-- Record technology choices only with user, pre-existing repository or explicit delegation evidence.\n"
        "Leave unspecified choices pending and ask the user; an Agent proposal is not user confirmation. -->\n\n"
        f"## Acceptance criteria\n\n<!-- What must be true when this is done. -->\n\n"
        f"## Out of scope\n\n<!-- What is explicitly not part of this requirement (optional). -->\n"
    )


def requirement_files(repo: Path, cfg: dict[str, Any]) -> list[Path]:
    sdd = cfg.get("sdd") or {}
    files: set[Path] = set()
    # CAO's own numbered requirement documents always count, including in
    # projects initialized before the glob was added to the default config.
    patterns = list(dict.fromkeys(list(sdd.get("requirement_globs") or []) + [str(REQUIREMENT_DIR / "**" / "*.md")]))
    for pattern in patterns:
        for item in glob.glob(str(repo / pattern), recursive=True):
            p = Path(item)
            if p.is_file() and not matches_any(rel(p, repo), sdd.get("exclude_globs") or []):
                files.add(p.resolve())
    return sorted(files)


def requirement_evidence(repo: Path, requirement: Path) -> dict[str, str]:
    return {"path": rel(requirement, repo), "sha256": sha256_file(requirement)}


def resolve_requirement(repo: Path, path: str | None) -> Path | None:
    if path:
        candidate = Path(path)
        p = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
        return p if p.is_file() else None
    files = requirement_files(repo, config(repo))
    return files[0] if len(files) == 1 else None


@locked_command
def cmd_requirement_next_id(args: argparse.Namespace) -> int:
    """Allocate the next numbered requirement path without writing any content.

    CAO allocates the number and creates the file with a skeleton; the Agent
    edits that file. Creating it here matters because a mutating tool call is
    gated until a Work Item exists, so an Agent that has to `write` a brand new
    requirement file can be blocked before `cao start` can ever run.
    """
    repo = repo_root(args.repo or os.getcwd())
    ensure_initialized(repo)
    title = args.title or "requirement"
    path = new_requirement_path(repo, title, args.date)
    rel_path = rel(path, repo)
    number = re.match(r"^(\d+)-", path.name)
    number = number.group(1) if number else "001"
    created = False
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(requirement_skeleton(title, number, args.date, config(repo)), encoding="utf-8")
        created = True
    print(json.dumps({
        "ok": True,
        "number": number,
        "title": title,
        "path": rel_path,
        "absolute_path": str(path.resolve()),
        "directory": REQUIREMENT_DIR.as_posix(),
        "created": created,
        "next": f"Edit `{rel_path}` with the requirement content, then run `cao start --requirement {rel_path}`",
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_requirement_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo or os.getcwd())
    ensure_initialized(repo)
    files = [p for p in requirement_files(repo, config(repo)) if REQUIREMENT_DIR.as_posix() in p.as_posix()]
    items = []
    for p in sorted(files)[: max(0, args.limit)]:
        items.append({
            "path": rel(p, repo),
            "name": p.name,
            "sha256": sha256_file(p),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime)),
        })
    print(json.dumps({"ok": True, "directory": REQUIREMENT_DIR.as_posix(), "requirements": items, "next": f"{next_requirement_number(repo):03d}"}, ensure_ascii=False, indent=2))
    return 0
