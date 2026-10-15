"""Canonical filesystem ownership checks and structured patch destinations."""

import re
from pathlib import Path


def inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def repo_file(repo: Path, value: str) -> Path:
    path = Path(value)
    path = (path if path.is_absolute() else repo / path).resolve()
    if not inside(path, repo):
        raise ValueError("PATH_OUTSIDE_REPOSITORY")
    return path


def provider_artifact(repo: Path, value: str, dirs: list[str]) -> Path:
    path = repo_file(repo, value)
    if not any(inside(path, repo / directory) for directory in dirs):
        raise ValueError("NATIVE_WORKFLOW_ARTIFACT_OUTSIDE_PROVIDER_DIRS")
    return path


def patch_targets(text: str) -> list[str]:
    return [match.group(1).strip() for match in re.finditer(
        r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", text, re.M)]
