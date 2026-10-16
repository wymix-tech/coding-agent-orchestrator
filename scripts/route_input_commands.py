"""Prepare route drafts and generate literal evidence without accepting decisions."""
from scripts import route_inputs


def dispatch(args, core, repo, work):
    baseline = core.verification_baseline_state(repo, work)
    if not baseline["ready"]:
        raise ValueError(baseline["reason"])
    if not core.path_assessment_state(repo, work)["ready"]:
        raise ValueError("PATH_ASSESSMENT_REQUIRED")
    preparation = core.route_preparation_action(repo, work, {"reason": "NATIVE_ROUTE_REQUIRED"})
    if preparation["reason"] != "NATIVE_ROUTE_REQUIRED":
        raise ValueError(preparation["reason"])
    provider = work["sdd"]["provider"]
    if args.route_action == "rule":
        path, rule, index = route_inputs.add_rule(repo, work, args.source, args.start_line, args.end_line,
                                               core.provider_native_skills(repo, provider), args.index)
        return {"ok": True, "declaration_path": core.rel(path, repo), "rule_index": index, "rule": rule,
                "next": "Review all declaration fields, run `cao sdd route check`, then propose; this only generated source evidence."}
    source = core.repo_file(repo, args.from_declaration) if args.from_declaration else None
    sdd = work["sdd"]
    primary = sdd.get("native_ref") or ""
    draft = {"provider": provider, "provider_version": "", "requirement_sha256": work["requirement"]["sha256"],
             "profile": work["path_assessment"]["profile"], "workflow": (sdd.get("workflow") or {}).get("name") or ("bmad-build" if provider == "bmad" else ""),
             "route": "", "build_route": core.native_build_route(core.repo_file(repo, primary)) if provider == "bmad" else None,
             "reason": "", "primary_artifact": primary, "required_artifacts": [{"path": primary, "role": "spec"}],
             "rules": [], "omitted": []}
    path, draft, created = route_inputs.prepare(repo, work, draft, source)
    return {"ok": True, "status": "draft_created" if created else "existing_draft_preserved",
            "declaration_path": core.rel(path, repo), "declaration": draft,
            "next": "Fill the actual provider version, route, reason, required artifacts and omissions in this CAO input. Generate native evidence with `cao sdd route rule`, then `cao sdd route check` before proposing. A draft does not approve a SPEC or execute a workflow."}
