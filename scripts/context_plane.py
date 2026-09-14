#!/usr/bin/env python3
"""V6.2 Context Plane: build a canonical Context Manifest and role/stage Context Packs.

The manifest indexes current authorities, snapshots, and compact artifact summaries.
Context Packs are snapshot-bound projections for one role/stage. They are deliberately
small: graph/rule/history corpora remain external and are retrieved only when needed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

ROLES = {"planner", "implementer", "reviewer", "verifier", "debugger", "resume"}
STAGES = {"bootstrap", "planning", "implementation", "review", "verification", "resume"}

# Scores are selection preferences, not governance decisions.
ROLE_WEIGHTS: dict[str, dict[str, int]] = {
    "planner": {
        "requirement": 120, "request_input": 105, "decision": 115, "work_facts": 110, "semantic_impact": 95,
        "policy_plan": 90, "policy_context": 85, "execution_state": 75,
        "verification_plan": 65, "policy_evaluation": 45, "execution_history": 25,
    },
    "implementer": {
        "requirement": 120, "request_input": 100, "execution_state": 120, "semantic_impact": 115,
        "policy_context": 115, "policy_plan": 105, "decision": 100,
        "verification_plan": 95, "work_facts": 80, "policy_evaluation": 70,
        "execution_history": 30,
    },
    "reviewer": {
        "requirement": 120, "request_input": 90, "semantic_impact": 115, "policy_evaluation": 115,
        "execution_state": 110, "verification_plan": 105, "policy_plan": 95,
        "decision": 85, "work_facts": 65, "execution_history": 60,
    },
    "verifier": {
        "requirement": 120, "request_input": 75, "execution_state": 120, "verification_plan": 120,
        "policy_evaluation": 115, "semantic_impact": 95, "decision": 75,
        "policy_plan": 70, "work_facts": 55, "execution_history": 50,
    },
    "debugger": {
        "execution_state": 120, "semantic_impact": 115, "work_facts": 100,
        "decision": 95, "verification_plan": 95, "policy_context": 85,
        "policy_evaluation": 80, "requirement": 80, "request_input": 70, "execution_history": 70,
    },
    "resume": {
        "execution_state": 125, "requirement": 110, "request_input": 85, "decision": 105,
        "semantic_impact": 100, "verification_plan": 95, "policy_context": 85,
        "policy_evaluation": 80, "work_facts": 75, "execution_history": 70,
    },
}

STAGE_BOOSTS: dict[str, dict[str, int]] = {
    "bootstrap": {"requirement": 20, "policy_plan": 15, "execution_state": 10},
    "planning": {"requirement": 20, "decision": 20, "work_facts": 20, "semantic_impact": 10, "policy_plan": 10},
    "implementation": {"execution_state": 20, "semantic_impact": 20, "policy_context": 20, "verification_plan": 10},
    "review": {"semantic_impact": 20, "policy_evaluation": 20, "execution_state": 15, "verification_plan": 15},
    "verification": {"execution_state": 20, "verification_plan": 25, "policy_evaluation": 20, "semantic_impact": 10},
    "resume": {"execution_state": 25, "execution_history": 15, "decision": 10},
}

MANDATORY_BY_STAGE: dict[str, set[str]] = {
    "bootstrap": {"requirement"},
    "planning": {"requirement", "decision", "work_facts"},
    "implementation": {"requirement", "execution_state", "semantic_impact", "decision"},
    "review": {"requirement", "execution_state", "semantic_impact"},
    "verification": {"requirement", "execution_state", "verification_plan"},
    "resume": {"execution_state", "requirement", "decision"},
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_obj(data: Any) -> str:
    return _sha_bytes(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))




def _path_digest(path: Path) -> tuple[str, int, int]:
    """Hash a file or directory tree deterministically."""
    if path.is_file():
        raw = path.read_bytes()
        return _sha_bytes(raw), len(raw), 1
    if path.is_dir():
        entries = []
        total = 0
        count = 0
        for child in sorted(x for x in path.rglob("*") if x.is_file()):
            try:
                raw = child.read_bytes()
            except OSError:
                continue
            rel = child.relative_to(path).as_posix()
            entries.append((rel, _sha_bytes(raw), len(raw)))
            total += len(raw)
            count += 1
        return _sha_obj(entries), total, count
    raise FileNotFoundError(path)

def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to read YAML context sources")
        return yaml.safe_load(text)
    return text


def _dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve(repo: Path, ref: Optional[str | Path]) -> Optional[Path]:
    if ref is None:
        return None
    p = Path(ref)
    if p.is_absolute():
        return p
    return (repo / p).resolve()


def _display_ref(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _summary(kind: str, doc: Any) -> str:
    if not isinstance(doc, dict):
        text = str(doc).strip().replace("\r", "")
        return text[:900] + ("…" if len(text) > 900 else "")
    if kind == "decision":
        blockers = doc.get("implementation_blockers") or []
        return f"status={doc.get('status')}; flow={doc.get('flow_profile')}; blockers={len(blockers)}"
    if kind == "work_facts":
        unresolved = len((doc.get("extraction") or {}).get("resolution_queue", []))
        elevated = []
        for group in ("risk", "verification", "scope", "complexity"):
            vals = doc.get(group) or {}
            if isinstance(vals, dict):
                for key, value in vals.items():
                    if value is True:
                        elevated.append(f"{group}.{key}")
        return f"evidence-backed facts; unresolved={unresolved}; true_signals={','.join(elevated[:10])}"
    if kind == "semantic_impact":
        impact = doc.get("impact") or {}
        changes = doc.get("changes") or {}
        bounds = doc.get("boundaries") or {}
        complete = (doc.get("completeness") or {}).get("complete", True)
        symbol_names = []
        for sym in (changes.get("changed_symbols") or [])[:5]:
            if isinstance(sym, dict):
                symbol_names.append(str(sym.get("qualified_name") or sym.get("name") or sym.get("id") or "?"))
            else:
                symbol_names.append(str(sym))
        modules = [str(x) for x in (impact.get("affected_modules") or [])[:6]]
        services = [str(x) for x in (impact.get("affected_services") or [])[:6]]
        return (
            f"changed_symbols={len(changes.get('changed_symbols') or [])}[{','.join(symbol_names)}]; "
            f"impacted_symbols={impact.get('impacted_symbol_count', len(impact.get('impacted_symbols') or []))}; "
            f"modules={','.join(modules)}; services={','.join(services)}; "
            f"cross_service={bool(bounds.get('cross_service'))}; async={bool(bounds.get('async_boundary'))}; complete={complete}"
        )
    if kind == "policy_plan":
        rules = doc.get("applicable_rules") or []
        ids = [str(r.get("id")) for r in rules[:8]]
        return f"status={doc.get('status')}; applicable_rules={len(rules)}; ids={','.join(ids)}"
    if kind == "policy_evaluation":
        blocking = doc.get("blocking_violations") or []
        return f"status={doc.get('status')}; blocking_violations={len(blocking)}"
    if kind == "verification_plan":
        items = doc.get("items") or []
        kinds = [str(x.get("kind")) for x in items[:10]]
        return f"verification_items={len(items)}; kinds={','.join(kinds)}"
    if kind == "execution_state":
        cursor = doc.get("cursor") or {}
        ver = doc.get("verification") or {}
        review = doc.get("review") or {}
        failed = [name for name, gate in (doc.get("quality_gates") or {}).items() if gate.get("status") == "failed"]
        enf = doc.get("enforcement") or {}
        return (
            f"revision={doc.get('revision')}; flow={doc.get('flow_profile')}; phase={doc.get('phase')}; status={doc.get('status')}; "
            f"blocked={bool(doc.get('blocked'))}; next={cursor.get('next_action')}; review={review.get('status')}; "
            f"verification={ver.get('status')}/fresh={ver.get('fresh')}; enforcement_dirty={enf.get('dirty')}; "
            f"semantic_fresh={enf.get('semantic_fresh')}; failed_gates={','.join(failed)}"
        )
    return json.dumps(doc, ensure_ascii=False, sort_keys=True)[:900]


def _source(
    repo: Path,
    source_id: str,
    kind: str,
    ref: Optional[str | Path],
    authority: str,
    tier: int,
    *,
    roles: Optional[list[str]] = None,
    stages: Optional[list[str]] = None,
    snapshot_id: Optional[str] = None,
    required: bool = False,
) -> Optional[dict[str, Any]]:
    path = _resolve(repo, ref)
    if path is None or not path.exists():
        return None
    digest, size_bytes, file_count = _path_digest(path)
    entrypoints: list[str] = []
    if path.is_dir():
        all_names = [x.relative_to(path).as_posix() for x in sorted(y for y in path.rglob("*") if y.is_file())]
        preferred_tokens = ("proposal", "spec", "requirement", "story", "task", "design", "architecture", "prd", "acceptance")
        preferred = [n for n in all_names if any(t in n.lower() for t in preferred_tokens)]
        entrypoints = (preferred + [n for n in all_names if n not in preferred])[:12]
        summary = f"SDD/context directory; files={file_count}; entrypoints={','.join(entrypoints)}"
    else:
        try:
            doc = _load(path)
            summary = _summary(kind, doc)
        except Exception as exc:
            summary = f"unreadable context source: {exc}"
    return {
        "id": source_id,
        "kind": kind,
        "ref": _display_ref(repo, path),
        "authority": authority,
        "tier": tier,
        "roles": roles or sorted(ROLES),
        "stages": stages or sorted(STAGES),
        "required": required,
        "snapshot_id": snapshot_id,
        "content_sha256": digest,
        "size_bytes": size_bytes,
        "file_count": file_count,
        "summary": summary,
        "entrypoints": entrypoints,
    }


def _load_if(repo: Path, ref: Optional[str | Path]) -> Any:
    p = _resolve(repo, ref)
    if p and p.exists() and p.is_file():
        return _load(p)
    return None


def _infer_requirement_provider(explicit: Optional[str], state: Any) -> str:
    if explicit:
        return explicit
    if isinstance(state, dict):
        p = ((state.get("authority") or {}).get("provider"))
        if p in {"openspec", "bmad", "generic", "other"}:
            return p
    return "generic"


def build_manifest(
    repo: Path,
    intake_dir: Path,
    *,
    state_ref: Optional[str | Path] = None,
    request_ref: Optional[str | Path] = None,
    sdd_ref: Optional[str | Path] = None,
    sdd_provider: Optional[str] = None,
    policy_manifest_ref: Optional[str | Path] = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    intake_dir = intake_dir.resolve()
    state_doc = _load_if(repo, state_ref)
    provider = _infer_requirement_provider(sdd_provider, state_doc)
    requirement_ref = sdd_ref or request_ref
    requirement_authority = "repository_sdd" if sdd_ref else "request_input"

    sources: list[dict[str, Any]] = []
    def add(item: Optional[dict[str, Any]]) -> None:
        if item:
            sources.append(item)

    add(_source(repo, "requirement", "requirement", requirement_ref, requirement_authority, 0, required=True))
    if sdd_ref and request_ref:
        add(_source(repo, "request_input", "request_input", request_ref, "current_request_input", 1))
    add(_source(repo, "work_facts", "work_facts", intake_dir / "work-facts.resolved.json" if (intake_dir / "work-facts.resolved.json").exists() else intake_dir / "work-facts.semantic-draft.json", "evidence_layer", 1))
    decision_doc = _load_if(repo, intake_dir / "decision.json")
    add(_source(repo, "decision", "decision", intake_dir / "decision.json", "decision_engine", 0, required=True))
    impact_doc = _load_if(repo, intake_dir / "semantic-impact.json")
    add(_source(repo, "semantic_impact", "semantic_impact", intake_dir / "semantic-impact.json", "cbm_structural_evidence", 1, snapshot_id=((impact_doc or {}).get("snapshot") or {}).get("id")))
    policy_plan_doc = _load_if(repo, intake_dir / "policy-plan.json")
    add(_source(repo, "policy_plan", "policy_plan", intake_dir / "policy-plan.json", "project_engineering_policy", 1, snapshot_id=(policy_plan_doc or {}).get("policy_snapshot_id")))
    add(_source(repo, "policy_context", "policy_context", intake_dir / "policy-context.md", "project_engineering_policy_projection", 0))
    add(_source(repo, "policy_evaluation", "policy_evaluation", intake_dir / "policy-evaluation.json", "policy_enforcement_evidence", 0))
    add(_source(repo, "verification_plan", "verification_plan", intake_dir / "verification-plan.json", "verification_planner", 1))
    if policy_manifest_ref:
        add(_source(repo, "policy_manifest", "policy_manifest", policy_manifest_ref, "project_engineering_policy", 2))
        # Include the actual project-owned policy packs referenced by the effective plan.
        # External guidance remains non-authoritative and is not promoted by being indexed.
        manifest_path = _resolve(repo, policy_manifest_ref)
        if manifest_path and isinstance(policy_plan_doc, dict):
            for idx, src in enumerate(policy_plan_doc.get("policy_sources") or []):
                if not isinstance(src, dict) or not src.get("ref"):
                    continue
                pack_path = (manifest_path.parent / str(src["ref"])).resolve()
                add(_source(repo, f"policy_source_{idx}", "policy_source", pack_path, "project_engineering_policy", 2, snapshot_id=src.get("content_hash")))
    if state_ref:
        state_path = _resolve(repo, state_ref)
        add(_source(repo, "execution_state", "execution_state", state_ref, "canonical_execution_state", 0, required=True,
                    snapshot_id=(state_doc or {}).get("execution_snapshot_id")))
        if state_path:
            hist = state_path.with_name("execution-history.jsonl")
            add(_source(repo, "execution_history", "execution_history", hist, "append_only_execution_history", 3,
                        roles=["reviewer", "debugger", "resume"], stages=["review", "resume", "implementation"]))

    analysis_snapshot = None
    execution_snapshot = None
    state_revision = None
    verification_snapshot = None
    verification_fresh = None
    if isinstance(state_doc, dict):
        state_revision = state_doc.get("revision")
        analysis_snapshot = ((state_doc.get("analysis") or {}).get("analysis_snapshot_id"))
        execution_snapshot = state_doc.get("execution_snapshot_id")
        ver = state_doc.get("verification") or {}
        verification_snapshot = ver.get("snapshot_id")
        verification_fresh = ver.get("fresh")
    if analysis_snapshot is None:
        wf = _load_if(repo, intake_dir / "work-facts.resolved.json") or _load_if(repo, intake_dir / "work-facts.semantic-draft.json") or {}
        analysis_snapshot = (wf.get("extraction") or {}).get("analysis_snapshot_id") if isinstance(wf, dict) else None

    warnings: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    if isinstance(decision_doc, dict) and decision_doc.get("status") != "CLASSIFIED":
        blockers.append({"code": "DECISION_NOT_CLASSIFIED", "detail": decision_doc.get("reason") or decision_doc.get("status")})
    if isinstance(impact_doc, dict) and not (impact_doc.get("completeness") or {"complete": True}).get("complete", True):
        blockers.append({"code": "SEMANTIC_IMPACT_PARTIAL", "detail": "CBM impact collection is incomplete"})
    if isinstance(policy_plan_doc, dict) and policy_plan_doc.get("status") == "CONFLICT":
        blockers.append({"code": "POLICY_CONFLICT", "detail": "project policy contains unresolved conflicts"})
    policy_eval = _load_if(repo, intake_dir / "policy-evaluation.json")
    if isinstance(policy_eval, dict) and policy_eval.get("status") == "FAILED":
        blockers.append({"code": "POLICY_VIOLATION", "detail": f"blocking={len(policy_eval.get('blocking_violations') or [])}"})
    if isinstance(state_doc, dict):
        if state_doc.get("blocked"):
            blockers.append({"code": "EXECUTION_BLOCKED", "detail": f"active_blockers={len(state_doc.get('blockers') or [])}"})
        if analysis_snapshot and execution_snapshot and analysis_snapshot != execution_snapshot:
            warnings.append({"code": "ANALYSIS_EXECUTION_SNAPSHOT_MISMATCH", "detail": f"analysis={analysis_snapshot}; execution={execution_snapshot}"})
        if verification_snapshot and not verification_fresh:
            warnings.append({"code": "VERIFICATION_STALE", "detail": f"verification_snapshot={verification_snapshot}; execution={execution_snapshot}"})

    source_fingerprint = [{k: s.get(k) for k in ("id", "ref", "content_sha256", "snapshot_id", "authority")} for s in sources]
    context_snapshot_id = _sha_obj({
        "sources": source_fingerprint,
        "state_revision": state_revision,
        "analysis_snapshot_id": analysis_snapshot,
        "execution_snapshot_id": execution_snapshot,
    })[:24]

    work_item = None
    current = None
    evidence = None
    if isinstance(state_doc, dict):
        work_item = state_doc.get("work_item")
        current = {
            "flow_profile": state_doc.get("flow_profile"),
            "phase": state_doc.get("phase"),
            "status": state_doc.get("status"),
            "blocked": state_doc.get("blocked"),
            "next_action": (state_doc.get("cursor") or {}).get("next_action"),
            "revision": state_revision,
        }
        gates = state_doc.get("quality_gates") or {}
        gate_status_counts: dict[str, int] = {}
        gate_evidence_refs: list[str] = []
        required_not_passed: list[str] = []
        for name, gate in gates.items():
            status = gate.get("status", "pending")
            gate_status_counts[status] = gate_status_counts.get(status, 0) + 1
            if gate.get("required") and status != "passed":
                required_not_passed.append(name)
            if gate.get("evidence_ref"):
                gate_evidence_refs.append(gate.get("evidence_ref"))
        review_doc = state_doc.get("review") or {}
        verification_doc = state_doc.get("verification") or {}
        evidence = {
            "quality_gates": {
                "total": len(gates),
                "status_counts": gate_status_counts,
                "required_not_passed": sorted(required_not_passed),
                "evidence_refs": sorted(set(gate_evidence_refs)),
            },
            "review": {
                "required": review_doc.get("required"),
                "status": review_doc.get("status"),
                "blocking_findings": review_doc.get("blocking_findings"),
                "evidence_refs": review_doc.get("evidence") or [],
            },
            "verification": {
                "required": verification_doc.get("required"),
                "status": verification_doc.get("status"),
                "fresh": verification_doc.get("fresh"),
                "snapshot_id": verification_doc.get("snapshot_id"),
                "evidence_refs": verification_doc.get("evidence") or [],
            },
        }

    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "context_snapshot_id": context_snapshot_id,
        "work_item": work_item,
        "current": current,
        "authorities": {
            "requirement": {"provider": provider, "ref": _display_ref(repo, _resolve(repo, requirement_ref)) if _resolve(repo, requirement_ref) else None, "authority": requirement_authority},
            "decision": {"authority": "decision_engine", "ref": _display_ref(repo, intake_dir / "decision.json") if (intake_dir / "decision.json").exists() else None},
            "execution": {"authority": "canonical_execution_state", "ref": _display_ref(repo, _resolve(repo, state_ref)) if _resolve(repo, state_ref) else None, "revision": state_revision},
            "semantic": {"authority": "cbm_structural_evidence", "ref": _display_ref(repo, intake_dir / "semantic-impact.json") if (intake_dir / "semantic-impact.json").exists() else None},
            "engineering_policy": {"authority": "project_engineering_policy", "ref": _display_ref(repo, _resolve(repo, policy_manifest_ref)) if _resolve(repo, policy_manifest_ref) else None},
            "verification": {"authority": "verification_planner_plus_fresh_evidence", "ref": _display_ref(repo, intake_dir / "verification-plan.json") if (intake_dir / "verification-plan.json").exists() else None},
        },
        "snapshots": {
            "analysis_snapshot_id": analysis_snapshot,
            "execution_snapshot_id": execution_snapshot,
            "semantic_snapshot_id": ((impact_doc or {}).get("snapshot") or {}).get("id") if isinstance(impact_doc, dict) else None,
            "policy_snapshot_id": (policy_plan_doc or {}).get("policy_snapshot_id") if isinstance(policy_plan_doc, dict) else None,
            "verification_snapshot_id": verification_snapshot,
            "verification_fresh": verification_fresh,
        },
        "sources": sources,
        "evidence": evidence,
        "warnings": warnings,
        "blockers": blockers,
        "principles": [
            "manifest indexes truth; it does not duplicate full corpora",
            "context packs are role/stage projections, not new authorities",
            "graph, rule, and history corpora remain external and lazy-retrieved",
            "regenerate packs when context_snapshot_id or execution revision changes",
        ],
    }


def _score(source: dict[str, Any], role: str, stage: str) -> int:
    base = ROLE_WEIGHTS.get(role, {}).get(source.get("id"), 20)
    base += STAGE_BOOSTS.get(stage, {}).get(source.get("id"), 0)
    base += max(0, 20 - int(source.get("tier", 3)) * 7)
    if role not in set(source.get("roles") or []):
        return -1000
    if stage not in set(source.get("stages") or []):
        return -1000
    if source.get("required"):
        base += 30
    return base


def build_pack(manifest: dict[str, Any], role: str, stage: str, *, max_items: int = 12, max_chars: int = 16000) -> dict[str, Any]:
    role = role.lower()
    stage = stage.lower()
    if role not in ROLES:
        raise ValueError(f"invalid role: {role}")
    if stage not in STAGES:
        raise ValueError(f"invalid stage: {stage}")

    sources = list(manifest.get("sources") or [])
    mandatory_ids = set(MANDATORY_BY_STAGE.get(stage, set()))
    source_ids = {s.get("id") for s in sources}
    if stage in {"review", "verification"} and "policy_evaluation" in source_ids:
        mandatory_ids.add("policy_evaluation")
    scored = sorted((( _score(s, role, stage), s) for s in sources), key=lambda x: (-x[0], x[1].get("id", "")))
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    used_chars = 0
    for score, src in scored:
        if score < 0:
            deferred.append({"id": src.get("id"), "ref": src.get("ref"), "reason": "not applicable to role/stage"})
            continue
        entry = {
            "id": src.get("id"),
            "kind": src.get("kind"),
            "ref": src.get("ref"),
            "authority": src.get("authority"),
            "tier": src.get("tier"),
            "summary": src.get("summary"),
            "entrypoints": src.get("entrypoints") or [],
            "snapshot_id": src.get("snapshot_id"),
            "content_sha256": src.get("content_sha256"),
            "reason": f"role={role}; stage={stage}; score={score}",
            "mandatory": src.get("id") in mandatory_ids or bool(src.get("required")),
        }
        size = len(json.dumps(entry, ensure_ascii=False))
        must = entry["mandatory"]
        if (len(selected) >= max_items or used_chars + size > max_chars) and not must:
            deferred.append({"id": src.get("id"), "ref": src.get("ref"), "reason": "context budget / lower relevance"})
            continue
        selected.append(entry)
        used_chars += size

    # Missing mandatory source kinds are an explicit warning; do not synthesize them.
    present = {x.get("id") for x in selected}
    missing_mandatory = sorted(x for x in mandatory_ids if x not in present)
    warnings = list(manifest.get("warnings") or [])
    if missing_mandatory:
        warnings.append({"code": "MISSING_MANDATORY_CONTEXT", "detail": ",".join(missing_mandatory)})

    current = manifest.get("current") or {}
    pack = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "context_snapshot_id": manifest.get("context_snapshot_id"),
        "role": role,
        "stage": stage,
        "valid_for": {
            "context_snapshot_id": manifest.get("context_snapshot_id"),
            "analysis_snapshot_id": (manifest.get("snapshots") or {}).get("analysis_snapshot_id"),
            "execution_snapshot_id": (manifest.get("snapshots") or {}).get("execution_snapshot_id"),
            "state_revision": current.get("revision"),
            "policy_snapshot_id": (manifest.get("snapshots") or {}).get("policy_snapshot_id"),
        },
        "current": current,
        "work_item": manifest.get("work_item"),
        "blockers": manifest.get("blockers") or [],
        "warnings": warnings,
        "evidence_status": manifest.get("evidence"),
        "selected": selected,
        "deferred": deferred,
        "budget": {
            "max_items": max_items,
            "max_chars": max_chars,
            "selected_items": len(selected),
            "estimated_metadata_chars": used_chars,
        },
        "retrieval_policy": {
            "default": "use selected summaries/refs; lazy-retrieve deferred sources only when the current step needs them",
            "do_not_inline": ["full CBM graph", "full execution history", "entire external rule corpus"],
            "refresh_when": ["execution revision changes materially", "analysis snapshot changes", "policy snapshot changes", "verification becomes stale"],
        },
    }
    pack["pack_snapshot_id"] = _sha_obj({k: v for k, v in pack.items() if k not in {"generated_at", "pack_snapshot_id"}})[:24]
    return pack


def render_pack(pack: dict[str, Any]) -> str:
    current = pack.get("current") or {}
    work = pack.get("work_item") or {}
    lines = [
        f"# Context Pack — {pack.get('role')} / {pack.get('stage')}",
        "",
        f"Context snapshot: `{pack.get('context_snapshot_id')}`  ",
        f"Pack snapshot: `{pack.get('pack_snapshot_id')}`",
        "",
        "## Current work",
        f"- Work item: `{work.get('id')}` {work.get('title') or ''}",
        f"- Flow: `{current.get('flow_profile')}`",
        f"- Phase/status: `{current.get('phase')}` / `{current.get('status')}`",
        f"- Next action: `{current.get('next_action')}`",
        f"- State revision: `{current.get('revision')}`",
    ]
    blockers = pack.get("blockers") or []
    if blockers:
        lines += ["", "## Blocking context"]
        for b in blockers:
            lines.append(f"- **{b.get('code')}**: {b.get('detail')}")
    warnings = pack.get("warnings") or []
    if warnings:
        lines += ["", "## Freshness / context warnings"]
        for w in warnings:
            lines.append(f"- **{w.get('code')}**: {w.get('detail')}")
    lines += ["", "## Selected context"]
    for item in pack.get("selected") or []:
        marker = "MUST-READ" if item.get("mandatory") else "selected"
        lines.append(f"- **{item.get('id')}** [{marker}] `{item.get('ref')}`")
        if item.get("summary"):
            lines.append(f"  - {item.get('summary')}")
    if pack.get("deferred"):
        lines += ["", "## Deferred / lazy retrieval"]
        for item in (pack.get("deferred") or [])[:20]:
            lines.append(f"- `{item.get('id')}` → `{item.get('ref')}` ({item.get('reason')})")
    lines += [
        "",
        "## Context discipline",
        "- Treat source authorities as authoritative; this pack is only a projection.",
        "- Do not load full graph/history/rule corpora unless the current step requires them.",
        "- Regenerate this pack if its snapshot/revision no longer matches the repository state.",
    ]
    return "\n".join(lines) + "\n"


def validate_manifest(repo: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    repo = repo.resolve()
    stale: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for src in manifest.get("sources") or []:
        p = _resolve(repo, src.get("ref"))
        if not p or not p.exists():
            missing.append({"id": src.get("id"), "ref": src.get("ref")})
            continue
        digest, _, _ = _path_digest(p)
        if digest != src.get("content_sha256"):
            stale.append({"id": src.get("id"), "ref": src.get("ref"), "expected": src.get("content_sha256"), "actual": digest})
    return {
        "status": "FRESH" if not stale and not missing else "STALE",
        "context_snapshot_id": manifest.get("context_snapshot_id"),
        "stale_sources": stale,
        "missing_sources": missing,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build/refresh manifest and one role/stage pack")
    b.add_argument("--repo", type=Path, default=Path("."))
    b.add_argument("--intake-dir", type=Path, default=Path(".orchestrator/intake"))
    b.add_argument("--state", type=Path, default=Path(".orchestrator/execution-state.yaml"))
    b.add_argument("--request-ref", type=Path)
    b.add_argument("--sdd-ref", type=Path)
    b.add_argument("--sdd-provider", choices=["openspec", "bmad", "generic", "other"])
    b.add_argument("--policy-manifest", type=Path, default=Path(".orchestrator/policies/manifest.yaml"))
    b.add_argument("--role", default="implementer", choices=sorted(ROLES))
    b.add_argument("--stage", default="implementation", choices=sorted(STAGES))
    b.add_argument("--max-items", type=int, default=12)
    b.add_argument("--max-chars", type=int, default=16000)
    b.add_argument("--manifest-output", type=Path)
    b.add_argument("--pack-output", type=Path)
    b.add_argument("--markdown-output", type=Path)

    k = sub.add_parser("pack", help="project a new role/stage pack from a current manifest")
    k.add_argument("--manifest", type=Path, required=True)
    k.add_argument("--role", required=True, choices=sorted(ROLES))
    k.add_argument("--stage", required=True, choices=sorted(STAGES))
    k.add_argument("--max-items", type=int, default=12)
    k.add_argument("--max-chars", type=int, default=16000)
    k.add_argument("--output", type=Path)
    k.add_argument("--markdown-output", type=Path)

    v = sub.add_parser("validate", help="check source hashes backing a manifest")
    v.add_argument("--repo", type=Path, default=Path("."))
    v.add_argument("--manifest", type=Path, required=True)
    args = p.parse_args(argv)

    if args.cmd == "build":
        repo = args.repo.resolve()
        intake = _resolve(repo, args.intake_dir) or args.intake_dir.resolve()
        state_ref = args.state if _resolve(repo, args.state) and _resolve(repo, args.state).exists() else None
        policy_ref = args.policy_manifest if _resolve(repo, args.policy_manifest) and _resolve(repo, args.policy_manifest).exists() else None
        manifest = build_manifest(repo, intake, state_ref=state_ref, request_ref=args.request_ref,
                                  sdd_ref=args.sdd_ref, sdd_provider=args.sdd_provider,
                                  policy_manifest_ref=policy_ref)
        manifest_out = args.manifest_output or (intake / "context-manifest.json")
        _dump_json(manifest_out, manifest)
        pack = build_pack(manifest, args.role, args.stage, max_items=args.max_items, max_chars=args.max_chars)
        pack_out = args.pack_output or (intake / f"context-pack.{args.role}.{args.stage}.json")
        md_out = args.markdown_output or (intake / f"context-pack.{args.role}.{args.stage}.md")
        _dump_json(pack_out, pack)
        md_out.write_text(render_pack(pack), encoding="utf-8")
        print(json.dumps({"status": "BUILT", "manifest": str(manifest_out), "pack": str(pack_out), "markdown": str(md_out),
                          "context_snapshot_id": manifest.get("context_snapshot_id"), "pack_snapshot_id": pack.get("pack_snapshot_id")}, ensure_ascii=False, indent=2))
        return 0 if not manifest.get("blockers") else 2
    if args.cmd == "pack":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        pack = build_pack(manifest, args.role, args.stage, max_items=args.max_items, max_chars=args.max_chars)
        if args.output:
            _dump_json(args.output, pack)
        if args.markdown_output:
            args.markdown_output.write_text(render_pack(pack), encoding="utf-8")
        print(json.dumps(pack, ensure_ascii=False, indent=2))
        return 0
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = validate_manifest(args.repo.resolve(), manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "FRESH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
