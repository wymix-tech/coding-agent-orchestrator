#!/usr/bin/env python3
"""Map canonical V6 semantic-impact evidence into V3/V4 Work Facts conservatively."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ACCEPTED = {"authoritative", "observed", "derived"}


def get_path(data: Dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(data: Dict[str, Any], path: str, value: Any) -> None:
    cur = data
    parts = path.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _remove_resolution_queue(facts: Dict[str, Any], path: str) -> None:
    extraction = facts.get("extraction") or {}
    q = extraction.get("resolution_queue")
    if isinstance(q, list):
        extraction["resolution_queue"] = [x for x in q if x != path]


def _supersede_previous(facts: Dict[str, Any], path: str, reason: str) -> None:
    for entry in (facts.get("provenance") or {}).get(path, []):
        if entry.get("strength") in ACCEPTED:
            entry["previous_strength"] = entry["strength"]
            entry["strength"] = "superseded"
            entry["superseded_by"] = "v6-impact-mapper"
            entry["superseded_reason"] = reason


def _record(
    facts: Dict[str, Any],
    path: str,
    value: Any,
    *,
    impact: Dict[str, Any],
    claim: str,
    strength: str = "derived",
    negative_proof: bool = False,
    raise_only: bool = False,
) -> bool:
    current = get_path(facts, path)
    if current is not None:
        if raise_only and isinstance(current, int) and not isinstance(current, bool) and isinstance(value, int) and value <= current:
            return False
        if current == value and type(current) is type(value):
            return False
        if not raise_only:
            # Do not overwrite an already resolved semantic fact. A later resolver must
            # reconcile conflicting evidence explicitly.
            return False
        _supersede_previous(facts, path, "semantic blast radius expands the structural change-set estimate")
    set_path(facts, path, value)
    facts.setdefault("provenance", {}).setdefault(path, []).append({
        "value": value,
        "source_type": "code_graph",
        "source": "codebase-memory-mcp/detect_changes",
        "evidence": {
            "claim": claim,
            "semantic_impact_snapshot": impact.get("snapshot", {}).get("id"),
            "provider": impact.get("provider", {}).get("id"),
        },
        "strength": strength,
        "detector": "v6-impact-mapper",
        "negative_proof": negative_proof,
    })
    _remove_resolution_queue(facts, path)
    return True


def enrich_work_facts(facts: Dict[str, Any], impact: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(facts)
    imp = impact.get("impact") or {}
    boundaries = impact.get("boundaries") or {}
    contracts = impact.get("contracts") or {}

    affected_modules = list(imp.get("affected_modules") or [])
    affected_services = list(imp.get("affected_services") or [])
    affected_projects = list(imp.get("affected_projects") or [])
    impacted_symbols = int(imp.get("impacted_symbol_count") or 0)
    changed_symbols = len((impact.get("changes") or {}).get("changed_symbols") or [])
    impact_complete = bool((impact.get("completeness") or {}).get("complete", True))

    # Components are module/service-level, not symbol-level. Never use blast-radius node
    # count as predicted_components; that would turn a utility function into 100 components.
    component_count = max(len(affected_modules), len(affected_services), len(affected_projects))
    if component_count > 0 and impact_complete:
        _record(out, "complexity.predicted_components", component_count, impact=impact, claim="affected_components", raise_only=True)
        _record(out, "scope.modules_touched", max(1, len(affected_modules)) if affected_modules else component_count, impact=impact, claim="affected_modules", raise_only=True)

    if affected_services and impact_complete:
        _record(out, "scope.deployable_units", len(affected_services), impact=impact, claim="affected_services", raise_only=True)

    cross_project = bool(boundaries.get("cross_project"))
    if cross_project:
        _record(out, "scope.external_consumers", True, impact=impact, claim="cross_project_boundary")

    if contracts.get("public_contract_signal"):
        _record(out, "scope.public_contract_change", True, impact=impact, claim="changed_route_or_contract")
        _record(out, "verification.compatibility_or_migration", True, impact=impact, claim="public_contract_compatibility")

    integration_boundary = bool(boundaries.get("cross_service") or boundaries.get("integration_relations") or len(affected_services) >= 2)
    if integration_boundary:
        _record(out, "verification.requires_integration_boundary", True, impact=impact, claim="cross_service_or_deployable_boundary")
        # Strict evidence treats false as a negative claim, so attach explicit proof: the
        # graph positively demonstrates a non-local boundary.
        _record(out, "verification.deterministic_local", False, impact=impact, claim="non_local_boundary_proves_not_local_only", negative_proof=True)

    async_boundary = bool(boundaries.get("async_boundary"))
    if async_boundary:
        _record(out, "verification.async_retry_timing", True, impact=impact, claim="async_execution_boundary")

    # Distributed coordination is stronger than merely crossing one HTTP boundary. Require
    # multiple affected deployables/projects or an async boundary before setting it.
    distributed = async_boundary or len(affected_services) >= 2 or len(affected_projects) >= 2
    if distributed:
        _record(out, "complexity.distributed_coordination", True, impact=impact, claim="multi_runtime_coordination")

    semantic = {
        "provider": impact.get("provider"),
        "snapshot_id": impact.get("snapshot", {}).get("id"),
        "changed_symbol_count": changed_symbols,
        "impacted_symbol_count": impacted_symbols,
        "boundary_types": boundaries.get("relation_types") or [],
        "provider_risk_ignored": True,
        "complete": impact_complete,
    }
    out["semantic_impact"] = semantic
    out.setdefault("extraction", {})["semantic_impact"] = semantic
    return out


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--facts", type=Path, required=True)
    p.add_argument("--impact", type=Path, required=True)
    p.add_argument("--output", type=Path)
    args = p.parse_args(argv)
    facts = json.loads(args.facts.read_text(encoding="utf-8"))
    impact = json.loads(args.impact.read_text(encoding="utf-8"))
    out = enrich_work_facts(facts, impact)
    text = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
