#!/usr/bin/env python3
"""Parse native execution state from the project's actual configured source.

A projection is produced inside a single call: locate source -> read once -> use the same
bytes for both the digest and the parse -> map the native status for that version.
There is no token to carry across processes: only the result of *this* call is trustworthy.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    yaml = None

try:
    import state_provider_detector as detector
except ImportError:  # pragma: no cover - loaded by path in tests
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "state_provider_detector", Path(__file__).resolve().parent / "state_provider_detector.py")
    detector = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(detector)

# Conceptual default mapping. A repository may carry version-specific guidance; when the
# parsed status is not listed here it is reported as unsupported rather than guessed.
STATUS_TO_CANONICAL: Dict[str, Tuple[str, str]] = {
    "backlog": ("planning", "pending"),
    "draft": ("planning", "pending"),
    "ready-for-dev": ("planning", "ready"),
    "ready": ("planning", "ready"),
    "approved": ("planning", "ready"),
    "in-progress": ("implementation", "in_progress"),
    "in_progress": ("implementation", "in_progress"),
    "review": ("review", "in_progress"),
    "done": ("closed", "completed"),
    "completed": ("closed", "completed"),
}

CONTAINER_KEYS = ("development_status", "sprint_status", "stories", "story_status")


def _diagnostic(code: str, message: str, next_action: str, **extra: Any) -> Dict[str, Any]:
    diag: Dict[str, Any] = {"error": code, "message": message, "next_action": next_action}
    diag.update(extra)
    return diag


class NativeProjection:
    """Internal result type produced by `resolve_projection` inside the current call.

    It is deliberately not importable from serialized data and provides no constructor for
    external dicts. Being an instance of this class is a convenience, never a proof: callers
    must still own the verification that produced it. Type checks must never replace that.
    """

    __slots__ = ("work_item_id", "requirement_revision", "source_ref", "content_digest",
                 "expected_state_revision", "phase", "status_detail", "progress",
                 "native_state_revision", "container_key")

    def __init__(self, *, work_item_id: str, requirement_revision: Optional[str], source_ref: str,
                 content_digest: str, expected_state_revision: Optional[str], phase: str,
                 status_detail: str, progress: Optional[Dict[str, int]],
                 native_state_revision: str, container_key: Optional[str] = None) -> None:
        self.work_item_id = work_item_id
        self.requirement_revision = requirement_revision
        self.source_ref = source_ref
        self.content_digest = content_digest
        self.expected_state_revision = expected_state_revision
        self.phase = phase
        self.status_detail = status_detail
        self.progress = progress
        self.native_state_revision = native_state_revision
        self.container_key = container_key

    def as_record(self) -> Dict[str, Any]:
        """Audit record of the observation. Not a trust carrier: it cannot be imported back."""
        return {
            "work_item_id": self.work_item_id,
            "requirement_revision": self.requirement_revision,
            "source_ref": self.source_ref,
            "content_digest": self.content_digest,
            "expected_state_revision": self.expected_state_revision,
            "phase": self.phase,
            "status_detail": self.status_detail,
            "progress": dict(self.progress or {}),
            "native_state_revision": self.native_state_revision,
            "container_key": self.container_key,
        }


def _locate_source(repo: Path, state: Optional[dict], native_state_ref: Optional[str]) -> Optional[Path]:
    candidates: list[Path] = []
    for ref in [native_state_ref, (state or {}).get("authority", {}).get("native_state_ref")]:
        if ref:
            path = Path(ref)
            candidates.append(path if path.is_absolute() else repo / path)
    detection = detector.detect(repo)
    preferred = detection.get("preferred") or {}
    if preferred.get("native_state_ref"):
        candidates.append(repo / preferred["native_state_ref"])
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def _status_container(doc: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not isinstance(doc, dict):
        return None
    for key in CONTAINER_KEYS:
        value = doc.get(key)
        if isinstance(value, dict):
            return key, value
    # A bare mapping of work item -> status is also a valid native container.
    entries = {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, (str, dict))}
    if entries:
        return None, entries
    return None


def _native_status(entry: Any) -> Optional[str]:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("status", "state", "story_status"):
            if isinstance(entry.get(key), str):
                return entry[key]
    return None


def _progress(entry: Any) -> Optional[Dict[str, int]]:
    if not isinstance(entry, dict):
        return None
    tasks = entry.get("tasks")
    if isinstance(tasks, dict):
        return {"completed": sum(1 for v in tasks.values() if v), "total": len(tasks)}
    return None


def _select_work_item(container: Dict[str, Any], state: Optional[dict]) -> Tuple[Optional[str], str]:
    wanted = None
    if state:
        wanted = (state.get("work_item") or {}).get("id") or state.get("work_item_id")
    keys = [str(k) for k in container.keys()]
    if wanted and str(wanted) in keys:
        return str(wanted), "state_work_item"
    if wanted:
        return None, "NATIVE_TASK_MISSING"
    if len(keys) == 1:
        return keys[0], "single_entry"
    return None, "NATIVE_WORK_ITEM_AMBIGUOUS"


def resolve_projection(repo: Path, state: Optional[dict] = None, *,
                       native_state_ref: Optional[str] = None) -> Tuple[Optional[NativeProjection], Dict[str, Any]]:
    """Read once, then use the same bytes for digest and for parsing."""
    repo = Path(repo).resolve()
    source = _locate_source(repo, state, native_state_ref)
    if source is None:
        return None, _diagnostic(
            "NATIVE_SOURCE_UNCONFIGURED",
            "no native status source is configured for this project",
            "configure_native_state_source",
        )
    try:
        raw = source.read_bytes()
    except OSError as exc:
        return None, _diagnostic("NATIVE_SOURCE_UNCONFIGURED", f"native source unreadable: {exc}",
                                 "configure_native_state_source")
    if yaml is None:
        return None, _diagnostic("NATIVE_FORMAT_UNKNOWN", "PyYAML is required to parse native state",
                                 "install_pyyaml")
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, _diagnostic("NATIVE_FORMAT_UNKNOWN", f"native source is not parsable: {exc}",
                                 "inspect_native_state_format")
    container = _status_container(doc)
    if container is None:
        return None, _diagnostic("NATIVE_FORMAT_UNKNOWN",
                                 "native source has no recognizable status container",
                                 "inspect_native_state_format")
    container_key, entries = container
    work_item_id, note = _select_work_item(entries, state)
    if work_item_id is None:
        candidates = sorted(str(k) for k in entries)[:20]
        if note == "NATIVE_TASK_MISSING":
            return None, _diagnostic(
                "NATIVE_TASK_MISSING",
                "the native source does not contain the current work item",
                "run_native_intake", candidates=candidates)
        return None, _diagnostic(
            "NATIVE_WORK_ITEM_AMBIGUOUS",
            "several native work items are present and none was selected",
            "select_native_work_item", candidates=candidates)
    entry = entries[work_item_id]
    native_status = _native_status(entry)
    if native_status is None:
        return None, _diagnostic("NATIVE_FORMAT_UNKNOWN",
                                 f"work item {work_item_id!r} has no readable native status",
                                 "inspect_native_state_format")
    canonical = STATUS_TO_CANONICAL.get(str(native_status).strip().lower())
    if canonical is None:
        return None, _diagnostic(
            "NATIVE_STATUS_UNSUPPORTED",
            f"native status {native_status!r} is not part of the supported lifecycle for this version",
            "discover_native_status_mapping",
            observed_status=native_status,
            supported=sorted(STATUS_TO_CANONICAL),
        )
    try:
        source_ref = str(source.relative_to(repo))
    except ValueError:
        source_ref = str(source)
    digest = hashlib.sha256(raw).hexdigest()
    phase, status_detail = canonical
    expected = None
    if state:
        expected = ((state.get("authority") or {}).get("last_native_sync") or {}).get("native_revision")
    return NativeProjection(
        work_item_id=work_item_id,
        requirement_revision=(state or {}).get("work_item", {}).get("requirement_revision"),
        source_ref=source_ref,
        content_digest=digest,
        expected_state_revision=expected,
        phase=phase,
        status_detail=status_detail,
        progress=_progress(entry),
        native_state_revision=digest,
        container_key=container_key,
    ), {}


def detect_source_change(repo: Path, projection: NativeProjection) -> Dict[str, Any]:
    """Re-read the source before committing: detect parse-then-commit drift (best effort)."""
    path = Path(projection.source_ref)
    path = path if path.is_absolute() else Path(repo).resolve() / path
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"changed": True, "error": "NATIVE_SOURCE_UNCONFIGURED",
                "message": f"native source unreadable: {exc}", "next_action": "configure_native_state_source"}
    digest = hashlib.sha256(raw).hexdigest()
    if digest != projection.content_digest:
        return {
            "changed": True,
            "error": "NATIVE_SOURCE_CHANGED",
            "message": "native source changed after it was parsed; the projection is stale",
            "next_action": "re_run_native_sync",
            "observed_revision": digest,
            "expected_revision": projection.content_digest,
        }
    return {"changed": False, "observed_revision": digest}


def parse_state_document(text: str) -> Optional[Dict[str, Any]]:
    """Parse an already-read native document; used by tests and by the single-read path."""
    if yaml is None:
        return None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return doc if isinstance(doc, dict) else None


def main(argv: Optional[list] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo", nargs="?", default=".")
    args = p.parse_args(argv)
    projection, diag = resolve_projection(Path(args.repo))
    print(json.dumps(projection.as_record() if projection else diag, indent=2, ensure_ascii=False))
    return 0 if projection else 1


if __name__ == "__main__":
    raise SystemExit(main())
