"""Single authorization policy for execution, phase advance, and closure.

collect_evidence reads authoritative inputs; evaluate is a pure decision function.
Host/CLI/state adapters translate operations, never redefine their eligibility.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
import repository_snapshot as snapshots
import provider_incident
import cbm_provider

ACTIONS = {"read", "prepare", "mutate_code", "mutate_governance", "advance", "close", "finish_role"}
PHASES = ("discovery", "specification", "design", "planning", "implementation", "review", "verification", "release", "closed")
STATUSES = {"pending", "ready", "in_progress", "completed", "failed", "cancelled"}
LATE_PHASES = {"review", "verification", "release", "closed"}
GUARDED_PHASES = LATE_PHASES | {"implementation"}
ANALYSIS_REFS = ("decision_ref", "semantic_impact_ref", "work_facts_ref", "verification_plan_ref",
                 "policy_plan_ref", "policy_evaluation_ref", "policy_context_ref", "requirement_ref")


def active_blockers(state: dict | None) -> list[dict]:
    return [b for b in (state or {}).get("blockers", []) if b.get("resolved_at") is None]


def resolve_ref(repo: Path, ref: str) -> Path:
    path = Path(ref)
    return path if path.is_absolute() else repo / path


def _json(repo: Path, ref: str | None) -> dict | None:
    if not ref:
        return None
    try:
        doc = json.loads(resolve_ref(repo, ref).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def semantic_complete(impact: dict | None) -> bool:
    if not isinstance(impact, dict):
        return False
    completeness, provider = impact.get("completeness"), impact.get("provider", {})
    return bool(isinstance(completeness, dict) and completeness.get("complete") is True
                and isinstance(provider, dict) and provider.get("available") is not False)


def bind_analysis(repo: Path, analysis: dict) -> dict:
    """Bind analysis to inputs, excluding mutable state and generated projections."""
    refs = [str(analysis[k]) for k in ANALYSIS_REFS if analysis.get(k)]
    refs += [str(p) for p in (repo / ".orchestrator/config.yaml", repo / ".orchestrator/policies") if p.exists()]
    # Bind the exact policy sources selected by the plan, including packs outside the
    # default .orchestrator/policies directory. This closes the gap where an external
    # project-owned pack could change while the generated plan file remained untouched.
    policy_plan = _json(repo, analysis.get("policy_plan_ref"))
    if isinstance(policy_plan, dict):
        manifest_ref = policy_plan.get("manifest")
        manifest_path = resolve_ref(repo, str(manifest_ref)) if manifest_ref else None
        if manifest_path is not None:
            refs.append(str(manifest_path))
            base = manifest_path.parent
            for src in policy_plan.get("policy_sources") or []:
                ref = (src or {}).get("ref") if isinstance(src, dict) else None
                if ref:
                    refs.append(str((base / str(ref)).resolve()))
    fallback = repo / ".orchestrator/intake/request-context.md"
    if fallback.exists():
        refs.append(str(fallback))
    hashes = {}
    for ref in sorted(set(refs)):
        try:
            hashes[ref] = snapshots.digest_path(resolve_ref(repo, ref))
        except OSError:
            hashes[ref] = None
    decision = _json(repo, analysis.get("decision_ref"))
    impact = _json(repo, analysis.get("semantic_impact_ref"))
    complete = semantic_complete(impact)
    binding = {"repository_snapshot_id": snapshots.fingerprint(repo), "authority_hashes": hashes,
               "decision_status": (decision or {}).get("status"), "semantic_complete": complete}
    # Caller-supplied analysis IDs can be reused even when requirements or policy
    # content changes. Gate/review/verification evidence must bind to actual inputs.
    binding["evidence_snapshot_id"] = hashlib.sha256(json.dumps({
        "analysis_snapshot_id": analysis.get("analysis_snapshot_id"), **binding,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return binding


def evidence_matches(state: dict, result: dict) -> bool:
    binding = (state.get("analysis") or {}).get("evidence_snapshot_id")
    return bool(binding and result.get("evidence_snapshot_id") == binding
                and state.get("execution_snapshot_id")
                and result.get("snapshot_id") == state.get("execution_snapshot_id"))


def required_gates(state: dict, evidence: dict | None = None) -> list[str]:
    required = {name for name, gate in (state.get("quality_gates") or {}).items() if gate.get("required")}
    # Durable active obligations outrank a later plan that simply omits them. They disappear
    # only through an audited disposition recorded in execution state.
    for obligation in (state.get("verification_obligations") or {}).values():
        if obligation.get("status") == "active" and obligation.get("required"):
            if obligation.get("gate_name"):
                required.add(str(obligation["gate_name"]))
    evidence = evidence or {}
    for item in (evidence.get("verification_plan") or {}).get("items", []):
        if item.get("required_by_impact"):
            required.add(str(item.get("gate_name") or "impact:" + str(item.get("kind"))))
    for item in (evidence.get("policy_plan") or {}).get("enforcements", []):
        if item.get("required"):
            required.add("policy:" + str(item.get("rule_id")) + ":" + str(item.get("gate") or item.get("engine") or "policy"))
    return sorted(required)


def collect_evidence(repo: Path | None, state: dict | None) -> dict:
    if repo is None:
        analysis = (state or {}).get("analysis") or {}
        return {"repository_available": False, "decision_status": analysis.get("decision_status")}
    repo = repo.resolve()
    evidence: dict[str, Any] = {"repository_available": True, "bootstrap_error": None}
    provider_state = provider_incident.inspect(repo)
    if provider_state.get("status") == "open":
        try:
            current_provider = cbm_provider.CBMProvider("codebase-memory-mcp")
            health = current_provider.health()
            current_identity = provider_incident.identity(health.get("binary"), health.get("version"))
            evidence["provider_incident"] = provider_state if provider_incident.is_open(repo, provider_identity=current_identity) else None
            evidence["provider_incident_stale"] = provider_state if evidence["provider_incident"] is None else None
        except Exception:
            evidence["provider_incident"] = provider_state
    else:
        evidence["provider_incident"] = None
    config = repo / ".orchestrator/config.yaml"
    if config.exists():
        try:
            cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
            orch = cfg.get("orchestrator") if isinstance(cfg, dict) else None
            if not isinstance(orch, dict):
                raise ValueError("invalid orchestrator config")
            if (orch.get("bootstrap") or {}).get("unresolved"):
                evidence["bootstrap_error"] = "Project bootstrap has unresolved authority/configuration."
        except (OSError, ValueError, yaml.YAMLError, AttributeError) as exc:
            evidence["bootstrap_error"] = str(exc)
    if state is None:
        return evidence
    analysis = state.get("analysis") or {}
    evidence["decision"] = _json(repo, analysis.get("decision_ref"))
    evidence["decision_status"] = (evidence["decision"] or {}).get("status")
    evidence["impact"] = _json(repo, analysis.get("semantic_impact_ref"))
    evidence["verification_plan"] = _json(repo, analysis.get("verification_plan_ref"))
    evidence["policy_plan"] = _json(repo, analysis.get("policy_plan_ref"))
    evidence["policy_evaluation"] = _json(repo, analysis.get("policy_evaluation_ref"))
    evidence["policy_configured"] = bool(analysis.get("policy_plan_ref") or
                                          (repo / ".orchestrator/policies/manifest.yaml").exists())
    try:
        current = snapshots.fingerprint(repo)
    except OSError:
        current = None
    evidence["repository_snapshot_id"] = current
    evidence["repository_fresh"] = bool(current and current == analysis.get("repository_snapshot_id"))
    hashes = analysis.get("authority_hashes") or {}
    stale_authorities = []
    for ref, expected in hashes.items():
        try:
            actual = snapshots.digest_path(resolve_ref(repo, ref))
        except OSError:
            actual = None
        if expected is None or actual != expected:
            stale_authorities.append(ref)
    evidence["authority_fresh"] = bool(hashes) and not stale_authorities
    evidence["stale_authorities"] = stale_authorities
    manifest = _json(repo, analysis.get("context_manifest_ref"))
    stale_sources = []
    for src in (manifest or {}).get("sources", []):
        if src.get("id") in {"execution_state", "execution_history"}:
            continue  # current state is supplied directly, not trusted through a projection
        try:
            actual = snapshots.digest_path(resolve_ref(repo, src["ref"]))
        except (OSError, KeyError, TypeError):
            actual = None
        if actual is None or actual != src.get("content_sha256"):
            stale_sources.append(src.get("id"))
    source_ids = {x.get("id") for x in (manifest or {}).get("sources", [])}
    evidence["context_fresh"] = bool(manifest and {"requirement", "decision", "semantic_impact"} <= source_ids and not stale_sources)
    evidence["stale_context_sources"] = stale_sources
    return evidence


def evaluate(state: dict | None, action: str, *, evidence: dict | None = None,
             target_phase: str | None = None, target_status: str = "in_progress",
             role: str = "implementer", native_confirmed: bool = False,
             allow_governance_mutation: bool = False,
             governance_paths: list[str] | None = None) -> dict:
    """Pure, deterministic eligibility decision; no mutation, IO, or host overrides."""
    facts = evidence or {}
    reasons = []

    def deny(code: str, message: str, next_action: str) -> None:
        reasons.append({"code": code, "message": message, "next_action": next_action})

    if action not in ACTIONS:
        deny("UNKNOWN_ACTION", "Unknown governance action.", "inspect_action")
    elif action in {"read", "prepare"}:
        pass  # reading and non-production preparation remain available for recovery
    elif action == "mutate_governance":
        # Governance inputs grant authority; an agent must not be able to widen its own
        # authority by rewriting them. Legitimate changes go through the orchestrator CLI
        # (classified as prepare) or an explicit operator action.
        targets = ", ".join(sorted(set(governance_paths or []))) or "governance configuration"
        if not allow_governance_mutation:
            deny("GOVERNANCE_CONFIG_PROTECTED",
                 f"Governance input is not agent-writable: {targets}. Authority config, policy sources, "
                 "execution state, and requirement identity must be changed through the orchestrator CLI "
                 "or an explicit operator action, never by an agent edit.",
                 "reconfigure_governance_explicitly")
    elif state is None:
        deny("STATE_MISSING", "Canonical Execution State is missing; run intake before production-code mutation or advance.", "run_intake")
    elif action == "finish_role":
        outcome = state.get("review" if role == "reviewer" else "verification") or {}
        if role in {"reviewer", "verifier"} and (outcome.get("status") not in {"passed", "failed"} or not outcome.get("evidence")):
            deny("ROLE_OUTCOME_MISSING", f"Role {role} must record its outcome and evidence before finishing.", "record_role_outcome")
    else:
        phase = "closed" if action == "close" else target_phase
        if action in {"advance", "close"} and (phase not in PHASES or target_status not in STATUSES):
            deny("INVALID_TRANSITION", "Invalid target phase or status.", "inspect_state")
        blockers = active_blockers(state)
        if blockers or state.get("blocked"):
            next_step = f"resolve_blocker:{blockers[0]['id']}" if blockers else "reconcile_blockers"
            deny("ACTIVE_BLOCKER", "Active blocker prevents execution, phase advance, or close.", next_step)
        if facts.get("bootstrap_error"):
            deny("BOOTSTRAP_UNRESOLVED", facts["bootstrap_error"], "resolve_init_ambiguity")
        guarded = action in {"mutate_code", "close"} or phase in GUARDED_PHASES
        if guarded:
            provider_block = facts.get("provider_incident") or {}
            if provider_block.get("status") == "open":
                deny(
                    "SEMANTIC_PROVIDER_BLOCKED",
                    f"Semantic provider {provider_block.get('provider') or 'codebase-memory-mcp'} has an unresolved operational incident ({provider_block.get('error_class') or 'UNKNOWN'}).",
                    "repair_cbm_provider",
                )
            if facts.get("decision_status") != "CLASSIFIED":
                deny("DECISION_NOT_CLASSIFIED", "A CLASSIFIED Decision Engine result is required.", "resolve_fact_evidence" if facts.get("decision_status") == "NEEDS_EVIDENCE" else "run_semantic_intake")
            readiness = state.get("readiness") or {}
            if not readiness.get("sdd_ready"):
                deny("SDD_NOT_READY", "SDD/native planning state is not implementation-ready.", "advance_native_sdd_to_ready")
            if readiness.get("behavior_change") is True and not readiness.get("acceptance_criteria_present"):
                deny("ACCEPTANCE_CRITERIA_MISSING", "Behavior change requires acceptance criteria.", "define_acceptance_criteria")
            if not facts.get("repository_available"):
                deny("REPOSITORY_CONTEXT_REQUIRED", "Current repository evidence must be checked before authorization.", "revalidate_current_evidence")
            else:
                impact = facts.get("impact")
                if not semantic_complete(impact):
                    deny("SEMANTIC_EVIDENCE_INCOMPLETE", "Semantic impact evidence is missing, partial, or unavailable.", "run_semantic_intake")
                if not facts.get("authority_fresh"):
                    deny("AUTHORITY_EVIDENCE_STALE", "Requirement, decision, policy, or analysis evidence is stale or unbound.", "run_semantic_intake")
                plan = facts.get("policy_plan") or {}
                policy = facts.get("policy_evaluation") or {}
                if facts.get("policy_configured") and (not plan or not policy):
                    deny("POLICY_EVIDENCE_MISSING", "Configured Engineering Policy has no current plan/evaluation.", "run_semantic_intake")
                if plan.get("status") == "CONFLICT":
                    deny("POLICY_CONFLICT", "Engineering Policy contains unresolved conflicts.", "resolve_policy_conflict")
                # Existing violations can be repaired during implementation, but cannot advance.
                if action != "mutate_code" and policy.get("status") == "FAILED":
                    deny("POLICY_VIOLATION", "Blocking Engineering Policy violations remain.", "resolve_policy_violations")
                if not facts.get("context_fresh"):
                    deny("CONTEXT_STALE", "Context Manifest is missing, stale, or lacks required sources.", "refresh_context")
                enf = state.get("enforcement") or {}
                dirty = bool(enf.get("dirty"))
                tracked_edit = (dirty and enf.get("last_event") == "mutation"
                                and enf.get("last_mutation_snapshot_id") == facts.get("repository_snapshot_id"))
                if action != "mutate_code" or not tracked_edit:
                    if not facts.get("repository_fresh"):
                        deny("REPOSITORY_CHANGED", "Repository content no longer matches the analyzed snapshot.", "run_semantic_intake")
                if action != "mutate_code":
                    enf = state.get("enforcement") or {}
                    if dirty or enf.get("semantic_fresh") is False or enf.get("policy_fresh") is False:
                        deny("ANALYSIS_DIRTY", "Runtime enforcement evidence is dirty or stale after material mutation.", "run_semantic_intake")
        if action in {"advance", "close"} and (state.get("authority") or {}).get("mode") == "native" and not native_confirmed:
            status = "completed" if action == "close" else target_status
            if state.get("phase") != phase or state.get("status") != status:
                deny("NATIVE_AUTHORITY_REQUIRED", "Native authority owns phase/status; advance through the native adapter.", "advance_native_state")
        if action == "mutate_code":
            if state.get("phase") != "implementation" or state.get("status") not in {"ready", "in_progress"}:
                deny("CODE_PHASE_FORBIDDEN", f"Code mutation is not allowed in phase={state.get('phase')}; transition legally to implementation first.", "transition_to_implementation")
            if role in {"reviewer", "verifier", "planner", "resume"}:
                deny("ROLE_READ_ONLY", f"Role {role} may not mutate production code.", "handoff_to_implementer")
        if phase in LATE_PHASES:
            readiness = state.get("readiness") or {}
            if not readiness.get("implementation_tasks_complete"):
                deny("TASKS_INCOMPLETE", "Implementation tasks are not complete.", "implement_next_task")
        if phase in {"verification", "release", "closed"}:
            review = state.get("review") or {}
            if int(review.get("blocking_findings", 0)) > 0:
                deny("REVIEW_BLOCKING_FINDINGS", "Blocking review findings remain.", "resolve_review_findings")
            if review.get("required") and review.get("status") != "passed":
                deny("REVIEW_NOT_PASSED", "Required review has not passed.", "complete_required_review")
            if review.get("required") and review.get("status") == "passed" and not evidence_matches(state, review):
                deny("REVIEW_STALE", "Required review does not cover the current execution snapshot.", "complete_required_review")
        if phase in {"release", "closed"}:
            if not (state.get("readiness") or {}).get("acceptance_satisfied"):
                deny("ACCEPTANCE_UNSATISFIED", "Acceptance criteria are not recorded as satisfied.", "verify_acceptance_criteria")
            gates = state.get("quality_gates") or {}
            for name in required_gates(state, facts):
                gate = gates.get(name) or {}
                if gate.get("status") != "passed":
                    deny("REQUIRED_GATE_NOT_PASSED", f"Required gate {name} is {gate.get('status', 'missing')}.", f"run_required_gate:{name}")
                elif not evidence_matches(state, gate):
                    deny("GATE_EVIDENCE_STALE", f"Required gate {name} does not cover the current execution snapshot.", f"run_required_gate:{name}")
            ver = state.get("verification") or {}
            current = state.get("execution_snapshot_id")
            if ver.get("status") != "passed":
                deny("VERIFICATION_NOT_PASSED", "Final verification has not passed.", "run_fresh_final_verification")
            if not current or not ver.get("fresh") or not evidence_matches(state, ver):
                deny("VERIFICATION_STALE", "Final verification is not fresh for the current execution snapshot.", "run_fresh_final_verification")
    return {"schema_version": 1, "action": action, "target_phase": "closed" if action == "close" else target_phase,
            "allowed": not reasons, "decision": "deny" if reasons else "allow", "reasons": reasons,
            "reason_codes": list(dict.fromkeys(r["code"] for r in reasons)),
            "next_action": reasons[0]["next_action"] if reasons else None,
            "state_revision": (state or {}).get("revision"), "work_item_id": ((state or {}).get("work_item") or {}).get("id")}


def authorize(repo: Path | None, state: dict | None, action: str, **kwargs: Any) -> dict:
    if action in {"read", "prepare"}:
        return evaluate(state, action, **kwargs)
    return evaluate(state, action, evidence=collect_evidence(repo, state), **kwargs)


def next_action(state: dict | None, evidence: dict | None = None) -> str:
    if state is None:
        return "run_intake"
    facts = evidence or {"decision_status": (state.get("analysis") or {}).get("decision_status")}
    blockers = active_blockers(state)
    if blockers:
        return f"resolve_blocker:{blockers[0]['id']}"
    if state.get("blocked"):
        return "reconcile_blockers"
    if (facts.get("provider_incident") or {}).get("status") == "open":
        return "repair_cbm_provider"
    if facts.get("decision_status") != "CLASSIFIED":
        return "resolve_fact_evidence" if facts.get("decision_status") == "NEEDS_EVIDENCE" else "run_semantic_intake"
    phase = state.get("phase")
    if phase == "implementation" and not (state.get("readiness") or {}).get("implementation_tasks_complete"):
        check = evaluate(state, "mutate_code", evidence=facts)
        if not check["allowed"]:
            return check["next_action"]
        task = (state.get("cursor") or {}).get("current_task_id")
        return f"implement_task:{task}" if task else "implement_next_task"
    target = ("implementation" if phase in PHASES[:4] else
              "review" if phase == "implementation" and (state.get("review") or {}).get("required") else
              "verification" if phase in {"implementation", "review"} else "closed")
    check = evaluate(state, "close" if target == "closed" else "advance", evidence=facts, target_phase=target)
    if not check["allowed"]:
        return check["next_action"]
    return "none" if phase == "closed" else f"transition_to_{target}"
