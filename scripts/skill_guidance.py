"""Small, read-only document router; workflow gates remain owned by CAO."""
from pathlib import Path


STAGE_FILES = {
    "startup": "references/startup.md",
    "intake": "references/work-intake.md",
    "verification_setup": "references/verification-setup.md",
    "path_assessment": "references/execution.md",
    "execution": "references/execution.md",
    "completion": "references/completion.md",
    "recovery": "references/recovery.md",
}
PROVIDER_FILES = {name: f"references/provider-{name}.md" for name in ("bmad", "openspec", "speckit")}
CONTRACT_FILES = {
    "native_route": "references/native-route-contract.md",
    "acceptance": "references/sdd-acceptance-protocol.md",
}


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
        "return_to": "bootstrap" if stage in {"startup", "intake", "verification_setup", "recovery"} else "next",
        "instruction": ("Read the Skill once, then only the listed stage resources before acting. "
                        "Use command as the actual invocation for examples written as cao. "
                        "Follow the current action and native instructions; document loading is not execution."
                        if not missing else "Repair the CAO installation: required guidance resources are missing. Do not bypass workflow gates."),
    }
