#!/usr/bin/env python3
"""Apply evidence-bearing semantic resolutions to a Work Facts draft.

The resolver makes Agent/human interpretation explicit and auditable. It refuses heuristic
claims as final evidence and requires negative proof for non-authoritative false claims.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ALLOWED_STRENGTH = {"authoritative", "observed", "derived"}
WORK_SECTIONS = {"ambiguity", "complexity", "scope", "risk", "novelty", "verification"}
INT_PATHS = {
    "complexity.predicted_components",
    "scope.files_estimate",
    "scope.modules_touched",
    "scope.deployable_units",
}


def get_path(data: Dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    return cur


def set_path(data: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = data
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            raise KeyError(path)
        cur = cur[part]
    if parts[-1] not in cur:
        raise KeyError(path)
    cur[parts[-1]] = value


def validate_resolution(r: Dict[str, Any]) -> None:
    required = ["path", "value", "source_type", "source", "evidence", "strength"]
    missing = [k for k in required if k not in r]
    if missing:
        raise ValueError(f"resolution missing fields {missing}: {r}")
    path = r["path"]
    if not isinstance(path, str) or path.split(".", 1)[0] not in WORK_SECTIONS:
        raise ValueError(f"resolution path is not a Work Fact: {path}")
    value = r["value"]
    if path in INT_PATHS:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"resolution value must be a non-negative integer: {path}")
    elif not isinstance(value, bool):
        raise ValueError(f"resolution value must be boolean: {path}")
    if r["strength"] not in ALLOWED_STRENGTH:
        raise ValueError(f"resolution strength must be one of {sorted(ALLOWED_STRENGTH)}; heuristic is hint-only")
    if r["value"] is False and r["strength"] != "authoritative" and not r.get("negative_proof"):
        raise ValueError(f"false resolution requires negative_proof unless authoritative: {r['path']}")
    if r["value"] is None:
        raise ValueError(f"resolution cannot finalize null: {r['path']}")


def apply_resolutions(draft: Dict[str, Any], resolution_doc: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(draft)
    provenance = out.setdefault("provenance", {})
    changes: List[Dict[str, Any]] = []
    for r in resolution_doc.get("resolutions", []):
        validate_resolution(r)
        path = r["path"]
        old = get_path(out, path)
        if old is not None and old != r["value"]:
            raise ValueError(
                f"resolution conflicts with an already proven fact in this extraction snapshot: {path}={old!r} vs {r['value']!r}; create a new snapshot or reconcile evidence"
            )
        set_path(out, path, r["value"])
        entry = {
            "value": r["value"],
            "source_type": r["source_type"],
            "source": r["source"],
            "evidence": r["evidence"],
            "strength": r["strength"],
            "resolver": r.get("resolver", "semantic_resolver"),
        }
        if r.get("negative_proof"):
            entry["negative_proof"] = r["negative_proof"]
        provenance.setdefault(path, []).append(entry)
        changes.append({"path": path, "old": old, "new": r["value"]})
    out.setdefault("resolution", {})["applied"] = changes
    out["resolution"]["source"] = resolution_doc.get("source", "resolution_doc")
    # Refresh queue without knowing Decision Engine implementation.
    queue: List[str] = []
    for section in ("ambiguity", "complexity", "scope", "risk", "novelty", "verification"):
        for key, value in out.get(section, {}).items():
            if value is None:
                queue.append(f"{section}.{key}")
    out.setdefault("extraction", {})["resolution_queue"] = queue
    return out


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("draft", type=Path)
    p.add_argument("resolutions", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--pretty", action="store_true")
    args = p.parse_args(argv)
    try:
        draft = json.loads(args.draft.read_text(encoding="utf-8"))
        resolutions = json.loads(args.resolutions.read_text(encoding="utf-8"))
        result = apply_resolutions(draft, resolutions)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty or args.output else None, sort_keys=bool(args.pretty or args.output))
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
