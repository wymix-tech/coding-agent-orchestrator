"""Small, read-only document router; workflow gates remain owned by CAO."""
from pathlib import Path


STAGE_FILES = {
    "startup": "references/startup.md",
    "intake": "references/work-intake.md",
    "project_setup": "references/project-context.md",
    "verification_setup": "references/verification-setup.md",
    "path_assessment": "references/execution.md",
    "execution": "references/execution.md",
    "completion": "references/completion.md",
    "recovery": "references/recovery.md",
}
PROVIDER_FILES = {name: f"references/provider-{name}.md" for name in ("bmad", "openspec", "speckit")}
CONTRACT_FILES = {
    "continuity": "references/task-continuity.md",
    "native_lifecycle": "references/native-lifecycle.md",
    "conformance": "references/conformance-knowledge.md",
    "native_route": "references/native-route-contract.md",
    "bmad_approval": "references/bmad-approval.md",
    "bmad_dispatch_review": "references/bmad-review-dispatch.md",
    "bmad_oneshot_review": "references/bmad-review-oneshot.md",
    "acceptance": "references/sdd-acceptance-protocol.md",
    "acceptance_command": "references/sdd-acceptance-command.md",
    "acceptance_skill": "references/sdd-acceptance-skill.md",
}


def action_files(reason: str, work: dict | None) -> list[str]:
    """Select contracts by the current operation, not a whole native namespace."""
    sdd = (work or {}).get("sdd") or {}
    keys = []
    if reason.startswith(("WORK_TRANSITION_", "RUNTIME_", "RECOVERY_", "STALE_SESSION_")):
        keys.append("continuity")
    from scripts.lifecycle_contract import enabled
    if enabled(work):
        keys.append("native_lifecycle")
    if reason.startswith("CONFORMANCE_"):
        keys.append("conformance")
    if reason.startswith("NATIVE_ROUTE_"):
        keys.append("native_route")
    if sdd.get("provider") == "bmad":
        if reason in {"NATIVE_SPEC_APPROVAL_REQUIRED", "NATIVE_SPEC_CLARIFICATION_REQUIRED", "NATIVE_IMPLEMENTATION_PAUSED"}:
            keys.append("bmad_approval")
        if reason.startswith("NATIVE_REVIEW_"):
            route = sdd.get("route_decision") or {}
            keys.append("bmad_dispatch_review" if route.get("build_route") == "dispatch" else "bmad_oneshot_review")
    if reason.startswith(("SDD acceptance", "SDD_ACCEPTANCE_", "TASK_ACCEPTANCE_")):
        keys.append("acceptance")
        mode = (((sdd.get("acceptance") or {}).get("declaration") or {}).get("executor") or {}).get("mode")
        if mode in {"command", "skill"}:
            keys.append("acceptance_" + mode)
    return [CONTRACT_FILES[key] for key in keys]


def guidance(root: Path | None, command: str, stage: str, provider: str = "") -> dict:
    """Resolve installed resources without loading their content into the context."""
    files = [STAGE_FILES[stage]] if stage in STAGE_FILES else []
    if stage in {"execution", "completion"} and provider in PROVIDER_FILES:
        files.append(PROVIDER_FILES[provider])
    resources = ["SKILL.md", *files]
    missing = [name for name in resources if root is None or not (root / name).is_file()]
    return {
        "version": 1,
        "stage": stage,
        "command": command,
        "skill": str((root / "SKILL.md").resolve()) if root else None,
        "read_before_action": [str((root / name).resolve()) for name in files] if root else [],
        "ready": not missing,
        "missing_resources": missing,
        "return_to": "bootstrap" if stage in {"startup", "intake", "project_setup", "verification_setup", "recovery"} else "next",
        "instruction": ("Read the Skill once, then only the listed stage resources before acting. "
                        "Use command as the actual invocation for examples written as cao. "
                        "Follow the current action and native instructions; document loading is not execution."
                        if not missing else "Repair the CAO installation: required guidance resources are missing. Do not bypass workflow gates."),
    }
