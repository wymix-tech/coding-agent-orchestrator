"""Pure verification declaration and first-project setup policy; no execution."""
from copy import deepcopy
from typing import Any

from scripts.completion_policy import normalize_scripts, required_entries
from scripts.technology_guidance import technology_instruction


PROJECT_PREPARATION_POLICY = 2


SCAFFOLD_ROOTS = ("tests", "test", "acceptance-tests", "src/test", "scripts/verification", ".venv", "venv")
SCAFFOLD_MANIFESTS = frozenset({
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "pytest.ini", "setup.cfg", "uv.lock",
    "package.json", "package-lock.json", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "tsconfig.json", "Makefile",
})


def scaffold_instruction() -> str:
    return ("Put new CAO verification wrappers in .orchestrator/scripts/verification/. Reserve exact paths with `cao verification prepare --path <path>` before writing; legacy scripts/verification/ remains compatible. "
            "Allowed setup-only roots: " + ", ".join(root + "/" for root in SCAFFOLD_ROOTS) +
            "; supported build/test manifests also remain writable. Legacy test/manifest scaffold exceptions require an active Work; prepared verification inputs and project setup files are writable without Work. ")


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
    ready = bool(required_entries(scripts))
    return {"ready": ready, "reason": None if ready else "no required verification scripts configured",
            "scripts": scripts,
            "tests_bound": any(script["required"] and "tests" in script["gates"] for script in scripts)}


def declared_scripts(general: list[str], tests: list[str], security: list[str] | None = None) -> list[dict[str, Any]]:
    if not general and not tests and not security:
        raise ValueError("VERIFICATION_SCRIPT_REQUIRED")
    items = [{"name": f"script-{i}", "command": command, "required": True}
             for i, command in enumerate(general, 1)]
    items += [{"name": f"test-{i}", "command": command, "required": True, "gates": ["tests"]}
              for i, command in enumerate(tests, 1)]
    for i, command in enumerate(security or [], 1):
        existing = next((item for item in items if item['command'].strip() == command.strip()), None)
        if existing is not None:
            existing.setdefault('gates', []).append('security_verification')
        else:
            items.append({'name': f'security-{i}', 'command': command, 'required': True, 'gates': ['security_verification']})
    return normalize_scripts(items)


def setup_instruction(command: str) -> str:
    return (
        "CAO verification setup: establish and explicitly accept a configured baseline before feature development; product writes are blocked until then, while native artifacts, manifests, tests and verification wrappers remain writable. "
        "Run bootstrap first and honor explicit SDD selection/install authorization. "
        "Empty products first use `cao project setup rules`, `draft`, `plan`, `apply` and `check` without a business Work; resume the original request after setup is ready. "
        "For existing products, establish actual required checks and run `cao verification check` before creating business Work; plan-only does not establish readiness. Existing Work stays pending while missing project baselines are repaired at project level; use `cao verification check --project` before resuming it. "
        "For an existing project, analyze product manifests, source, tests and CI; reuse meaningful verification. "
        "Do not infer the product tech stack from .orchestrator derived state, .agents, .pi, host assets, SDD tools/outputs or caches; explicit project technology choices remain relevant. "
        + technology_instruction(command) +
        "Generate project-wide scripts from accepted project technology, manifests, native build/test/CI entries and project quality standards; reuse suitable existing scripts. Never use the pending Work requirement, business acceptance criteria, Work decisions or handoff to generate the project baseline. SDD owns task-specific behavior tests and acceptance. "
        + scaffold_instruction() +
        "Zero tests, missing tools or missing dependencies must fail/block, not echo/true a pass. Preserve the user's selected framework and test runner; do not replace JUnit 5 with a main program when dependencies are missing. For Java/Jupiter use JUnit5-capable Surefire and fail on zero tests. "
        f'Configure through `{command} verification configure --test-script "<actual project test wrapper>" --verify-script "<actual general check>"` using only applicable scripts; register a combined general/tests wrapper once as a test script; do not hand-edit config or use init --force. '
        "Do not overwrite existing scripts. Resolve normal declared dependencies through the accepted project toolchain; global installation and unknown remote scripts require separate authorization. Configuration readiness is not verification success: within an active Work, initial scaffolding/tests may be written before tests pass. Do not expand verification setup into business implementation; configure wrappers, rerun bootstrap and follow the path assessment/native SDD instructions first. "
        "Rerun bootstrap after configuration; if an active Work already has an acceptance anchor, review and explicitly reanchor instead of silently changing it."
    )


def verification_setup_context(work: dict[str, Any] | None) -> dict[str, Any] | None:
    if not work:
        return None
    review = work.get('verification_review') or {}
    return {'id': work['id'], 'status': work['status'], 'purpose': 'work_baseline_adoption',
            'review': {key: review[key] for key in ('id', 'status', 'config_sha256') if key in review},
            'instruction': 'Adopt/review the project-owned standard for this Work; do not generate baseline scripts from its requirement. Resume full task context after baseline preparation/adoption.'}



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
