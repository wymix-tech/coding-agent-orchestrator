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
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

ALLOWED_STRENGTH = {"authoritative", "observed", "derived"}
REQUIRED_FIELDS = ["path", "value", "source_type", "source", "evidence", "strength"]
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
    required = REQUIRED_FIELDS
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
    # `authoritative` is a claim about the source, not a proof: a negative fact needs either
    # negative proof or a verification result, which apply_resolutions checks against the repo.
    if r["value"] is False and not r.get("negative_proof") and r["strength"] == "authoritative" \
            and not isinstance(r.get("evidence_record"), dict):
        raise ValueError(
            f"false resolution requires negative_proof or a verifiable evidence_record: {r['path']}")
    if r["value"] is None:
        raise ValueError(f"resolution cannot finalize null: {r['path']}")
    for field in ("source_type", "source", "evidence"):
        if not str(r.get(field) or "").strip():
            raise ValueError(
                f"resolution {field} must reference something checkable, not a placeholder: {r['path']}")


def _evidence_module():
    try:
        import evidence_provenance
        return evidence_provenance
    except ImportError:  # pragma: no cover - import shim only
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "evidence_provenance", Path(__file__).resolve().parent / "evidence_provenance.py")
        module = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(module)
        return module


def evidence_result(resolution: Dict[str, Any], repo: Path | None = None) -> Dict[str, Any]:
    """Verify the evidence a resolution cites. Returns the full result of *this* check."""
    record = resolution.get("evidence_record")
    if not isinstance(record, dict) or repo is None:
        return {"validation_status": "unverified", "reason_code": "EVIDENCE_UNVERIFIED"}
    ep = _evidence_module()
    result = ep.revalidate(
        repo, record, policy=ep.load_policy(repo),
        work_item_id=str(resolution.get("work_item_id") or "") or None,
        requirement_revision=str(resolution.get("requirement_revision") or "") or None,
    )
    return result if isinstance(result, dict) else {"validation_status": "unverified"}


def evidence_validation(resolution: Dict[str, Any], repo: Path | None = None) -> str:
    """A recorded evidence string is not a verification; only a checked record can be verified.

    Without a repo there is nothing to check against, which is `unverified`, never `verified`.
    """
    return str(evidence_result(resolution, repo).get("validation_status") or "unverified")


NEGATIVE_PROOF_ERRORS = {
    "NEGATIVE_PROOF_SHAPE_INVALID",
    "NEGATIVE_PROOF_SOURCE_UNRESOLVED",
    "NEGATIVE_PROOF_SCOPE_MISSING",
    "NEGATIVE_PROOF_CONTRADICTED",
    "NEGATIVE_PROOF_COUNT_MISMATCH",
}


def _proof_scopes(repo: Path, proof: Dict[str, Any]) -> tuple[list[Path] | None, str | None]:
    """Resolve the files a negative proof searched. `None` means the scope is unusable."""
    refs = proof.get("paths")
    if not isinstance(refs, list) or not refs:
        single = proof.get("ref")
        refs = [single] if single else []
    if not refs:
        return None, "NEGATIVE_PROOF_SCOPE_MISSING"
    root = Path(repo).resolve()
    resolved: list[Path] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            return None, "NEGATIVE_PROOF_SCOPE_MISSING"
        candidate = Path(ref)
        candidate = candidate if candidate.is_absolute() else root / candidate
        try:
            if not candidate.is_file():
                return None, "NEGATIVE_PROOF_SOURCE_UNRESOLVED"
            if root not in candidate.resolve().parents and candidate.resolve() != root:
                return None, "NEGATIVE_PROOF_SOURCE_UNRESOLVED"
        except OSError:
            return None, "NEGATIVE_PROOF_SOURCE_UNRESOLVED"
        resolved.append(candidate)
    return resolved, None


def check_negative_proof(proof: Any, repo: Path | None = None) -> Dict[str, Any]:
    """A negative fact is an observation, not a label: the search has to be re-runnable.

    `{"ref": "does-not-exist", "matches": 0}` claims a search that never happened. The
    scope is resolved and the search is redone here; only a search that really finds
    nothing can carry a false fact.
    """
    if not isinstance(proof, dict):
        return {"valid": False, "error": "NEGATIVE_PROOF_SHAPE_INVALID",
                "message": ("a bounded description is not a search: negative_proof must name the "
                            "scope that was searched (paths/ref) and how many matches it found")}
    query = proof.get("search") or proof.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"valid": False, "error": "NEGATIVE_PROOF_SHAPE_INVALID",
                "message": "negative_proof must name what was searched for (search/query)"}
    matches = proof.get("matches")
    if isinstance(matches, bool) or not isinstance(matches, int):
        return {"valid": False, "error": "NEGATIVE_PROOF_SHAPE_INVALID",
                "message": "negative_proof.matches must be an integer"}
    if repo is None:
        return {"valid": False, "error": "NEGATIVE_PROOF_SOURCE_UNRESOLVED",
                "message": "a negative proof cannot be checked without the project it searched"}
    scopes, error = _proof_scopes(repo, proof)
    if error or scopes is None:
        return {"valid": False, "error": error or "NEGATIVE_PROOF_SOURCE_UNRESOLVED",
                "message": "the negative proof names no file inside the project that was searched"}
    found = 0
    for path in scopes:
        try:
            found += path.read_text(encoding="utf-8", errors="replace").count(query)
        except OSError:
            return {"valid": False, "error": "NEGATIVE_PROOF_SOURCE_UNRESOLVED",
                    "message": f"{path.name} could not be read to redo the search"}
    if found != matches:
        return {"valid": False, "error": "NEGATIVE_PROOF_COUNT_MISMATCH",
                "message": (f"the negative proof records matches={matches} but the search finds "
                            f"{found} in the same scope"), "observed_matches": found}
    if found != 0:
        return {"valid": False, "error": "NEGATIVE_PROOF_CONTRADICTED",
                "message": f"the search found {found} occurrence(s), so this is not a negative fact",
                "observed_matches": found}
    return {"valid": True, "searched": [str(p) for p in scopes], "matches": found}


def apply_resolutions(draft: Dict[str, Any], resolution_doc: Dict[str, Any],
                      *, repo: Path | None = None) -> Dict[str, Any]:
    out = copy.deepcopy(draft)
    provenance = out.setdefault("provenance", {})
    changes: List[Dict[str, Any]] = []
    validations: List[Dict[str, Any]] = []
    for r in resolution_doc.get("resolutions", []):
        validate_resolution(r)
        result = evidence_result(r, repo)
        validation = str(result.get("validation_status") or "unverified")
        has_record = isinstance(r.get("evidence_record"), dict)
        if r.get("strength") == "authoritative" and has_record and validation != "verified":
            raise ValueError(
                f"authoritative resolution requires verified evidence, got {validation}: {r['path']}")
        proof_check = None
        if r.get("negative_proof") is not None:
            proof_check = check_negative_proof(r["negative_proof"], repo)
            if r.get("value") is False and not proof_check["valid"]:
                raise ValueError(
                    f"false resolution requires a negative proof that can be re-run: "
                    f"{proof_check['error']}: {proof_check['message']} ({r['path']})")
        if r.get("value") is False and r.get("negative_proof") is None and validation != "verified":
            raise ValueError(
                f"false resolution requires negative_proof or verified evidence, got {validation}: {r['path']}")
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
            # The Decision Engine consumes this, never `strength` alone.
            "verified_authority": validation == "verified",
            "verification_status": validation,
            "evidence_outcome": result.get("outcome"),
        }
        if isinstance(r.get("evidence_record"), dict):
            entry["evidence_id"] = r["evidence_record"].get("evidence_id")
        if r.get("negative_proof") is not None:
            entry["negative_proof"] = r["negative_proof"]
            entry["negative_proof_verified"] = bool(proof_check and proof_check.get("valid"))
            entry["negative_proof_check"] = {
                "error": (proof_check or {}).get("error"),
                "message": (proof_check or {}).get("message"),
                "observed_matches": (proof_check or {}).get("observed_matches"),
            }
        provenance.setdefault(path, []).append(entry)
        changes.append({"path": path, "old": old, "new": r["value"]})
        validations.append({
            "path": path,
            "validation_status": validation,
            "authority": "verified" if validation == "verified" else "self_declared",
        })
    out.setdefault("resolution", {})["applied"] = changes
    out["resolution"]["validation"] = validations
    out["resolution"]["source"] = resolution_doc.get("source", "resolution_doc")
    # Refresh queue without knowing Decision Engine implementation.
    queue: List[str] = []
    for section in ("ambiguity", "complexity", "scope", "risk", "novelty", "verification"):
        for key, value in out.get(section, {}).items():
            if value is None:
                queue.append(f"{section}.{key}")
    out.setdefault("extraction", {})["resolution_queue"] = queue
    return out


def value_type(path: str) -> str:
    return "non_negative_integer" if path in INT_PATHS else "boolean"


def write_resolution_template(output_dir: Path, facts: Dict[str, Any], *, source_ref: str | None = None) -> Path:
    """Publish a new scaffold without replacing any existing, possibly filled, file.

    Exclusive creation also protects inputs reached through aliases or hard links.
    Callers must report the returned path, which may differ on a repeated intake.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    template = build_resolution_template(facts, source_ref=source_ref)
    path = output_dir / "fact-resolutions.template.json"
    while True:
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(template, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
            return path
        except FileExistsError:
            path = output_dir / f"fact-resolutions.{uuid.uuid4().hex}.template.json"


def build_resolution_template(facts: Dict[str, Any], *, source_ref: str | None = None) -> Dict[str, Any]:
    """Emit a fillable scaffold for every unresolved Work Fact.

    Intake can report dozens of missing facts without saying how to answer them. The
    scaffold removes that dead end: one entry per queued fact, pre-typed, with any
    heuristic hint attached as inspection guidance that still cannot finalize the fact.
    """
    extraction = facts.get("extraction") or {}
    queue = [p for p in (extraction.get("resolution_queue") or []) if isinstance(p, str)]
    hints: Dict[str, Dict[str, Any]] = {}
    for suggestion in (extraction.get("suggestions") or []):
        if isinstance(suggestion, dict) and isinstance(suggestion.get("fact"), str):
            hints.setdefault(suggestion["fact"], suggestion)
    items: List[Dict[str, Any]] = []
    for path in queue:
        item: Dict[str, Any] = {
            "path": path,
            "value": None,
            "value_type": value_type(path),
            "source_type": "",
            "source": "",
            "evidence": "",
            "strength": "",
        }
        hint = hints.get(path)
        if hint is not None:
            item["hint"] = {
                "suggested_value": hint.get("suggested_value"),
                "reason": hint.get("reason"),
                "evidence": hint.get("evidence"),
                "strength": "heuristic",
                "note": ("heuristic is hint-only and cannot finalize this fact; inspect the cited "
                         "location and restate it as authoritative, observed, or derived"),
            }
        if value_type(path) == "boolean":
            item["negative_proof_required_if_false"] = "unless strength is authoritative"
        items.append(item)
    return {
        "schema_version": 1,
        "generated_by": "fact_resolver.build_resolution_template",
        "status": "COMPLETE" if not items else "UNRESOLVED",
        "unresolved_count": len(items),
        "source_ref": source_ref,
        "usage": ("Fill every entry you can evidence, delete the rest, then re-run intake with "
                  "--resolutions <this file>. Unfilled entries keep the fact unresolved."),
        "rules": {
            "required_fields": list(REQUIRED_FIELDS),
            "strength_allowed": sorted(ALLOWED_STRENGTH),
            "heuristic_is_hint_only": True,
            "false_requires_negative_proof_unless_authoritative": True,
            "value_must_match_type": ("boolean facts use JSON true/false, never the string \"false\"; "
                                      "structural counts use non-negative integers"),
            "conflict_rule": ("a resolution cannot overwrite a mechanically proven fact with a "
                              "different value; reconcile the sources or create a new snapshot"),
        },
        "resolutions": items,
    }


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
