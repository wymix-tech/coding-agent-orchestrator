"""Prepare route drafts and generate literal evidence without accepting decisions."""


from scripts import route_inputs

from scripts.cao_native_sync import route_preparation_action
from scripts.cao_path import path_assessment_state
from scripts.cao_provider_workflows import provider_native_skills
from scripts.cao_runtime import rel
from scripts.cao_tool_policy import verification_baseline_state
from scripts.native_route import build_route as native_build_route
from scripts.native_route import RouteValidationError
from scripts.path_guard import repo_file


def dispatch(args, repo, work):
    baseline = verification_baseline_state(repo, work)
    if not baseline["ready"]:
        raise ValueError(baseline["reason"])
    if not path_assessment_state(repo, work)["ready"]:
        raise ValueError("PATH_ASSESSMENT_REQUIRED")
    preparation = route_preparation_action(repo, work, {"reason": "NATIVE_ROUTE_REQUIRED"})
    if preparation["reason"] != "NATIVE_ROUTE_REQUIRED":
        if preparation.get("diagnostics"):
            raise RouteValidationError(preparation["diagnostics"])
        raise ValueError(preparation["reason"])
    provider = work["sdd"]["provider"]
    if args.route_action == "rule":
        path, rule, index = route_inputs.add_rule(repo, work, args.source, args.start_line, args.end_line,
                                               provider_native_skills(repo, provider), args.index)
        return {"ok": True, "declaration_path": rel(path, repo), "rule_index": index, "rule": rule,
                "next": "Review all declaration fields, run `cao sdd route check`, then propose; this only generated source evidence."}
    source = repo_file(repo, args.from_declaration) if args.from_declaration else None
    sdd = work["sdd"]
    primary = sdd.get("native_ref") or ""
    draft = {"provider": provider, "provider_version": "", "requirement_sha256": work["requirement"]["sha256"],
             "profile": work["path_assessment"]["profile"], "workflow": (sdd.get("workflow") or {}).get("name") or ("bmad-build" if provider == "bmad" else ""),
             "route": "", "build_route": native_build_route(repo_file(repo, primary)) if provider == "bmad" else None,
             "reason": "", "primary_artifact": primary, "required_artifacts": [{"path": primary, "role": "spec"}],
             "rules": [], "omitted": []}
    path, draft, created = route_inputs.prepare(repo, work, draft, source)
    return {"ok": True, "status": "draft_created" if created else "existing_draft_preserved",
            "declaration_path": rel(path, repo), "declaration": draft,
            "next": "Fill the actual provider version, route, reason, required artifacts and omissions in this CAO input. Generate native evidence with `cao sdd route rule`, then `cao sdd route check` before proposing. A draft does not approve a SPEC or execute a workflow."}
