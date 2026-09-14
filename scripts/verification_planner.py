#!/usr/bin/env python3
"""Build an evidence-linked verification plan from Work Facts + V6 semantic impact."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _get(d: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = d
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def build_plan(facts: Dict[str, Any], impact: Dict[str, Any], decision: Dict[str, Any], policy_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    seen = set()

    def add(kind: str, reason: str, evidence: str, *, required: bool = True) -> None:
        key = (kind, reason)
        if key in seen:
            return
        seen.add(key)
        gate_name = "impact:" + kind
        obligation_id = "obl-impact-" + hashlib.sha256(gate_name.encode("utf-8")).hexdigest()[:12]
        items.append({
            "obligation_id": obligation_id,
            "source": "semantic_impact",
            "kind": kind,
            "gate_name": gate_name,
            "required_by_impact": required,
            "reason": reason,
            "evidence_ref": evidence,
            "status": "pending",
        })

    changed_symbols = (impact.get("changes") or {}).get("changed_symbols") or []
    imp = impact.get("impact") or {}
    boundaries = impact.get("boundaries") or {}
    flow = decision.get("flow_profile")

    if changed_symbols:
        add("targeted_unit_or_component_tests", "changed executable symbols", "semantic-impact.json#/changes/changed_symbols")
    if _get(facts, "verification.requires_integration_boundary") is True:
        add("integration_tests", "semantic impact crosses a runtime/module integration boundary", "work-facts.resolved.json#/verification/requires_integration_boundary")
    if _get(facts, "scope.public_contract_change") is True or _get(facts, "verification.compatibility_or_migration") is True:
        add("contract_compatibility_checks", "public contract or migration compatibility is in scope", "work-facts.resolved.json#/verification/compatibility_or_migration")
    if boundaries.get("async_boundary") or _get(facts, "verification.async_retry_timing") is True:
        add("async_failure_and_timing_tests", "async execution boundary is impacted", "semantic-impact.json#/boundaries/async_boundary")
    if any(_get(facts, f"risk.{k}") is True for k in ("authn_authz", "security_sensitive", "cryptography_or_secrets")):
        add("security_regression_tests", "security-sensitive behavior is affected", "work-facts.resolved.json#/risk")
    if any(_get(facts, f"risk.{k}") is True for k in ("persistent_data_change", "destructive_migration")):
        add("migration_and_recovery_checks", "persistent data or destructive migration risk exists", "work-facts.resolved.json#/risk")
    if _get(facts, "verification.concurrency_or_distributed_faults") is True or _get(facts, "complexity.distributed_coordination") is True:
        add("distributed_failure_path_tests", "multiple runtimes or distributed coordination are affected", "work-facts.resolved.json#/complexity/distributed_coordination")
    if _get(facts, "verification.performance_or_load") is True:
        add("performance_or_load_checks", "performance/load properties require verification", "work-facts.resolved.json#/verification/performance_or_load")
    if flow in {"STANDARD", "DEEP"} or int(imp.get("impacted_symbol_count") or 0) >= 10:
        add("regression_suite", "flow/blast radius requires broader regression", "decision.json#/flow_profile")
    if flow == "DEEP":
        add("adversarial_verification_review", "DEEP flow requires independent verification pressure", "decision.json#/flow_profile")

    policy_gates: List[Dict[str, Any]] = []
    if policy_plan:
        for enf in policy_plan.get("enforcements", []):
            gate_name = "policy:" + str(enf.get("rule_id")) + ":" + str(enf.get("gate") or enf.get("engine") or "policy")
            policy_gates.append({
                "obligation_id": "obl-policy-" + hashlib.sha256(gate_name.encode("utf-8")).hexdigest()[:12],
                "source": "engineering_policy",
                "gate_name": gate_name,
                "rule_id": enf.get("rule_id"),
                "level": enf.get("level"),
                "engine": enf.get("engine"),
                "gate": enf.get("gate"),
                "required_by_policy": bool(enf.get("required")),
                "command": enf.get("command"),
                "status": "pending",
            })

    # Project policy may add stricter gates. Semantic impact can add breadth, but can never
    # downgrade a policy/native REQUIRED gate.
    material = {
        "facts_snapshot": _get(facts, "extraction.snapshot_id"),
        "impact_snapshot": _get(impact, "snapshot.id"),
        "flow": flow,
        "items": items,
        "policy_gates": policy_gates,
    }
    plan_id = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
    return {
        "schema_version": 1,
        "plan_id": plan_id,
        "flow_profile": flow,
        "semantic_impact_snapshot": _get(impact, "snapshot.id"),
        "items": items,
        "policy_gates": policy_gates,
        "policy_snapshot_id": (policy_plan or {}).get("policy_snapshot_id"),
        "policy_note": "repository/native quality policy may add stricter REQUIRED gates; this plan cannot downgrade them",
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--facts", type=Path, required=True)
    p.add_argument("--impact", type=Path, required=True)
    p.add_argument("--decision", type=Path, required=True)
    p.add_argument("--policy-plan", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args(argv)
    out = build_plan(
        json.loads(args.facts.read_text(encoding="utf-8")),
        json.loads(args.impact.read_text(encoding="utf-8")),
        json.loads(args.decision.read_text(encoding="utf-8")),
        json.loads(args.policy_plan.read_text(encoding="utf-8")) if args.policy_plan else None,
    )
    text = json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
