#!/usr/bin/env python3
"""Deterministic Work Profile / Flow classifier for the Adaptive SDD Orchestrator."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

FLOW_RANK = {"TRIVIAL": 0, "FAST": 1, "STANDARD": 2, "DEEP": 3}
RANK_FLOW = {v: k for k, v in FLOW_RANK.items()}
SCORE_LABEL = {0: "low", 1: "medium", 2: "high", 3: "critical"}
SCOPE_LABEL = {0: "local", 1: "module", 2: "system", 3: "ecosystem"}

REQUIRED_PATHS = [
    "ambiguity.goal_explicit",
    "ambiguity.acceptance_criteria_explicit",
    "ambiguity.boundaries_explicit",
    "ambiguity.conflicting_requirements",
    "ambiguity.multiple_observable_interpretations",
    "ambiguity.unresolved_external_contract",
    "complexity.predicted_components",
    "complexity.architecture_decision_required",
    "complexity.concurrency_or_transaction",
    "complexity.distributed_coordination",
    "complexity.new_state_machine",
    "scope.files_estimate",
    "scope.modules_touched",
    "scope.deployable_units",
    "scope.external_consumers",
    "scope.cross_team_contract",
    "scope.public_contract_change",
    "risk.user_visible_failure",
    "risk.persistent_data_change",
    "risk.security_sensitive",
    "risk.authn_authz",
    "risk.cryptography_or_secrets",
    "risk.payment_or_financial",
    "risk.destructive_migration",
    "risk.compliance_or_privacy",
    "risk.production_infra",
    "risk.irreversible_or_hard_to_recover",
    "novelty.exact_repo_precedent",
    "novelty.new_external_dependency_or_protocol",
    "novelty.first_repo_use",
    "novelty.unproven_architecture_assumption",
    "novelty.docs_or_examples_missing",
    "verification.deterministic_local",
    "verification.requires_integration_boundary",
    "verification.async_retry_timing",
    "verification.compatibility_or_migration",
    "verification.security_properties",
    "verification.concurrency_or_distributed_faults",
    "verification.performance_or_load",
    "verification.special_harness_required",
]


def get_path(data: Dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def validate_facts(facts: Dict[str, Any]) -> List[str]:
    return [p for p in REQUIRED_PATHS if get_path(facts, p) is None]

INT_PATHS = {
    "complexity.predicted_components",
    "scope.files_estimate",
    "scope.modules_touched",
    "scope.deployable_units",
}


def validate_fact_types(facts: Dict[str, Any]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    for path in REQUIRED_PATHS:
        value = get_path(facts, path)
        if value is None:
            continue
        if path in INT_PATHS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors[path] = "expected non-negative integer"
        else:
            if not isinstance(value, bool):
                errors[path] = "expected boolean"
    return errors


def clamp3(value: int) -> int:
    return max(0, min(3, value))


def score_ambiguity(f: Dict[str, Any]) -> Tuple[int, List[str]]:
    a = f["ambiguity"]
    points = 0
    trace: List[str] = []
    if not a["goal_explicit"]:
        points += 2; trace.append("A1 goal_not_explicit +2")
    if not a["acceptance_criteria_explicit"]:
        points += 1; trace.append("A2 acceptance_criteria_missing +1")
    if not a["boundaries_explicit"]:
        points += 1; trace.append("A3 boundaries_missing +1")
    if a["conflicting_requirements"]:
        points += 3; trace.append("A4 conflicting_requirements +3")
    if a["multiple_observable_interpretations"]:
        points += 2; trace.append("A5 multiple_observable_interpretations +2")
    if a["unresolved_external_contract"]:
        points += 1; trace.append("A6 unresolved_external_contract +1")
    return clamp3(points), trace or ["A0 no ambiguity signals"]


def score_complexity(f: Dict[str, Any]) -> Tuple[int, List[str]]:
    c = f["complexity"]
    n = int(c["predicted_components"])
    if n <= 1:
        points = 0; trace = ["X0 <=1 predicted component +0"]
    elif n <= 3:
        points = 1; trace = ["X1 2-3 predicted components +1"]
    else:
        points = 2; trace = ["X2 >=4 predicted components +2"]
    if c["architecture_decision_required"]:
        points += 1; trace.append("X3 architecture_decision_required +1")
    if c["concurrency_or_transaction"]:
        points += 1; trace.append("X4 concurrency_or_transaction +1")
    if c["distributed_coordination"]:
        points += 2; trace.append("X5 distributed_coordination +2")
    if c["new_state_machine"]:
        points += 1; trace.append("X6 new_state_machine +1")
    return clamp3(points), trace


def score_scope(f: Dict[str, Any]) -> Tuple[int, List[str]]:
    s = f["scope"]
    if s["external_consumers"] or s["cross_team_contract"] or s["public_contract_change"]:
        return 3, ["S3 external/cross-team/public contract => ecosystem"]
    if int(s["deployable_units"]) >= 2 or int(s["modules_touched"]) >= 2:
        return 2, ["S2 >=2 deployable units/modules => system"]
    if int(s["files_estimate"]) >= 2:
        return 1, ["S1 >=2 production/config/schema files => module"]
    return 0, ["S0 localized scope"]


def score_risk(f: Dict[str, Any]) -> Tuple[int, List[str]]:
    r = f["risk"]
    critical = ["cryptography_or_secrets", "payment_or_financial", "destructive_migration", "irreversible_or_hard_to_recover"]
    high = ["persistent_data_change", "security_sensitive", "authn_authz", "compliance_or_privacy", "production_infra"]
    hits = [k for k in critical if r[k]]
    if hits:
        return 3, ["R3 critical consequence: " + ",".join(hits)]
    hits = [k for k in high if r[k]]
    if hits:
        return 2, ["R2 high consequence: " + ",".join(hits)]
    if r["user_visible_failure"]:
        return 1, ["R1 reversible user-visible failure"]
    return 0, ["R0 no elevated consequence signal"]


def score_novelty(f: Dict[str, Any]) -> Tuple[int, List[str]]:
    n = f["novelty"]
    if n["unproven_architecture_assumption"]:
        return 3, ["N3 unproven_architecture_assumption"]
    if n["first_repo_use"] and n["new_external_dependency_or_protocol"]:
        return 3, ["N3 first_repo_use + new_external_dependency_or_protocol"]
    if n["first_repo_use"] or n["new_external_dependency_or_protocol"] or (n["docs_or_examples_missing"] and not n["exact_repo_precedent"]):
        hits = []
        for k in ["first_repo_use", "new_external_dependency_or_protocol", "docs_or_examples_missing"]:
            if n[k]: hits.append(k)
        return 2, ["N2 novelty signal: " + ",".join(hits)]
    if not n["exact_repo_precedent"]:
        return 1, ["N1 no exact repo precedent"]
    return 0, ["N0 exact repo precedent"]


def score_verification(f: Dict[str, Any]) -> Tuple[int, List[str]]:
    v = f["verification"]
    if v["concurrency_or_distributed_faults"] or v["special_harness_required"]:
        hits = [k for k in ["concurrency_or_distributed_faults", "special_harness_required"] if v[k]]
        return 3, ["V3 special verification: " + ",".join(hits)]
    level2 = ["async_retry_timing", "compatibility_or_migration", "security_properties", "performance_or_load"]
    hits = [k for k in level2 if v[k]]
    if hits:
        return 2, ["V2 extended verification: " + ",".join(hits)]
    if v["requires_integration_boundary"] or not v["deterministic_local"]:
        return 1, ["V1 integration/non-local verification"]
    return 0, ["V0 deterministic local verification"]


def max_flow(current: str, minimum: str) -> str:
    return RANK_FLOW[max(FLOW_RANK[current], FLOW_RANK[minimum])]


ACCEPTED_EVIDENCE_STRENGTH = {"authoritative", "observed", "derived"}


def evidence_observed_false(entry: Dict[str, Any], path: str) -> bool:
    """Does a trusted source say *path* is false, and not merely that something failed?

    An observer that ran for this predicate and reported `False` carries the fact. A run that
    failed carries a failure: without a value the source reported for this predicate it says
    nothing about the direction of the fact, so it is not accepted as a false.
    """
    if not entry.get("verified_authority"):
        return False
    if str(entry.get("evidence_fact") or "") != path:
        return False
    return entry.get("evidence_observed_value") is False


def validate_provenance(facts: Dict[str, Any]) -> Dict[str, List[str]]:
    provenance = facts.get("provenance") or {}
    missing: List[str] = []
    weak: List[str] = []
    invalid_negative: List[str] = []
    conflicts: List[str] = []
    for path in REQUIRED_PATHS:
        value = get_path(facts, path)
        if value is None:
            continue
        entries = provenance.get(path) or []
        accepted_all = [e for e in entries if e.get("strength") in ACCEPTED_EVIDENCE_STRENGTH]
        distinct = {(type(e.get("value")).__name__, json.dumps(e.get("value"), sort_keys=True)) for e in accepted_all}
        if len(distinct) > 1:
            conflicts.append(path)
        matching = [e for e in entries if e.get("value") == value and type(e.get("value")) is type(value)]
        accepted = [e for e in matching if e.get("strength") in ACCEPTED_EVIDENCE_STRENGTH]
        if not entries:
            missing.append(path)
            continue
        if not accepted:
            weak.append(path)
            continue
        if value is False:
            # `strength=authoritative` is how the source describes itself. A false fact needs
            # either a negative proof whose search was actually redone, or a trusted source
            # that observed this predicate and reported it false. `verified` on its own, or a
            # run that failed, says nothing about the direction of the fact.
            ok = any(
                (bool(e.get("negative_proof_verified")) and str(e.get("negative_proof_fact")) == path)
                or evidence_observed_false(e, path)
                for e in accepted
            )
            if not ok:
                invalid_negative.append(path)
    return {"missing": missing, "weak": weak, "invalid_negative": invalid_negative, "conflicts": conflicts}


def classify(facts: Dict[str, Any], strict_evidence: bool = False) -> Dict[str, Any]:
    type_errors = validate_fact_types(facts)
    if type_errors:
        return {
            "status": "INVALID_FACTS",
            "type_errors": type_errors,
            "implementation_blockers": ["fix Work Fact types before flow selection"],
        }
    missing = validate_facts(facts)
    if missing:
        return {
            "status": "NEEDS_EVIDENCE",
            "missing_facts": missing,
            "implementation_blockers": ["establish all required work facts before flow selection"]
        }

    if strict_evidence:
        ev = validate_provenance(facts)
        if any(ev.values()):
            return {
                "status": "NEEDS_EVIDENCE",
                "missing_evidence": ev["missing"],
                "weak_evidence": ev["weak"],
                "invalid_negative_evidence": ev["invalid_negative"],
                "conflicting_evidence": ev["conflicts"],
                "implementation_blockers": ["attach accepted, non-conflicting evidence to every resolved Work Fact before flow selection"],
            }

    scored = {
        "ambiguity": score_ambiguity(facts),
        "complexity": score_complexity(facts),
        "scope": score_scope(facts),
        "risk": score_risk(facts),
        "novelty": score_novelty(facts),
        "verification_difficulty": score_verification(facts),
    }
    scores = {k: v[0] for k, v in scored.items()}
    profile: Dict[str, Any] = {}
    for k, (score, trace) in scored.items():
        profile[k] = {
            "score": score,
            "level": SCOPE_LABEL[score] if k == "scope" else SCORE_LABEL[score],
            "trace": trace,
        }

    dimension_floors = {
        "ambiguity": min(scores["ambiguity"], 2),
        "complexity": scores["complexity"],
        "scope": scores["scope"],
        "risk": scores["risk"],
        "novelty": scores["novelty"],
        "verification_difficulty": scores["verification_difficulty"],
    }
    baseline_rank = max(dimension_floors.values())
    flow = RANK_FLOW[baseline_rank]
    floor_text = ",".join(f"{k}={v}" for k, v in dimension_floors.items())
    decision_trace = [f"BASE dimension_flow_floors({floor_text}) => {flow}"]

    high_count = sum(1 for x in scores.values() if x >= 2)
    if high_count >= 4:
        flow = max_flow(flow, "DEEP")
        decision_trace.append(f"C1 count(score>=2)={high_count} => min DEEP")
    if scores["ambiguity"] >= 2 and scores["novelty"] >= 2:
        flow = max_flow(flow, "DEEP")
        decision_trace.append("C2 ambiguity>=2 and novelty>=2 => min DEEP")
    if scores["complexity"] >= 2 and scores["risk"] >= 2 and scores["verification_difficulty"] >= 2:
        flow = max_flow(flow, "DEEP")
        decision_trace.append("C3 complexity/risk/verification all >=2 => min DEEP")

    override_signals = [
        ("O1", "risk.authn_authz", "STANDARD"),
        ("O2", "risk.cryptography_or_secrets", "DEEP"),
        ("O3", "risk.payment_or_financial", "DEEP"),
        ("O4", "risk.destructive_migration", "DEEP"),
        ("O5", "risk.irreversible_or_hard_to_recover", "DEEP"),
        ("O6", "scope.public_contract_change", "STANDARD"),
        ("O7", "risk.production_infra", "STANDARD"),
        ("O8", "risk.compliance_or_privacy", "STANDARD"),
    ]
    for rule_id, path, minimum in override_signals:
        if get_path(facts, path):
            before = flow
            flow = max_flow(flow, minimum)
            decision_trace.append(f"{rule_id} {path}=true => min {minimum}" + ("" if flow != before else " (already satisfied)"))

    policy = facts.get("policy") or {}
    policy_min = policy.get("minimum_flow")
    fixed = policy.get("fixed_flow")
    if policy_min:
        policy_min = str(policy_min).upper()
        if policy_min not in FLOW_RANK:
            raise ValueError(f"invalid policy.minimum_flow: {policy_min}")
        flow = max_flow(flow, policy_min)
        decision_trace.append(f"P1 repository minimum_flow={policy_min}")
    if fixed:
        fixed = str(fixed).upper()
        if fixed not in FLOW_RANK:
            raise ValueError(f"invalid policy.fixed_flow: {fixed}")
        if FLOW_RANK[fixed] < FLOW_RANK[flow]:
            return {
                "status": "POLICY_CONFLICT",
                "computed_minimum_flow": flow,
                "fixed_flow": fixed,
                "work_profile": profile,
                "decision_trace": decision_trace + [f"P2 fixed_flow={fixed} is below computed minimum"],
                "implementation_blockers": ["resolve flow-policy conflict; lower rigor may not override computed/project minima"],
            }
        flow = fixed
        decision_trace.append(f"P2 fixed_flow={fixed}")

    activities: List[str] = []
    blockers: List[str] = []
    if scores["ambiguity"] >= 1:
        activities.append("clarify_missing_requirement_facts")
    if scores["ambiguity"] >= 2:
        activities.extend(["write_explicit_acceptance_criteria", "reclassify_after_clarification"])
        blockers.append("resolve_ambiguity_before_implementation")
    if scores["complexity"] >= 2:
        activities.extend(["write_explicit_design", "decompose_dependencies_and_tasks"])
    if scores["scope"] >= 2:
        activities.extend(["build_impact_map", "check_contract_and_compatibility"])
    if scores["risk"] >= 2:
        activities.extend(["perform_risk_analysis", "independent_risk_focused_review"])
    if scores["novelty"] >= 2:
        activities.extend(["research_or_spike", "validate_assumptions_before_implementation"])
    if scores["verification_difficulty"] >= 2:
        activities.append("write_explicit_verification_plan")
    if FLOW_RANK[flow] >= FLOW_RANK["STANDARD"]:
        activities.extend(["write_implementation_plan", "spec_compliance_review", "code_quality_review"])
    if flow == "DEEP":
        activities.extend(["adversarial_or_failure_mode_review", "release_readiness_check"])
    # stable dedupe
    activities = list(dict.fromkeys(activities))

    return {
        "status": "CLASSIFIED",
        "flow_profile": flow,
        "work_profile": profile,
        "required_activities": activities,
        "implementation_blockers": blockers,
        "decision_trace": decision_trace,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("facts", type=Path, help="Path to work-facts JSON")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--strict-evidence", action="store_true", help="Require accepted provenance for every resolved fact")
    args = parser.parse_args(argv)
    try:
        facts = json.loads(args.facts.read_text(encoding="utf-8"))
        result = classify(facts, strict_evidence=args.strict_evidence)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if result.get("status") == "CLASSIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
