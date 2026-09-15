#!/usr/bin/env python3
"""V6.2 end-to-end intake: facts + CBM impact + policy + decision + verification + context projection.

CBM contributes structural evidence only; the Decision Engine remains the sole flow classifier.
The Context Plane indexes authorities and emits snapshot-bound role/stage packs without
becoming a new source of truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
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
import context_plane
import provider_incident


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


INTAKE_HISTORY_REL = ".orchestrator/runtime/intake-history.json"


def _file_sha256(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def intake_fingerprint(request: str, base_ref: str | None, resolutions: Path | None,
                       *, explicit_revision: str | None = None, evidence_refs: Iterable[str] | None = None,
                       policy_manifest: Path | None = None, reanalyze: bool = False) -> str:
    """Identity of the *inputs* an agent controls, independent of repository state.

    Explicit inputs re-analysis depends on are part of the fingerprint: a new resolutions file,
    a new base-ref or a new explicit revision is new evidence and must not be swallowed by a
    "source unchanged" shortcut.
    """
    material = {
        "request": request,
        "base_ref": base_ref or "",
        "resolutions": _file_sha256(resolutions),
        "explicit_revision": explicit_revision or "",
        "policy_manifest": _file_sha256(policy_manifest),
        "reanalyze": bool(reanalyze),
        "evidence_refs": sorted({str(ref) for ref in (evidence_refs or []) if ref}),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def requirement_key(state_path: Path) -> str:
    try:
        state = execution_state_manager._load(state_path)
    except Exception:
        return "_unknown"
    wi = (state or {}).get("work_item") or {}
    rid, rev = wi.get("requirement_id"), wi.get("requirement_revision")
    return f"{rid}:{rev or ''}" if rid else "_unknown"


def record_intake(repo: Path, key: str, fingerprint: str, decision_status: str,
                  resolutions_provided: bool, run_dir: Path) -> dict | None:
    """Detect the retry-without-new-evidence loop and return a warning when it occurs.

    Deterministic collectors guarantee that identical inputs produce an identical draft.
    Re-running intake is therefore not a repair action; only new evidence changes the
    outcome. Without this signal an agent can spin on NEEDS_EVIDENCE indefinitely.
    """
    path = repo / INTAKE_HISTORY_REL
    doc: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            doc = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            doc = {}
    entries = doc.setdefault("requirements", {})
    if not isinstance(entries, dict):
        entries = doc["requirements"] = {}
    prev = entries.get(key) if isinstance(entries.get(key), dict) else {}

    warning = None
    if (
        decision_status != "CLASSIFIED"
        and prev.get("fingerprint") == fingerprint
        and prev.get("decision_status") == decision_status
    ):
        repeat = int(prev.get("repeat_count", 1)) + 1
        if resolutions_provided:
            next_action = "strengthen_resolution_evidence"
            remedy = ("the same resolutions file was supplied again; raise evidence strength to "
                      "authoritative/observed/derived, or add negative_proof for false claims")
        else:
            next_action = "supply_fact_resolutions"
            remedy = ("no resolutions were supplied; fill the resolutions template with evidence "
                      "and re-run with --resolutions")
        warning = {
            "code": "IDENTICAL_INPUT_NO_NEW_EVIDENCE",
            "message": (f"This requirement revision has been analyzed {repeat} times with identical inputs "
                        f"and returned {decision_status} every time. Re-running intake cannot change a "
                        f"deterministic result: {remedy}."),
            "repeat_count": repeat,
            "next_action": next_action,
        }
    else:
        repeat = 1

    entries[key] = {
        "fingerprint": fingerprint,
        "decision_status": decision_status,
        "resolutions_provided": resolutions_provided,
        "repeat_count": repeat,
        "run_count": int(prev.get("run_count", 0)) + 1,
        "last_run_dir": str(run_dir),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    dump(path, doc)
    return warning


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
    p.add_argument("--retry-cbm", action="store_true", help="explicitly retry an open CBM provider incident once")
    p.add_argument("--allow-partial-impact", action="store_true", help="do not fail closed on CBM pagination/partial blast radius")
    p.add_argument("--state", type=Path, default=Path(".orchestrator/execution-state.yaml"))
    p.add_argument("--sync-state", action="store_true", help="attach analysis refs/snapshot and reconcile computed flow")
    p.add_argument("--expected-state-revision", type=int, help="optimistic revision captured before long-running analysis")
    p.add_argument("--actor", default="orchestrator")
    p.add_argument("--policy-manifest", type=Path, help="project Engineering Policy manifest; defaults to .orchestrator/policies/manifest.yaml when present")
    p.add_argument("--policy-stage", default="implementation", choices=["bootstrap", "planning", "implementation", "review", "verification"])
    p.add_argument("--ecc-root", type=Path, help="optional ECC rules root; overrides manifest source root")
    p.add_argument("--skip-policy", action="store_true")
    p.add_argument("--skip-context", action="store_true", help="do not generate V6.2 Context Manifest/Pack")
    p.add_argument("--context-role", default="implementer", choices=sorted(context_plane.ROLES))
    p.add_argument("--context-stage", choices=sorted(context_plane.STAGES), help="defaults to policy stage")
    p.add_argument("--context-max-items", type=int, default=12)
    p.add_argument("--context-max-chars", type=int, default=16000)
    p.add_argument("--sdd-provider", choices=["openspec", "bmad", "generic", "other"])
    p.add_argument("--sdd-ref", type=Path, help="authoritative SDD artifact/change/story reference for Context Manifest")
    p.add_argument("--requirement-revision", help="explicit external revision label; tracks alongside content, never replaces it")
    p.add_argument("--evidence-ref", action="append", default=[], dest="evidence_refs",
                   help="explicit evidence reference that must reach re-analysis")
    p.add_argument("--reanalyze", action="store_true", help="explicit re-analysis intent; defeats the source-unchanged shortcut")
    args = p.parse_args(argv)

    request = args.request_file.read_text(encoding="utf-8") if args.request_file else args.request
    repo = args.repo.resolve()
    outdir = args.output_dir if args.output_dir.is_absolute() else repo / args.output_dir
    state_path_resolved = args.state if args.state.is_absolute() else repo / args.state

    request_ref = None
    if request:
        request_ref = outdir / "request-context.md"
        request_ref.parent.mkdir(parents=True, exist_ok=True)
        request_ref.write_text(request, encoding="utf-8")

    draft = fact_extractor.extract(repo, request=request, base_ref=args.base_ref)
    dump(outdir / "work-facts.v4-draft.json", draft)

    baseline = cbm_provider.repository_baseline(repo)
    if args.cbm_fixture:
        raw = json.loads(args.cbm_fixture.read_text(encoding="utf-8"))
        impact = cbm_provider.normalize_detect_changes(raw, repo=repo, project=repo.name, base_ref=args.base_ref)
    elif baseline["status"] == "empty_greenfield":
        # BMAD/Orchestrator installation metadata is not a product codebase. With no product
        # files, CBM detect_changes is not applicable and must not open a provider incident.
        impact = cbm_provider.empty_greenfield_impact(repo, baseline)
    else:
        provider = cbm_provider.CBMProvider(args.cbm_binary)
        health = provider.health()
        provider_id = provider_incident.identity(health.get("binary"), health.get("version"))
        open_incident = provider_incident.inspect(repo)
        if (
            not args.retry_cbm
            and open_incident.get("status") == "open"
            and open_incident.get("provider_identity") == provider_id
        ):
            blocked = {
                "status": "PROVIDER_BLOCKED",
                "provider": "codebase-memory-mcp",
                "error": open_incident.get("error"),
                "error_class": open_incident.get("error_class"),
                "incident": str(repo / ".orchestrator/providers/codebase-memory-mcp.json"),
                "attempt_count": open_incident.get("attempt_count"),
                "next_action": "repair_cbm_provider",
                "retry": "explicit_only",
                "principle": "provider failure is an operational blocker, not a request for more work-fact evidence",
            }
            dump(outdir / "semantic-impact.error.json", blocked)
            print(json.dumps({**blocked, "artifact": str(outdir / "semantic-impact.error.json")}, ensure_ascii=False, indent=2))
            return 3
        try:
            impact = provider.collect_impact(
                repo,
                scope="branch" if args.base_ref else "all",
                base_branch=args.base_ref,
                depth=args.cbm_depth,
                refresh_index=True,
            )
            provider_incident.record_success(
                repo, binary=health.get("binary"), version=health.get("version")
            )
        except Exception as exc:
            diagnostics = provider.diagnostics()
            error_class = cbm_provider._classify_provider_error(str(exc))
            incident = provider_incident.record_failure(
                repo, error_class=error_class, error=str(exc),
                binary=health.get("binary"), version=health.get("version"), diagnostics=diagnostics,
            )
            if not args.allow_cbm_unavailable:
                failure = {
                    "status": "PROVIDER_UNAVAILABLE",
                    "provider": "codebase-memory-mcp",
                    "error": str(exc),
                    "error_class": error_class,
                    "incident": str(repo / ".orchestrator/providers/codebase-memory-mcp.json"),
                    "attempt_count": incident.get("attempt_count"),
                    "next_action": "repair_cbm_provider",
                    "retry": "explicit_only",
                    "diagnostics": diagnostics,
                    "principle": "do not convert provider failure into low impact or repeatedly request unrelated evidence",
                }
                dump(outdir / "semantic-impact.error.json", failure)
                print(json.dumps({**failure, "artifact": str(outdir / "semantic-impact.error.json")}, ensure_ascii=False, indent=2))
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
    if policy_manifest is not None and not policy_manifest.is_absolute():
        policy_manifest = repo / policy_manifest
    if policy_manifest is None:
        candidate = repo / ".orchestrator" / "policies" / "manifest.yaml"
        if candidate.exists():
            policy_manifest = candidate
    ecc_root = args.ecc_root
    if ecc_root is not None and not ecc_root.is_absolute():
        ecc_root = repo / ecc_root
    if policy_manifest is not None and not args.skip_policy:
        policy_plan = policy_engine.route(repo, impact, policy_manifest.resolve(), args.policy_stage, ecc_root)
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

    template_path = fact_resolver.write_resolution_template(
        outdir, facts, source_ref=str(args.resolutions) if args.resolutions else str(request_ref) if request_ref else None
    )

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

    warnings: list[dict[str, Any]] = []
    try:
        loop_warning = record_intake(
            repo,
            requirement_key(state_path_resolved),
            intake_fingerprint(
                request, args.base_ref, args.resolutions,
                explicit_revision=getattr(args, "requirement_revision", None),
                evidence_refs=getattr(args, "evidence_refs", None) or [],
                policy_manifest=args.policy_manifest,
                reanalyze=getattr(args, "reanalyze", False),
            ),
            str(decision.get("status")),
            args.resolutions is not None,
            outdir,
        )
    except OSError:
        loop_warning = None  # history is diagnostic only; never fail intake for it
    if loop_warning:
        loop_warning["resolutions_template"] = str(template_path)
        warnings.append(loop_warning)

    state_sync = None
    if args.sync_state:
        state_path = state_path_resolved
        try:
            current = execution_state_manager._load(state_path)
            base_revision = args.expected_state_revision if args.expected_state_revision is not None else current["revision"]
            if current["revision"] != base_revision:
                raise execution_state_manager.RevisionConflict(
                    f"analysis started from revision {base_revision}, current is {current['revision']}"
                )
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
                base_revision,
                policy_plan_ref=str(outdir / "policy-plan.json") if policy_plan else None,
                policy_evaluation_ref=str(outdir / "policy-evaluation.json") if policy_evaluation else None,
                policy_context_ref=str(outdir / "policy-context.md") if policy_plan else None,
                policy_snapshot_id=(policy_plan or {}).get("policy_snapshot_id"),
                context_manifest_ref=str(outdir / "context-manifest.json") if not args.skip_context else None,
                context_pack_ref=str(outdir / f"context-pack.{args.context_role}.{args.context_stage or args.policy_stage}.json") if not args.skip_context else None,
                requirement_ref=str(args.sdd_ref) if args.sdd_ref else str(request_ref) if request_ref else None,
            )
            if decision.get("status") == "CLASSIFIED" and decision.get("flow_profile"):
                current = execution_state_manager.set_flow_profile(
                    state_path, decision["flow_profile"], args.actor, str(outdir / "decision.json"), current["revision"]
                )
            current = execution_state_manager.reconcile_verification_obligations(
                state_path, plan, args.actor, current["revision"]
            )
            if policy_plan and policy_evaluation:
                for gate in policy_engine.build_state_gates(policy_plan, policy_evaluation):
                    current = execution_state_manager.record_gate(
                        state_path, gate["name"], gate["required"], gate["status"], args.actor,
                        "policy", gate.get("evidence_ref") or str(outdir / "policy-plan.json"),
                        gate.get("command"), current["revision"],
                    )
            for item in plan.get("items", []):
                if item.get("required_by_impact"):
                    current = execution_state_manager.record_gate(
                        state_path, item["gate_name"], True, "pending", args.actor,
                        "verification", item.get("evidence_ref"), expected_revision=current["revision"],
                    )
            state_sync = {
                "state": str(state_path),
                "revision": current["revision"],
                "flow_profile": current["flow_profile"],
                "execution_snapshot_id": current.get("execution_snapshot_id"),
            }
        except execution_state_manager.RevisionConflict as exc:
            conflict = {
                "status": "STATE_CONFLICT",
                "error": str(exc),
                "expected_revision": args.expected_state_revision,
                "state": str(state_path),
                "artifacts_preserved_for_diagnostics": str(outdir),
            }
            dump(outdir / "state-sync.error.json", conflict)
            print(json.dumps(conflict, ensure_ascii=False, indent=2))
            return 4

    context_summary = None
    if not args.skip_context:
        context_stage = args.context_stage or args.policy_stage
        manifest = context_plane.build_manifest(
            repo,
            outdir,
            state_ref=state_path_resolved if state_path_resolved.exists() else None,
            request_ref=request_ref,
            sdd_ref=args.sdd_ref,
            sdd_provider=args.sdd_provider,
            policy_manifest_ref=policy_manifest,
        )
        manifest_path = outdir / "context-manifest.json"
        context_plane._dump_json(manifest_path, manifest)
        pack = context_plane.build_pack(
            manifest, args.context_role, context_stage,
            max_items=args.context_max_items, max_chars=args.context_max_chars,
        )
        pack_path = outdir / f"context-pack.{args.context_role}.{context_stage}.json"
        pack_md_path = outdir / f"context-pack.{args.context_role}.{context_stage}.md"
        context_plane._dump_json(pack_path, pack)
        pack_md_path.write_text(context_plane.render_pack(pack), encoding="utf-8")
        context_summary = {
            "manifest": str(manifest_path),
            "pack": str(pack_path),
            "markdown": str(pack_md_path),
            "context_snapshot_id": manifest.get("context_snapshot_id"),
            "pack_snapshot_id": pack.get("pack_snapshot_id"),
            "role": args.context_role,
            "stage": context_stage,
            "blockers": manifest.get("blockers", []),
            "warnings": manifest.get("warnings", []),
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
        "warnings": warnings,
        "state_sync": state_sync,
        "context": context_summary,
        "artifacts": {
            "resolutions_template": str(template_path),
            "semantic_impact": str(outdir / "semantic-impact.json"),
            "work_facts": str(outdir / ("work-facts.resolved.json" if args.resolutions else "work-facts.semantic-draft.json")),
            "decision": str(outdir / "decision.json"),
            "verification_plan": str(outdir / "verification-plan.json"),
            "policy_plan": str(outdir / "policy-plan.json") if policy_plan else None,
            "policy_evaluation": str(outdir / "policy-evaluation.json") if policy_evaluation else None,
            "policy_context": str(outdir / "policy-context.md") if policy_plan else None,
            "request_context": str(request_ref) if request_ref else None,
            "context_manifest": str(outdir / "context-manifest.json") if context_summary else None,
            "context_pack": context_summary.get("pack") if context_summary else None,
            "context_pack_markdown": context_summary.get("markdown") if context_summary else None,
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
