"""Execution profile assessment, risk minimums, and gate evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.cao_defaults import (
    AUTO_DERIVED_GATES,
    PATH_RISK_GATES,
    PATH_RISK_MINIMUM,
    PROFILE_BASE_GATES,
    PROFILE_RANK,
    SDD_CATALOG,
)
from scripts.cao_instructions import cao_invocation
from scripts.cao_runtime import (
    action,
    config,
    ensure_initialized,
    locked_command,
    now,
    repo_root,
    sha256_file,
)
from scripts.cao_work_store import load_index, load_work, save_work, work_summary
from scripts.completion_policy import (
    capability_satisfied,
    evidence_matches,
    tests_satisfied,
    verification_fresh as bound_verification_fresh,
)
from scripts.technology_guidance import technology_instruction
from scripts.requirement_content import security_evidence, template_diagnostics


def gate_is_auto_derived(name: str) -> bool:
    low = str(name).strip().lower()
    return low in AUTO_DERIVED_GATES or low.endswith("_verification")


def gate_state(work: dict[str, Any], verification_fresh: bool, sdd_ready: bool = False, *, repo: Path | None = None) -> dict[str, Any]:
    """Split the assessment's required gates into satisfied and pending ones.

    Verification gates require fresh configured evidence; tests additionally need
    successful required scripts declaring that capability. Native review/approval
    evidence satisfies corresponding review gates once. Remaining manual gates
    record actual process evidence, never a duplicate CAO code review.
    """
    from scripts.lifecycle_contract import enabled, NATIVE_VERIFICATION_GATES
    derived = {}
    if repo is not None:
        from scripts.conformance import native_gate_evidence
        derived = native_gate_evidence(repo, work)
    assessment = work.get("path_assessment") or {}
    gates = [str(gate) for gate in (assessment.get("required_gates") or [])]
    status = work.get("gate_status") or {}
    satisfied: list[str] = []
    pending: list[str] = []
    for gate in gates:
        if gate == "sdd_acceptance":
            ok = bool(sdd_ready)
        elif enabled(work) and gate in NATIVE_VERIFICATION_GATES:
            ok = bool(sdd_ready)
        elif gate == "tests":
            ok = bool(verification_fresh and tests_satisfied(work.get("verification") or {}))
        elif gate == "security_verification":
            ok = bool(verification_fresh and capability_satisfied(work.get("verification") or {}, gate))
        elif gate_is_auto_derived(gate):
            ok = bool(verification_fresh)
        elif gate in derived:
            ok = True
        else:
            evidence = status.get(gate) or {}
            ok = bool(evidence.get("satisfied") and evidence_matches(work, evidence))
        (satisfied if ok else pending).append(gate)
    return {"gates": gates, "satisfied": satisfied, "pending": pending, "native_evidence": derived}


def normalize_path_risk_flag(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def path_guidance_for_provider(provider: str, profile: str) -> str:
    provider_meta = SDD_CATALOG.get(provider) or {}
    guidance = provider_meta.get("path_guidance") or {}
    return str(guidance.get(profile) or (
        "Use the selected SDD's smallest native workflow that satisfies the required gates. "
        "Do not create duplicate artifacts or bypass provider-native prerequisites."
    ))


def build_path_assessment(
    *,
    requested_profile: str,
    reason: str,
    risk_flags: list[str],
    provider: str,
    requirement_sha256: str,
    questions: list[str] | None = None,
    assessed_by: str = "agent",
) -> dict[str, Any]:
    """Validate an Agent's lightweight path assessment and enforce hard minimums."""
    if requested_profile not in PROFILE_RANK:
        raise ValueError(f"unsupported execution profile: {requested_profile}")
    if not reason.strip():
        raise ValueError("path assessment reason must not be empty")

    normalized: list[str] = []
    for flag in risk_flags:
        value = normalize_path_risk_flag(flag)
        if value not in PATH_RISK_MINIMUM:
            allowed = ", ".join(sorted(PATH_RISK_MINIMUM))
            raise ValueError(f"unknown path risk flag `{flag}`; expected one of: {allowed}")
        if value not in normalized:
            normalized.append(value)

    open_questions = [str(q).strip() for q in (questions or []) if str(q).strip()]
    # Open questions in this model are material questions that affect the
    # expected behavior. Never let an assessment record questions while
    # accidentally leaving the implementation gate open.
    if open_questions and "major_ambiguity" not in normalized:
        normalized.append("major_ambiguity")

    effective_profile = requested_profile
    escalation_reasons: list[str] = []
    for flag in normalized:
        minimum = PATH_RISK_MINIMUM[flag]
        if PROFILE_RANK[minimum] > PROFILE_RANK[effective_profile]:
            escalation_reasons.append(f"risk flag `{flag}` requires at least `{minimum}`")
            effective_profile = minimum

    gates = list(PROFILE_BASE_GATES[effective_profile])
    for flag in normalized:
        for gate in PATH_RISK_GATES.get(flag, []):
            if gate not in gates:
                gates.append(gate)

    return {
        "policy_version": 1,
        "requested_profile": requested_profile,
        "profile": effective_profile,
        "reason": reason.strip(),
        "risk_flags": normalized,
        "escalation_reasons": escalation_reasons,
        "required_gates": gates,
        "provider": provider,
        "provider_guidance": path_guidance_for_provider(provider, effective_profile),
        "open_questions": open_questions,
        "assessed_by": assessed_by.strip() or "agent",
        "assessed_at": now(),
        "requirement_sha256": requirement_sha256,
    }


def path_assessment_state(repo: Path, work: dict[str, Any]) -> dict[str, Any]:
    """Return freshness and blocking details for a Work Item's path assessment."""
    requirement_path = repo / str((work.get("requirement") or {}).get("path") or "")
    if requirement_path.is_file():
        diagnostics = template_diagnostics(requirement_path)
        if diagnostics:
            return {"ready": False, "reason": "REQUIREMENT_CONTENT_REQUIRED", "diagnostics": diagnostics,
                    "next": f"Fill the actual request in `{requirement_path}`, then review and reanchor the same Work with `cao start --requirement <same-path> --replace`. Do not restart intake or create another file."}
    assessment = work.get("path_assessment")
    if not isinstance(assessment, dict):
        return {"ready": False, "reason": "execution path has not been assessed", "assessment": None}
    if assessment.get("policy_version") != 1:
        return {"ready": False, "reason": "execution path assessment uses an unsupported policy version", "assessment": assessment}
    if not assessment.get("revision"):
        return {"ready": False, "reason": "legacy execution path assessment requires reassessment with `cao path assess`", "assessment": assessment}
    profile = assessment.get("profile")
    if profile not in PROFILE_RANK:
        return {"ready": False, "reason": "execution path assessment is invalid", "assessment": assessment}
    work_provider = str((work.get("sdd") or {}).get("provider") or "auto")
    if assessment.get("provider") != work_provider:
        return {"ready": False, "reason": "execution path assessment is stale because the SDD provider changed", "assessment": assessment}
    flags = assessment.get("risk_flags") or []
    if not isinstance(flags, list) or any(flag not in PATH_RISK_MINIMUM for flag in flags):
        return {"ready": False, "reason": "execution path assessment is invalid", "assessment": assessment}
    required_profile = max((PATH_RISK_MINIMUM[flag] for flag in flags), key=lambda item: PROFILE_RANK[item], default="light")
    if PROFILE_RANK[profile] < PROFILE_RANK[required_profile]:
        return {"ready": False, "reason": "execution path assessment violates a required risk minimum", "assessment": assessment}
    requirement = work.get("requirement") or {}
    req_path = repo / str(requirement.get("path") or "")
    if not req_path.is_file():
        return {"ready": False, "reason": "requirement file missing", "assessment": assessment}
    current_sha = sha256_file(req_path)
    if current_sha != requirement.get("sha256"):
        return {"ready": False, "reason": "requirement changed since work started", "assessment": assessment}
    if current_sha != assessment.get("requirement_sha256"):
        return {"ready": False, "reason": "execution path assessment is stale", "assessment": assessment}
    if "security" not in flags and security_evidence(req_path):
        return {"ready": False, "reason": "PATH_RISK_REASSESSMENT_REQUIRED", "assessment": assessment,
                "next": "The requirement explicitly describes authentication or credential handling. Rerun `cao path assess --profile comprehensive --risk-flag security --reason <reviewed-reason>` for this Work; preserve its progress and refresh affected evidence."}
    if "major_ambiguity" in flags or assessment.get("open_questions"):
        return {"ready": False, "reason": "major requirement ambiguity requires clarification", "assessment": assessment}
    return {"ready": True, "reason": None, "assessment": assessment}


def path_assessment_instruction(work: dict[str, Any] | None = None) -> str:
    wid = (work or {}).get("id")
    work_arg = f" --work {wid}" if wid else ""
    return (
        technology_instruction(cao_invocation()["command"]) +
        "Perform a lightweight scope/risk assessment, choose light, standard, or comprehensive, "
        "then record it with `cao path assess --profile <profile> --reason \"<short reason>\"" +
        " [--risk-flag <flag>]" + work_arg + "`. Do not build a separate evidence model or scorecard."
    )


@locked_command
def cmd_path_assess(args: argparse.Namespace) -> int:
    from scripts.cao_guidance import agent_guidance, current_work_action

    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2))
        return 2
    requirement = work.get("requirement") or {}
    req_path = repo / str(requirement.get("path") or "")
    if not req_path.is_file():
        print(json.dumps({"ok": False, "error": "REQUIREMENT_FILE_MISSING", "path": requirement.get("path")}, ensure_ascii=False, indent=2))
        return 2
    diagnostics = template_diagnostics(req_path)
    if diagnostics:
        print(json.dumps({"ok": False, "error": "REQUIREMENT_CONTENT_REQUIRED", "diagnostics": diagnostics,
                          "next": "Fill and review the existing requirement, then reanchor this same Work before reassessment."}, ensure_ascii=False))
        return 2
    current_sha = sha256_file(req_path)
    if current_sha != requirement.get("sha256"):
        print(json.dumps({
            "ok": False,
            "error": "REQUIREMENT_CHANGED",
            "reason": "Review the requirement change and re-anchor the Work Item before reassessing its execution path.",
            "next": f"Review `{requirement.get('path')}`; if intentional, run `cao start --requirement {requirement.get('path')} --replace`.",
        }, ensure_ascii=False, indent=2))
        return 2

    provider = str((work.get("sdd") or {}).get("provider") or "auto")
    detected = security_evidence(req_path)
    assessment = build_path_assessment(
        requested_profile=args.profile,
        reason=args.reason,
        risk_flags=list(dict.fromkeys([*(args.risk_flag or []), *(["security"] if detected else [])])),
        provider=provider,
        requirement_sha256=current_sha,
        questions=args.question or [],
        assessed_by=args.assessed_by,
    )
    if detected:
        assessment["detected_risks"] = {"security": {"source": requirement["path"], "evidence": detected}}
    assessment["revision"] = int((work.get("path_assessment") or {}).get("revision") or 0) + 1
    work["path_assessment"] = assessment
    execution = work.setdefault("execution", {})
    if "major_ambiguity" in assessment["risk_flags"]:
        execution["step"] = "clarification-required"
        questions = "; ".join(assessment["open_questions"])
        detail = f" Open questions: {questions}." if questions else ""
        execution["next_action"] = action(
            "human",
            "Clarify the material requirement ambiguity with the user, then rerun `cao path assess` without `--risk-flag major-ambiguity`. Do not implement until clarified." + detail,
            profile=assessment["profile"],
        )
    else:
        execution["step"] = "path-selected"
        gate_text = ", ".join(assessment["required_gates"])
        execution["next_action"] = action(
            "agent",
            f"Apply the {assessment['profile']} path using {provider}'s native workflow. {assessment['provider_guidance']} "
            + ((f'Bind a BMAD oneshot SPEC with `cao sdd spec bind --spec <native-spec.md> --work {work["id"]}` without criteria selectors. For ordinary SPECs select explicit criteria with --criteria/--criteria-section. Light does not require workflow/plan/independent acceptance registration. '
                if provider == "bmad" else f'Bind the native SPEC and explicit criteria with `cao sdd spec bind --spec <provider-spec.md> --criteria-section "<heading>" --work {work["id"]}`. Light does not require workflow/plan/independent acceptance registration. ')
               if assessment["profile"] == "light" else
               f"Before implementation, select and record the native workflow with `cao sdd workflow select --name \"<native workflow name>\" --entrypoint \"<native workflow command or skill>\" --work {work['id']}`. Then execute it and record the run with `cao sdd workflow execute --command \"<command actually run>\" --exit-code <code> --work {work['id']}`. Finally record its real output files with `cao sdd workflow complete --work {work['id']} --artifact <repo-relative-file>` (repeat --artifact for each produced native artifact). Outputs must live under the provider's own directory; product source files are not SDD artifacts. `cao done` blocks until this contract is satisfied. " if provider in SDD_CATALOG else "")
            + f"Required gates: {gate_text}. Reuse current native evidence via `cao conformance show`; do not repeat functional code review. Record only remaining observed process obligations with `cao gate satisfy --gate <gate> --evidence \"<what was done>\"`; verification and tests gates come from fresh configured scripts, not manual claims. `cao done` blocks until all are satisfied. Reassess only if the scope, risk, or requirement materially changes.",
            profile=assessment["profile"],
            provider=provider,
        )
    save_work(repo, work)
    current = current_work_action(repo, work)
    summary = work_summary(work)
    summary["next_action"] = current
    print(json.dumps({"ok": True, "work_item": summary, "path_assessment": assessment, "next_action": current,
                      "guidance": agent_guidance(repo, work, next_action=current)}, ensure_ascii=False, indent=2))
    return 0


def cmd_path_show(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({
        "ok": True,
        "work_item": work_summary(work),
        "path_assessment": work.get("path_assessment"),
        "state": path_assessment_state(repo, work),
    }, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_gate(args: argparse.Namespace) -> int:
    """Mark or inspect the execution gates that only the Agent can observe."""
    from scripts.cao_completion import sdd_acceptance_state, verification_inputs

    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2))
        return 2
    assessment = work.get("path_assessment")
    if not isinstance(assessment, dict):
        print(json.dumps({"ok": False, "error": "PATH_ASSESSMENT_REQUIRED", "next": path_assessment_instruction(work)}, ensure_ascii=False, indent=2))
        return 2
    if args.gate_command == "satisfy":
        if not assessment.get("revision"):
            print(json.dumps({"ok": False, "error": "PATH_ASSESSMENT_REQUIRED",
                              "next": f"Run `cao path assess --work {work['id']} --profile <profile> --reason \"<why>\"` to upgrade the legacy assessment."}, ensure_ascii=False)); return 2
        name = str(args.gate or "").strip()
        required = [str(gate) for gate in (assessment.get("required_gates") or [])]
        if name not in required:
            print(json.dumps({
                "ok": False,
                "error": "GATE_NOT_REQUIRED",
                "gate": name,
                "required_gates": required,
            }, ensure_ascii=False, indent=2))
            return 2
        if gate_is_auto_derived(name):
            print(json.dumps({
                "ok": False,
                "error": "GATE_IS_AUTO_DERIVED",
                "gate": name,
                "reason": ("This gate is derived from current independent acceptance; follow `cao sdd acceptance show`." if name == "sdd_acceptance" else "This gate is derived from verification freshness; run `cao verify` instead."),
            }, ensure_ascii=False, indent=2))
            return 2
        evidence = str(args.evidence or "").strip()
        if not evidence:
            print(json.dumps({"ok": False, "error": "GATE_EVIDENCE_REQUIRED", "reason": "Record what was actually done to satisfy the gate."}, ensure_ascii=False, indent=2))
            return 2
        work.setdefault("gate_status", {})[name] = {
            "satisfied": True,
            "evidence": evidence,
            "at": now(),
            "by": str(args.satisfied_by or "agent"),
            "requirement_sha256": work["requirement"]["sha256"],
            "assessment_revision": assessment.get("revision"),
        }
        save_work(repo, work)
        print(json.dumps({
            "ok": True,
            "work_item": work_summary(work),
            "gate": name,
            "gates": gate_state(work, False, repo=repo),
        }, ensure_ascii=False, indent=2))
        return 0
    fresh = bound_verification_fresh(work, verification_inputs(repo, work, config(repo)))
    print(json.dumps({
        "ok": True,
        "work_item": work_summary(work),
        "required_gates": assessment.get("required_gates") or [],
        "gates": gate_state(work, fresh, sdd_acceptance_state(repo, work)["ready"], repo=repo),
        "gate_status": work.get("gate_status") or {},
    }, ensure_ascii=False, indent=2))
    return 0
