#!/usr/bin/env python3
"""Conservative discovery of actionable requirement sources.

This module never invents product scope. It only identifies repository artifacts that are
strong enough to be considered requirement candidates for the Start Intent Router.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

import requirement_identity
import state_provider_detector

MAX_BYTES = 200_000
EXCLUDED_DIRS = {".git", ".orchestrator", ".agents", ".claude", ".codex", ".pi", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__"}
GENERIC_NAMES = {
    "requirements.md", "requirement.md", "spec.md", "specification.md", "prd.md",
    "product-requirements.md", "product_requirements.md", "需求.md", "规格.md", "产品需求.md",
}
STRONG_HEADINGS = re.compile(
    r"(?im)^#{1,4}\s*(requirements?|需求|功能需求|product requirements?|goals?|目标|features?|功能|acceptance criteria|验收标准)\b"
)
ACTIONABLE_TERMS = re.compile(
    r"(?i)\b(must|shall|should|user story|acceptance criteria|endpoint|api|feature|requirement)\b|必须|需要|应当|验收|接口|功能|需求"
)


def _safe_text(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _candidate(path: Path, repo: Path, source_type: str, confidence: str, reason: str, title: str | None = None, *, provider: str = "generic", native_id: str | None = None) -> dict[str, Any]:
    text = _safe_text(path)
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    requirement_id = requirement_identity.stable_requirement_id(
        provider=provider, source_type=source_type, source_path=rel, native_id=native_id, request_text=text
    )
    return {
        "id": f"{source_type}:{rel}",
        "requirement_id": requirement_id,
        "revision_id": requirement_identity.revision_id(text),
        "native_id": native_id,
        "source_type": source_type,
        "path": rel,
        "confidence": confidence,
        "reason": reason,
        "title": title or path.stem.replace("-", " ").replace("_", " ").strip(),
        "request_text": text.strip(),
    }


def _openspec(repo: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    roots = [p for p in [repo / "openspec" / "changes", repo / ".openspec" / "changes"] if p.exists() and p.is_dir()]
    for root in roots:
        for change in sorted(p for p in root.iterdir() if p.is_dir() and p.name.lower() not in {"archive", "archived"}):
            docs = [change / n for n in ["proposal.md", "spec.md", "design.md", "tasks.md"] if (change / n).exists()]
            if not docs:
                docs = sorted([p for p in change.glob("*.md") if p.is_file()])[:4]
            if not docs:
                continue
            primary = next((p for p in docs if p.name == "proposal.md"), docs[0])
            c = _candidate(primary, repo, "openspec_change", "high", "active OpenSpec change artifact detected", change.name, provider="openspec", native_id=change.name)
            c["supporting_paths"] = [p.resolve().relative_to(repo.resolve()).as_posix() for p in docs]
            out.append(c)
    return out


def _load_bmad_native_state(repo: Path) -> tuple[Path | None, Any]:
    # Project config/native detector is authoritative for where BMAD emits execution state.
    refs: list[Path] = []
    cfg = repo / ".orchestrator" / "config.yaml"
    if cfg.exists() and yaml is not None:
        try:
            raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            sdd = ((raw.get("orchestrator") or {}).get("sdd") or {}) if isinstance(raw, dict) else {}
            if sdd.get("provider") == "bmad" and sdd.get("native_state_ref"):
                refs.append(repo / str(sdd["native_state_ref"]))
        except Exception:
            pass
    detected = state_provider_detector.detect(repo)
    for c in detected.get("candidates", []):
        if c.get("provider") == "bmad" and c.get("native_state_ref"):
            refs.append(repo / str(c["native_state_ref"]))
    for path in refs:
        if path.exists() and yaml is not None:
            try:
                return path.resolve(), yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                return path.resolve(), None
    return None, None


def _story_status_items(doc: Any) -> list[tuple[str, str, str | None]]:
    """Return (native_id, status, explicit_path) for supported BMAD state shapes."""
    out: list[tuple[str, str, str | None]] = []
    if not isinstance(doc, dict):
        return out
    dev = doc.get("development_status") or doc.get("developmentStatus")
    if isinstance(dev, dict):
        for key, value in dev.items():
            if isinstance(value, str):
                out.append((str(key), value, None))
            elif isinstance(value, dict):
                out.append((str(value.get("id") or key), str(value.get("status") or "unknown"), value.get("path") or value.get("file")))
    stories = doc.get("stories") or doc.get("work_items") or doc.get("workItems")
    if isinstance(stories, list):
        for item in stories:
            if isinstance(item, dict) and (item.get("id") or item.get("story_id")):
                out.append((str(item.get("id") or item.get("story_id")), str(item.get("status") or "unknown"), item.get("path") or item.get("file")))
    return out


def _find_bmad_story(base: Path, native_id: str, explicit_path: str | None) -> Path | None:
    if explicit_path:
        p = (base / explicit_path).resolve() if not Path(explicit_path).is_absolute() else Path(explicit_path)
        if p.exists() and p.is_file():
            return p
    tokens = {native_id.lower(), native_id.lower().replace("_", "-"), native_id.lower().replace("-", "_")}
    candidates: list[Path] = []
    for p in base.rglob("*.md"):
        rel = p.relative_to(base).as_posix().lower()
        if any(x in rel for x in ["/templates/", "/template/", "/examples/", "/example/"]):
            continue
        low = p.stem.lower()
        if any(tok == low or low.startswith(tok + "-") or low.startswith(tok + "_") for tok in tokens):
            candidates.append(p)
    return sorted(candidates)[0] if candidates else None


def _bmad(repo: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    state_path, doc = _load_bmad_native_state(repo)
    if state_path is None:
        return out, diagnostics
    state_ref = state_path.relative_to(repo.resolve()).as_posix()
    if doc is None:
        diagnostics.append({
            "code": "BMAD_NATIVE_STATE_UNREADABLE",
            "source_ref": state_ref,
            "detail": "BMAD native sprint state exists but could not be parsed; do not guess work items from templates or prose.",
            "next_action": "repair_or_select_bmad_native_state",
        })
        return out, diagnostics
    items = _story_status_items(doc)
    if not items:
        diagnostics.append({
            "code": "BMAD_NATIVE_STATE_FORMAT_UNSUPPORTED",
            "source_ref": state_ref,
            "detail": "BMAD native sprint state uses an unsupported/unknown work-item shape.",
            "supported_formats": ["development_status-v1", "stories/work_items-v1"],
            "next_action": "locate_supported_bmad_work_items",
        })
        return out, diagnostics
    completed = {"done", "completed", "closed", "archived"}
    # Real story artifacts are expected next to/under the native output area, not in the
    # BMAD installation/resource tree.
    base = state_path.parent
    for native_id, status, explicit_path in items:
        normalized = status.strip().lower().replace("_", "-")
        if normalized in completed:
            continue
        story = _find_bmad_story(base, native_id, explicit_path)
        if story is None:
            diagnostics.append({
                "code": "BMAD_WORK_ITEM_ARTIFACT_MISSING",
                "source_ref": state_ref,
                "native_id": native_id,
                "native_status": status,
                "explicit_path": explicit_path,
                "detail": "Native BMAD state references an actionable work item, but no matching story artifact was found in the output area.",
                "next_action": "locate_bmad_story_artifact",
            })
            continue
        text = _safe_text(story)
        if len(text.strip()) < 40:
            diagnostics.append({
                "code": "BMAD_WORK_ITEM_ARTIFACT_INSUFFICIENT",
                "source_ref": story.relative_to(repo.resolve()).as_posix(),
                "native_id": native_id,
                "native_status": status,
                "detail": "BMAD story artifact is too small to serve as an actionable requirement source.",
                "next_action": "complete_bmad_story_artifact",
            })
            continue
        c = _candidate(story, repo, "bmad_work_item", "high", f"BMAD native sprint status={status}", story.stem, provider="bmad", native_id=native_id)
        c["native_status"] = status
        c["native_state_ref"] = state_ref
        c["supported_state_format"] = "development_status|stories-v1"
        out.append(c)
    return out, diagnostics

def _generic(repo: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        current = Path(dirpath)
        try:
            rel = current.relative_to(repo)
        except ValueError:
            continue
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        if len(rel.parts) > 4:
            dirnames[:] = []
        for name in filenames:
            p = current / name
            low = name.lower()
            if low in GENERIC_NAMES:
                paths.append(p)
            elif rel.parts and rel.parts[0].lower() in {"docs", "requirements", "specs", "product"} and p.suffix.lower() in {".md", ".txt"}:
                if any(t in low for t in ["require", "spec", "prd", "feature", "goal", "需求", "规格"]):
                    paths.append(p)
    for p in sorted(set(paths)):
        text = _safe_text(p)
        if len(text.strip()) < 80:
            continue
        if not (STRONG_HEADINGS.search(text) or len(ACTIONABLE_TERMS.findall(text)) >= 2):
            continue
        candidates.append(_candidate(p, repo, "requirement_document", "high", "dedicated requirement/specification document detected"))

    # A dedicated requirement/specification artifact outranks README. Only use README as a
    # fallback when no stronger generic requirement source exists, preventing duplicate intake
    # candidates for the same project scope.
    readme = repo / "README.md"
    if not candidates and readme.exists():
        text = _safe_text(readme)
        # README is only auto-actionable when it contains an explicit requirements/goals/features
        # section plus concrete requirement language. A normal install/usage README must not start work.
        if len(text.strip()) >= 120 and STRONG_HEADINGS.search(text) and ACTIONABLE_TERMS.search(text):
            candidates.append(_candidate(readme, repo, "readme_requirement", "high", "README contains an explicit requirement/goal/feature section"))
    return candidates


def discover(repo: Path, provider: str | None = None) -> dict[str, Any]:
    repo = repo.resolve()
    provider = (provider or "generic").lower()
    candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    if provider == "openspec":
        candidates.extend(_openspec(repo))
    elif provider == "bmad":
        bmad_candidates, diagnostics = _bmad(repo)
        candidates.extend(bmad_candidates)
    else:
        # Generic projects may still have requirement docs even when no SDD is installed.
        candidates.extend(_generic(repo))

    # SDD authority wins. Do not mix generic README/docs with native SDD candidates unless the
    # selected provider is generic, otherwise the same scope can appear twice with different authority.
    actionable = [c for c in candidates if c.get("confidence") == "high" and c.get("request_text")]
    # Unknown/missing BMAD native artifacts are not equivalent to "no requirement".
    # Fail closed with sourceful diagnostics rather than keyword-guessing a story.
    if provider == "bmad" and diagnostics:
        status = "ACTION_REQUIRED"
    else:
        status = "NONE" if not actionable else ("ONE" if len(actionable) == 1 else "MULTIPLE")
    return {
        "status": status,
        "provider": provider,
        "count": len(actionable),
        "candidates": actionable,
        "diagnostics": diagnostics,
        "next_action": (diagnostics[0].get("next_action") if diagnostics else None),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repo", nargs="?", default=".")
    p.add_argument("--provider", choices=["generic", "openspec", "bmad"], default="generic")
    args = p.parse_args(argv)
    result = discover(Path(args.repo), args.provider)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"NONE", "ONE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
