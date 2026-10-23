"""Agent actions, prerequisites, resume payloads, and rendered context."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.cao_completion import completion_next_instruction, completion_status, work_sdd_ready
from scripts.cao_instructions import cao_invocation, package_root_dir
from scripts.cao_native_sync import native_route_state, native_sync_state, route_preparation_action
from scripts.cao_path import path_assessment_instruction, path_assessment_state
from scripts.cao_provider_workflows import provider_native_skills, workflow_skill_identity
from scripts.cao_providers import detect_current_host, sdd_status
from scripts.cao_runtime import action, config
from scripts.cao_work_store import execution_phase, work_summary
from scripts.native_obligations import dispatch as dispatch_review
from scripts.provider_restart import restart_state
from scripts.skill_guidance import CONTRACT_FILES, action_files, guidance as resolve_skill_guidance
from scripts.startup_request import ORDER as STARTUP_ORDER, request_context
from scripts.technology_guidance import technology_instruction
from scripts.verification_setup import setup_state, verification_setup_context


def resume_payload(repo: Path, work: dict[str, Any], session_id: str | None = None, host: str | None = None, *, next_action: dict | None = None) -> dict[str, Any]:
    from scripts.cao_sessions import latest_handoff
    from scripts.project_context import context_state

    c = completion_status(repo, work)
    current = next_action or current_work_action(repo, work, completion=c)
    summary = work_summary(work)
    summary["next_action"] = current
    handoff = latest_handoff(repo, work["id"]) or {}
    return {
        "work_item": summary,
        "project_context": context_state(repo, work=work),
        "project_binding": work.get("project_binding"),
        "requirement": work.get("requirement"),
        "execution": {**(work.get("execution") or {}), "next_action": current},
        "path_assessment": work.get("path_assessment"),
        "path_assessment_state": path_assessment_state(repo, work),
        "native_sync": native_sync_state(repo, work),
        "resume": work.get("resume"),
        "decisions": work.get("decisions") or [],
        "blockers": work.get("blockers") or [],
        "verification": work.get("verification"),
        "completion": c,
        "handoff": handoff or None,
        "session": {"id": session_id, "host": host} if session_id else None,
    }


def work_intake_instruction(work: dict[str, Any] | None = None) -> str:
    command = cao_invocation()["command"]
    if work and work.get("status") in {"todo", "suspended"}:
        return f'Resume the existing Work before product writes with `{command} resume --work {work["id"]} --reactivate`; do not create a replacement requirement.'
    return (
        "Create an active Work before product code, configuration, tests or verification scaffolding. "
        "Honor bootstrap's SDD authorization/install instructions first. "
        f"Inspect `{command} list` and resume existing suspended/todo work instead of repeating intake. "
        + technology_instruction(command) +
        f'For an inline requirement run `{command} requirement next-id --title "<short title>"`, '
        "fill the returned numbered file with the user's confirmed requirement, "
        f"then run `{command} start --requirement <returned-path>`. "
        "If a requirement file already exists, use it directly; do not invent another requirement. "
        "Verification need not pass before start; the project must have actual entries or a declared plan. After start, rerun bootstrap "
        "for verification setup and follow `next` for path assessment and the native SDD workflow. "
        "CAO commands, numbered requirement documents and native SDD output writes remain allowed; "
        "direct edits to CAO-derived state remain denied."
    )


def work_prerequisite_action(repo: Path, work: dict | None) -> dict | None:
    """One prerequisite order for CLI navigation, synchronization and host reminders."""
    from scripts.cao_tool_policy import verification_baseline_state, verification_capability_state

    command = cao_invocation()["command"]
    restart = restart_state(repo, detect_current_host())
    if not restart["ready"]:
        return action("human", restart["next"], stage="startup", reason=restart["reason"])
    if work and work.get("status") == "completed":
        return action("none", "Work item is completed.", stage="completed")
    if not work:
        status = sdd_status(repo)
        startup = not status["installed"] or status["selected"] == "auto" and len(status["installed"]) > 1
        if not startup:
            from scripts.project_verification import project_prerequisite
            project = project_prerequisite(repo)
            if project:
                return project
        return action("agent", f"Run `{command} bootstrap` and resolve SDD selection/installation first." if startup else work_intake_instruction(),
                      stage="startup" if startup else "intake", reason="SDD_SELECTION_REQUIRED" if startup else "WORK_REQUIRED")
    if work.get("status") == "completed":
        return action("none", "Work item is completed.", stage="completed")
    if not work_sdd_ready(repo, work)[0]:
        return action("human", f"Restore the Work's selected SDD, then run `{command} bootstrap`; do not restart intake.", stage="startup", reason="SDD_NOT_INSTALLED")
    from scripts.project_verification import project_prerequisite
    project = project_prerequisite(repo, work)
    if project:
        return project
    if work.get("status") in {"todo", "suspended"}:
        return action("agent", f"Resume this work item with `{command} resume --work {work['id']} --reactivate`.", stage="intake", reason="WORK_NOT_ACTIVE")
    if work.get("blockers"):
        return action("human", "Resolve the active blocker before continuing.", stage="recovery", reason="active blocker exists")
    assessment = path_assessment_state(repo, work)
    if assessment.get("reason") in {"REQUIREMENT_CONTENT_REQUIRED", "PATH_RISK_REASSESSMENT_REQUIRED"}:
        return action("agent", assessment["next"], stage="path_assessment", reason=assessment["reason"],
                      diagnostics=assessment.get("diagnostics", []))
    baseline = verification_baseline_state(repo, work)
    if not baseline["ready"]:
        return action("human" if baseline['reason'] == 'VERIFICATION_REVIEW_REQUIRED' else "agent",
                      baseline["next"], stage="verification_setup", reason=baseline["reason"])
    if not assessment["ready"]:
        reason = assessment["reason"]
        if reason in {"requirement changed since work started", "requirement file missing"}:
            return action("human", completion_next_instruction(repo, work, assessment), stage="recovery", reason=reason)
        if reason == "major requirement ambiguity requires clarification":
            return action("human", "Clarify the material requirement ambiguity, then rerun `cao path assess` without `--risk-flag major-ambiguity`.", stage="path_assessment", reason=reason)
        return action("agent", path_assessment_instruction(work), stage="path_assessment", reason=reason)
    capabilities = verification_capability_state(repo, work)
    if not capabilities['ready']:
        return action('agent', capabilities['next'], stage='verification_setup', reason=capabilities['reason'])
    return None


def current_work_action(repo: Path, work: dict | None, *, check_completion: bool = True, completion: dict | None = None) -> dict:
    prerequisite = work_prerequisite_action(repo, work)
    if prerequisite:
        return prerequisite
    route = native_route_state(repo, work)
    if not route["ready"]:
        return route_preparation_action(repo, work, route)
    phase = execution_phase(work.get("execution") or {})
    stage = "completion" if phase in {"review", "verification", "done"} else "execution"
    verification = (work.get("verification") or {}).get("status")
    if verification == "running":
        return action("agent", "Wait for the current verification, or supersede an abandoned run with `cao verify` after managing its process.", stage="recovery", reason="VERIFICATION_RUNNING")
    if verification == "failed":
        return action("agent", "Fix the verification failure, then run `cao verify`.", stage="recovery", reason="VERIFICATION_FAILED")
    if check_completion:
        completion = completion if completion is not None else completion_status(repo, work)
        if completion["ready"]:
            return action("agent", "Run `cao done` to close the work item.", stage="completion")
        if stage == "completion" and completion.get("next"):
            return action("agent", completion["next"], stage=stage, reason=completion["reason"])
    configured = dict((work.get("execution") or {}).get("next_action") or
                      action("agent", "Continue the current native workflow and record a checkpoint."))
    configured.setdefault("stage", stage)
    return configured


def agent_guidance(repo: Path, work: dict[str, Any] | None, mode: str | None = None, *, next_action: dict | None = None) -> dict[str, Any]:
    """Route to stage documents using existing state predicates, never new gates."""
    provider = str(((work or {}).get("sdd") or {}).get("provider") or "")
    if mode in {"sdd_select", "sdd_install_required", "language_configuration_failed", "host_restart_required"} or not restart_state(repo, detect_current_host())["ready"]:
        stage = "startup"
        next_action = None
    elif mode == "verification_setup":
        stage = "verification_setup"
        next_action = None
    else:
        next_action = next_action or current_work_action(repo, work, check_completion=False)
        stage = next_action.get("stage", "execution")
    root = package_root_dir()
    navigation = resolve_skill_guidance(root, cao_invocation()["command"], stage, provider)
    reason = (next_action or {}).get("reason", "")
    contracts = action_files(reason, work)
    sdd = (work or {}).get('sdd') or {}
    if stage in {'execution', 'completion'} and sdd.get('provider') == 'bmad' and sdd.get('route_decision'):
        review_contract = CONTRACT_FILES['bmad_dispatch_review' if dispatch_review(work) else 'bmad_oneshot_review']
        if review_contract not in contracts:
            contracts.append(review_contract)
    for name in contracts:
        if root:
            navigation["read_before_action"].append(str((root / name).resolve()))
        if root is None or not (root / name).is_file():
            navigation["missing_resources"].append(name)
    skill_acceptance = CONTRACT_FILES["acceptance_skill"] in contracts
    if reason.startswith("NATIVE_") or skill_acceptance:
        sdd = (work or {}).get("sdd") or {}
        if skill_acceptance:
            executor = (((sdd.get("acceptance") or {}).get("declaration") or {}).get("executor") or {})
            entrypoint = executor.get("entrypoint")
        else:
            entrypoint = (sdd.get("workflow") or {}).get("entrypoint")
        selected = workflow_skill_identity(repo, provider, entrypoint or "")
        for skill in provider_native_skills(repo, provider):
            if skill["name"] == (selected or ("bmad-build" if not skill_acceptance else None)):
                navigation["read_before_action"].append(str((repo / skill["rel_path"]).resolve()))
    navigation["ready"] = not navigation["missing_resources"]
    if not navigation["ready"]:
        navigation["instruction"] = "Repair the CAO installation: required guidance resources are missing. Do not bypass workflow gates."
    return navigation


def runtime_action_context(repo: Path, work: dict | None, current: dict) -> str:
    return (f"{current.get('reason') or 'CURRENT_ACTION'}: {current['instruction']}\n"
            "Read before acting: " + json.dumps(agent_guidance(repo, work, next_action=current), ensure_ascii=False))


def render_context(repo: Path, work: dict[str, Any] | None, *, session_id: str | None = None, host: str | None = None) -> str:
    from scripts.cao_tool_policy import verification_baseline_state

    restart = restart_state(repo, host or detect_current_host())
    if not restart["ready"]:
        return restart["reason"] + ": " + restart["next"]
    from scripts.project_context import context_state
    inspection = setup_state(config(repo))
    current = current_work_action(repo, work)
    project = context_state(repo, work=work) if current.get("stage") != "startup" else {}
    navigation = "## Read before acting\n" + json.dumps(agent_guidance(repo, work, next_action=current), ensure_ascii=False)
    if not work or work.get("status") in {"todo", "suspended", "completed"}:
        instruction = STARTUP_ORDER if current.get('stage') == 'startup' else current['instruction']
        return "# Coding Agent Orchestrator Work Intake\n" + navigation + "\n" + instruction + "\nProject background: " + json.dumps(project, ensure_ascii=False) + request_context(repo)
    if current.get("stage") == "project_setup":
        return "# CAO Project Setup\n" + navigation + "\n" + current["instruction"] + "\n" + json.dumps(current.get("project_state", {}), ensure_ascii=False) + request_context(repo)
    baseline = verification_baseline_state(repo, work)
    if not baseline["ready"]:
        # Preserve continuity without calculating completion from invalid declarations.
        return "\n".join([
            "# Coding Agent Orchestrator Verification Setup",
            navigation,
            baseline["next"],
            json.dumps({"verification_setup": inspection, "project_context": project, "work": verification_setup_context(work),
                        "session_id": session_id, "host": host}, ensure_ascii=False),
        ])
    p = resume_payload(repo, work, session_id=session_id, host=host, next_action=current)
    e = p["execution"] or {}
    lines = [
        "# Coding Agent Orchestrator Resume Context",
        navigation,
        f"Work Item: {p['work_item']['title']} ({p['work_item']['id']})",
        "Project background: " + json.dumps(p["project_context"], ensure_ascii=False),
        f"Status: {p['work_item']['status']}",
        f"Requirement: {p['requirement']['path']}",
        f"SDD: {(p.get('work_item', {}).get('sdd') or {}).get('provider', 'none')}",
        f"Step: {e.get('step') or 'not recorded'}",
        "",
        "## Minimum-Safe Execution Path",
        json.dumps(p.get("path_assessment") or {"status": "required", "next": path_assessment_instruction(work)}, ensure_ascii=False),
        f"Path assessment ready: {p['path_assessment_state'].get('ready')}",
        "",
        "## Completed / Current",
        str((p.get("resume") or {}).get("summary") or "No handoff summary recorded."),
        "",
        "## Important Decisions",
    ]
    decisions = p.get("decisions") or []
    lines.extend([f"- {d.get('decision')}" + (f" | reason: {d.get('reason')}" if d.get("reason") else "") for d in decisions[-8:]] or ["- None recorded"])
    lines.append("")
    lines.append("## Blockers")
    blockers = p.get("blockers") or []
    lines.extend([f"- {b.get('reason')}" + (f" | resolution: {b.get('resolution')}" if b.get("resolution") else "") for b in blockers] or ["- None"])
    lines.extend([
        "",
        "## Next Action",
        json.dumps(current, ensure_ascii=False),
        "",
        "## Verification",
        json.dumps(p.get("verification") or {"status": "pending"}, ensure_ascii=False),
        f"Completion ready: {p['completion'].get('ready')}",
    ])
    from scripts.project_knowledge import context_excerpt
    lines.extend(["", "## Relevant project knowledge (context only; follow original evidence)", context_excerpt(repo, work)])
    return "\n".join(lines)
