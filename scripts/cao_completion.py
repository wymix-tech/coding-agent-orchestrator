"""Evidence freshness, acceptance inputs, and completion decisions."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.cao_defaults import SDD_CATALOG, SNAPSHOT_IGNORE_DIRS
from scripts.cao_native_sync import native_route_state
from scripts.cao_path import gate_is_auto_derived, gate_state, path_assessment_state
from scripts.cao_plans import provider_pending_plan_items, provider_verification_plan_state
from scripts.cao_provider_workflows import provider_output_dirs, validate_workflow_artifacts
from scripts.cao_providers import detect_current_host, detect_sdd, run_sdd_command
from scripts.cao_runtime import config, sha256_file
from scripts.cao_snapshot import (
    SNAPSHOT_FORMAT,
    code_snapshot,
    rules_snapshot,
    verification_snapshot,
)
from scripts.completion_policy import verification_fresh as bound_verification_fresh, workflow_ready
from scripts.light_spec_policy import binding_digest, light_spec_state
from scripts.native_obligations import (
    commit_state as native_commit_state,
    planning_state,
    review_state as native_review_state,
)
from scripts.path_guard import repo_file
from scripts.provider_restart import restart_state
from scripts.sdd_acceptance_files import output_paths, report_hashes, source_hashes
from scripts.sdd_acceptance_policy import digest as acceptance_digest
from scripts.sdd_acceptance_runtime import acceptance_status


def sdd_acceptance_outputs(repo: Path, work: dict) -> set[str]:
    record = (work.get("sdd") or {}).get("acceptance") or {}
    declaration = record.get("declaration")
    if not declaration:
        return set()
    previous = dict(record.get("owned_outputs") or {})
    run = record.get("run") or {}
    if run.get("status") == "running":
        # The run validated absence/ownership before starting. It owns only its
        # frozen exact output paths, never a directory or a newly changed declaration.
        for value in declaration.get("outputs", []):
            path = repo_file(repo, value)
            if path.is_file():
                previous[value] = sha256_file(path)
    return output_paths(repo, declaration, provider_output_dirs(work["sdd"]["provider"], repo), work["id"], previous)


def sdd_acceptance_inputs(repo: Path, work: dict, cfg: dict) -> dict:
    record = (work.get("sdd") or {}).get("acceptance") or {}
    if not record.get("declaration"):
        raise ValueError("SDD_ACCEPTANCE_NOT_CONFIGURED")
    from scripts.acceptance_inputs import task_inputs
    inputs = task_inputs(repo, work, cfg) if work.get("project_binding") else verification_inputs(repo, work, cfg)
    return {**inputs, "work_id": work["id"], "provider": work["sdd"]["provider"],
            "declaration_sha256": record["declaration_sha256"],
            "source_hashes": source_hashes(repo, record["declaration"], provider_output_dirs(work["sdd"]["provider"], repo))}


def sdd_acceptance_current_hashes(repo: Path, work: dict) -> dict:
    record = (work.get("sdd") or {}).get("acceptance") or {}
    run = record.get("run") or {}
    if run.get("status") != "passed":
        return {}
    return report_hashes(repo, run.get("results") or [], record["declaration"]["outputs"])


def sdd_acceptance_state(repo: Path, work: dict) -> dict:
    profile = (work.get("path_assessment") or {}).get("profile")
    if profile == "light" and not work.get("project_binding"):
        return {"ready": True, "state": "not_applicable", "reason": None}
    if work.get("status") == "completed":
        run = ((work.get("sdd") or {}).get("acceptance") or {}).get("run") or {}
        status = run.get("status", "unconfigured")
        return {"ready": status == "passed", "state": status, "historical": True,
                "run_id": run.get("id"), "reason": run.get("reason"),
                "note": "Frozen result at completion; not certification of current repository inputs."}
    try:
        if not ((work.get("sdd") or {}).get("acceptance") or {}).get("declaration"):
            state = {"ready": False, "state": "unconfigured", "reason": "SDD acceptance is not configured"}
        else:
            inputs = sdd_acceptance_inputs(repo, work, config(repo))
            state = acceptance_status(work, inputs, sdd_acceptance_current_hashes(repo, work))
    except (ValueError, OSError, RuntimeError) as exc:
        state = {"ready": False, "state": "blocked", "reason": "SDD acceptance: " + str(exc)}
    if not state["ready"]:
        record = (work.get("sdd") or {}).get("acceptance") or {}
        if not record.get("declaration"):
            state["next"] = 'Configure native acceptance: `cao sdd acceptance configure --declaration <repo-json>`; review sources and explicitly `cao reopen --reanchor --reason "Reviewed SDD scope"`, then `cao verify`.'
        elif not record.get("anchor_sha256") or "reanchor" in str(state.get("reason")):
            state["next"] = 'Review sources/standard, then `cao reopen --reanchor --reason "Reviewed SDD scope"` and rerun `cao verify`.'
        else:
            mode = record["declaration"]["executor"]["mode"]
            state["next"] = ('Run `cao verify`, then `cao sdd acceptance run`.' if mode == "command" else
                             'Run `cao verify`, then `cao sdd acceptance begin`; follow the installed native Skill and submit the current report with `cao sdd acceptance submit --run <id> --report <repo-json>`. Renderer success is not acceptance.')
    return state


def verification_inputs(repo: Path, work: dict, cfg: dict) -> dict:
    if work.get("project_binding"):
        from scripts.acceptance_inputs import technical_inputs
        return technical_inputs(repo, work, cfg)
    record = (work.get("sdd") or {}).get("acceptance") or {}
    native_acceptance = bool(record.get("declaration") and (work.get("path_assessment") or {}).get("profile") != "light")
    excluded = {work["requirement"]["path"]}
    if native_acceptance:
        excluded |= sdd_acceptance_outputs(repo, work)
    inputs = {"snapshot_format": SNAPSHOT_FORMAT, "requirement_sha256": sha256_file(repo / work["requirement"]["path"]),
            "code_snapshot": code_snapshot(repo, excluded),
            "rules_snapshot": rules_snapshot(repo, cfg),
            "verification_config_sha256": verification_snapshot(cfg, repo),
            "assessment_revision": (work.get("path_assessment") or {}).get("revision")}
    binding = (work.get("sdd") or {}).get("spec_binding")
    if binding:
        inputs["rules_snapshot"] = binding_digest({"rules": inputs["rules_snapshot"], "spec_binding": binding})
    if (work.get("sdd") or {}).get("route_decision"):
        inputs["rules_snapshot"] = binding_digest({"rules": inputs["rules_snapshot"],
            "route": work["sdd"]["route_decision"], "approval": work["sdd"].get("spec_approval"),
            "route_state": native_route_state(repo, work)})
    plan_state = provider_verification_plan_state(repo, work)
    if plan_state["state"] != "not_applicable":
        inputs["rules_snapshot"] = binding_digest({"rules": inputs["rules_snapshot"], "native_plan": plan_state})
    if native_acceptance:
        sources = source_hashes(repo, record["declaration"], provider_output_dirs(work["sdd"]["provider"], repo))
        inputs["rules_snapshot"] = acceptance_digest({"rules": inputs["rules_snapshot"],
                                                      "declaration": record["declaration_sha256"], "sources": sources})
    return inputs


def legacy_completion_status(repo: Path, work: dict[str, Any] | None) -> dict[str, Any]:
    from scripts.cao_tool_policy import verification_baseline_state

    restart = restart_state(repo, detect_current_host())
    if not restart["ready"]:
        return restart
    if not work:
        return {"ready": False, "reason": "no work item"}
    if work.get("status") != "active":
        return {"ready": False, "reason": f"work status is {work.get('status')}"}
    if work.get("blockers"):
        return {"ready": False, "reason": "active blocker exists"}
    plan_state = provider_verification_plan_state(repo, work)
    if not plan_state["ready"]:
        return plan_state
    req = repo / work["requirement"]["path"]
    if not req.exists():
        return {"ready": False, "reason": "requirement file missing"}
    req_sha = sha256_file(req)
    if req_sha != work["requirement"]["sha256"]:
        return {"ready": False, "reason": "requirement changed since work started"}
    path_state = path_assessment_state(repo, work)
    if not path_state["ready"]:
        return {"ready": False, "reason": path_state["reason"]}
    cfg = config(repo)
    baseline = verification_baseline_state(repo, work)
    if not baseline["ready"]:
        return {**baseline, "reason_code": baseline["reason"]}
    route = native_route_state(repo, work)
    if not route["ready"]:
        return route
    if (work.get("verification") or {}).get("status") == "running":
        return {"ready": False, "reason": "VERIFICATION_RUNNING", "next": "Wait for the current verification, or rerun `cao verify` to supersede an abandoned run."}
    current = verification_inputs(repo, work, cfg)
    code_sha = current["code_snapshot"]
    rules_sha = current["rules_snapshot"]
    verification_cfg_sha = current["verification_config_sha256"]
    fresh = bound_verification_fresh(work, current)

    # Light binds minimal native intent; full profiles require execution/plan evidence.
    sdd = work.get("sdd") or {}
    assessment = work.get("path_assessment") or {}
    provider = str(sdd.get("provider") or "none")
    profile = str(assessment.get("profile") or "light")
    if profile == "light":
        spec_state = light_spec_state(repo, work, provider_output_dirs(provider, repo))
        if not spec_state["ready"]:
            return {"ready": False, "reason": spec_state["reason"], "next": spec_state.get("next"), "light_spec": spec_state}
    review = native_review_state(repo, work, code_snapshot)
    if not review["ready"]:
        return review
    planning = planning_state(repo, work)
    if not planning["ready"]:
        return planning
    if provider in SDD_CATALOG and profile in {"standard", "comprehensive"}:
        workflow = sdd.get("workflow") or {}
        if workflow.get("status") == "completed":
            ownership = validate_workflow_artifacts(repo, provider, workflow.get("artifacts") or [])
            if not ownership["ok"]:
                return {"ready": False, "reason": "native workflow artifacts are outside provider output directories: " + ", ".join(ownership["offending"]),
                        "next": "Rerun the native workflow and complete it with files in its declared output directories."}
        workflow_ok, workflow_reason = workflow_ready(work, repo)
        if not workflow_ok:
            return {
                "ready": False,
                "reason": workflow_reason,
                "workflow": workflow or None,
                "provider": provider,
                "profile": profile,
                "next": f"Run the provider-native workflow, then record it: `cao sdd workflow select --name \"<native workflow>\" --entrypoint \"<native command or skill>\" --work {work['id']}` → `cao sdd workflow execute --command \"<command actually run>\" --work {work['id']}` → `cao sdd workflow complete --artifact <repo-relative-file> --work {work['id']}`.",
            }
        if not str(workflow.get("name") or "").strip() or not str(workflow.get("entrypoint") or "").strip():
            return {"ready": False, "reason": "native SDD workflow record is incomplete", "workflow": workflow}
        # Running the workflow once is not the same as finishing the plan it
        # produced. A single Work Item can span many stories/specs, and without
        # this check an Agent closes the requirement after the first one.
        try:
            pending_items = provider_pending_plan_items(repo, work)
        except ValueError as exc:
            return {"ready": False, "reason": str(exc),
                    "next": f"Bind this requirement's plans with `cao sdd workflow complete --work {work['id']} --artifact <output> --plan <tasks.md>` or `--plan-item <sprint-status.yaml>#<story-id>`; use `--no-plan` only when this workflow produces no plan."}
        if pending_items:
            shown = ", ".join(pending_items[:5])
            more = f" (+{len(pending_items) - 5} more)" if len(pending_items) > 5 else ""
            return {
                "ready": False,
                "reason": f"the provider's plan still has {len(pending_items)} unfinished item(s)",
                "pending_plan_items": pending_items,
                "provider": provider,
                "next": f"Finish the remaining items in the provider's own plan ({shown}{more}), or get the user to confirm the reduced scope, then run `cao verify` and `cao done` again.",
            }

    sdd_state = sdd_acceptance_state(repo, work)
    gates = gate_state(work, fresh, sdd_state["ready"], repo=repo)
    if gates["pending"]:
        manual = [gate for gate in gates["pending"] if not gate_is_auto_derived(gate)]
        if gates["pending"] == ["sdd_acceptance"]:
            return {"ready": False, "reason": sdd_state["reason"], "next": sdd_state.get("next"), "sdd_acceptance": sdd_state, "gates": gates}
        return {
            "ready": False,
            "reason": "required execution gates are not satisfied",
            "pending_gates": gates["pending"],
            "gates": gates,
            "next": (
                "Inspect `cao conformance show` and reuse native evidence; do not repeat code review. Record only remaining observed obligations with `cao gate satisfy --gate " + "|".join(manual) + " --evidence \"<what was done>\"`."
                if manual else "Run `cao verify` so the verification gates become fresh."
            ),
        }

    if profile in {"standard", "comprehensive"} and not sdd_state["ready"]:
        return {"ready": False, "reason": sdd_state["reason"], "next": sdd_state.get("next"), "sdd_acceptance": sdd_state}
    if fresh:
        from scripts.native_completion import state as native_completion_state
        terminal = native_completion_state(repo, work)
        if not terminal['ready']:
            return terminal
        commit = native_commit_state(repo, work, SNAPSHOT_IGNORE_DIRS | {".agents", ".pi", "_bmad", "_bmad-output"})
        if not commit["ready"]:
            return commit
    return {
        "ready": fresh,
        "reason": None if fresh else "verification is missing or stale",
        "gates": gates,
        "sdd_acceptance": sdd_state,
        "current_code_snapshot": code_sha,
        "current_rules_snapshot": rules_sha,
        "current_verification_config_sha256": verification_cfg_sha,
    }


def work_sdd_ready(repo: Path, work: dict[str, Any] | None) -> tuple[bool, dict[str, Any] | None]:
    if not work:
        return False, None
    provider = str(((work.get("sdd") or {}).get("provider") or "auto"))
    if provider not in SDD_CATALOG:
        return True, None
    detected = detect_sdd(repo, provider)
    return bool(detected["installed"]), detected


def sdd_transition(repo: Path, target: str) -> dict[str, Any] | None:
    return run_sdd_command(repo, "transition", [target])


def completion_next_instruction(repo: Path, work: dict[str, Any] | None, completion: dict[str, Any]) -> str:
    """Return a concrete, executable next step for a blocked completion."""
    reason = str(completion.get("reason") or "")
    if completion.get("next"):
        return completion["next"]
    if not work:
        return "Start a work item first: `cao start --requirement <path>`."
    wid = work["id"]
    if reason == "active blocker exists":
        return f"Resolve the active blocker, then run `cao unblock --work {wid}`."
    if reason == "requirement file missing":
        return f"Restore the requirement file `{work['requirement']['path']}`, then run `cao verify`."
    if reason == "requirement changed since work started":
        path = work["requirement"]["path"]
        return (
            f"The requirement changed after work started. Review and confirm the new requirement; "
            f"if the change is intentional, re-anchor the work item with `cao start --requirement {path} --replace`; "
            f"otherwise restore the previous requirement and run `cao verify`."
        )
    if reason in {"execution path has not been assessed", "execution path assessment is stale"}:
        return f"Review the requirement and current scope, then record or refresh the minimum-safe-path assessment: `cao path assess --profile <light|standard|comprehensive> --reason \"<short reason>\" --work {wid}`."
    if reason == "major requirement ambiguity requires clarification":
        return "Ask the user to resolve the material requirement ambiguity, then rerun `cao path assess` without `--risk-flag major-ambiguity`. Do not implement while the ambiguity remains unresolved."
    if reason.startswith("the provider's plan still has"):
        items = completion.get("pending_plan_items") or []
        listed = "; ".join(str(item) for item in items[:5])
        more = f"; and {len(items) - 5} more" if len(items) > 5 else ""
        return (
            f"Run the native workflow for each remaining item in the provider's own plan ({listed}{more}). "
            f"Closing now would mark the whole requirement complete while that work is still outstanding. "
            f"If the user has actually reduced the scope, update the plan in the provider's own files first, then run `cao verify` and `cao done`."
        )
    if reason == "native SDD workflow is not recorded as completed":
        return (f"Select and execute the native {((work.get('sdd') or {}).get('provider') or 'SDD')} workflow before completion. "
                f"Record it with `cao sdd workflow select --name \"<workflow name>\" --entrypoint \"<native workflow command or skill>\" --work {wid}`, "
                f"then after actually running it record its real output files with `cao sdd workflow complete --work {wid} --artifact <repo-relative-file>`. Do not mark it complete before execution.")
    if reason == "native SDD workflow record is incomplete":
        return f"Record the actual native workflow name and entrypoint with `cao sdd workflow select --name \"<workflow name>\" --entrypoint \"<native workflow command or skill>\" --work {wid}`, then execute it and complete the record."
    if reason == "native SDD workflow has no recorded output artifacts":
        return f"Record the actual files produced by the selected native workflow using `cao sdd workflow complete --work {wid} --artifact <repo-relative-file>`; if the workflow legitimately produces no files, reassess the profile or document the provider-native exception explicitly rather than bypassing the gate."
    if reason == "native SDD workflow output artifacts are missing":
        missing = ", ".join(completion.get("missing_artifacts") or [])
        return f"The native SDD workflow references missing output files ({missing}). Execute/fix the workflow and record existing files with `cao sdd workflow complete --work {wid} --artifact <repo-relative-file>`."
    if reason == "verification is missing or stale":
        return "Run `cao verify` after the requirement, code, rules, and verification configuration are stable."
    if reason.startswith("work status is "):
        status = reason.removeprefix("work status is ")
        if status in {"suspended", "todo"}:
            return f"Resume the work item with `cao resume --work {wid} --reactivate`, then continue from its recorded next action."
        if status == "completed":
            return "The work item is already completed; no further action is required."
        return f"Make the work item active before closing it: `cao resume --work {wid} --reactivate`."
    return "Resolve the blocking condition shown above, then run `cao verify` and retry `cao done`."


def lifecycle_status(repo, work):
    """Collect independent closing obligations without short-circuiting acceptance."""
    states = []
    def check(name, fn):
        try:
            value = fn()
        except (ValueError, OSError, RuntimeError) as exc:
            value = {'ready': False, 'reason': str(exc)}
        states.append({'obligation': name, **value})
    if not work:
        return {'ready': False, 'reason': 'NO_WORK_ITEM', 'obligations': []}
    check('restart', lambda: restart_state(repo, detect_current_host()))
    check('active', lambda: {'ready': work.get('status') == 'active', 'reason': 'Work must be active'})
    check('blockers', lambda: {'ready': not work.get('blockers'), 'reason': 'active blocker exists'})
    check('requirement', lambda: {'ready': sha256_file(repo / work['requirement']['path']) == work['requirement']['sha256'], 'reason': 'requirement changed since work started'})
    check('assessment', lambda: path_assessment_state(repo, work))
    from scripts.cao_tool_policy import verification_baseline_state
    check('baseline', lambda: verification_baseline_state(repo, work))
    check('native_route', lambda: native_route_state(repo, work))
    check('plan_scope', lambda: provider_verification_plan_state(repo, work))
    check('native_planning', lambda: planning_state(repo, work))
    check('native_review', lambda: native_review_state(repo, work, code_snapshot))
    profile = (work.get('path_assessment') or {}).get('profile')
    if profile == 'light':
        check('light_spec', lambda: light_spec_state(repo, work, provider_output_dirs(work['sdd']['provider'], repo)))
    else:
        def native_workflow():
            ready, reason = workflow_ready(work, repo)
            if ready:
                ownership = validate_workflow_artifacts(repo, work['sdd']['provider'], (work['sdd'].get('workflow') or {}).get('artifacts') or [])
                if not ownership['ok']:
                    return {'ready': False, 'reason': 'native workflow artifact ownership invalid', 'offending': ownership['offending']}
            return {'ready': ready, 'reason': reason}
        check('workflow', native_workflow)
        def pending_plan():
            pending = provider_pending_plan_items(repo, work)
            return {'ready': not pending, 'reason': 'native plan has unfinished items', 'pending_plan_items': pending}
        check('native_plan_complete', pending_plan)
    def manual_gates():
        gates = gate_state(work, True, True, repo=repo)
        return {'ready': not gates['pending'], 'reason': 'required execution gates are not satisfied', 'pending_gates': gates['pending']}
    if work.get('project_change'):
        from scripts.project_context import load_context
        from scripts.project_inspection import drift
        def target_matches():
            differences = drift(repo, load_context(repo), work['project_binding']['scopes'], work, allow_migration=False)
            return {'ready': not differences, 'reason': 'PROJECT_MIGRATION_TARGET_MISMATCH', 'differences': differences}
        check('migration_target', target_matches)
    check('manual_gates', manual_gates)
    from scripts.native_completion import state as native_completion_state
    check('native_finalize', lambda: native_completion_state(repo, work))
    check('native_commit', lambda: native_commit_state(repo, work, SNAPSHOT_IGNORE_DIRS | {'.agents', '.pi', '_bmad', '_bmad-output'}))
    blocked = [state for state in states if not state['ready']]
    return {'ready': not blocked, 'reason': blocked[0].get('reason') if blocked else None,
            'obligations': states, 'blockers': blocked}


def completion_status(repo, work):
    if not work or (work.get('status') == 'completed' and not work.get('project_binding')):
        return legacy_completion_status(repo, work)
    from scripts.acceptance_summary import summarize
    lifecycle = lifecycle_status(repo, work)
    result = summarize(repo, work, lifecycle)
    result['lifecycle'] = lifecycle
    from scripts.conformance import summarize as conformance_summary
    result['conformance'] = conformance_summary(repo, work, lifecycle)
    if not result['conformance']['ready']:
        result['ready'] = False
        missing = result['conformance']['blockers'][0]
        result['reason'] = missing.get('reason_code') or 'CONFORMANCE_NOT_READY'
        result['next'] = {'type': 'agent', 'instruction': missing.get('repair_action') or 'Restore the nonempty declared artifact; run `cao conformance check`.'}
        result['blockers'] = list(dict.fromkeys([*result['blockers'], 'CONFORMANCE_NOT_READY']))
    legacy = legacy_completion_status(repo, work)
    if not legacy['ready']:
        result['reason'] = legacy.get('reason') or result['reason']
        result['next'] = {'type': 'agent', 'instruction': legacy.get('next') or completion_next_instruction(repo, work, legacy)}
    for key in ('gates', 'pending_plan_items', 'pending_gates', 'missing_artifacts', 'workflow'):
        if key in legacy:
            result[key] = legacy[key]
    # Existing callers consume an instruction string in completion.next.
    result['next_action'] = result['next']
    result['next'] = result['next_action']['instruction']
    return result
