#!/usr/bin/env python3
"""V6 end-to-end intake: V4 facts + CBM impact + deterministic decision + verification plan.

The pipeline never lets CBM select a flow. CBM contributes structural evidence only;
V3 Decision Engine remains the sole flow classifier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Optional

import cbm_provider
import decision_engine
import fact_extractor
import fact_resolver
import impact_mapper
import verification_planner
import execution_state_manager
import policy_engine


def dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def combined_snapshot(facts: dict, impact: dict) -> str:
    material = {
        "facts": (facts.get("extraction") or {}).get("snapshot_id"),
        "impact": (impact.get("snapshot") or {}).get("id"),
        "provider": (impact.get("provider") or {}).get("id"),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--request", default="")
    p.add_argument("--request-file", type=Path)
    p.add_argument("--base-ref")
    p.add_argument("--resolutions", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path(".orchestrator/intake"))
    p.add_argument("--cbm-binary", default="codebase-memory-mcp")
    p.add_argument("--cbm-depth", type=int, default=3)
    p.add_argument("--cbm-fixture", type=Path, help="offline/test raw detect_changes JSON")
    p.add_argument("--allow-cbm-unavailable", action="store_true")
    p.add_argument("--allow-partial-impact", action="store_true", help="do not fail closed on CBM pagination/partial blast radius")
    p.add_argument("--state", type=Path, default=Path(".orchestrator/execution-state.yaml"))
    p.add_argument("--sync-state", action="store_true", help="attach analysis refs/snapshot and reconcile computed flow")
    p.add_argument("--actor", default="orchestrator")
    p.add_argument("--policy-manifest", type=Path, help="project Engineering Policy manifest; defaults to .orchestrator/policies/manifest.yaml when present")
    p.add_argument("--policy-stage", default="implementation", choices=["bootstrap", "planning", "implementation", "review", "verification"])
    p.add_argument("--ecc-root", type=Path, help="optional ECC rules root; overrides manifest source root")
    p.add_argument("--skip-policy", action="store_true")
    args = p.parse_args(argv)

    request = args.request_file.read_text(encoding="utf-8") if args.request_file else args.request
    repo = args.repo.resolve()
    outdir = args.output_dir

    draft = fact_extractor.extract(repo, request=request, base_ref=args.base_ref)
    dump(outdir / "work-facts.v4-draft.json", draft)

    if args.cbm_fixture:
        raw = json.loads(args.cbm_fixture.read_text(encoding="utf-8"))
        impact = cbm_provider.normalize_detect_changes(raw, repo=repo, project=repo.name)
    else:
        provider = cbm_provider.CBMProvider(args.cbm_binary)
        try:
            impact = provider.collect_impact(
                repo,
                scope="branch" if args.base_ref else "all",
                base_branch=args.base_ref,
                depth=args.cbm_depth,
                refresh_index=True,
            )
        except Exception as exc:
            if not args.allow_cbm_unavailable:
                dump(outdir / "semantic-impact.error.json", {
                    "status": "PROVIDER_UNAVAILABLE",
                    "provider": "codebase-memory-mcp",
                    "error": str(exc),
                    "principle": "do not convert provider absence into low impact",
                })
                print(json.dumps({
                    "status": "PROVIDER_UNAVAILABLE",
                    "provider": "codebase-memory-mcp",
                    "error": str(exc),
                    "artifact": str(outdir / "semantic-impact.error.json"),
                }, ensure_ascii=False, indent=2))
                return 2
            impact = {
                "schema_version": 1,
                "provider": {"id": "codebase-memory-mcp", "available": False, "authority": "none"},
                "snapshot": {"id": None},
                "changes": {"changed_files": [], "changed_symbols": []},
                "impact": {"impacted_symbols": [], "impacted_symbol_count": 0, "affected_modules": [], "affected_services": [], "affected_projects": []},
                "boundaries": {"relation_types": [], "integration_relations": [], "async_relations": [], "data_relations": [], "cross_service": False, "cross_project": False, "async_boundary": False},
                "contracts": {"public_contract_signal": False, "changed_contract_files": [], "changed_route_symbols": []},
                "coverage": {"status": "unknown"},
                "provider_opinion": {"risk_labels": [], "authoritative_for_flow": False},
                "evidence": [],
                "error": str(exc),
            }

    dump(outdir / "semantic-impact.json", impact)

    policy_plan = None
    policy_evaluation = None
    policy_manifest = args.policy_manifest
    if policy_manifest is None:
        candidate = repo / ".orchestrator" / "policies" / "manifest.yaml"
        if candidate.exists():
            policy_manifest = candidate
    if policy_manifest is not None and not args.skip_policy:
        policy_plan = policy_engine.route(repo, impact, policy_manifest.resolve(), args.policy_stage, args.ecc_root)
        dump(outdir / "policy-plan.json", policy_plan)
        (outdir / "policy-context.md").write_text(policy_engine.render_context(policy_plan), encoding="utf-8")
        policy_evaluation = policy_engine.evaluate(repo, policy_plan, policy_manifest.resolve())
        dump(outdir / "policy-evaluation.json", policy_evaluation)

    enriched = impact_mapper.enrich_work_facts(draft, impact)
    enriched.setdefault("extraction", {})["analysis_snapshot_id"] = combined_snapshot(enriched, impact)
    dump(outdir / "work-facts.semantic-draft.json", enriched)

    facts = enriched
    if args.resolutions:
        resolution_doc = json.loads(args.resolutions.read_text(encoding="utf-8"))
        facts = fact_resolver.apply_resolutions(enriched, resolution_doc)
        dump(outdir / "work-facts.resolved.json", facts)

    decision = decision_engine.classify(facts, strict_evidence=True)
    completeness = impact.get("completeness") or {"complete": True, "status": "complete"}
    if not completeness.get("complete", True) and not args.allow_partial_impact:
        decision = {
            "status": "NEEDS_EVIDENCE",
            "reason": "CBM semantic impact is partial/paginated; collect continuation pages before final flow classification",
            "semantic_impact_completeness": completeness,
            "provisional_decision": decision,
            "implementation_blockers": ["complete_semantic_impact_collection"],
        }
    dump(outdir / "decision.json", decision)
    plan = verification_planner.build_plan(facts, impact, decision, policy_plan)
    dump(outdir / "verification-plan.json", plan)

    state_sync = None
    if args.sync_state:
        state_path = args.state
        current = execution_state_manager._load(state_path)
        analysis_snapshot = (enriched.get("extraction") or {}).get("analysis_snapshot_id")
        current = execution_state_manager.attach_analysis(
            state_path,
            args.actor,
            analysis_snapshot,
            str(outdir / "semantic-impact.json"),
            str(outdir / ("work-facts.resolved.json" if args.resolutions else "work-facts.semantic-draft.json")),
            str(outdir / "decision.json"),
            str(outdir / "verification-plan.json"),
            (impact.get("provider") or {}).get("id") or "codebase-memory-mcp",
            current["revision"],
            policy_plan_ref=str(outdir / "policy-plan.json") if policy_plan else None,
            policy_evaluation_ref=str(outdir / "policy-evaluation.json") if policy_evaluation else None,
            policy_context_ref=str(outdir / "policy-context.md") if policy_plan else None,
            policy_snapshot_id=(policy_plan or {}).get("policy_snapshot_id"),
        )
        if decision.get("status") == "CLASSIFIED" and decision.get("flow_profile"):
            current = execution_state_manager.set_flow_profile(
                state_path, decision["flow_profile"], args.actor, str(outdir / "decision.json"), current["revision"]
            )
        if policy_plan and policy_evaluation:
            for gate in policy_engine.build_state_gates(policy_plan, policy_evaluation):
                current = execution_state_manager.record_gate(
                    state_path,
                    gate["name"],
                    gate["required"],
                    gate["status"],
                    args.actor,
                    "policy",
                    gate.get("evidence_ref") or str(outdir / "policy-plan.json"),
                    gate.get("command"),
                    current["revision"],
                )
        state_sync = {
            "state": str(state_path),
            "revision": current["revision"],
            "flow_profile": current["flow_profile"],
            "execution_snapshot_id": current.get("execution_snapshot_id"),
        }

    policy_blocked = bool(
        policy_plan and (
            policy_plan.get("status") == "CONFLICT"
            or (policy_evaluation or {}).get("status") == "FAILED"
        )
    )
    summary = {
        "status": decision.get("status"),
        "flow_profile": decision.get("flow_profile"),
        "analysis_snapshot_id": (enriched.get("extraction") or {}).get("analysis_snapshot_id"),
        "semantic_impact_snapshot": (impact.get("snapshot") or {}).get("id"),
        "provider": (impact.get("provider") or {}).get("id"),
        "provider_risk_ignored": True,
        "policy": {
            "enabled": policy_plan is not None,
            "snapshot_id": (policy_plan or {}).get("policy_snapshot_id"),
            "status": (policy_evaluation or {}).get("status"),
            "applicable_rule_count": len((policy_plan or {}).get("applicable_rules", [])),
            "blocked": policy_blocked,
            "conflicts": (policy_plan or {}).get("conflicts", []),
        },
        "unresolved_count": len((facts.get("extraction") or {}).get("resolution_queue", [])),
        "state_sync": state_sync,
        "artifacts": {
            "semantic_impact": str(outdir / "semantic-impact.json"),
            "work_facts": str(outdir / ("work-facts.resolved.json" if args.resolutions else "work-facts.semantic-draft.json")),
            "decision": str(outdir / "decision.json"),
            "verification_plan": str(outdir / "verification-plan.json"),
            "policy_plan": str(outdir / "policy-plan.json") if policy_plan else None,
            "policy_evaluation": str(outdir / "policy-evaluation.json") if policy_evaluation else None,
            "policy_context": str(outdir / "policy-context.md") if policy_plan else None,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if decision.get("status") != "CLASSIFIED":
        return 1
    if policy_blocked:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
