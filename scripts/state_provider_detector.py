#!/usr/bin/env python3
"""Detect the recommended execution-state provider without assuming tool-version formats."""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any, Dict, List, Optional


def _bounded_find(root: pathlib.Path, names: set[str], max_depth: int = 6) -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    root = root.resolve()
    excluded_segments = {"templates", "template", "examples", "example", ".agents", "node_modules"}
    install_roots = {"_bmad", ".bmad", "bmad"}
    for path in root.rglob("*"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if len(rel.parts) > max_depth:
            continue
        lower_parts = {x.lower() for x in rel.parts}
        if lower_parts & excluded_segments:
            continue
        # BMAD installation/resource roots are markers, not execution output. A real
        # sprint-status should live in an output/workspace area or explicit config.
        if rel.parts and rel.parts[0].lower() in install_roots:
            continue
        if path.is_file() and path.name.lower() in names:
            out.append(path)
    return sorted(out)


def detect(root: pathlib.Path) -> Dict[str, Any]:
    root = root.resolve()
    sprint_files = _bounded_find(root, {"sprint-status.yaml", "sprint-status.yml"})
    bmad_markers = [p for p in [root / "_bmad", root / ".bmad", root / "bmad"] if p.exists()]
    openspec_markers = [p for p in [root / "openspec", root / ".openspec"] if p.exists()]

    candidates: List[Dict[str, Any]] = []
    if sprint_files:
        candidates.append({
            "provider": "bmad",
            "authority_mode": "native",
            "confidence": "high",
            "evidence": [str(p.relative_to(root)) for p in sprint_files],
            "native_state_ref": str(sprint_files[0].relative_to(root)),
            "reason": "native sprint-status file detected",
        })
    elif bmad_markers:
        candidates.append({
            "provider": "bmad",
            "authority_mode": "hybrid",
            "confidence": "medium",
            "evidence": [str(p.relative_to(root)) for p in bmad_markers],
            "native_state_ref": None,
            "reason": "BMAD markers detected but no sprint-status file found",
        })

    if openspec_markers:
        candidates.append({
            "provider": "openspec",
            "authority_mode": "hybrid",
            "confidence": "high",
            "evidence": [str(p.relative_to(root)) for p in openspec_markers],
            "native_state_ref": None,
            "reason": "OpenSpec owns planning/artifact state; orchestrator augments execution state",
        })

    if not candidates:
        candidates.append({
            "provider": "generic",
            "authority_mode": "orchestrator",
            "confidence": "default",
            "evidence": [],
            "native_state_ref": None,
            "reason": "no supported native execution-state provider detected",
        })

    preferred = candidates[0] if len(candidates) == 1 else None
    return {
        "status": "DETECTED" if preferred else "MULTIPLE_CANDIDATES",
        "preferred": preferred,
        "candidates": candidates,
        "requires_authority_resolution": preferred is None,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", nargs="?", default=".")
    args = p.parse_args(argv)
    print(json.dumps(detect(pathlib.Path(args.root)), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
