"""Pure verification declaration and first-project setup policy; no execution."""
from copy import deepcopy
from typing import Any

from scripts.completion_policy import normalize_scripts


def configured_scripts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(cfg, dict):
        raise ValueError("VERIFICATION_CONFIG_INVALID")
    verification = cfg.get("verification") or {}
    if not isinstance(verification, dict):
        raise ValueError("VERIFICATION_CONFIG_INVALID")
    raw = verification.get("scripts")
    if raw is None:
        raw = verification.get("commands") or []
        if not raw and verification.get("command"):
            raw = [{"name": "default", "command": str(verification["command"]), "required": True}]
    return normalize_scripts(raw)


def setup_state(cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        scripts = configured_scripts(cfg)
    except ValueError as exc:
        return {"ready": False, "reason": "invalid verification declarations", "scripts": [],
                "tests_bound": False, "error": str(exc)}
    ready = any(script["required"] for script in scripts)
    return {"ready": ready, "reason": None if ready else "no required verification scripts configured",
            "scripts": scripts,
            "tests_bound": any(script["required"] and "tests" in script["gates"] for script in scripts)}


def declared_scripts(general: list[str], tests: list[str]) -> list[dict[str, Any]]:
    if not general and not tests:
        raise ValueError("VERIFICATION_SCRIPT_REQUIRED")
    items = [{"name": f"script-{i}", "command": command, "required": True}
             for i, command in enumerate(general, 1)]
    items += [{"name": f"test-{i}", "command": command, "required": True, "gates": ["tests"]}
              for i, command in enumerate(tests, 1)]
    return normalize_scripts(items)


def setup_instruction(command: str) -> str:
    return (
        "CAO verification setup: establish a configured acceptance baseline before feature development. "
        "Run bootstrap first and honor explicit SDD selection/install authorization. "
        "Create or resume an active Work before writing product or verification scaffolding; "
        "an empty verification configuration does not prevent cao start. "
        "For an existing project, analyze product manifests, source, tests and CI; reuse meaningful verification. "
        "Do not infer the product tech stack from .orchestrator derived state, .agents, .pi, host assets, SDD tools/outputs or caches; current user requirements remain relevant. "
        "For a new project, use the tech stack already specified by the user; otherwise ask the user for language/framework/build/test choices and wait. Do not guess a stack if unable to ask. "
        "Generate project-owned verification scripts or reuse suitable existing scripts. Zero tests, missing tools or missing dependencies must fail/block, not echo/true a pass. Preserve the user's selected framework and test runner; do not replace JUnit 5 with a main program when dependencies are missing. For Java/Jupiter use JUnit5-capable Surefire and fail on zero tests. "
        f'Configure through `{command} verification configure --test-script "<actual project test wrapper>" --verify-script "<actual general check>"` using only applicable scripts; register a combined general/tests wrapper once as a test script; do not hand-edit config or use init --force. '
        "Do not overwrite existing scripts or install dependencies automatically. Configuration readiness is not verification success: within an active Work, initial scaffolding/tests may be written before tests pass. Do not expand verification setup into business implementation; configure wrappers, rerun bootstrap and follow the path assessment/native SDD instructions first. "
        "Rerun bootstrap after configuration; if an active Work already has an acceptance anchor, review and explicitly reanchor instead of silently changing it."
    )


def verification_setup_context(work: dict[str, Any] | None) -> dict[str, Any] | None:
    if not work:
        return None
    return {key: work.get(key) for key in ("id", "title", "status", "requirement", "sdd",
            "execution", "resume", "decisions", "blockers")}


def prepare_config(raw: dict[str, Any], scripts: list[dict[str, Any]], replace: bool,
                   timestamp: int) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw, dict):
        raise ValueError("VERIFICATION_CONFIG_INVALID")
    try:
        previous = configured_scripts(raw)
    except ValueError:
        previous = None
    if previous == scripts:
        return raw, False
    if (previous or previous is None) and not replace:
        raise ValueError("VERIFICATION_CONFIG_REPLACE_REQUIRED")
    candidate = deepcopy(raw)
    if not isinstance(candidate.get("verification"), dict):
        candidate["verification"] = {}
    candidate["verification"]["scripts"] = scripts
    candidate["verification"]["configured_at"] = timestamp
    return candidate, True
