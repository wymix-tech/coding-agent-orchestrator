"""Shared configuration defaults, provider metadata, and execution profiles."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.runtime_store import DEFAULTS as RUNTIME_DEFAULTS


ORCH = Path(".orchestrator")


CONFIG_REL = ORCH / "config.json"


INDEX_REL = ORCH / "index.json"


WORK_DIR = ORCH / "work"


SESSION_DIR = ORCH / "sessions"


HANDOFF_DIR = ORCH / "handoffs"


REQUIREMENT_DIR = ORCH / "requirements"


SNAPSHOT_INTERNAL_ROOTS = {".git", ".orchestrator"}
SNAPSHOT_CACHE_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}
# Compatibility for native commit checks. build/dist/target are not inherently generated.
SNAPSHOT_IGNORE_DIRS = SNAPSHOT_INTERNAL_ROOTS | SNAPSHOT_CACHE_DIRS


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "language": {"user": "zh-CN"},
    "runtime": RUNTIME_DEFAULTS,
    "knowledge": {"enabled": True},
    "verification": {"scripts": []},
    "rules": {"files": []},
    "sdd": {
        "provider": "auto",
        "selected_at": None,
        "requirement_globs": ["requirements/**/*.md", "specs/**/*.md", "openspec/**/*.md", "_bmad/**/*.md", ".orchestrator/requirements/**/*.md"],
        "exclude_globs": [],
        "command": {"inspect": "", "next": "", "transition": ""},
    },
}


WORK_STATUSES = {"todo", "active", "suspended", "completed"}


ACTION_TYPES = {"agent", "verification", "sdd", "human", "none"}


# Keep path selection intentionally small and deterministic. The Agent performs
# the lightweight contextual assessment; CAO only validates the result and
# applies these hard minimums.
EXECUTION_PROFILES = ("light", "standard", "comprehensive")


PROFILE_RANK = {name: index for index, name in enumerate(EXECUTION_PROFILES)}


PATH_RISK_MINIMUM: dict[str, str] = {
    "security": "comprehensive",
    "data_migration": "comprehensive",
    "irreversible_change": "comprehensive",
    "cross_system": "comprehensive",
    "major_ambiguity": "comprehensive",
    "external_contract": "standard",
    "multi_module": "standard",
    "compatibility": "standard",
}


PATH_RISK_GATES: dict[str, list[str]] = {
    "security": ["security_review", "security_verification"],
    "data_migration": ["migration_plan", "rollback_plan", "data_verification"],
    "irreversible_change": ["recovery_plan", "explicit_human_confirmation"],
    "cross_system": ["integration_plan", "integration_verification"],
    "major_ambiguity": ["requirements_clarification"],
    "external_contract": ["contract_review", "compatibility_verification"],
    "multi_module": ["integration_verification"],
    "compatibility": ["compatibility_verification"],
}


PROFILE_BASE_GATES: dict[str, list[str]] = {
    "light": ["targeted_verification"],
    "standard": ["scope_review", "tests", "implementation_review", "verification", "sdd_acceptance"],
    "comprehensive": ["requirements_review", "design_review", "implementation_review", "verification", "sdd_acceptance"],
}


SDD_CATALOG: dict[str, dict[str, Any]] = {
    "openspec": {
        "name": "OpenSpec",
        "description": "Spec-driven development for AI coding assistants.",
        "project_markers": ["openspec/config.yaml", "openspec"],
        "cli": "openspec",
        "install": {
            "prerequisite": "Node.js >= 20.19.0",
            "external_commands": [
                ["npm", "install", "-g", "@fission-ai/openspec@latest"],
                ["pnpm", "add", "-g", "@fission-ai/openspec@latest"],
                ["bun", "add", "-g", "@fission-ai/openspec@latest"],
                ["yarn", "global", "add", "@fission-ai/openspec@latest"],
            ],
            # Never install integrations for every coding host by default.
            # The active host is detected at runtime and mapped to one native
            # OpenSpec integration; an explicit --tools value remains an override.
            "tool_by_host": {
                "claude-code": "claude",
                "codex": "codex",
                "pi": "pi",
            },
            "project_command": ["openspec", "init", "--tools", "{tools}"],
        },
        "path_guidance": {
            "light": "For a clear small OpenSpec change, invoke the installed /opsx:propose then /opsx:apply agent commands (host syntax varies), and sync/archive after checks. Expanded /opsx:ff is available only if installed/enabled and creates artifacts, not a skip. Follow the active schema: default apply requires tasks and upstream artifacts; keep them concise rather than deleting them. A smaller custom schema must be explicitly configured and validated. CAO light skips extra workflow registration; finish all existing native tracked tasks before CAO verify and associate every split change.",
            "standard": "Create or update the relevant OpenSpec proposal/spec/tasks as needed, apply the change, and verify the affected behavior.",
            "comprehensive": "Use a complete OpenSpec change with explicit proposal, affected specs, design/tasks where supported, staged implementation, and verification. Split independent changes when that reduces risk.",
        },
        # Directories the provider owns. Native workflow outputs must live here so
        # ordinary product source files cannot be recorded as SDD artifacts.
        "output_dirs": ["openspec/"],
        # Tokens that identify this provider's own CLI/skill entrypoints.
        "workflow_hints": ["openspec", "opsx"],
        # Optional native workflow name allow-list. Left empty on purpose: provider
        # versions differ, so an incomplete list would reject valid workflows. When
        # empty, entrypoint validation falls back to the provider-identity check.
        "workflows": {},
    },
    "bmad": {
        "name": "BMad Method",
        "description": "Agile AI-driven development method with project-local agents and workflows.",
        "project_markers": ["_bmad"],
        "cli": "bmad",
        "install": {
            "prerequisite": "Node.js >= 20.12.0, Python >= 3.10, uv",
            "external_commands": [],
            "tool_by_host": {
                "claude-code": "claude-code",
                "codex": "codex",
                "pi": "pi",
            },
            # BMad requires host-specific tool selection for a fresh non-interactive install.
            # Placeholders are rendered by build_bmad_project_command().
            "project_command": [
                "npx", "bmad-method@6.12.4", "install",
                "--directory", "{repo}",
                "--modules", "bmm",
                "--tools", "{tool}",
                "--yes",
            ],
        },
        "path_guidance": {
            "light": "Invoke the installed bmad-build Skill and its exact renderer; bmad quick-fix is not an official CLI command. Let Build choose oneshot for clear small low-risk work; do not force the route or bypass its reviewers. A oneshot SPEC intentionally omits Tasks & Acceptance: bind it with cao sdd spec bind --spec <native-spec.md> without criteria selectors; CAO recognizes its native Intent and current requirement. Explicit criteria can still be bound. Do not restore deleted sections or create PRD/architecture/stories just for CAO.",
            "standard": "Use relevant BMAD analysis/specification or build workflows and create only the native artifacts needed to clarify scope, implement, review, and verify the change.",
            "comprehensive": "Use the applicable full BMAD planning path, including requirements, architecture, epics/stories when warranted, then implement in stages with explicit review and verification.",
        },
        "output_dirs": ["_bmad-output/"],
        "workflow_hints": ["bmad"],
        "workflows": {},
    },
    "speckit": {
        "name": "GitHub Spec Kit",
        "description": "GitHub's spec-driven development toolkit with the `specify` CLI and agent integrations.",
        "project_markers": [".specify"],
        "cli": "specify",
        "install": {
            "prerequisite": "uv",
            "external_commands": [["uv", "tool", "install", "specify-cli"]],
            "integration_by_host": {
                "claude-code": "claude",
                "codex": "codex",
                "pi": "pi",
            },
            "project_command": ["specify", "init", "--here", "--force", "--non-interactive", "--integration", "codex", "--ignore-agent-tools"],
        },
        "path_guidance": {
            "light": "For an existing-behavior bug, use speckit-bug-assess -> speckit-bug-fix -> speckit-bug-test only when the bug extension is installed; its assessment.md can serve as scoped repair intent with explicit expected behavior. Do not assume the extension exists or auto-install it. For a small new feature use the installed specify -> plan -> tasks -> implement skills and converge where supported: native implement requires plan.md and tasks.md. Keep documents short; optional clarify/analyze/checklist steps need not be added. These are agent invocations, not specify implement CLI commands. CAO light does not waive native preconditions.",
            "standard": "Use the relevant Spec Kit Specify/Plan/Tasks/Implement steps needed for the change; create only the specifications and plans justified by the scope.",
            "comprehensive": "Use a full Spec Kit specification, planning, task decomposition, and staged implementation flow. Apply clarification and analysis steps where uncertainty or integration risk warrants them.",
        },
        "output_dirs": ["specs/", ".specify/"],
        "workflow_hints": ["specify", "speckit"],
        "workflows": {},
    },
}


# Generic output/setup utilities do not invoke provider-native workflows, even
# when their arguments or parent directories mention the provider.
PLACEHOLDER_ENTRYPOINT_COMMANDS = {
    "echo", "true", "false", ":", "printf", "cat", "sleep", "test", "touch",
    "mkdir", "cp", "mv", "tee", "cd", "rm", "rmdir", "install",
}


# Gates CAO can derive from its own state. Everything else must be marked
# explicitly with `cao gate satisfy` because only the Agent can observe it.
AUTO_DERIVED_GATES = {"verification", "targeted_verification", "tests", "sdd_acceptance"}
