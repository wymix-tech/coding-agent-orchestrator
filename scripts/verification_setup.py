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
