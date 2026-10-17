#!/usr/bin/env python3
"""Coding Agent Orchestrator: a small, agent-neutral work control plane."""
from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Callable

# When invoked as `python scripts/cao.py`, Python puts only the `scripts/`
# directory on sys.path. Dashboard helpers import the `scripts` package, so
# make the repository/package root available for source-checkout execution.
# Installed console-script execution already imports `scripts.cao` as a package
# and does not need this adjustment.
if __package__ in (None, ""):
    _source_root = str(Path(__file__).resolve().parent.parent)
    if _source_root not in sys.path:
        sys.path.insert(0, _source_root)

from scripts.state_store import atomic_json, backup_once, repo_transaction
from scripts.native_route import (
    normalize_decision as normalize_route_decision, route_state, intent_hash,
    build_route as native_build_route,
    owned_file as native_owned_file, approve_observed_response, approve_baseline_response,
    bind_request_context, bind_approval_question,
)
from scripts.native_progress import direct_build_decision, spec_task_state, merge_plan_states
from scripts.path_guard import inside, repo_file, provider_artifact, patch_targets
from scripts.route_inputs import declaration_path as route_declaration_path, writable_input, read_declaration as read_route_declaration
from scripts.completion_policy import (
    acceptance_hash, anchor_acceptance, reset_requirement, evidence_matches,
    verification_fresh as bound_verification_fresh, normalize_scripts, tests_satisfied, capability_satisfied, workflow_ready, normalize_timeout, required_verification_status,
)
from scripts.verification_runner import run_script
from scripts.native_obligations import (
    review_state as native_review_state, review_inputs, submit_review, commit_state as native_commit_state,
    digest as native_digest, dispatch as dispatch_review, active_layers, classify_review, resolve_review,
    dispatch_patch_state,
    planning_state,
    implementation_contract, implementation_state,
)
from scripts.smoke_runner import run_smoke
from scripts.provider_preparation import bmad_preparation
from scripts.skill_guidance import guidance as resolve_skill_guidance, action_files, CONTRACT_FILES
from scripts.instruction_updates import update_instructions
from scripts.provider_restart import require_restart, observe_start, restart_state
from scripts.startup_request import (ORDER as STARTUP_ORDER, capture_request, consume_request,
                                     pending_request, request_context)
from scripts.review_binding import attach_reviewer, runtime_model_name
from scripts.native_obligations import load_findings, dispatch_review_next
from scripts.technology_guidance import technology_instruction
from scripts.light_spec_policy import make_binding, binding_digest, light_spec_state
from scripts.sdd_acceptance_policy import digest as acceptance_digest
from scripts.sdd_acceptance_files import source_hashes, output_paths, report_hashes
from scripts.sdd_acceptance_runtime import acceptance_status, reanchor_record
from scripts.openspec_archive import relocate as relocate_openspec_archive, relocate_acceptance_sources
from scripts.verification_setup import (
    configured_scripts, setup_state, declared_scripts, prepare_config, setup_instruction, verification_setup_context,
    SCAFFOLD_ROOTS, SCAFFOLD_MANIFESTS, scaffold_instruction,
)


def locked_command(fn):
    @wraps(fn)
    def call(args):
        with repo_transaction(repo_root(args.repo)):
            return fn(args)
    return call


def locked_repo(fn):
    @wraps(fn)
    def call(repo, *args, **kwargs):
        with repo_transaction(repo):
            return fn(repo, *args, **kwargs)
    return call


ORCH = Path(".orchestrator")
CONFIG_REL = ORCH / "config.json"
INDEX_REL = ORCH / "index.json"
WORK_DIR = ORCH / "work"
SESSION_DIR = ORCH / "sessions"
HANDOFF_DIR = ORCH / "handoffs"
REQUIREMENT_DIR = ORCH / "requirements"
SNAPSHOT_IGNORE_DIRS = {".git", ".orchestrator", ".venv", "venv", "node_modules", "target", "build", "dist", "__pycache__", ".pytest_cache", ".mypy_cache"}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "language": {"user": "zh-CN"},
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
                "npx", "bmad-method", "install",
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


def provider_output_dirs(provider: str) -> list[str]:
    meta = SDD_CATALOG.get(provider) or {}
    return [str(item) for item in (meta.get("output_dirs") or [])]


def workflow_entrypoint_tokens(entrypoint: str) -> list[str]:
    try:
        return shlex.split(entrypoint)
    except ValueError:
        return str(entrypoint).split()


def workflow_command_identities(entrypoint: str, depth: int = 0) -> list[str]:
    """Extract command/module/script positions, never output paths or prose.

    Literal shell wrappers are bounded; unknown/dynamic forms need an explicit
    unverified exception rather than a provider word somewhere in their data.
    """
    if depth >= 8:
        return []
    try:
        segments = _shell_segments(entrypoint)
    except ValueError:
        return []
    identities: list[str] = []
    for tokens in segments:
        redirect = next((i for i, token in enumerate(tokens) if token and all(c in "<>&|" for c in token)), len(tokens))
        tokens = tokens[:redirect]
        for _ in range(8):
            while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
                tokens = tokens[1:]
            if not tokens:
                break
            head = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe").removesuffix(".cmd")
            if head == "invoke" and tokens[1:2] == ["skill"]:
                tokens = tokens[2:3]  # Agent-native invocation notation, not arbitrary prose.
            elif head in {"env", "exec"}:
                tokens = tokens[1:]
            elif head in {"sh", "bash"}:
                if len(tokens) > 2 and tokens[1] in {"-c", "-lc"}:
                    identities.extend(workflow_command_identities(tokens[2], depth + 1))
                    tokens = []
                    break
                tokens = tokens[1:]
            elif re.fullmatch(r"python(?:3(?:\.\d+)?)?", head):
                tokens = tokens[1:]
                while tokens and tokens[0] in {"-u", "-B", "-S"}:
                    tokens = tokens[1:]
                if tokens and tokens[0] == "-m":
                    tokens = tokens[1:2]
                elif tokens and tokens[0].startswith("-"):
                    tokens = []  # In particular, Python -c data is not a native identity.
            elif head in {"npx", "uvx", "uv"}:
                tokens = tokens[1:]
                if head == "uv":
                    if tokens[:2] == ["tool", "run"]:
                        tokens = tokens[2:]
                    elif tokens[:1] == ["run"]:
                        tokens = tokens[1:]
                    else:
                        tokens = []
                flags = {"-y", "--yes", "--no-install"} if head == "npx" else {"-q", "--quiet", "--offline", "--no-sync", "--no-cache"}
                valued = {"-p", "--package"} if head == "npx" else {"--from", "--project", "--python", "--with"}
                index = 0
                while index < len(tokens) and tokens[index].startswith("-"):
                    option, separator, _value = tokens[index].partition("=")
                    if option == "--":
                        index += 1
                        break
                    if option in flags:
                        index += 1
                    elif option in valued:
                        index += 1 if separator else 2
                    else:
                        index = len(tokens)
                tokens = tokens[index:]
            else:
                break
        else:
            tokens = []
        if tokens:
            head = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe").removesuffix(".cmd")
            if head not in PLACEHOLDER_ENTRYPOINT_COMMANDS and not tokens[0].startswith("-") and not any(c in tokens[0] for c in "$`()"):
                identities.append(tokens[0])
    return identities


# Where hosts install agent skills inside a project. BMad v6, for example,
# installs its workflows as `.agents/skills/bmad-<workflow>/SKILL.md` — they are
# model-invoked skills, not shell commands.
PROVIDER_SKILL_ROOTS = (".agents/skills", ".claude/skills", ".codex/skills", "skills")


def provider_native_skills(repo: Path, provider: str) -> list[dict[str, str]]:
    """Agent-skill assets this provider installed into the project.

    Providers differ in how they ship workflows, so this scans every known skill
    root rather than assuming one. BMad installs them as `.agents/skills/bmad-<workflow>/`
    skills; OpenSpec and Spec Kit expose their implementation commands as agent
    skills or host prompt assets too. Each result carries the real repo-relative path, so guidance can never
    point at a plausible-looking but wrong location.
    """
    hints = [str(hint).strip().lower() for hint in (SDD_CATALOG.get(provider, {}).get("workflow_hints") or [provider])]
    found: dict[str, str] = {}
    for root in PROVIDER_SKILL_ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or not (child / "SKILL.md").is_file():
                continue
            lowered = child.name.lower()
            if not any(lowered == hint or lowered.startswith(f"{hint}-") for hint in hints):
                continue
            rel_path = f"{root}/{child.name}/SKILL.md"
            found.setdefault(child.name, rel_path)
    return [{"name": name, "rel_path": found[name]} for name in sorted(found)]


def skill_invocation_guidance(repo: Path, provider: str, entrypoint: str) -> str:
    """Explain how to actually run the selected native workflow for any provider.

    An Agent skips a workflow mostly when it cannot tell how to invoke it. The
    answer depends on how the provider ships workflows, so this covers both
    forms instead of assuming BMad's skill layout.
    """
    skills = provider_native_skills(repo, provider)
    if skills:
        lowered = entrypoint.strip().lower().removeprefix("invoke skill ")
        matched = next((s for s in skills if s["name"].lower() == lowered), None)
        if matched:
            return (
                f" This provider exposes this workflow as an agent skill, not a shell command: invoke the "
                f"`{matched['name']}` skill (read `{matched['rel_path']}` and follow it, including its native "
                f"preconditions). Do not substitute your own implementation for running it."
                + (" Read the complete SKILL.md and run its exact project-root renderer; do not guess a skill/scripts location. Renderer success loads instructions, it is not workflow completion. Preserve the configured user language."
                   if provider == "bmad" else " Read the complete installed Skill and follow its native prerequisites. CLI setup/status does not execute the implementation Skill. Preserve the configured user language.")
            )
        listed = ", ".join(f"`{s['name']}`" for s in skills)
        return (
            f" This provider's installed agent skills are: {listed}. Invoke the one matching this workflow "
            f"(read its SKILL.md and follow it). Do not substitute your own implementation."
        )
    if provider == "openspec":
        return (" OpenSpec uses its CLI (`openspec`) for setup/status/instructions; implementation is an agent command or Skill. Invoke the installed /opsx:propose and /opsx:apply using the host's syntax, follow the active schema, and record the actual invocation. Do not run an invented `openspec apply` CLI subcommand.")
    if provider == "speckit":
        return (" Spec Kit uses its CLI (`specify`) for setup/extensions; specify/plan/tasks/implement are agent commands or Skills, not terminal subcommands. Read the installed integration's instructions and use its invocation syntax. Feature implement requires plan.md and tasks.md; use the separate bug-assess/fix/test path only if its extension is installed.")
    return ""



def workflow_skill_identity(repo: Path, provider: str, entrypoint: str) -> str | None:
    """Identify an installed Skill only in invocation/script argument positions."""
    tokens = workflow_entrypoint_tokens(entrypoint)
    skills = {skill["name"]: skill for skill in provider_native_skills(repo, provider)}
    if tokens[:2] == ["invoke", "skill"] and len(tokens) == 3:
        return tokens[2] if tokens[2] in skills else None
    if len(tokens) == 1 and tokens[0] in skills:
        return tokens[0]
    if any(Path(identity).name == "render_skill.py" for identity in workflow_command_identities(entrypoint)):
        if "--skill" in tokens and tokens.index("--skill") + 1 < len(tokens):
            try:
                target = repo_file(repo, tokens[tokens.index("--skill") + 1])
                for name, skill in skills.items():
                    if target == (repo / skill["rel_path"]).parent.resolve():
                        return name
            except (ValueError, OSError, RuntimeError):
                pass
    return None


def mistaken_terminal_workflow(provider: str, entrypoint: str, depth: int = 0) -> bool:
    """Specific verified CLI/agent confusion, including bounded literal runners."""
    if depth >= 8:
        return False
    cli_name, commands = {"bmad": ("bmad", {"quick-fix"}),
                          "openspec": ("openspec", {"apply", "propose", "ff"}),
                          "speckit": ("specify", {"specify", "plan", "tasks", "implement", "converge"})}.get(provider, ("", set()))
    if not cli_name:
        return False
    identities = workflow_command_identities(entrypoint)
    try:
        segments = _shell_segments(entrypoint)
    except ValueError:
        return False
    for tokens in segments:
        head = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if tokens else ""
        if head in {"sh", "bash"} and len(tokens) > 2 and tokens[1] in {"-c", "-lc"}:
            if mistaken_terminal_workflow(provider, tokens[2], depth + 1):
                return True
        for identity in identities:
            base = identity.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe").removesuffix(".cmd")
            native = (base == cli_name or (provider == "openspec" and
                      (identity == "@fission-ai/openspec" or identity.startswith("@fission-ai/openspec@"))))
            if native and identity in tokens:
                index = tokens.index(identity)
                if index + 1 < len(tokens) and tokens[index + 1] in commands:
                    return True
    return False


def validate_workflow_entrypoint(provider: str, name: str, entrypoint: str) -> dict[str, Any]:
    """Reject placeholder entrypoints and entrypoints unrelated to the provider.

    The native-workflow contract is only meaningful when the recorded entrypoint
    can plausibly invoke the provider's own workflow. A placeholder such as
    `echo '...'` passes every structural check while executing nothing, so it is
    rejected explicitly. Providers that declare a workflow allow-list get strict
    name validation; the rest fall back to a provider-identity check.
    """
    tokens = workflow_entrypoint_tokens(entrypoint)
    if not tokens:
        return {"ok": False, "reason": "workflow entrypoint is empty"}
    if mistaken_terminal_workflow(provider, entrypoint):
        return {"ok": False, "reason": "This is not a supported terminal workflow entrypoint. Invoke the installed provider agent Skill/command using its host syntax."}
    meta = SDD_CATALOG.get(provider) or {}
    declared = meta.get("workflows") or {}
    known: list[str] = []
    for values in declared.values():
        if isinstance(values, list):
            known.extend(str(value).strip().lower() for value in values)
    if known:
        if str(name).strip().lower() not in known:
            return {
                "ok": False,
                "reason": f"workflow name `{name}` is not one of the native {provider} workflows declared by this CAO version",
            }
    hints = [str(hint).strip().lower() for hint in (meta.get("workflow_hints") or [provider])]
    identities = workflow_command_identities(entrypoint)
    # A provider in an unrelated executable's parent directory is not its identity.
    markers = [str(marker).strip("/") for marker in (meta.get("project_markers") or [])]
    referenced = False
    for identity in identities:
        path = identity.replace("\\", "/").lower()
        native_script = any(f"/{marker}/scripts/" in "/" + path for marker in markers)
        if any(hint in path.rsplit("/", 1)[-1] for hint in hints) or native_script:
            referenced = True
            break
    if not referenced:
        return {
            "ok": False,
            "reason": f"workflow entrypoint does not reference the {provider} provider; expected one of: {', '.join(hints)}",
        }
    return {"ok": True, "reason": None}


def validate_workflow_artifacts(repo: Path, provider: str, paths: list[str]) -> dict[str, Any]:
    """Require native workflow outputs to live inside the provider's own directories."""
    dirs = provider_output_dirs(provider)
    if not dirs:
        return {"ok": True, "offending": [], "reason": None}
    offending: list[str] = []
    for value in paths:
        try:
            provider_artifact(repo, str(value), dirs)
        except ValueError:
            offending.append(str(value))
    if offending:
        return {
            "ok": False,
            "offending": offending,
            "reason": f"native {provider} workflow outputs must live under {', '.join(dirs)}; recorded product files are not SDD artifacts: {', '.join(offending)}",
        }
    return {"ok": True, "offending": [], "reason": None}


def gate_is_auto_derived(name: str) -> bool:
    low = str(name).strip().lower()
    return low in AUTO_DERIVED_GATES or low.endswith("_verification")


def gate_state(work: dict[str, Any], verification_fresh: bool, sdd_ready: bool = False) -> dict[str, Any]:
    """Split the assessment's required gates into satisfied and pending ones.

    Verification gates require fresh configured evidence; tests additionally need
    successful required scripts declaring that capability. Other gates can only be
    observed by the Agent, so it must be marked with `cao gate satisfy`.
    """
    assessment = work.get("path_assessment") or {}
    gates = [str(gate) for gate in (assessment.get("required_gates") or [])]
    status = work.get("gate_status") or {}
    satisfied: list[str] = []
    pending: list[str] = []
    for gate in gates:
        if gate == "sdd_acceptance":
            ok = bool(sdd_ready)
        elif gate == "tests":
            ok = bool(verification_fresh and tests_satisfied(work.get("verification") or {}))
        elif gate == "security_verification":
            ok = bool(verification_fresh and capability_satisfied(work.get("verification") or {}, gate))
        elif gate_is_auto_derived(gate):
            ok = bool(verification_fresh)
        else:
            evidence = status.get(gate) or {}
            ok = bool(evidence.get("satisfied") and evidence_matches(work, evidence))
        (satisfied if ok else pending).append(gate)
    return {"gates": gates, "satisfied": satisfied, "pending": pending}


def command_exists(command: str) -> bool:
    from shutil import which
    return which(command) is not None


# Files unknown hosts might read. When the host is not known, install every
# template rather than guessing which one applies.
ALL_INSTRUCTION_TEMPLATES = ("AGENTS.md", "CLAUDE.md")

# Which instruction file each host actually reads. Only Claude Code reads
# `CLAUDE.md`; Codex and Pi read `AGENTS.md`. Installing both meant a Pi project
# carried a `CLAUDE.md` telling it to run `bootstrap --host claude-code`.
INSTRUCTION_TEMPLATES_BY_HOST = {
    "claude-code": ("CLAUDE.md",),
    "codex": ("AGENTS.md",),
    "pi": ("AGENTS.md",),
}


def instruction_templates_for_host(host: str | None) -> tuple[str, ...]:
    return INSTRUCTION_TEMPLATES_BY_HOST.get(host or "", ALL_INSTRUCTION_TEMPLATES)


def instruction_template_dir() -> Path | None:
    """Locate project-level instruction templates in a checkout or installed wheel."""
    source_root = Path(__file__).resolve().parents[1]
    candidates = [
        source_root / "templates",
        Path(sysconfig.get_path("data")) / "share" / "coding-agent-orchestrator" / "templates",
        Path(sysconfig.get_path("data")) / "templates",
    ]
    required = list(ALL_INSTRUCTION_TEMPLATES)
    for candidate in candidates:
        if all((candidate / name).is_file() for name in required):
            return candidate
    return None


def cao_invocation(root: Path | None = None) -> dict[str, str]:
    """Resolve exactly how the Agent should invoke CAO in this installation.

    The skill directory ships a `cao` launcher at its root; the installed package
    root *is* the skill directory. Agents must call that launcher and must never
    be pointed at `scripts/cao.py`, so the implementation stays out of the
    Agent's context and cannot be read around or reasoned about instead of run.

    Guessing produced failures such as `cao: command not found` followed by
    invented paths like `.agents/skills/.../scripts/cao.py`, so the resolved
    invocation is rendered into the project instruction files instead.
    """
    if command_exists("cao"):
        return {"command": "cao", "source": "console-script"}
    root = root or package_root_dir()
    if root:
        launcher = root / "cao"
        if launcher.is_file():
            # Wheel installs can drop the executable bit, so fall back to `sh`.
            usable = os.access(launcher, os.X_OK)
            return {
                "command": str(launcher) if usable else f"sh {launcher}",
                "source": "skill-launcher",
                "path": str(launcher),
                "executable_bit": usable,
            }
        # Last resort only: the launcher is missing from this installation.
        script = root / "scripts" / "cao.py"
        if script.is_file():
            return {"command": f"python3 {script}", "source": "checkout-script", "path": str(script)}
    return {"command": "cao", "source": "unresolved"}


def install_project_instruction_templates(repo: Path, host: str | None = None) -> dict[str, Any]:
    """Install missing project bootstrap instructions without overwriting user files.

    Instruction files are read when a session starts, so like the host hooks they
    belong to provisioning. They are installed from `cao init` and re-checked
    from `cao bootstrap`, which is idempotent and never overwrites. Only the file
    the provisioned host reads is installed, so a project is not left carrying
    instructions for a different host.
    """
    templates = instruction_templates_for_host(host)
    template_dir = instruction_template_dir()
    result: dict[str, Any] = {
        "status": "installed",
        "installed": [],
        "skipped_existing": [],
        "missing_templates": [],
    }
    if template_dir is None:
        result["status"] = "templates_unavailable"
        result["missing_templates"] = list(templates)
        return result

    for name in templates:
        target = repo / name
        source = template_dir / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(render_project_instruction(source, host))
            result["installed"].append(name)
        except FileExistsError:
            result["skipped_existing"].append(name)
        except OSError as exc:
            result["status"] = "partial"
            result.setdefault("errors", []).append({"file": name, "reason": str(exc)})
    if result.get("status") != "partial":
        if result["installed"]:
            result["status"] = "installed"
        elif result["skipped_existing"]:
            result["status"] = "already_present"
    if result["skipped_existing"]:
        rendered = {name: render_project_instruction(template_dir / name, host)
                    for name in result["skipped_existing"]}
        preview = update_instructions(repo, rendered)
        updates = [item["file"] for item in preview["files"] if item["status"] == "preview"]
        if updates:
            result["upgrade_available"] = updates
            host_arg = f" --host {host}" if host else ""
            result["upgrade_command"] = f"{cao_invocation()['command']} instructions update{host_arg}"
    return result


def render_project_instruction(source: Path, host: str | None) -> str:
    content = source.read_text(encoding="utf-8")
    if host:
        content = content.replace("--host <host>", f"--host {host}")
    return content.replace("{{CAO_COMMAND}}", cao_invocation()["command"])


@locked_command
def cmd_instructions_update(args: argparse.Namespace) -> int:
    template_dir = instruction_template_dir()
    if template_dir is None:
        raise RuntimeError("INSTRUCTION_TEMPLATES_UNAVAILABLE")
    host = args.host or detect_current_host()
    rendered = {name: render_project_instruction(template_dir / name, host)
                for name in instruction_templates_for_host(host)}
    out = update_instructions(repo_root(args.repo), rendered, args.apply)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# Host adapters are process-startup integrations: Claude Code reads its settings
# file and Pi loads its extensions when the host process starts, not when a
# command inside the session runs. They must therefore be wired during project
# provisioning (`cao init`), never from `cao bootstrap` — by then the current
# session's SessionStart hook has already been missed.
HOST_ADAPTER_META: dict[str, dict[str, Any]] = {
    "claude-code": {
        "fragment": "hosts/claude-code/settings.fragment.json",
        # Local, usually git-ignored: only affects this machine, so a team member
        # who does not use CAO is never forced to load its hooks.
        "target": ".claude/settings.local.json",
    },
    "codex": {
        "fragment": "hosts/codex/hooks.json",
        "target": ".codex/hooks.json",
    },
    "pi": {
        # Pi registers extensions as packages, not as a JSON fragment.
        "target": ".pi/settings.json",
        "install": "pi-cli",
    },
}

# Placeholders inside the shipped fragments that must be resolved to the real
# install location. `${CLAUDE_PROJECT_DIR}` is deliberately absent: it is
# expanded by the host at runtime.
HOST_FRAGMENT_PLACEHOLDERS = ("/PATH/TO/coding-agent-orchestrator", "${PLUGIN_ROOT}")


def package_root_dir() -> Path | None:
    """Locate the installed CAO package root that owns the `hosts/` adapters."""
    source_root = Path(__file__).resolve().parents[1]
    candidates = [
        source_root,
        Path(sysconfig.get_path("data")) / "share" / "coding-agent-orchestrator",
    ]
    for candidate in candidates:
        if (candidate / "hosts").is_dir():
            return candidate
    return None


def render_host_fragment(fragment: dict[str, Any], root: Path) -> dict[str, Any]:
    """Resolve install-location placeholders inside a host fragment."""
    text = json.dumps(fragment)
    for token in HOST_FRAGMENT_PLACEHOLDERS:
        text = text.replace(token, str(root))
    return json.loads(text)


def merge_hook_config(existing: dict[str, Any], incoming: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Deep-merge incoming host hooks into an existing host settings file.

    Existing entries are never removed or rewritten. An incoming command that is
    already registered for the same event is skipped, so the merge is idempotent
    and safe to re-run.
    """
    merged = json.loads(json.dumps(existing)) if isinstance(existing, dict) else {}
    changed = False
    for event, entries in (incoming.get("hooks") or {}).items():
        if not isinstance(entries, list):
            continue
        bucket = merged.setdefault("hooks", {}).setdefault(event, [])
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for inner in entry.get("hooks") or []:
                if not isinstance(inner, dict):
                    continue
                if any(inner.get("command") == h.get("command") for e in bucket for h in (e.get("hooks") or [])):
                    continue
                matcher = entry.get("matcher")
                target = next((e for e in bucket if e.get("matcher") == matcher), None)
                if target is None:
                    target = {"hooks": []}
                    if matcher:
                        target["matcher"] = matcher
                    bucket.append(target)
                target.setdefault("hooks", []).append(json.loads(json.dumps(inner)))
                changed = True
    return merged, changed


def host_adapter_status(repo: Path, host: str, root: Path | None = None) -> dict[str, Any]:
    """Report whether a host adapter is wired into the project."""
    meta = HOST_ADAPTER_META.get(host)
    if not meta:
        return {"host": host, "installed": False, "reason": "unsupported host"}
    info = {"host": host, "session_end_supported": host != "codex"}
    if host == "codex":
        info["limitations"] = ["No verified native SessionEnd hook; Stop records turn end only. Use `cao handoff --end-session` on actual exit."]
    root = root or package_root_dir()
    if meta.get("install") == "pi-cli":
        target = repo / meta["target"]
        try:
            settings = load_json(target, {}) or {}
        except (OSError, ValueError):
            settings = {}
        entries = settings.get("packages") or []
        packages = [str(p) for p in entries]
        sources = [p if isinstance(p, str) else str(p.get("source", ""))
                   for p in entries if isinstance(p, (str, dict))]
        installed = any("coding-agent-orchestrator" in source for source in sources)
        # A local Pi package records its checkout path, whose folder need not
        # share the package name (for example a checkout named `cao`).
        if root and not installed:
            for source in sources:
                try:
                    candidate = Path(source).expanduser()
                    if not candidate.is_absolute():
                        # Pi resolves local package entries relative to .pi/.
                        candidate = target.parent / candidate
                    installed = candidate.resolve() == root.resolve()
                except (OSError, ValueError, RuntimeError):
                    continue
                if installed:
                    break
        subagents = settings.get('subagents') or {}
        child_extensions = subagents.get('defaultSubagentOnlyExtensions', []) if isinstance(subagents, dict) else []
        child_hook = str(root / 'hosts/pi/coding-orchestrator.ts') if root else ''
        child_ready = isinstance(child_extensions, list) and child_hook in child_extensions
        return {**info, "installed": installed, "target": meta["target"], "packages": packages,
                'child_hooks_installed': child_ready,
                'child_hooks_next': None if child_ready else 'Run cao host install --host pi before restarting; foreground children need the CAO adapter explicitly.'}
    if root is None:
        return {**info, "installed": False, "target": meta["target"], "reason": "adapter files unavailable"}
    fragment_path = root / meta["fragment"]
    if not fragment_path.is_file():
        return {**info, "installed": False, "target": meta["target"], "reason": "fragment missing"}
    target = repo / meta["target"]
    try:
        fragment = render_host_fragment(json.loads(fragment_path.read_text(encoding="utf-8")), root)
    except (OSError, ValueError) as exc:
        return {**info, "installed": False, "target": meta["target"], "reason": str(exc)}
    if not target.is_file():
        return {**info, "installed": False, "target": str(target), "reason": "host settings file missing"}
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {**info, "installed": False, "target": str(target), "reason": str(exc)}
    _, changed = merge_hook_config(existing, fragment)
    return {**info, "installed": not changed, "target": str(target)}


def install_host_adapter(repo: Path, host: str) -> dict[str, Any]:
    """Wire one host adapter into the project. Idempotent and non-destructive."""
    meta = HOST_ADAPTER_META.get(host)
    if not meta:
        return {"host": host, "status": "error", "reason": f"unsupported host: {host}"}
    root = package_root_dir()
    if root is None:
        return {"host": host, "status": "error", "reason": "CAO host adapter files are unavailable in this installation"}

    if meta.get("install") == "pi-cli":
        if not command_exists("pi"):
            return {
                "host": host,
                "status": "skipped",
                "reason": "`pi` CLI not found on PATH",
                "next": "pi install --local <path-to-coding-agent-orchestrator> --approve",
            }
        result = subprocess.run(
            # `host install` explicitly requests this local adapter registration.
            # Pi 1.1 needs command-scoped approval to write local package config;
            # this does not persist global project trust.
            ["pi", "install", "--local", str(root), "--approve"],
            cwd=repo, text=True, capture_output=True, timeout=120,
        )
        if result.returncode == 0:
            target = repo / meta['target']
            try:
                settings = load_json(target, {})
                if not isinstance(settings, dict):
                    raise ValueError('Pi settings must be an object')
                subagents = settings.setdefault('subagents', {})
                if not isinstance(subagents, dict):
                    raise ValueError('Pi subagents settings must be an object')
                if 'defaultSubagentOnlyExtensions' not in subagents:
                    # A new project-level key shadows the user's global default.
                    # Carry the effective inherited list forward before appending CAO.
                    agent_dir = Path(os.environ.get('PI_CODING_AGENT_DIR') or Path.home() / '.pi/agent').expanduser()
                    user_settings = load_json(agent_dir / 'settings.json', {})
                    inherited = user_settings.get('subagents', {}) if isinstance(user_settings, dict) else {}
                    inherited = inherited.get('defaultSubagentOnlyExtensions', []) if isinstance(inherited, dict) else []
                    subagents['defaultSubagentOnlyExtensions'] = inherited
                extensions = subagents.setdefault('defaultSubagentOnlyExtensions', [])
                if not isinstance(extensions, list) or any(not isinstance(p, str) for p in extensions):
                    raise ValueError('Pi child extension defaults must be a list of paths')
                adapter = str(root / 'hosts/pi/coding-orchestrator.ts')
                if adapter not in extensions:
                    extensions.append(adapter)
                    save_json(target, settings)
            except (OSError, ValueError, TypeError) as exc:
                return {'host': host, 'status': 'error', 'target': meta['target'],
                        'reason': 'Cannot configure CAO child hooks: ' + str(exc)}
        return {
            "host": host,
            "status": "installed" if result.returncode == 0 else "error",
            "target": meta["target"],
            "command": f"pi install --local {shlex.quote(str(root))} --approve",
            "exit_code": result.returncode,
            "stderr": result.stderr.strip()[-500:] or None,
        }

    fragment_path = root / meta["fragment"]
    target = repo / meta["target"]
    try:
        fragment = render_host_fragment(json.loads(fragment_path.read_text(encoding="utf-8")), root)
    except (OSError, ValueError) as exc:
        return {"host": host, "status": "error", "reason": str(exc)}

    try:
        existing = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    except (OSError, ValueError) as exc:
        return {"host": host, "status": "error", "target": str(target), "reason": f"cannot read existing host settings: {exc}"}
    if not isinstance(existing, dict):
        return {"host": host, "status": "error", "target": str(target), "reason": "existing host settings is not an object"}

    merged, changed = merge_hook_config(existing, fragment)
    if not changed:
        return {"host": host, "status": "already_present", "target": str(target)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        save_json(target, merged)
    except OSError as exc:
        return {"host": host, "status": "error", "target": str(target), "reason": str(exc)}
    return {"host": host, "status": "installed", "target": str(target)}


def install_host_adapters(repo: Path, hosts: list[str]) -> dict[str, Any]:
    results = [install_host_adapter(repo, host) for host in hosts]
    return {
        "hosts": results,
        "installed": [r["host"] for r in results if r["status"] == "installed"],
        "already_present": [r["host"] for r in results if r["status"] == "already_present"],
        "failed": [r["host"] for r in results if r["status"] in {"error", "skipped"}],
    }


def command_output(argv: list[str], timeout: float = 3.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)


def version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def version_at_least(actual: tuple[int, ...] | None, required: tuple[int, ...]) -> bool:
    if actual is None:
        return False
    a = actual + (0,) * (len(required) - len(actual))
    return a[: len(required)] >= required


def find_sdd_project_markers(repo: Path, provider: str) -> list[str]:
    meta = SDD_CATALOG.get(provider) or {}
    found: list[str] = []
    for marker in meta.get("project_markers") or []:
        p = repo / marker
        if p.exists():
            found.append(marker)
    return found


def detect_sdd(repo: Path, provider: str, include_cli_version: bool = False) -> dict[str, Any]:
    meta = SDD_CATALOG.get(provider) or {}
    markers = find_sdd_project_markers(repo, provider)
    cli = str(meta.get("cli") or provider)
    cli_installed = command_exists(cli)
    cli_version = None
    if cli_installed and include_cli_version:
        rc, out, err = command_output([cli, "--version"])
        if rc == 0:
            cli_version = out or err
    return {
        "provider": provider,
        "name": meta.get("name", provider),
        "description": meta.get("description", ""),
        "installed": bool(markers),
        "initialized": bool(markers),
        "project_markers": markers,
        "cli_installed": cli_installed,
        "cli_version": cli_version,
    }


def sdd_status(repo: Path) -> dict[str, Any]:
    cfg = config(repo)
    selected = str((cfg.get("sdd") or {}).get("provider") or "auto")
    providers = [detect_sdd(repo, name) for name in SDD_CATALOG]
    installed = [x for x in providers if x["installed"]]
    selected_status = None
    if selected in SDD_CATALOG:
        selected_status = detect_sdd(repo, selected)
    return {
        "selected": selected,
        "selected_status": selected_status,
        "installed": installed,
        "providers": providers,
        "selection_required": not installed or (selected == "auto" and len(installed) > 1),
    }


def sdd_options(status: dict[str, Any]) -> list[dict[str, Any]]:
    options = []
    for item in status["providers"]:
        options.append({
            "provider": item["provider"],
            "name": item["name"],
            "description": item["description"],
            "installed": item["installed"],
            "cli_installed": item["cli_installed"],
            "project_markers": item["project_markers"],
        })
    return options


@locked_repo
def select_sdd_provider(repo: Path, provider: str) -> None:
    if provider not in SDD_CATALOG:
        raise ValueError(f"Unsupported SDD provider: {provider}")
    cfg_path = repo / CONFIG_REL
    cfg = config(repo)
    cfg["sdd"]["provider"] = provider
    cfg["sdd"]["selected_at"] = now()
    save_json(cfg_path, cfg)


def find_python_runtime(required: tuple[int, ...]) -> dict[str, Any]:
    """Find any usable Python interpreter meeting the minimum version.

    Do not require the generic `python3` alias: macOS/Linux environments often
    expose a versioned executable such as `python3.11` instead.
    """
    candidates = ["python3"] + [f"python3.{minor}" for minor in range(20, 9, -1)] + ["python"]
    if sys.executable:
        candidates.append(sys.executable)

    seen: set[str] = set()
    available: list[dict[str, Any]] = []
    selected: str | None = None
    selected_version: str | None = None

    for name in candidates:
        if name in seen or not command_exists(name):
            continue
        seen.add(name)
        rc, out, err = command_output([name, "--version"])
        version_text = out or err
        actual = version_tuple(version_text)
        ok = rc == 0 and version_at_least(actual, required)
        available.append({"name": name, "ok": ok, "version": version_text})
        if ok and selected is None:
            selected = name
            selected_version = version_text

    if selected:
        return {
            "name": "python",
            "ok": True,
            "selected": selected,
            "version": selected_version,
            "required": ".".join(map(str, required)) + "+",
            "candidates": available,
        }

    return {
        "name": "python",
        "ok": False,
        "reason": "Python >= 3.10 is not installed or no compatible executable was found",
        "required": ".".join(map(str, required)) + "+",
        "candidates": available,
    }


def sdd_prerequisite_check(provider: str) -> dict[str, Any]:
    meta = SDD_CATALOG[provider]
    checks: list[dict[str, Any]] = []
    if provider == "openspec":
        if not command_exists("node"):
            checks.append({"name": "node", "ok": False, "reason": "Node.js is not installed", "required": ">= 20.19.0"})
        else:
            rc, out, err = command_output(["node", "--version"])
            actual = version_tuple(out or err)
            checks.append({"name": "node", "ok": rc == 0 and version_at_least(actual, (20, 19, 0)), "version": out or err, "required": ">= 20.19.0"})
    elif provider == "bmad":
        if not command_exists("node"):
            checks.append({"name": "node", "ok": False, "reason": "node is not installed", "required": ">= 20.12.0"})
        else:
            rc, out, err = command_output(["node", "--version"])
            actual = version_tuple(out or err)
            checks.append({"name": "node", "ok": rc == 0 and version_at_least(actual, (20, 12, 0)), "version": out or err, "required": "20.12.0+"})

        python_check = find_python_runtime((3, 10, 0))
        checks.append(python_check)
        uv_ok = command_exists("uv")
        checks.append({"name": "uv", "ok": uv_ok, "reason": "uv is not installed" if not uv_ok else None})
    elif provider == "speckit":
        checks.append({"name": "uv", "ok": command_exists("uv"), "reason": "uv is not installed" if not command_exists("uv") else None})
    return {"provider": provider, "ok": all(c["ok"] for c in checks), "checks": checks, "declared": meta["install"].get("prerequisite")}


def choose_external_install_command(provider: str, upgrade: bool = False) -> list[str] | None:
    if provider == "openspec" and not upgrade and command_exists("openspec"):
        rc, out, err = command_output(["openspec", "--version"])
        if rc == 0 and version_tuple(out or err):
            return None
    for candidate in SDD_CATALOG[provider]["install"].get("external_commands") or []:
        if candidate and command_exists(candidate[0]):
            return candidate
    return None


def run_install_command(argv: list[str], repo: Path) -> dict[str, Any]:
    started = now()
    try:
        result = subprocess.run(argv, cwd=repo, text=True, capture_output=True)
    except OSError as exc:
        return {"command": shlex.join(argv), "exit_code": 127, "stdout": "", "stderr": str(exc), "started_at": started}
    return {"command": shlex.join(argv), "exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "started_at": started}


def repo_root(path: str | Path = ".") -> Path:
    p = Path(path).resolve()
    try:
        out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=p, text=True, stderr=subprocess.DEVNULL).strip()
        return Path(out).resolve()
    except Exception:
        return p


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    atomic_json(path, value)


def deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v


def config(repo: Path) -> dict[str, Any]:
    raw = load_json(repo / CONFIG_REL, {})
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if raw:
        deep_merge(merged, raw)

    # Migrate the previous command-list model only when the persisted config
    # does not explicitly contain the new `scripts` key. This distinction is
    # important because DEFAULT_CONFIG itself supplies an empty scripts list.
    raw_verification = raw.get("verification") if isinstance(raw, dict) else None
    if isinstance(raw_verification, dict) and "scripts" not in raw_verification:
        verification = merged.setdefault("verification", {})
        old_default = "python -m unittest discover -s tests -p 'test_*.py'"
        legacy_command = raw_verification.get("command")
        legacy_commands = raw_verification.get("commands")
        if isinstance(legacy_commands, list) and legacy_commands:
            verification["scripts"] = legacy_commands
        elif legacy_command and legacy_command != old_default:
            verification["scripts"] = [{"name": "default", "command": str(legacy_command), "required": True}]
        else:
            verification["scripts"] = []
    return merged


def verification_scripts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return configured_scripts(cfg)


# Compatibility alias for internal callers and older integrations.
verification_commands = verification_scripts

LANGUAGE_LABELS = {
    "zh-cn": "Simplified Chinese",
    "zh-hans": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
    "zh-hant": "Traditional Chinese",
    "en": "English",
    "en-us": "English",
    "en-gb": "English",
    "ja": "Japanese",
    "ja-jp": "Japanese",
    "ko": "Korean",
    "ko-kr": "Korean",
    "es": "Spanish",
    "es-es": "Spanish",
    "fr": "French",
    "fr-fr": "French",
    "de": "German",
    "de-de": "German",
    "pt": "Portuguese",
    "pt-br": "Brazilian Portuguese",
    "ru": "Russian",
}
LANGUAGE_POLICY_START = "CAO_LANGUAGE_POLICY_START"
LANGUAGE_POLICY_END = "CAO_LANGUAGE_POLICY_END"


@locked_repo
def ensure_language_preference(repo: Path) -> str:
    """Persist the default user-language preference into existing CAO configs."""
    path = repo / CONFIG_REL
    if not path.exists():
        return "zh-CN"
    raw = load_json(path, {})
    language_cfg = raw.get("language") if isinstance(raw, dict) else None
    if not isinstance(language_cfg, dict) or not str(language_cfg.get("user") or "").strip():
        if not isinstance(language_cfg, dict):
            language_cfg = {}
        language_cfg["user"] = "zh-CN"
        raw["language"] = language_cfg
        save_json(path, raw)
    return str(language_cfg["user"]).strip()


def user_language(repo: Path) -> str:
    raw = (config(repo).get("language") or {}).get("user")
    language = str(raw or "zh-CN").strip()
    if not language:
        raise ValueError("language.user must be a non-empty language name or locale such as zh-CN")
    return language


def language_label(language: str) -> str:
    return LANGUAGE_LABELS.get(language.strip().lower(), language.strip())


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _set_toml_table_values(text: str, table: str, values: dict[str, str]) -> tuple[str, bool]:
    """Update keys inside a TOML table while preserving unrelated settings."""
    lines = text.splitlines()
    header_re = re.compile(r"^\s*\[" + re.escape(table) + r"\]\s*(?:#.*)?$")
    starts = [i for i, line in enumerate(lines) if header_re.match(line)]
    assignments = {key: f"{key} = {_toml_quote(value)}" for key, value in values.items()}
    if not starts:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{table}]")
        lines.extend(assignments.values())
        updated = "\n".join(lines) + "\n"
        return updated, updated != text

    start = starts[0]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^\s*\[[^\]]+\]\s*(?:#.*)?$", lines[i]):
            end = i
            break
    found: set[str] = set()
    out: list[str] = []
    for i, line in enumerate(lines):
        if start < i < end:
            matched = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", line)
            if matched and matched.group(1) in assignments:
                key = matched.group(1)
                if key in found:
                    continue
                out.append(assignments[key])
                found.add(key)
                continue
        out.append(line)
    missing = [assignments[key] for key in values if key not in found]
    if missing:
        # Recalculate the section end after removing duplicate assignments.
        new_end = len(out)
        for i in range(start + 1, len(out)):
            if re.match(r"^\s*\[[^\]]+\]\s*(?:#.*)?$", out[i]):
                new_end = i
                break
        out[new_end:new_end] = missing
    updated = "\n".join(out) + ("\n" if text.endswith("\n") or out else "")
    return updated, updated != text


def _set_yaml_root_values(text: str, values: dict[str, str]) -> tuple[str, bool]:
    """Update top-level YAML keys without rewriting unrelated module config."""
    lines = text.splitlines()
    found: set[str] = set()
    out: list[str] = []
    key_re = {key: re.compile(r"^" + re.escape(key) + r"\s*:") for key in values}
    for line in lines:
        key = next((key for key, pattern in key_re.items() if pattern.match(line)), None)
        if key is None:
            out.append(line)
        elif key not in found:
            out.append(f"{key}: {json.dumps(values[key], ensure_ascii=False)}")
            found.add(key)
    missing = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in values.items() if key not in found]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.extend(missing)
    updated = "\n".join(out) + ("\n" if text.endswith("\n") or out else "")
    return updated, updated != text


def _set_toml_language_values(text: str, values: dict[str, str]) -> tuple[str, bool]:
    """Update only BMAD's core language keys, preserving other tables."""
    return _set_toml_table_values(text, "core", values)


def configure_bmad_language(repo: Path, language: str) -> dict[str, Any]:
    """Synchronize CAO's two language keys across BMAD's installed config layers."""
    bmad_root = repo / "_bmad"
    if not bmad_root.is_dir():
        raise ValueError("BMAD is not installed: expected _bmad/ in the project")
    label = "Chinese" if language.lower() in {"zh-cn", "zh-hans"} else language_label(language)
    values = {"communication_language": label, "document_output_language": label}

    modern_candidates = [
        bmad_root / "config.toml",
        bmad_root / "config.user.toml",
        bmad_root / "custom" / "config.toml",
        bmad_root / "custom" / "config.user.toml",
    ]
    modern_installed = any(path.is_file() for path in modern_candidates)
    yaml_candidates = sorted(bmad_root.rglob("config.yaml")) + sorted(bmad_root.rglob("config.yml"))
    targets: list[tuple[Path, str]] = []
    if modern_installed:
        # Keep installer answers and existing overrides consistent. Only the two
        # CAO-managed language keys change; all other project settings survive.
        targets.extend((path, "toml") for path in modern_candidates
                       if path.is_file() or path.name == "config.user.toml")
    elif yaml_candidates:
        targets.extend((path, "yaml") for path in yaml_candidates)
    else:
        # A partially initialized/new BMAD directory still gets a valid modern
        # user-level config rather than failing after installation.
        targets.extend((path, "toml") for path in modern_candidates if path.name == "config.user.toml")

    # If a hybrid project contains both modern TOML and legacy YAML config files,
    # update the legacy files too so old module workflows don't fall back to English.
    if modern_installed and yaml_candidates:
        targets.extend((path, "yaml") for path in yaml_candidates)

    changed: list[str] = []
    unchanged: list[str] = []
    for path, fmt in targets:
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        updated, did_change = (_set_toml_language_values(original, values) if fmt == "toml"
                               else _set_yaml_root_values(original, values))
        if did_change:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(updated, encoding="utf-8")
            changed.append(path.relative_to(repo).as_posix())
        else:
            unchanged.append(path.relative_to(repo).as_posix())
    return {"provider": "bmad", "language": language, "label": label,
            "configured": True, "language_values": values,
            "changed_files": changed, "unchanged_files": unchanged}


def _strip_managed_yaml_context(lines: list[str]) -> list[str]:
    out: list[str] = []
    inside = False
    for line in lines:
        marker = line.strip()
        if marker == LANGUAGE_POLICY_START:
            inside = True
            continue
        if marker == LANGUAGE_POLICY_END:
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


def _update_openspec_context(text: str, language: str) -> tuple[str, bool]:
    label = language_label(language)
    lines = _strip_managed_yaml_context(text.splitlines())
    if language.strip().lower() in {"zh-cn", "zh-hans"}:
        policy_lines = [
            "用户交流语言：简体中文。",
            "所有 OpenSpec 原生产物（变更提案、规格、设计、任务和总结）均使用简体中文。",
            "保留工作流要求必须保持不变的结构化关键字、代码标识符、命令和文件名。",
            "如果用户明确要求某个范围使用其他语言，则该范围遵循用户的明确要求。",
        ]
    elif language.strip().lower() in {"zh-tw", "zh-hant"}:
        policy_lines = [
            "使用者交流語言：繁體中文。",
            "所有 OpenSpec 原生產物（變更提案、規格、設計、任務和摘要）均使用繁體中文。",
            "保留工作流程要求必須保持不變的結構關鍵字、程式碼識別符、命令和檔名。",
            "若使用者明確要求特定範圍使用其他語言，該範圍遵循使用者的明確要求。",
        ]
    else:
        policy_lines = [
            f"User communication language: {label}.",
            f"Write all OpenSpec artifacts (proposal, specs, design, tasks, and summaries) in {label}.",
            "Preserve required structural keywords, code identifiers, commands, and file names.",
            "An explicit user request for another language takes precedence for the requested scope.",
        ]
    block = [f"  {LANGUAGE_POLICY_START}", *[f"  {line}" for line in policy_lines], f"  {LANGUAGE_POLICY_END}"]
    context_index = next((i for i, line in enumerate(lines) if re.match(r"^context\s*:", line)), None)
    if context_index is None:
        if lines and any(line.strip() for line in lines):
            lines.append("")
        lines.extend(["context: |", *block])
        return "\n".join(lines) + "\n", True

    context_line = lines[context_index]
    _, raw_value = context_line.split(":", 1)
    raw_value = raw_value.strip()
    if raw_value in {"|", "|-", "|+", ">", ">-", ">+"} or raw_value == "":
        end = len(lines)
        for i in range(context_index + 1, len(lines)):
            line = lines[i]
            if line.strip() and not line[0].isspace() and re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", line):
                end = i
                break
        lines[end:end] = block
    else:
        value = raw_value
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        lines[context_index:context_index + 1] = [f"context: |", f"  {value}", *block]
    updated = "\n".join(lines) + "\n"
    return updated, updated != text


def _openspec_language_config(repo: Path) -> Path:
    local = repo / "openspec" / "config.yaml"
    if not local.is_file():
        return local
    text = local.read_text(encoding="utf-8")
    match = re.search(r"^store\s*:\s*(.+?)\s*(?:#.*)?$", text, flags=re.MULTILINE)
    if not match:
        return local
    raw_store = match.group(1).strip().strip("\"'")
    if not raw_store:
        return local
    store = Path(raw_store).expanduser()
    if not store.is_absolute():
        store = (repo / store).resolve()
    if store.is_file():
        return store
    for candidate in (store / "openspec" / "config.yaml", store / "config.yaml"):
        if candidate.is_file():
            return candidate
    raise ValueError(f"OpenSpec points to external store {store}, but no config.yaml was found there")


def configure_openspec_language(repo: Path, language: str) -> dict[str, Any]:
    """Add a CAO-managed language context to OpenSpec's native project config."""
    config_path = _openspec_language_config(repo)
    if not config_path.parent.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
    original = config_path.read_text(encoding="utf-8") if config_path.exists() else "schema: spec-driven\n"
    updated, changed = _update_openspec_context(original, language)
    if changed:
        config_path.write_text(updated, encoding="utf-8")
    return {"provider": "openspec", "language": language, "label": language_label(language),
            "configured": True, "config_file": config_path.resolve().as_posix(), "changed": changed}


def _update_managed_markdown_section(text: str, language: str, provider: str) -> tuple[str, bool]:
    label = language_label(language)
    start = f"<!-- {LANGUAGE_POLICY_START} -->"
    end = f"<!-- {LANGUAGE_POLICY_END} -->"
    if language.strip().lower() in {"zh-cn", "zh-hans"}:
        heading = "## 语言约定"
        lines = [
            "- 与用户交流时使用简体中文。",
            f"- {provider} 的所有规格、计划、任务、设计及相关文档均使用简体中文。",
            "- 保留必须保持不变的结构化关键字、代码符号、命令和文件名。",
            "- 如果用户明确要求某个范围使用其他语言，则该范围遵循用户的明确要求。",
        ]
    elif language.strip().lower() in {"zh-tw", "zh-hant"}:
        heading = "## 語言慣例"
        lines = [
            "- 與使用者交流時使用繁體中文。",
            f"- {provider} 的所有規格、計畫、任務、設計及相關文件均使用繁體中文。",
            "- 保留必須保持不變的結構關鍵字、程式碼符號、命令和檔名。",
            "- 若使用者明確要求特定範圍使用其他語言，該範圍遵循使用者的明確要求。",
        ]
    else:
        heading = "## Language policy"
        lines = [
            f"- Communicate with the user in {label}.",
            f"- Write all {provider} specifications, plans, tasks, designs, and related documents in {label}.",
            "- Preserve required structural keywords, code symbols, commands, and file names.",
            "- An explicit user request for another language takes precedence for the requested scope.",
        ]
    section = [start, heading, *lines, end]
    lines = text.splitlines()
    output: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == start:
            inside = True
            continue
        if line.strip() == end:
            inside = False
            continue
        if not inside:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    output.extend(section)
    updated = "\n".join(output) + "\n"
    return updated, updated != text


def configure_speckit_language(repo: Path, language: str) -> dict[str, Any]:
    """Maintain the language rule in Spec Kit's live constitution."""
    if not (repo / ".specify").is_dir():
        raise ValueError("Spec Kit is not installed: expected .specify/ in the project")
    constitution = repo / ".specify" / "memory" / "constitution.md"
    original = constitution.read_text(encoding="utf-8") if constitution.exists() else "# Project Constitution\n"
    updated, changed = _update_managed_markdown_section(original, language, "Spec Kit")
    if changed:
        constitution.parent.mkdir(parents=True, exist_ok=True)
        constitution.write_text(updated, encoding="utf-8")
    return {"provider": "speckit", "language": language, "label": language_label(language),
            "configured": True, "changed": changed, "config_file": constitution.relative_to(repo).as_posix()}


def apply_provider_language(repo: Path, provider: str, language: str | None = None) -> dict[str, Any]:
    actual_language = language or user_language(repo)
    handlers = {
        "bmad": configure_bmad_language,
        "openspec": configure_openspec_language,
        "speckit": configure_speckit_language,
    }
    if provider not in handlers:
        raise ValueError(f"Unsupported SDD provider for language configuration: {provider}")
    return handlers[provider](repo, actual_language)


def apply_installed_sdd_languages(repo: Path, providers: list[str] | None = None) -> dict[str, Any]:
    ensure_language_preference(repo)
    selected = providers if providers is not None else [name for name in SDD_CATALOG if detect_sdd(repo, name)["installed"]]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    language = user_language(repo)
    for provider in selected:
        if provider not in SDD_CATALOG or not detect_sdd(repo, provider)["installed"]:
            continue
        try:
            results.append(apply_provider_language(repo, provider, language))
        except (OSError, UnicodeError, ValueError) as exc:
            failures.append({"provider": provider, "reason": str(exc)})
    return {"language": language, "label": language_label(language), "results": results, "failures": failures}


def cmd_language_show(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    if not (repo / CONFIG_REL).exists():
        print(json.dumps({"ok": True, "initialized": False, "language": {"user": "zh-CN", "label": "Simplified Chinese"}}, ensure_ascii=False, indent=2))
        return 0
    ensure_language_preference(repo)
    current = user_language(repo)
    providers = {name: bool(detect_sdd(repo, name)["installed"]) for name in SDD_CATALOG}
    print(json.dumps({"ok": True, "language": {"user": current, "label": language_label(current), "communication": "follow-user", "sdd_artifacts": "follow-user"}, "installed_sdds": providers}, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_language_set(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    lang = str(args.language or "").strip()
    if not lang:
        raise ValueError("--language must be a non-empty locale or language name, for example zh-CN, en-US, ja-JP")
    cfg = config(repo)
    cfg.setdefault("language", {})["user"] = lang
    save_json(repo / CONFIG_REL, cfg)
    result = apply_installed_sdd_languages(repo)
    print(json.dumps({"ok": not result["failures"], "language": result, "config_file": str(repo / CONFIG_REL)}, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 1


@locked_command
def cmd_language_apply(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    if args.provider and not detect_sdd(repo, args.provider)["installed"]:
        print(json.dumps({"ok": False, "error": "SDD_NOT_INSTALLED", "provider": args.provider}, ensure_ascii=False, indent=2))
        return 2
    provider_filter = [args.provider] if args.provider else None
    result = apply_installed_sdd_languages(repo, provider_filter)
    print(json.dumps({"ok": not result["failures"], "language": result}, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 1


def now() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S', time.localtime())}-{uuid.uuid4().hex[:6]}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def rel(path: Path, repo: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(path, f"*/{p}") for p in patterns)


def requirement_slug(title: str) -> str:
    """Turn a requirement title into a readable filename stem.

    Chinese titles keep their characters; everything unsafe becomes a hyphen.
    """
    slug = re.sub(r"[^\w-]+", "-", str(title).strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:60]


def requirement_filename(number: int, slug: str, date: str) -> str:
    stem = f"{number:03d}-requirement"
    if slug:
        stem = f"{stem}-{slug}"
    return f"{stem}-{date}.md"


def next_requirement_number(repo: Path) -> int:
    """Next requirement number, derived from existing files so it never collides."""
    directory = repo / REQUIREMENT_DIR
    if not directory.is_dir():
        return 1
    highest = 0
    for path in directory.glob("*.md"):
        match = re.match(r"^(\d+)-", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def new_requirement_path(repo: Path, title: str, date: str | None = None) -> Path:
    """Allocate the numbered, dated path for a new requirement document."""
    day = date or time.strftime("%Y-%m-%d", time.localtime())
    return repo / REQUIREMENT_DIR / requirement_filename(next_requirement_number(repo), requirement_slug(title), day)


def requirement_skeleton(title: str, number: str, date: str | None, cfg: dict[str, Any]) -> str:
    """Starter content for a newly allocated requirement document."""
    day = date or time.strftime("%Y-%m-%d", time.localtime())
    language = str((cfg.get("language") or {}).get("user") or "zh-CN")
    if language.lower().startswith("zh"):
        return (
            f"# {title}\n\n"
            f"- 需求编号: {number}\n"
            f"- 创建日期: {day}\n"
            f"- 状态: draft\n\n"
            f"## 需求描述\n\n<!-- 在此描述需求。 -->\n\n"
            "<!-- 技术选择只记录用户指定、仓库既有事实或用户明确授权代选的内容，并注明来源。\n"
            "未明确的技术栈标记为待确认，先询问用户；模型建议不能写成用户已确认。 -->\n\n"
            f"## 验收标准\n\n<!-- 完成时必须满足的条件。 -->\n\n"
            f"## 范围外\n\n<!-- 明确不属于本次需求的内容（可选）。 -->\n"
        )
    return (
        f"# {title}\n\n"
        f"- Requirement ID: {number}\n"
        f"- Created: {day}\n"
        f"- Status: draft\n\n"
        f"## Description\n\n<!-- Describe the requirement here. -->\n\n"
        "<!-- Record technology choices only with user, pre-existing repository or explicit delegation evidence.\n"
        "Leave unspecified choices pending and ask the user; an Agent proposal is not user confirmation. -->\n\n"
        f"## Acceptance criteria\n\n<!-- What must be true when this is done. -->\n\n"
        f"## Out of scope\n\n<!-- What is explicitly not part of this requirement (optional). -->\n"
    )


def requirement_files(repo: Path, cfg: dict[str, Any]) -> list[Path]:
    sdd = cfg.get("sdd") or {}
    files: set[Path] = set()
    # CAO's own numbered requirement documents always count, including in
    # projects initialized before the glob was added to the default config.
    patterns = list(dict.fromkeys(list(sdd.get("requirement_globs") or []) + [str(REQUIREMENT_DIR / "**" / "*.md")]))
    for pattern in patterns:
        for item in glob.glob(str(repo / pattern), recursive=True):
            p = Path(item)
            if p.is_file() and not matches_any(rel(p, repo), sdd.get("exclude_globs") or []):
                files.add(p.resolve())
    return sorted(files)


SNAPSHOT_FORMAT = 3


def code_snapshot(repo: Path, excluded: set[str] | None = None, additional_ignore_dirs: set[str] | None = None) -> str:
    """Versioned content, execution bits and link topology; bounded internal traversal."""
    repo = repo.resolve()
    excluded = excluded or set()
    ignored = SNAPSHOT_IGNORE_DIRS | (additional_ignore_dirs or set())
    h = hashlib.sha256()
    records = []
    visited_entries = 0
    def walk_error(exc):
        raise RuntimeError("SNAPSHOT_READ_FAILED: " + str(exc.filename or repo)) from exc
    def identity(path):
        relative = path.relative_to(repo).as_posix()
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(repo):
                raise RuntimeError("SNAPSHOT_EXTERNAL_SYMLINK: " + relative)
            info = path.lstat()
            target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
            mode = path.stat().st_mode
            kind = "directory" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "special"
            bits = mode & 0o111 if os.name != "nt" else 0
            return relative, {"type": kind, "executable": bits, "link": target}
        except RuntimeError as exc:
            if str(exc).startswith("SNAPSHOT_"): raise
            raise RuntimeError("SNAPSHOT_SYMLINK_CYCLE: " + relative) from exc
        except (OSError, ValueError) as exc:
            raise RuntimeError("SNAPSHOT_READ_FAILED: " + relative) from exc
    for base, dirs, names in os.walk(repo, topdown=True, followlinks=True, onerror=walk_error):
        directory = Path(base)
        try:
            real = directory.resolve(strict=True)
            ancestors = [parent.resolve(strict=True) for parent in directory.parents if parent.is_relative_to(repo)]
            if real in ancestors:
                raise RuntimeError("SNAPSHOT_SYMLINK_CYCLE: " + directory.relative_to(repo).as_posix())
        except OSError as exc:
            raise RuntimeError("SNAPSHOT_READ_FAILED: " + str(directory)) from exc
        dirs[:] = sorted(name for name in dirs if name not in ignored)
        visited_entries += len(dirs) + len(names)
        if visited_entries > 100000 or len(directory.relative_to(repo).parts) > 100:
            raise RuntimeError("SNAPSHOT_SCAN_LIMIT: " + str(directory))
        for name in dirs:
            path = directory / name
            if path.is_symlink():
                relative, meta = identity(path)
                records.append((relative, meta, b""))
        for name in names:
            path = directory / name
            relative = path.relative_to(repo).as_posix()
            if name in ignored or relative in excluded: continue
            relative, meta = identity(path)
            if meta["type"] != "file":
                raise RuntimeError("SNAPSHOT_UNSUPPORTED_INPUT: " + relative)
            try: content = hashlib.sha256(path.read_bytes()).digest()
            except OSError as exc: raise RuntimeError("SNAPSHOT_READ_FAILED: " + relative) from exc
            records.append((relative, meta, content))
    for relative, meta, content in sorted(records):
        h.update(relative.encode()); h.update(b"\0")
        h.update(json.dumps(meta, sort_keys=True).encode()); h.update(b"\0")
        h.update(content); h.update(b"\0")
    return f"v{SNAPSHOT_FORMAT}:" + h.hexdigest()


def verification_timeout(cfg: dict[str, Any]) -> float:
    return normalize_timeout((cfg.get("verification") or {}).get("timeout_seconds", 300.0))


def verification_snapshot(cfg: dict[str, Any], repo: Path | None = None) -> str:
    config_hash = acceptance_hash(verification_scripts(cfg), verification_timeout(cfg))
    files = {}
    if repo is not None:
        for script in verification_scripts(cfg):
            for identity in workflow_command_identities(script["command"]):
                path = Path(identity)
                path = path if path.is_absolute() else repo / path
                # Executable wrappers are standards; their test data and business
                # dependencies remain covered by the ordinary code snapshot.
                if path.is_file() and path.resolve().is_relative_to(repo.resolve()):
                    files[path.relative_to(repo).as_posix()] = sha256_file(path)
                elif path.suffix in {".sh", ".py", ".ps1", ".cmd", ".bat"} and path.is_relative_to(repo):
                    files[path.relative_to(repo).as_posix()] = "missing"
    return native_digest({"config": config_hash, "wrappers": files}) if files else config_hash


def anchor_verification(repo: Path, work: dict, cfg: dict) -> None:
    anchor_acceptance(work, verification_scripts(cfg), verification_timeout(cfg))
    work["acceptance"]["config_sha256"] = verification_snapshot(cfg, repo)


def rules_snapshot(repo: Path, cfg: dict[str, Any]) -> str:
    files: set[Path] = set()
    for pattern in (cfg.get("rules") or {}).get("files") or []:
        for item in glob.glob(str(repo / pattern), recursive=True):
            p = Path(item)
            if not p.resolve().is_relative_to(repo.resolve()):
                raise RuntimeError("SNAPSHOT_EXTERNAL_SYMLINK: " + str(p))
            try:
                if stat.S_ISREG(p.stat().st_mode):
                    files.add(p.resolve())
            except OSError as exc:
                raise RuntimeError("SNAPSHOT_READ_FAILED: " + str(p)) from exc
    h = hashlib.sha256()
    for p in sorted(files):
        rp = rel(p, repo)
        try:
            digest = sha256_file(p)
        except OSError as exc:
            raise RuntimeError("SNAPSHOT_READ_FAILED: " + rp) from exc
        h.update(rp.encode()); h.update(b"\0"); h.update(digest.encode()); h.update(b"\0")
    return h.hexdigest()


def stable_work_id(repo: Path, requirement: Path) -> str:
    key = rel(requirement, repo)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(key).stem).strip("-").lower() or "work"
    return f"{slug}-{sha256_bytes(key.encode())[:8]}"


def work_path(repo: Path, work_id: str) -> Path:
    return repo / WORK_DIR / f"{work_id}.json"


def session_path(repo: Path, session_id: str) -> Path:
    """Return the storage path for a CAO-internal Session ID."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id)
    return repo / SESSION_DIR / f"{safe}.json"


def stable_session_id(host: str, host_session_id: str) -> str:
    """Build a stable internal ID that cannot collide across different hosts."""
    key = f"{host.strip().lower()}\0{host_session_id}"
    return f"session-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


def handoff_history_dir(repo: Path, work_id: str) -> Path:
    """Return the immutable history directory for a Work Item's Handoffs."""
    safe_work_id = re.sub(r"[^A-Za-z0-9._-]+", "-", work_id).strip(".-") or "work"
    return repo / HANDOFF_DIR / safe_work_id


def handoff_record_path(repo: Path, work_id: str, handoff_id: str) -> Path:
    safe_handoff_id = re.sub(r"[^A-Za-z0-9._-]+", "-", handoff_id).strip(".-") or "handoff"
    return handoff_history_dir(repo, work_id) / f"{safe_handoff_id}.json"


def _handoff_time_key(handoff: dict[str, Any]) -> tuple[float, int, str]:
    """Normalize timestamps and per-work sequence numbers for stable sorting."""
    value = handoff.get("created_at", 0)
    timestamp = 0.0
    if isinstance(value, (int, float)):
        timestamp = float(value)
    else:
        try:
            timestamp = float(str(value))
        except (TypeError, ValueError):
            try:
                from datetime import datetime
                timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                timestamp = 0.0
    try:
        sequence = int(handoff.get("sequence", 0))
    except (TypeError, ValueError):
        sequence = 0
    return timestamp, sequence, str(handoff.get("id") or "")


def list_handoff_records(repo: Path, work_id: str | None = None) -> list[dict[str, Any]]:
    """Read immutable Handoff records from the per-Work-Item history layout."""
    root = repo / HANDOFF_DIR
    if not root.is_dir():
        return []

    if work_id:
        paths = list(handoff_history_dir(repo, work_id).glob("*.json"))
    else:
        paths = [path for history_dir in root.iterdir() if history_dir.is_dir()
                 for path in history_dir.glob("*.json")]

    records: dict[str, dict[str, Any]] = {}
    for path in paths:
        record = load_json(path, None)
        if not isinstance(record, dict):
            continue
        record_work_id = record.get("work_item")
        if not isinstance(record_work_id, str) or (work_id and record_work_id != work_id):
            continue
        handoff_id = record.get("id")
        if not isinstance(handoff_id, str) or not handoff_id:
            continue
        records[handoff_id] = record
    return sorted(records.values(), key=_handoff_time_key, reverse=True)


def latest_handoff(repo: Path, work_id: str) -> dict[str, Any] | None:
    """Return the newest immutable Handoff record for a Work Item."""
    records = list_handoff_records(repo, work_id)
    return records[0] if records else None

def find_handoff(repo: Path, handoff_id: str) -> dict[str, Any] | None:
    return next((item for item in list_handoff_records(repo) if item.get("id") == handoff_id), None)


def empty_index() -> dict[str, Any]:
    return {"version": 1, "active_work": None, "work_items": []}


def load_index(repo: Path) -> dict[str, Any]:
    return recover_index(repo)


@locked_repo
def recover_index(repo: Path) -> dict[str, Any]:
    """Rebuild a missing, corrupt or stale projection from authoritative Work Items."""
    works = [load_json(p, None) for p in sorted((repo / WORK_DIR).glob("*.json"))]
    works = [work for work in works if isinstance(work, dict)]
    for work in works:
        if work.get("pending_events"):
            drain_events(repo, work)
    active = [work["id"] for work in works if work.get("status") == "active"]
    projection = {"version": 1, "active_work": active[0] if len(active) == 1 else None,
                  "work_items": [work_summary(work) for work in works]}
    try:
        index = load_json(repo / INDEX_REL, None)
    except (json.JSONDecodeError, UnicodeError):
        index = None
    if not isinstance(index, dict) or any(index.get(key) != value for key, value in projection.items()):
        save_index(repo, projection)
        index = projection
    if len(active) > 1:
        raise RuntimeError("MULTIPLE_ACTIVE_WORK_ITEMS: " + ",".join(active))
    return index


def save_index(repo: Path, index: dict[str, Any]) -> None:
    index["updated_at"] = now()
    save_json(repo / INDEX_REL, index)


def _read_work(repo: Path, work_id: str) -> dict[str, Any] | None:
    p = work_path(repo, work_id)
    return load_json(p, None) if p.exists() else None


@locked_repo
def load_work(repo: Path, work_id: str) -> dict[str, Any] | None:
    work = _read_work(repo, work_id)
    if work and work.get("pending_events"):
        drain_events(repo, work)
    if work:
        for migration in relocate_openspec_archive(repo, work, now()):
            queue_event(work, {"event": "sdd.native_paths.relocated", "source": "openspec",
                               "work_item_id": work["id"], **migration})
        if work.get("pending_events"):
            save_work(repo, work)
    return work


def sync_index_for_work(repo: Path, work: dict[str, Any]) -> None:
    """Project committed authoritative records; never trust an old active pointer."""
    recover_index(repo)


def other_active_work(repo: Path, wid: str) -> list[str]:
    return [work["id"] for path in sorted((repo / WORK_DIR).glob("*.json"))
            if isinstance(work := load_json(path, None), dict)
            and work.get("status") == "active" and work["id"] != wid]


def _activation_denied(repo: Path, wid: str) -> bool:
    others = other_active_work(repo, wid)
    if others:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_ACTIVE",
                          "active_work_items": others,
                          "next": "Suspend the active work first, or explicitly switch with `cao start --requirement <path> --replace`."},
                         ensure_ascii=False, indent=2))
    return bool(others)


def _persist_work(repo: Path, work: dict[str, Any]) -> None:
    """Write authority while holding the transaction, without recursively draining."""
    current = _read_work(repo, work["id"])
    revision = int((current or {}).get("state_revision", 0))
    if int(work.get("state_revision", 0)) != revision:
        raise RuntimeError("STALE_WORK_REVISION")
    # Queue maintenance must still work for existing legacy conflicts; new activation cannot.
    if work.get("status") == "active" and (current or {}).get("status") != "active":
        if others := other_active_work(repo, work["id"]):
            raise RuntimeError("WORK_ITEM_ALREADY_ACTIVE: " + ",".join(others))
    if current and (current.get("integrity_version") != 2 or
                    ((current.get("acceptance") or {}).get("version") != 2
                     and (work.get("acceptance") or {}).get("version") == 2)):
        backup_once(repo, work_path(repo, work["id"]))
    saved = {"pending_events": [], **work, "integrity_version": 2,
             "state_revision": revision + 1, "updated_at": now()}
    save_json(work_path(repo, work["id"]), saved)
    # Advance the caller only after authority commits.
    work.update(saved)


@locked_repo
def save_work(repo: Path, work: dict[str, Any]) -> None:
    current = _read_work(repo, work["id"])
    generation = int((current or {}).get("execution_generation", 0))
    if current and any(current.get(key) != work.get(key) for key in ("status", "requirement", "acceptance")):
        generation += 1
    work["execution_generation"] = max(generation, int(work.get("execution_generation", 0)))
    _persist_work(repo, work)
    if work.get("pending_events"):
        drain_events(repo, work)
    sync_index_for_work(repo, work)


def queue_event(work: dict, event: dict) -> None:
    key = str(event.get("idempotency_key") or uuid.uuid4().hex)
    work.setdefault("pending_events", []).append({**event,
        "event_id": event.get("event_id") or "evt-" + key, "idempotency_key": key})


@locked_repo
def drain_events(repo: Path, work: dict) -> None:
    fresh = _read_work(repo, work["id"])
    if fresh is None:
        raise RuntimeError("WORK_ITEM_NOT_FOUND")
    pending = fresh.get("pending_events") or []
    for event in pending:
        _append_lifecycle_event(repo, event)
    if pending:
        fresh["pending_events"] = []
        _persist_work(repo, fresh)
    work.clear()
    work.update(fresh)


@locked_repo
def mutate_work(repo: Path, wid: str, update: Callable[[dict], None]) -> dict:
    work = load_work(repo, wid)
    if work is None:
        raise RuntimeError("WORK_ITEM_NOT_FOUND")
    update(work)
    save_work(repo, work)
    return work


def _refresh_work(repo: Path, work: dict[str, Any]) -> None:
    """Helpers modify fresh authority, not a caller's potentially stale snapshot."""
    fresh = load_work(repo, work["id"])
    if fresh is None:
        raise RuntimeError("WORK_ITEM_NOT_FOUND")
    work.clear()
    work.update(fresh)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _native_md_files(repo: Path, provider: str, root: Path, *, recursive: bool = True) -> list[Path]:
    dirs = [directory for directory in provider_output_dirs(provider)
            if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
    files = []
    for path in (root.rglob("*.md") if recursive else root.glob("*.md")):
        try:
            owned = provider_artifact(repo, str(path), dirs)
        except (ValueError, OSError, RuntimeError):
            continue
        if owned.is_file() and owned not in files:
            files.append(owned)
    return sorted(files)


def _native_candidates(repo: Path, provider: str, work: dict[str, Any], native_ref: str) -> tuple[list[Path], Path | None]:
    """Return candidate artifact paths scoped to this Work Item, never the whole SDD tree by default."""
    sdd = work.get("sdd") or {}
    binding = sdd.get("spec_binding")
    if binding is not None:
        # The explicit Work-bound SPEC is authority even when its mutable native
        # status makes acceptance hashes stale. Never silently rebind those hashes.
        if (not isinstance(binding, dict) or binding.get("provider") != provider
                or binding.get("requirement_sha256") != (work.get("requirement") or {}).get("sha256")):
            return [], None
        spec_ref = binding.get("spec")
        if not isinstance(spec_ref, dict):
            return [], None
        native_ref = str(spec_ref.get("path") or "")
        if not native_ref:
            return [], None
    if native_ref:
        try:
            dirs = [directory for directory in provider_output_dirs(provider)
                    if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
            ref_path = provider_artifact(repo, native_ref, dirs)
        except (ValueError, OSError, RuntimeError):
            return [], None
        if ref_path.is_file():
            if provider == "bmad" or ref_path.name.lower() == "tasks.md":
                return [ref_path], ref_path
            parent = ref_path.parent
            feature_root = repo.resolve() / ("openspec/changes" if provider == "openspec" else "specs")
            if ref_path.is_relative_to(feature_root):
                parts = ref_path.relative_to(feature_root).parts
                if len(parts) >= 2:
                    parent = (feature_root / parts[0] / parts[1]
                              if provider == "openspec" and parts[0] == "archive" and len(parts) >= 3
                              else feature_root / parts[0])
            return _native_md_files(repo, provider, parent, recursive=False), parent
        if ref_path.is_dir():
            return _native_md_files(repo, provider, ref_path), ref_path
        return [], None  # A broken explicit association must not adopt another file.
    requirement_path = str((work.get("requirement") or {}).get("path") or "")
    title = str(work.get("title") or "")
    identity_slug = lambda value: re.sub(r"[^\w]+", "-", value.casefold(), flags=re.UNICODE).replace("_", "-").strip("-")
    slugs = [identity_slug(Path(requirement_path).stem), identity_slug(title)]
    slugs = [value for value in dict.fromkeys(slugs) if value]

    if provider == "bmad":
        root = repo / "_bmad-output/implementation-artifacts"
        if not root.is_dir():
            return [], None
        all_specs = sorted((p for p in _native_md_files(repo, provider, root, recursive=False)
                            if p.parent == root.resolve() and p.name.startswith("spec-")),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        def bmad_matches(path: Path) -> bool:
            file_slug = identity_slug(path.stem.removeprefix("spec-"))
            return bool(file_slug and any(file_slug == wanted or file_slug in wanted or wanted in file_slug for wanted in slugs))
        selected = [p for p in all_specs if bmad_matches(p)]
        # Prefer an exact identity match before considering partial slug matches.
        # When more than one candidate remains, fail closed: none of them may
        # drive lifecycle state until the current Work Item is unambiguously bound.
        exact = [p for p in selected if identity_slug(p.stem.removeprefix("spec-")) in slugs]
        if len(exact) == 1:
            return exact, exact[0].parent
        if exact:
            return [], None
        if len(selected) == 1:
            return selected, selected[0].parent
        return [], None

    root = repo / ("openspec/changes" if provider == "openspec" else "specs")
    if not root.is_dir():
        return [], None
    dirs = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != "archive"]
    matching = [p for p in dirs if any(identity_slug(p.name) == wanted or identity_slug(p.name) in wanted or wanted in identity_slug(p.name) for wanted in slugs)]
    exact = [p for p in matching if identity_slug(p.name) in slugs]
    if len(exact) == 1:
        matching = exact
    elif exact or len(matching) != 1:
        return [], None
    feature_dir = matching[0]
    return _native_md_files(repo, provider, feature_dir, recursive=False), feature_dir


def _read_native_status(provider: str, candidates: list[Path]) -> tuple[str | None, str | None, str | None, dict[str, Any] | None]:
    """Extract only explicit machine status or task-checkbox evidence from work-specific files."""
    if provider == "bmad":
        states = []
        for path in candidates:
            try:
                content = path.read_text(encoding="utf-8-sig")[:65536]
            except (OSError, UnicodeError):
                continue
            if not content.startswith("---"):
                continue
            end = content.find("\n---", 3)
            if end < 0:
                continue
            frontmatter = content[3:end]
            entries = re.findall(r"(?im)^[ \t]*status[ \t]*:[ \t]*(.*)$", frontmatter)
            if len(entries) != 1:
                continue
            match = re.fullmatch(r"(?:'([a-z_-]+)'|\"([a-z_-]+)\"|([a-z_-]+))[ \t]*(?:#.*)?", entries[0], re.I)
            if match:
                raw = next(value for value in match.groups() if value is not None).lower().replace("-", "_")
                states.append((raw, path.name, hashlib.sha256(content.encode("utf-8")).hexdigest(), None))
        return states[0] if len(states) == 1 else (None, None, None, None)

    task_files = [p for p in candidates if p.name.lower() == "tasks.md"]
    if len(task_files) != 1:
        return None, None, None, None
    tasks = task_files[0]
    try:
        content = tasks.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None, None, None, None
    tasks_read = _checkbox_tasks(content)
    checked = sum(task["done"] for task in tasks_read)
    unchecked = len(tasks_read) - checked
    total = checked + unchecked
    if not total:
        return None, tasks.name, hashlib.sha256(content.encode("utf-8")).hexdigest(), None
    raw = "completed" if checked == total else "in_progress" if checked > 0 else "ready_for_dev"
    return raw, tasks.name, hashlib.sha256(content.encode("utf-8")).hexdigest(), {"checked": checked, "unchecked": unchecked, "total": total}


def _canonical_native_state(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.lower().replace("-", "_").strip()
    if value in {"ready_for_dev", "ready", "ready_to_implement", "approved"}:
        return "ready"
    if value in {"in_progress", "in_process", "implementing", "implementation", "in_review", "in_reviewing"}:
        return "in_progress" if value not in {"in_review", "in_reviewing"} else "in_review"
    if value in {"done", "completed", "complete", "closed"}:
        return "completed"
    if value in {"blocked", "failed", "cancelled", "canceled"}:
        return value
    return None


def native_sync_state(repo: Path, work: dict[str, Any]) -> dict[str, Any]:
    """Read-only source/observation diagnostics, separate from acceptance readiness."""
    sdd = work.get("sdd") or {}
    provider = str(sdd.get("provider") or "").lower()
    if provider not in SDD_CATALOG or work.get("status") == "completed":
        return {"state": "not_applicable"}
    plan = provider_verification_plan_state(repo, work)
    if plan["state"] != "not_applicable":
        if plan["state"] == "blocked":
            return {"state": "source_unavailable", "source": None, "next": plan["next"], "verification_plan": plan}
        progress = bmad_progress_observation(repo, work, plan)
        return {"state": "observed" if plan["canonical"] else "status_unavailable", "source": ", ".join(plan["observations"]),
                "raw": plan["raw"], "canonical": plan["canonical"],
                "next": plan.get("next"), "verification_plan": plan, **progress}
    candidates, _ = _native_candidates(repo, provider, work, str(sdd.get("native_ref") or ""))
    if not candidates:
        explicit = sdd.get("spec_binding") is not None or bool(sdd.get("native_ref"))
        return {"state": "source_unavailable" if explicit else "unbound", "source": None,
                "next": "Review the Work's native source path and provider. For light, bind its actual SPEC with `cao sdd spec bind`; for other profiles, use the current Work's native reference. Do not guess another file or restart intake."}
    raw, source, _hash, _counts = _read_native_status(provider, candidates)
    canonical = _canonical_native_state(raw)
    path = next((p.relative_to(repo).as_posix() for p in candidates if p.name == source), None)
    return {"state": "observed" if canonical else "status_unavailable", "source": path,
            "raw": raw, "canonical": canonical,
            "next": None if canonical else "Follow the installed provider's native status/task conventions; do not infer execution from file existence."}


def bmad_progress_observation(repo: Path, work: dict, plan: dict) -> dict:
    decision = (work.get('sdd') or {}).get('route_decision') or {}
    if decision.get('provider') != 'bmad' or not decision.get('primary_artifact'):
        return {}
    try:
        path = native_owned_file(repo, decision['primary_artifact'], ['_bmad-output'])
    except (ValueError, OSError, RuntimeError):
        return {'progress_conflict': {'reason': 'bound SPEC unavailable'},
                'next': 'Restore the bound native SPEC; do not adopt another file or infer execution from plan state.'}
    raw, source, sha, counts = _read_native_status('bmad', [path])
    canonical = _canonical_native_state(raw)
    if canonical and plan.get('canonical') and canonical != plan['canonical']:
        result = {'progress_conflict': {'spec': decision['primary_artifact'], 'spec_state': canonical,
                                       'plan_state': plan['canonical']},
                  'next': 'Synchronize the active SPEC and this requirement\'s current Story using the native status steps; preserve other Stories. Plan completion still requires ALL bound items done.'}
        # Show actual native implementation/review activity without promoting an
        # unfinished plan to completion or rewriting any provider-owned file.
        if canonical in {'in_progress', 'in_review'} and plan['canonical'] in {'ready', 'completed'}:
            result.update(canonical=canonical, raw=raw)
        return result
    return {}


def native_route_state(repo: Path, work: dict) -> dict:
    sdd = work.get("sdd") or {}
    provider = sdd.get("provider")
    # Installed Build is an agent workflow with native routing and checkpoints.
    # Historical providers without this capability retain their native contracts.
    required = bool(sdd.get("native_route_required")) or provider == "bmad" and any(
        item["name"] == "bmad-build" for item in provider_native_skills(repo, provider))
    return route_state(repo, work, provider_output_dirs(provider), required)


def route_preparation_action(repo: Path, work: dict, state: dict) -> dict:
    command = cao_invocation()["command"]
    provider = (work.get("sdd") or {}).get("provider")
    target = route_declaration_path(work)
    common = {"stage": "execution", "reason": state["reason"], "declaration_path": target}
    if state["reason"] != "NATIVE_ROUTE_REQUIRED":
        kind = "human" if state["reason"] in {"NATIVE_SPEC_APPROVAL_REQUIRED", "NATIVE_SPEC_CLARIFICATION_REQUIRED", "NATIVE_IMPLEMENTATION_PAUSED"} else "agent"
        return action(kind, state["next"], **common)
    skills = provider_native_skills(repo, provider)
    workflow = (work.get("sdd") or {}).get("workflow") or {}
    selected = workflow_skill_identity(repo, provider, workflow.get("entrypoint") or "")
    skill = next((item for item in skills if item["name"] == (selected or "bmad-build")), None)
    instruction = technology_instruction(command) + (f"Read the complete installed Skill `{repo / skill['rel_path']}`; invoke `{skill['name']}` "
                   "and use its exact renderer only when required. Renderer success loads instructions, not completed execution. "
                   if skill else "Read the selected provider's installed native workflow instructions. ")
    if (work.get("path_assessment") or {}).get("profile") != "light" and not workflow.get("name"):
        common["reason"] = "NATIVE_WORKFLOW_SELECTION_REQUIRED"
        entry = skill["name"] if skill else "<installed-native-workflow>"
        instruction += (f'Select the actual workflow with `{command} sdd workflow select --name "{entry}" '
                        f'--entrypoint "{entry}" --work {work["id"]}` before preparing the route. ')
    else:
        ref = (work.get("sdd") or {}).get("native_ref")
        try:
            source = native_owned_file(repo, ref, provider_output_dirs(provider)) if ref else None
            has_spec = bool(source and source.is_file() and
                            (provider != "bmad" or native_build_route(source) in {"dispatch", "oneshot"}))
        except (ValueError, OSError, RuntimeError):
            has_spec = False
        if not has_spec:
            common["reason"] = "NATIVE_SPEC_PREPARATION_REQUIRED"
            instruction += (f"Continue the selected native workflow `{workflow.get('name') or 'bmad-build'}` and its planning prerequisites; "
                            "do not switch workflows merely because the route is missing. Execute only native planning/spec preparation now, "
                            "then immediately bind the actual primary Build SPEC "
                            f"with `{command} sdd artifact bind --artifact <actual-native-spec.md>`. ")
        else:
            instruction += f"Review the bound native SPEC `{ref}` and actual routing evidence. "
    instruction += (f"Once the workflow and SPEC exist, run `{command} sdd route prepare` to create/reuse `{target}` "
                    "(CAO input, separate from native artifacts and managed decisions). Fill the actual route, reason, version, artifacts and omissions. "
                    f"Use `{command} sdd route rule --source <native-rule-file> --start-line <first> --end-line <last>` "
                    "to write exact quotes and hashes; do not summarize or manually reconstruct evidence. "
                    f"Run `{command} sdd route check --declaration {target}` and repair ALL diagnostic fields before "
                    f"`{command} sdd route propose --declaration {target}`. --declaration is a file path, not inline JSON. "
                    "Read the bundled native route contract for all fields; for BMAD route is direct-build/full-planning "
                    "and build_route is dispatch/oneshot. Do not guess route names, rules or artifacts. "
                    "Do not begin business implementation or restart intake.")
    return action("agent", instruction, **common)


def implementation_preflight(repo: Path, work: dict) -> dict:
    restart = restart_state(repo, detect_current_host())
    if not restart["ready"]:
        return restart
    if not work_sdd_ready(repo, work)[0]:
        provider = (work.get("sdd") or {}).get("provider")
        return {"ready": False, "reason": "SDD_NOT_INSTALLED", "next": f"Restore the Work's provider with `cao sdd install --provider {provider} --yes`; do not restart intake."}
    for state in (verification_baseline_state(repo, work), path_assessment_state(repo, work)):
        if not state["ready"]:
            next_step = state.get("next") or path_assessment_instruction(work)
            if state.get("reason") == "requirement changed since work started":
                next_step = completion_next_instruction(repo, work, state) + " " + next_step
            return {**state, "next": next_step}
    route = native_route_state(repo, work)
    return {**route, "next": route_preparation_action(repo, work, route)["instruction"]} if not route["ready"] else route


def observe_implementation_read(repo: Path, work: dict, raw: dict, session_id: str) -> None:
    if _event_key(raw, 'tool_name', 'toolname', 'name').lower() != 'read' or raw.get('isError'):
        return
    try:
        contract = implementation_contract(repo, work)
    except (ValueError, OSError, RuntimeError):
        return
    if not contract:
        return
    content = '\n'.join(_collect_values_for_keys(raw.get('content') or [], {'text'}))
    source = repo / contract['path']
    if source.read_text().strip() not in content:
        return  # A partial read is not evidence of loading the complete native step.
    targets = _collect_path_strings(raw.get('input') or raw.get('tool_input') or {})
    if not any((Path(value) if Path(value).is_absolute() else repo / value).resolve() == source for value in targets):
        return
    work['sdd']['implementation_preparation'] = {'contract': contract, 'route_id': work['sdd']['route_decision']['id'],
                                               'session_id': session_id, 'observed_at': now()}
    save_work(repo, work)


def implementation_write_guard(repo: Path, work: dict, raw: dict, session_id: str, kind: str) -> dict:
    state = implementation_state(repo, work)
    if state['ready']:
        return state
    if state['reason'] != 'NATIVE_IMPLEMENTATION_HANDOFF_REQUIRED':
        return state
    contract = implementation_contract(repo, work)
    preparation = work['sdd'].get('implementation_preparation') or {}
    if preparation.get('contract') != contract or preparation.get('route_id') != work['sdd']['route_decision']['id']:
        return state
    spec = native_owned_file(repo, work['sdd']['route_decision']['primary_artifact'], ['_bmad-output'])
    text = spec.read_text()
    status, _, _, _ = _read_native_status('bmad', [spec])
    if status not in {'in_progress', 'in_review', 'done'} or not re.search(r'(?m)^baseline_commit:\s*\S+', text):
        return {'ready': False, 'reason': 'NATIVE_IMPLEMENTATION_STATUS_REQUIRED',
                'next': 'Before product implementation, use native step-03 to set SPEC in-progress and baseline_commit (HEAD or NO_VCS), and synchronize the current Story.'}
    tools = raw.get('host_tools')
    unavailable = isinstance(tools, list) and not {'subagent', 'subagents_enable'}.intersection(tools)
    mode = 'subagent' if kind == 'subagent' else 'fallback' if unavailable else 'native-override' if not contract['subagent_default'] else None
    if mode is None:
        return state
    work['sdd']['native_implementation'] = {'contract': contract, 'route_id': work['sdd']['route_decision']['id'],
        'requirement_sha256': work['requirement']['sha256'], 'session_id': session_id, 'mode': mode, 'observed_at': now()}
    queue_event(work, {'event': 'sdd.implementation.observed', 'work_item_id': work['id'],
                       'mode': mode, 'session_id': session_id, 'occurred_at': now()})
    save_work(repo, work)
    return {'ready': True}


def approval_request_contexts(repo: Path, work: dict, host: str | None = None) -> list[dict[str, str]]:
    contexts = [{"host": session["host"], "session_id": session["host_session_id"]}
                for session in list_sessions(repo)
                if session.get("work_item_id") == work["id"]
                and session.get("kind") == "primary" and session.get("status") == "active"]
    identity = host_session_identity(host)
    if identity:
        context = {"host": identity[0], "session_id": identity[1]}
        if context not in contexts:
            contexts.append(context)
    return contexts


def expose_approval_context(repo: Path, work: dict | None, host: str, session_id: str) -> None:
    if not work or work.get("status") != "active":
        return
    changed = False
    for record in ((work.get("sdd") or {}).get("spec_approval") or {},
                   work.get("verification_review") or {}):
        if record.get("status") == "pending" or record.get("stop_after_approval"):
            changed = bind_request_context(record, work["id"], host, session_id) or changed
    if changed:
        save_work(repo, work)


def ensure_verification_review(repo: Path, work: dict, cfg: dict) -> dict:
    """Reuse only a current Work-bound request; legacy approval is not transferable."""
    current_hash = verification_snapshot(cfg, repo)
    review = work.get("verification_review") or {}
    if (review.get("config_sha256") != current_hash or review.get("work_item_id") != work["id"]
            or "contexts" not in review):
        review = {"id": new_id("baseline-review"), "status": "pending",
                  "work_item_id": work["id"], "contexts": approval_request_contexts(repo, work),
                  "config_sha256": current_hash, "requested_at": now(),
                  "previous_config_sha256": (work.get("acceptance") or {}).get("config_sha256"),
                  "scripts": verification_scripts(cfg)}
        work["verification_review"] = review
        save_work(repo, work)
    return review


@locked_command
def cmd_sdd_route(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") != "active":
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    if args.route_action == "show":
        print(json.dumps({"ok": True, "route": (work.get("sdd") or {}).get("route_decision"),
                          "history": (work.get("sdd") or {}).get("route_history", []),
                          "state": native_route_state(repo, work)}, ensure_ascii=False)); return 0
    try:
        if args.route_action in {"prepare", "rule"}:
            from scripts.route_input_commands import dispatch
            result = dispatch(args, sys.modules[__name__], repo, work)
            print(json.dumps(result, ensure_ascii=False)); return 0
        args.declaration = args.declaration or route_declaration_path(work)
        if args.declaration.lstrip().startswith(("{", "[")):
            raise ValueError("NATIVE_ROUTE_DECLARATION_PATH_REQUIRED")
        if not path_assessment_state(repo, work)["ready"]:
            raise ValueError("PATH_ASSESSMENT_REQUIRED")
        declaration = repo_file(repo, args.declaration)
        if not declaration.is_file():
            raise ValueError("NATIVE_ROUTE_DECLARATION_FILE_MISSING")
        raw = read_route_declaration(declaration)
        provider = work["sdd"]["provider"]
        decision = normalize_route_decision(repo, work, raw, provider_output_dirs(provider),
                                            provider_native_skills(repo, provider))
        if args.route_action == "check":
            print(json.dumps({"ok": True, "status": "validated", "declaration_path": rel(declaration, repo),
                              "decision": decision, "next": "Declaration fields are valid; propose it to record the decision. Native approval and execution remain required."}, ensure_ascii=False)); return 0
        old = work["sdd"].get("route_decision")
        if old and all(old.get(key) == value for key, value in decision.items()):
            print(json.dumps({"ok": True, "status": "unchanged", "route": old,
                              "implementation": native_route_state(repo, work)}, ensure_ascii=False)); return 0
        decision.update(id=new_id("route"), accepted_at=now(), assessed_by="agent",
                        declaration={"path": rel(declaration, repo), "sha256": sha256_file(declaration)},
                        supersedes=old.get("id") if old else None,
                        previous_route=old.get("route") if old else None)
        work["sdd"].setdefault("route_history", []).append(decision)
        work["sdd"]["route_decision"] = decision
        work["sdd"]["native_ref"] = decision["primary_artifact"]
        work["sdd"].pop("spec_approval", None)
        work["sdd"].pop("spec_task_scope", None)
        work["verification"] = {}
        work["gate_status"] = {}
        # Reselecting a route must never retain a completed workflow certificate.
        workflow = work["sdd"].get("workflow")
        if workflow:
            workflow["status"] = "selected"
            for key in ("execution", "completed_at", "artifact_hashes", "failure_exception"):
                workflow.pop(key, None)
        for artifact in decision["required_artifacts"]:
            ref = artifact_reference(repo, artifact["path"], role=artifact["role"])
            refs = work["sdd"].setdefault("artifacts", [])
            refs[:] = [r for r in refs if r.get("path") != ref["path"]]
            refs.append(ref)
        queue_event(work, {"event": "sdd.route.accepted", "work_item_id": work["id"],
                           "source": "cao", "decision": decision, "occurred_at": now()})
        save_work(repo, work)
        reconcile_native_sdd_state(repo, work)
        state = native_route_state(repo, work)
        print(json.dumps({"ok": True, "route": decision, "implementation": state,
                          "next": state.get("next") or "Follow the accepted native route; synchronize progress with `cao sdd sync`."}, ensure_ascii=False)); return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        code = "NATIVE_ROUTE_DECLARATION_JSON_INVALID" if isinstance(exc, json.JSONDecodeError) else str(exc)
        current = current_work_action(repo, work, check_completion=False)
        result = {"ok": False, "error": code, "next": current["instruction"],
                  "guidance": agent_guidance(repo, work, next_action=current)}
        if getattr(exc, "diagnostic", None):
            result["diagnostic"] = exc.diagnostic
            result["diagnostics"] = exc.diagnostics
            result["next"] = ("Repair every listed diagnostic field; hashes and quotes are checked independently. "
                              "Use `cao sdd route prepare --from <existing-declaration>` for a legacy input, then "
                              "`cao sdd route rule --source <actual-native-file> --start-line <first> --end-line <last> --index <rule-index>` "
                              "to replace the named rule with literal source evidence. Run `cao sdd route check` before proposing again. "
                              "Do not resubmit unchanged evidence or rewrite native rules to make a quote match.")
            result["guidance"] = agent_guidance(repo, work, next_action={**current, "reason": "NATIVE_ROUTE_RULE_EVIDENCE_MISMATCH"})
        if code.startswith("NATIVE_ROUTE_DECLARATION_"):
            result["reason"] = ("--declaration expects a path to an existing JSON file, not inline JSON. "
                                "Use the Work-specific CAO input path after native preparation; "
                                "a rejected write did not create the file.")
        if work["sdd"]["provider"] in SDD_CATALOG:
            result["declaration_path"] = route_declaration_path(work)
        if code == "NATIVE_ROUTE_NAME_INVALID":
            result["allowed_routes"] = ["direct-build", "full-planning"]
            result["allowed_build_routes"] = ["dispatch", "oneshot"]
        print(json.dumps(result, ensure_ascii=False)); return 2


@locked_command
def cmd_sdd_artifact_bind(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") != "active":
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    try:
        path = native_owned_file(repo, args.artifact, provider_output_dirs(work["sdd"]["provider"]))
        value = rel(path, repo)
        decision = work["sdd"].get("route_decision")
        if decision and decision["primary_artifact"] != value:
            raise ValueError("NATIVE_ROUTE_SOURCE_CHANGE_REQUIRES_DECISION")
        work["sdd"]["native_ref"] = value
        refs = work["sdd"].setdefault("artifacts", [])
        refs[:] = [r for r in refs if r.get("path") != value]
        refs.append(artifact_reference(repo, value, role="native-progress-source"))
        queue_event(work, {"event": "sdd.artifact.bound", "work_item_id": work["id"],
                           "path": value, "sha256": sha256_file(path), "occurred_at": now()})
        save_work(repo, work)
        reconcile_native_sdd_state(repo, work)
        print(json.dumps({"ok": True, "native_sync": native_sync_state(repo, work),
                          "implementation": native_route_state(repo, work)}, ensure_ascii=False)); return 0
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); return 2


@locked_command
def cmd_sdd_approval(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") != "active":
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    if args.approval_action == "request":
        decision = work["sdd"].get("route_decision")
        state = native_route_state(repo, work)
        if not decision or (not state["ready"] and state["reason"] != "NATIVE_SPEC_APPROVAL_REQUIRED"):
            print(json.dumps({"ok": False, "error": state.get("reason", "NATIVE_ROUTE_REQUIRED")})); return 2
        path = native_owned_file(repo, decision["primary_artifact"], provider_output_dirs(work["sdd"]["provider"]))
        existing = work["sdd"].get("spec_approval") or {}
        if (existing.get("route_decision_id") == decision["id"]
                and existing.get("intent_sha256") == intent_hash(path)
                and existing.get("work_item_id") == work["id"]
                and existing.get("status") in {"pending", "approved"}):
            print(json.dumps({"ok": True, "status": "unchanged", "approval": existing,
                              "implementation": state}, ensure_ascii=False)); return 0
        approval = {"id": new_id("approval"), "status": "pending", "requested_at": now(),
                    "work_item_id": work["id"], "contexts": approval_request_contexts(repo, work),
                    "questions": [],
                    "path": decision["primary_artifact"], "intent_sha256": intent_hash(path),
                    "requirement_sha256": work["requirement"]["sha256"], "route_decision_id": decision["id"]}
        work["sdd"]["spec_approval"] = approval
        queue_event(work, {"event": "sdd.spec.approval_requested", "work_item_id": work["id"],
                           "approval": approval, "occurred_at": now()})
        save_work(repo, work)
    approval = work["sdd"].get("spec_approval") or {}
    next_step = ("Approval is recorded; follow the native workflow from its current stage and synchronize progress."
                 if approval.get("status") == "approved" else
                 "Resolve native Open Questions first. Present the complete spec summary and path. Ask the user to choose 批准并继续 / 批准并停止 (Approve and continue / Approve and stop), or review. Send the approval phrase as a standalone message without appended explanations. The host records the actual reply; answering implementation options is not approval.")
    if approval.get("stop_after_approval"):
        next_step = "The user approved and stopped; wait for explicit continuation of implementation."
    print(json.dumps({"ok": True, "approval": approval or None, "next": next_step}, ensure_ascii=False)); return 0


@locked_command
def cmd_sdd_sync(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"})); return 2
    reconcile_native_sdd_state(repo, work)
    print(json.dumps({"ok": True, "work_item": work_summary(work),
                      "native_sync": native_sync_state(repo, work),
                      "implementation": native_route_state(repo, work)}, ensure_ascii=False)); return 0


@locked_repo
def _append_lifecycle_event(repo: Path, event: dict[str, Any]) -> bool:
    """Append under the repository lock; keyless calls are independent operations."""
    key = str(event.get("idempotency_key") or uuid.uuid4().hex)
    event = {**event, "idempotency_key": key, "event_id": event.get("event_id") or "evt-" + key}
    path = repo / ORCH / "lifecycle" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    duplicate = False
    needs_separator = False
    if path.exists():
        with path.open("r", encoding="utf-8") as existing:
            for line in existing:
                needs_separator = not line.endswith("\n")
                try:
                    if json.loads(line).get("idempotency_key") == key:
                        duplicate = True
                except (json.JSONDecodeError, AttributeError):
                    continue
    with path.open("a", encoding="utf-8") as stream:
        # Keep a truncated tail as history, but do not concatenate the recovered event.
        if needs_separator:
            stream.write("\n")
        if not duplicate:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        # A visible duplicate may come from a failed fsync; certify durability on retries too.
        os.fsync(stream.fileno())
    if os.name != "nt":
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    return not duplicate


def list_lifecycle_events(repo: Path, work_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    path = repo / ORCH / "lifecycle" / "events.jsonl"
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and (not work_id or event.get("work_item_id") == work_id):
                events.append(event)
    return events[-max(1, limit):][::-1]


def execution_phase(execution: dict) -> str | None:
    """Machine phase is independent of an Agent's free-form checkpoint label."""
    known = {"verification": "verification", "review": "review", "implementation": "implementation",
             "done": "done", "ready-for-dev": "ready", "reopened": "planning",
             "path-selected": "planning", "planning": "planning", "path-assessment": "planning",
             "native-sdd-workflow-selected": "planning", "native-sdd-workflow-executed": "implementation",
             "native-sdd-workflow-completed": "review"}
    return known.get(execution.get("step")) or execution.get("phase")


@locked_repo
def reconcile_native_sdd_state(repo: Path, work: dict[str, Any]) -> bool:
    """Reconcile a Work Item with its work-specific native SDD evidence and emit durable events.

    Artifact discovery may bind a unique matching native reference, but artifact existence alone
    never means execution has started. Provider-native status/task progress is required for phase
    projection. Native completion advances CAO to review, never directly to closed/done.
    """
    _refresh_work(repo, work)
    sdd = work.get("sdd") if isinstance(work.get("sdd"), dict) else {}
    provider = str(sdd.get("provider") or "").lower()
    if provider not in {"bmad", "openspec", "speckit"} or work.get("status") not in {"active", "suspended"}:
        return False
    changed = False
    native_ref = str(sdd.get("native_ref") or "").strip()
    candidates, anchor = _native_candidates(repo, provider, work, native_ref)
    if isinstance(sdd.get("spec_binding"), dict) and anchor and candidates:
        bound = str(sdd["spec_binding"]["spec"]["path"])
        if native_ref != bound:
            sdd["native_ref"] = native_ref = bound
            changed = True
    if not native_ref and anchor and candidates:
        preferred_names = {
            "bmad": ("spec-",),
            "openspec": ("proposal.md", "tasks.md", "design.md"),
            "speckit": ("spec.md", "plan.md", "tasks.md"),
        }[provider]
        preferred: list[Path] = []
        for wanted in preferred_names:
            preferred = [p for p in candidates if (p.name.lower().startswith(wanted) if wanted.endswith("-") else p.name.lower() == wanted)]
            if preferred:
                break
        # Native directory anchors are valid stable references when no canonical entry file exists.
        chosen = preferred[0] if len(preferred) == 1 else (anchor if anchor.is_dir() and not preferred else None)
        if chosen:
            sdd["native_ref"] = chosen.relative_to(repo).as_posix()
            native_ref = sdd["native_ref"]
            changed = True
    raw_state, status_source, evidence_hash, task_counts = _read_native_status(provider, candidates)
    if status_source:
        source_path = next((p for p in candidates if p.name == status_source), None)
        if source_path:
            status_source = source_path.relative_to(repo).as_posix()
    canonical = _canonical_native_state(raw_state)
    plan = provider_verification_plan_state(repo, work)
    if plan["state"] != "not_applicable":
        if plan.get("spec_task_scope") and sdd.get("spec_task_scope") != plan["spec_task_scope"]:
            sdd["spec_task_scope"] = plan["spec_task_scope"]
            changed = True
        if plan.get("scope") and sdd.get("plan_scope") != plan["scope"]:
            sdd["plan_scope"] = plan["scope"]
            changed = True
        if plan["state"] == "blocked":
            raw_state = canonical = status_source = evidence_hash = task_counts = None
        else:
            raw_state = plan["raw"]
            canonical = plan["canonical"]
            status_source = ", ".join(plan["observations"])
            evidence_hash = binding_digest(plan["observations"])
            total = sum(item["total"] if "total" in item else len(item["states"])
                        for item in plan["observations"].values())
            task_counts = {"total": total, "checked": total - len(plan["pending_plan_items"]),
                           "unchecked": len(plan["pending_plan_items"])}
            progress = bmad_progress_observation(repo, work, plan)
            if progress.get('canonical'):
                raw_state, canonical = progress['raw'], progress['canonical']
                evidence_hash = binding_digest({'plan': plan['observations'], 'spec': sha256_file(repo / work['sdd']['route_decision']['primary_artifact'])})
            if progress.get('progress_conflict') != sdd.get('progress_conflict'):
                if progress.get('progress_conflict'):
                    sdd['progress_conflict'] = progress['progress_conflict']
                else:
                    sdd.pop('progress_conflict', None)
                changed = True
    old_native = sdd.get("native_state") if isinstance(sdd.get("native_state"), dict) else {}
    old_canonical = old_native.get("canonical")
    new_snapshot = {
        "raw": raw_state,
        "canonical": canonical,
        "source": status_source,
        "evidence_sha256": evidence_hash,
        "task_counts": task_counts,
    }
    if canonical is not None and (old_native.get("canonical") != canonical or old_native.get("raw") != raw_state or old_native.get("evidence_sha256") != evidence_hash or old_native.get("source") != status_source):
        revision = int(old_native.get("revision") or 0) + 1
        new_snapshot["revision"] = revision
        new_snapshot["observed_at"] = now()
        from_state = old_canonical or None
        identity = {
            "work_item_id": work.get("id"), "provider": provider, "native_ref": native_ref or status_source,
            "revision": revision, "from_state": from_state, "to_state": canonical,
            "evidence_sha256": evidence_hash,
        }
        idem = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
        base_event = {
            "event_id": "evt-" + idem[:20], "idempotency_key": idem, "work_item_id": work.get("id"),
            "source": provider, "native_ref": native_ref or None, "from_state": from_state,
            "to_state": canonical, "raw_state": raw_state, "source_path": status_source,
            "evidence_sha256": evidence_hash, "occurred_at": new_snapshot["observed_at"],
        }
        if old_canonical != canonical:
            queue_event(work, {**base_event, "event": "sdd.native_state.changed"})
            if canonical == "ready":
                queue_event(work, {**base_event, "event": "task.ready", "idempotency_key": idem + ":ready", "event_id": "evt-" + idem[:16] + "ready"})
            elif canonical == "in_progress":
                queue_event(work, {**base_event, "event": "task.execution.started", "idempotency_key": idem + ":started", "event_id": "evt-" + idem[:16] + "started"})
            elif canonical == "completed":
                queue_event(work, {**base_event, "event": "task.execution.completed", "idempotency_key": idem + ":execution-completed", "event_id": "evt-" + idem[:16] + "complete"})
            elif canonical == "in_review":
                queue_event(work, {**base_event, "event": "task.review.started", "idempotency_key": idem + ":review", "event_id": "evt-" + idem[:16] + "review"})
            elif canonical == "blocked":
                queue_event(work, {**base_event, "event": "task.blocked", "idempotency_key": idem + ":blocked", "event_id": "evt-" + idem[:16] + "blocked"})
        elif old_native.get("evidence_sha256") != evidence_hash or old_native.get("source") != status_source:
            queue_event(work, {**base_event, "event": "sdd.native_artifact.changed", "idempotency_key": idem + ":artifact", "event_id": "evt-" + idem[:16] + "artifact"})
        sdd["native_state"] = new_snapshot
        changed = True
        work.setdefault("resume", {})["last_native_sync_at"] = new_snapshot["observed_at"]

    # Reconcile the CAO phase on every read, not only on a newly observed native
    # transition. This repairs stale CAO state even if an earlier sync was missed.
    if canonical is not None:
        execution = work.setdefault("execution", {})
        current_step = str(execution.get("step") or "").lower()
        phase = execution_phase(execution)
        later_checkpoint = phase in {"verification", "review", "audit", "release", "done"}
        aggregate_pending_review = plan["state"] == "pending" and current_step == "review"
        if aggregate_pending_review:
            later_checkpoint = False  # A single completed SPEC cannot finish the whole requirement.
        if canonical == "ready" and (current_step in {"reopened", "path-selected", "planning", "plan-selected", "sdd-planning", "native-sdd-workflow-selected"} or aggregate_pending_review):
            execution["step"] = "ready-for-dev"
            execution["phase"] = "ready"
            execution["next_action"] = action("agent", f"Native {provider} work is ready; begin implementation using the bound native work item.")
            work.setdefault("resume", {})["summary"] = f"Native SDD reports ready for implementation ({provider})."
            changed = True
        elif canonical == "in_progress" and current_step != "implementation" and not later_checkpoint:
            execution["step"] = "implementation"
            execution["phase"] = "implementation"
            execution["next_action"] = action("agent", f"Continue implementation using the native {provider} work item; phase synchronized from explicit native status/task progress.")
            work.setdefault("resume", {})["summary"] = f"Native SDD reports implementation in progress ({provider}); CAO phase reconciled."
            changed = True
        elif canonical in {"completed", "in_review"} and not later_checkpoint:
            execution["step"] = "review"
            execution["phase"] = "review"
            execution["next_action"] = action("agent", "Native implementation is ready for review; complete implementation review and CAO verification before closing the Work Item.")
            work.setdefault("resume", {})["summary"] = f"Native SDD reports review/implementation completion ({provider}); CAO moved to review, not closed."
            changed = True
        elif canonical == "blocked":
            blockers = work.setdefault("blockers", [])
            if not any(isinstance(item, dict) and item.get("id") == "native-sdd-state" for item in blockers):
                blockers.append({"id": "native-sdd-state", "reason": f"Native {provider} state is blocked", "source": status_source, "observed_at": now()})
                changed = True
        if canonical in {"ready", "in_progress", "in_review", "completed"}:
            blockers = work.get("blockers") or []
            automatic = [item for item in blockers if isinstance(item, dict)
                         and item.get("id") == "native-sdd-state"]
            if automatic:
                work["blockers"] = [item for item in blockers if item not in automatic]
                queue_event(work, {"event": "sdd.native_blocker.cleared", "work_item_id": work["id"],
                                   "source": provider, "blockers": automatic, "to_state": canonical,
                                   "occurred_at": now()})
                changed = True
    # Observation is retained even when it reveals an unauthorized transition.
    # It must not itself satisfy routing or approval prerequisites.
    previous_route_state = sdd.get("implementation_state") or {}
    route = native_route_state(repo, work)
    if route.get("state") != "not_applicable" and sdd.get("implementation_state") != route:
        sdd["implementation_state"] = route
        changed = True
    if not route["ready"]:
        nxt = work_prerequisite_action(repo, work) or route_preparation_action(repo, work, route)
        if work.setdefault("execution", {}).get("next_action") != nxt:
            work["execution"]["next_action"] = nxt
            changed = True
    elif previous_route_state.get("ready") is False:
        work.setdefault("execution", {})["next_action"] = action("agent",
            "Follow the accepted native route from its current stage; record workflow execution after it actually runs, and completion only after native review finishes.")
        changed = True
    if changed:
        work["sdd"] = sdd
        save_work(repo, work)
    return changed


def cmd_lifecycle_events(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    print(json.dumps({"ok": True, "work_item_id": wid, "events": list_lifecycle_events(repo, wid, args.limit)}, ensure_ascii=False, indent=2))
    return 0

def is_current_session_record(raw: Any) -> bool:
    """Return whether a record satisfies the current Session data contract.

    Session persistence intentionally has no legacy-record normalization or
    migration path. The host-native ID and Host together determine the stable
    internal ID, and every required field must be present in the current shape.
    """
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        return False
    required = {
        "id", "host", "host_session_id", "work_item_id", "kind", "parent_session_id",
        "status", "started_at", "last_event", "last_event_at", "updated_at",
        "ended_at", "end_reason", "last_handoff_id",
    }
    if not required.issubset(raw):
        return False
    host = raw.get("host")
    native_id = raw.get("host_session_id")
    if not isinstance(host, str) or not host or not isinstance(native_id, str) or not native_id:
        return False
    if raw.get("id") != stable_session_id(host, native_id):
        return False
    if raw.get("kind") not in {"primary", "subagent"} or raw.get("status") not in {"active", "ended"}:
        return False
    if raw.get("work_item_id") is not None and not isinstance(raw.get("work_item_id"), str):
        return False
    if raw.get("parent_session_id") is not None and not isinstance(raw.get("parent_session_id"), str):
        return False
    for key in ("started_at", "last_event_at", "updated_at"):
        if not isinstance(raw.get(key), int) or isinstance(raw.get(key), bool) or raw[key] < 0:
            return False
    if raw.get("ended_at") is not None and (not isinstance(raw.get("ended_at"), int) or isinstance(raw.get("ended_at"), bool) or raw["ended_at"] < 0):
        return False
    if not isinstance(raw.get("last_event"), str) or not raw["last_event"]:
        return False
    if raw.get("end_reason") is not None and not isinstance(raw.get("end_reason"), str):
        return False
    if raw.get("last_handoff_id") is not None and not isinstance(raw.get("last_handoff_id"), str):
        return False
    return True


def _session_files(repo: Path) -> list[Path]:
    return sorted((repo / SESSION_DIR).glob("*.json")) if (repo / SESSION_DIR).exists() else []


def _read_current_session(path: Path) -> dict[str, Any] | None:
    raw = load_json(path, None)
    if not is_current_session_record(raw):
        return None
    if path.name != f"{raw['id']}.json":
        return None
    return raw


def load_session(repo: Path, session_ref: str | None, host: str | None = None) -> dict[str, Any] | None:
    """Find a current-schema Session by internal ID or host-native ID.

    Files from older Session layouts are ignored; this model deliberately does
    not migrate previous paths or fields.
    """
    if not session_ref:
        return None
    if host == "unknown":
        host = None
    direct = _read_current_session(session_path(repo, session_ref))
    if direct:
        if host and direct.get("host") not in {host, "unknown"}:
            return None
        return direct

    candidates = [
        session for path in _session_files(repo)
        if (session := _read_current_session(path)) is not None
        and (session.get("id") == session_ref or session.get("host_session_id") == session_ref)
        and (not host or session.get("host") in {host, "unknown"})
    ]
    if not candidates:
        return None
    if host:
        exact = [session for session in candidates if session.get("host") == host]
        if exact:
            candidates = exact
    if len(candidates) > 1 and not host:
        raise RuntimeError(f"Session reference {session_ref!r} is ambiguous across hosts; supply --host.")
    return candidates[0]


def save_session(repo: Path, session: dict[str, Any]) -> None:
    session["schema_version"] = 2
    session["updated_at"] = now()
    save_json(session_path(repo, session["id"]), session)


def session_activity(session: dict[str, Any], stale_after_minutes: int = 30, current_time: int | None = None) -> str:
    if session.get("status") == "ended":
        return "ended"
    current_time = now() if current_time is None else current_time
    try:
        last_event_at = int(session.get("last_event_at") or 0)
    except (TypeError, ValueError):
        last_event_at = 0
    if not last_event_at or current_time - last_event_at > max(1, stale_after_minutes) * 60:
        return "stale"
    return "recent"


def list_sessions(repo: Path) -> list[dict[str, Any]]:
    """List current-schema Session records without migrating older formats."""
    found: dict[str, dict[str, Any]] = {}
    for path in _session_files(repo):
        session = _read_current_session(path)
        if session is not None:
            found[session["id"]] = session
    return sorted(found.values(), key=lambda s: (s.get("last_event_at") or 0, s.get("id") or ""), reverse=True)


@locked_repo
def update_session_event(
    repo: Path,
    session_ref: str,
    host: str,
    event: str,
    work: dict[str, Any] | None = None,
    *,
    kind: str = "primary",
    parent_session_id: str | None = None,
) -> dict[str, Any]:
    # A delayed result reports the caller's earlier Work; it is not a reattach.
    event_work = None if event in {"post_tool", "file_changed", "session_end", "subagent_stop"} else work
    if work:
        _refresh_work(repo, work)
    session = load_session(repo, session_ref, host=host)
    if session is None:
        return register_session(repo, event_work, session_ref, host, event=event, kind=kind, parent_session_id=parent_session_id)
    if session.get("host") == "unknown" and host and host != "unknown":
        # Re-register to re-key the stored file using the now-known Host identity.
        return register_session(
            repo,
            event_work,
            session_ref,
            host,
            event=event,
            kind=kind if kind else session.get("kind", "primary"),
            parent_session_id=parent_session_id if parent_session_id is not None else session.get("parent_session_id"),
        )
    if host and host != "unknown":
        session["host"] = host
    session["work_item_id"] = event_work.get("id") if event_work else session.get("work_item_id")
    if session.get("status") != "ended" and event not in {"session_end", "subagent_stop"}:
        session["status"] = "active"
    session["last_event"] = event
    session["last_event_at"] = now()
    if event in {"session_start", "subagent_start"} and session.get("status") == "ended":
        session["started_at"] = now()
        session["ended_at"] = None
        session["end_reason"] = None
    save_session(repo, session)
    if work and session.get("work_item_id") == work["id"]:
        work.setdefault("resume", {})["last_agent"] = host
        save_work(repo, work)
    return session


@locked_repo
def touch_session(repo: Path, session_ref: str, event: str, work: dict[str, Any] | None = None, host: str | None = None) -> dict[str, Any] | None:
    session = load_session(repo, session_ref, host=host)
    if not session:
        return None
    session["last_event"] = event
    session["last_event_at"] = now()
    if work:
        session["work_item_id"] = work.get("id")
    save_session(repo, session)
    return session


@locked_repo
def end_session(repo: Path, session_ref: str, host: str | None, reason: str, event: str = "session_end") -> dict[str, Any] | None:
    session = load_session(repo, session_ref, host=host)
    if not session:
        return None
    session["status"] = "ended"
    session["last_event"] = event
    session["last_event_at"] = now()
    session["ended_at"] = now()
    session["end_reason"] = reason
    save_session(repo, session)
    return session


def cmd_sessions_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    sessions = list_sessions(repo)
    if args.host:
        sessions = [s for s in sessions if s.get("host") == args.host]
    if args.work:
        sessions = [s for s in sessions if s.get("work_item_id") == args.work]
    if args.status:
        sessions = [s for s in sessions if s.get("status") == args.status]
    out = []
    for session in sessions:
        item = dict(session)
        item["activity"] = session_activity(session, args.stale_after_minutes)
        out.append(item)
    print(json.dumps({"ok": True, "sessions": out, "stale_after_minutes": args.stale_after_minutes}, ensure_ascii=False, indent=2))
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    session = load_session(repo, args.id, host=args.host)
    if not session:
        print(json.dumps({"ok": False, "error": "SESSION_NOT_FOUND", "id": args.id}, ensure_ascii=False, indent=2))
        return 2
    out = dict(session)
    out["activity"] = session_activity(session, args.stale_after_minutes)
    print(json.dumps({"ok": True, "session": out}, ensure_ascii=False, indent=2))
    return 0


def requirement_evidence(repo: Path, requirement: Path) -> dict[str, str]:
    return {"path": rel(requirement, repo), "sha256": sha256_file(requirement)}


def infer_sdd_provider(repo: Path, requirement: Path, cfg: dict[str, Any]) -> str:
    p = rel(requirement, repo).lower()
    configured = str((cfg.get("sdd") or {}).get("provider") or "auto")
    if configured != "auto":
        return configured
    if p.startswith("openspec/"):
        return "openspec"
    if p.startswith("_bmad/") or p.startswith("bmad/"):
        return "bmad"
    if "/specs/" in f"/{p}" or p.startswith("specs/"):
        if (repo / ".specify").exists():
            return "speckit"
        return "generic-sdd"
    return "generic"


def run_sdd_command(repo: Path, name: str, extra: list[str]) -> dict[str, Any] | None:
    command = str((((config(repo).get("sdd") or {}).get("command") or {}).get(name) or "")).strip()
    if not command:
        return None
    env = os.environ.copy()
    env.update({"CAO_REPO": str(repo), "CAO_COMMAND": name})
    result = subprocess.run(shlex.split(command) + extra, cwd=repo, text=True, capture_output=True, env=env)
    return {"command": command, "args": extra, "exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def action(kind: str, instruction: str, **extra: Any) -> dict[str, Any]:
    if kind not in ACTION_TYPES:
        raise ValueError(f"unsupported action type: {kind}")
    return {"type": kind, "instruction": instruction, **extra}


def work_summary(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": work["id"],
        "title": work["title"],
        "status": work["status"],
        "step": work.get("execution", {}).get("step"),
        "next_action": work.get("execution", {}).get("next_action"),
        "execution_profile": (work.get("path_assessment") or {}).get("profile"),
        "native_route": (work.get("sdd") or {}).get("route_decision"),
        "implementation_state": (work.get("sdd") or {}).get("implementation_state"),
        "sdd": work.get("sdd"),
        "blockers": work.get("blockers") or [],
        "decision_count": len(work.get("decisions") or []),
        "verification": (work.get("verification") or {}).get("status"),
        "legacy_evidence": ((work.get("acceptance") or {}).get("version") != 2
                            or bool(work.get("verification") and work["verification"].get("version") != 2)
                            or bool(work.get("path_assessment") and not work["path_assessment"].get("revision"))),
        "last_agent": work.get("resume", {}).get("last_agent"),
    }


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


# Each provider records its own plan, and each format says "not done yet"
# differently: BMAD keeps epic/story states in `sprint-status.yaml`, OpenSpec
# and Spec Kit keep checkbox lists in `tasks.md`. A Work Item is a single unit,
# so without reading these an Agent can finish one story of thirteen and close
# the requirement as complete (demo11 did exactly that).
SPRINT_DONE_STATES = frozenset({"done"})
SPRINT_STATUS_NAMES = ("sprint-status.yaml", "sprint-status.yml")

# Same task-line contract as OpenSpec's parseTaskLines: CommonMark list
# markers, padded/empty/unknown single-character boxes, only x/X means done.
TASK_LINE_PATTERN = re.compile(r"^\s*(?:[-*+]|[0-9]{1,9}[.)])\s*\[(?:\s*([^\]\s]?)\s*\](?![\(\[])|\s+\])\s*(.*)")


def _checkbox_tasks(content: str) -> list[dict]:
    tasks = []
    for number, line in enumerate(content.split("\n"), 1):
        match = TASK_LINE_PATTERN.match(line)
        if match:
            tasks.append({"done": (match.group(1) or "").lower() == "x",
                          "description": match.group(2).strip(), "line": number})
    return tasks


def _pending_checkbox_tasks(path: Path) -> list[str]:
    """Unchecked tasks, as OpenSpec and Spec Kit write them."""
    return [(task["description"] or f"task at line {task['line']}")[:80]
            for task in _checkbox_tasks(path.read_text(encoding="utf-8-sig")) if not task["done"]]


def _sprint_states(path: Path) -> dict[str, str]:
    states = {}
    in_status = False
    seen_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.fullmatch(r"development_status:\s*(?:#.*)?", line):
            if seen_section:
                raise ValueError("PLAN_STATUS_INVALID: duplicate development_status")
            seen_section = in_status = True
            continue
        if not line[0].isspace():
            in_status = False
            continue
        if not in_status:
            continue
        match = re.fullmatch(r"[ \t]+(?P<name>[^#\s][^:]*?):[ \t]*(?:'(?P<single>[a-z][a-z_-]*)'|\"(?P<double>[a-z][a-z_-]*)\"|(?P<bare>[a-z][a-z_-]*))[ \t]*(?:#.*)?", line)
        if not match:
            raise ValueError("PLAN_STATUS_INVALID: " + path.name)
        name = match.group("name").strip().strip("\"'")
        if name in states:
            raise ValueError("PLAN_STATUS_INVALID: duplicate " + name)
        states[name] = next(match.group(key) for key in ("single", "double", "bare") if match.group(key))
    if not seen_section:
        raise ValueError("PLAN_STATUS_INVALID: missing/empty development_status")
    return states


def _sprint_selected_states(path: Path, ids: list[str]) -> dict[str, str]:
    """Epic selectors include their current and future stories, never another epic."""
    states = _sprint_states(path)
    selected = {}
    for item in ids:
        if item not in states:
            raise ValueError("PLAN_ITEM_MISSING: " + path.name + "#" + item)
        if re.fullmatch(r"epic-\d+", item):
            prefix = item.removeprefix("epic-") + "-"
            stories = {key: value for key, value in states.items()
                       if re.match(r"^" + re.escape(prefix) + r"\d+-", key)}
            if not stories:
                raise ValueError("PLAN_ITEM_INVALID: empty " + item)
            selected.update(stories)
        elif item.startswith("epic-") or item.endswith("-retrospective"):
            raise ValueError("PLAN_ITEM_INVALID: " + item)
        else:
            selected[item] = states[item]
    if not selected:
        raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + path.name)
    return selected


def _plan_pattern(repo: Path, provider: str, value: str) -> str:
    """Normalize explicit tracked-file globs without widening provider ownership."""
    if ".." in Path(value).parts:
        raise ValueError("PLAN_SOURCE_INVALID: " + value)
    dirs = [directory for directory in provider_output_dirs(provider)
            if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
    path = provider_artifact(repo, value, dirs)
    return path.relative_to(repo.resolve()).as_posix()


def _pattern_plan_paths(repo: Path, provider: str, patterns: list[str]) -> set[str]:
    paths = set()
    for value in patterns:
        pattern = _plan_pattern(repo, provider, value)
        matches = [Path(path) for path in glob.glob(str(repo / pattern), recursive=True) if Path(path).is_file()]
        if not matches:
            raise ValueError("PLAN_SOURCE_MISSING: " + pattern)
        for path in matches:
            owned = _plan_pattern(repo, provider, str(path))
            if path.suffix.lower() != ".md":
                raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + owned)
            paths.add(owned)
    return paths


def _associated_task_roots(repo: Path, work: dict) -> set[Path]:
    """Derive feature/change roots only from this Work's actual associations."""
    sdd = work.get("sdd") or {}
    provider = sdd.get("provider")
    if provider not in {"openspec", "speckit"}:
        return set()
    root = repo.resolve() / ("openspec/changes" if provider == "openspec" else "specs")
    dirs = [directory for directory in provider_output_dirs(provider)
            if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
    refs = [sdd.get("native_ref"), *((sdd.get("workflow") or {}).get("artifacts") or [])]
    binding = sdd.get("spec_binding")
    if binding is not None:
        if (not isinstance(binding, dict) or binding.get("provider") != provider or
                binding.get("requirement_sha256") != (work.get("requirement") or {}).get("sha256")):
            raise ValueError("PLAN_SOURCE_INVALID: SPEC association")
        refs.append((binding.get("spec") or {}).get("path"))
    refs.extend(item.get("path") for item in sdd.get("artifacts", []) if isinstance(item, dict))
    roots = set()
    for value in refs:
        if not value:
            continue
        path = provider_artifact(repo, str(value), dirs)
        if path.is_relative_to(root):
            parts = path.relative_to(root).parts
            if parts and parts[0] != "archive":
                roots.add(root / parts[0])
            elif len(parts) >= 2:
                for migration in sdd.get("archive_relocations", []):
                    moved = repo.resolve() / migration["archive_root"]
                    if path.is_relative_to(moved):
                        roots.add(moved)
    return roots


def bind_plan_scope(repo: Path, work: dict, plan_paths: list[str],
                    selections: dict[str, list[str]], no_plan: bool) -> dict:
    repo = repo.resolve()
    sdd = work.get("sdd") or {}
    roots = [(repo / directory).resolve() for directory in provider_output_dirs(sdd.get("provider"))]
    patterns = sorted(set((sdd.get("plan_scope") or {}).get("patterns") or []) |
                      {_plan_pattern(repo, sdd.get("provider"), str(value)) for value in plan_paths if glob.has_magic(str(value))})
    paths = {resolve_artifact_path(repo, value)[1] for value in plan_paths if not glob.has_magic(str(value))}
    paths.update(_pattern_plan_paths(repo, sdd.get("provider"), patterns))
    normalized = {}
    for value, ids in selections.items():
        relative = resolve_artifact_path(repo, value)[1]
        normalized.setdefault(relative, []).extend(ids)
    paths.update(normalized)
    anchors = set()
    if sdd.get("native_ref"):
        path, _ = resolve_artifact_path(repo, sdd["native_ref"])
        if path.exists():
            anchors.add(path if path.is_dir() else path.parent)
    for value in (sdd.get("workflow") or {}).get("artifacts", []):
        path, _ = resolve_artifact_path(repo, value)
        anchors.add(path.parent)
    anchors.update(_associated_task_roots(repo, work))
    discovered = set()
    ambiguous = False
    public_roots = set(roots) | {(repo / "openspec/changes").resolve(), (repo / "specs").resolve()}
    for anchor in anchors:
        for name in ("tasks.md", *SPRINT_STATUS_NAMES):
            path = anchor / name
            if path.is_file():
                discovered.add(path.relative_to(repo).as_posix())
                if anchor in public_roots:
                    ambiguous = True
    old = sdd.get("plan_scope") or {}
    if no_plan:
        if paths or discovered or old.get("mode") == "bound":
            raise ValueError("PLAN_SCOPE_REMOVAL_REQUIRES_REANCHOR")
        return {"version": 1, "mode": "none", "plans": []}
    if old.get("mode") == "bound":
        # Keep prior associations even on explicit repeat; a later native story
        # completion must not replace the requirement-wide scope with one story.
        if not paths:
            paths.update(value for value in discovered if (repo / value).parent not in public_roots)
        for plan in old.get("plans", []):
            paths.add(plan["path"])
            normalized.setdefault(plan["path"], []).extend(plan.get("item_ids") or [])
    if not paths and any(Path(value).name in SPRINT_STATUS_NAMES for value in discovered):
        raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + ", ".join(sorted(discovered)))
    if not paths and ambiguous:
        return {"version": 1, "mode": "unresolved", "plans": []}
    paths = paths or discovered
    if not paths:
        return {"version": 1, "mode": "unresolved", "plans": []}
    plans = []
    for value in sorted(paths):
        path, relative = resolve_artifact_path(repo, value)
        try:
            provider_artifact(repo, relative, provider_output_dirs(sdd.get("provider")))
        except ValueError as exc:
            raise ValueError("PLAN_SOURCE_INVALID: " + relative) from exc
        if not path.is_file():
            raise ValueError("PLAN_SOURCE_INVALID: " + relative)
        ids = sorted(set(normalized.get(value, [])))
        if path.name in SPRINT_STATUS_NAMES:
            if not ids:
                raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + relative)
            try:
                selected = _sprint_selected_states(path, ids)
                # Retain today's story identities as well as dynamic epic selectors:
                # deleting a previously associated story must not shrink the scope.
                ids = sorted(set(ids) | set(selected))
            except ValueError as exc:
                raise ValueError("PLAN_ITEM_INVALID: " + relative + ": " + str(exc)) from exc
        elif (path.name != "tasks.md" and not (sdd.get("provider") == "openspec" and path.suffix.lower() == ".md")) or ids:
            raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + relative)
        plan = {"path": relative, "item_ids": ids}
        previous = next((item for item in old.get("plans", []) if item.get("path") == relative), {})
        for field in ("task_ids", "minimum_tasks"):
            if field in previous:
                plan[field] = previous[field]
        if sdd.get("provider") == "openspec":
            plan["sha256"] = sha256_file(path)
        plans.append(plan)
    scope = {"version": 1, "mode": "bound", "plans": plans}
    if patterns:
        scope["patterns"] = patterns
    return scope


def provider_pending_plan_items(repo: Path, work: dict) -> list[str]:
    scope = (work.get("sdd") or {}).get("plan_scope") or {}
    if scope.get("mode") == "none":
        return []
    if scope.get("mode") != "bound" or not scope.get("plans"):
        raise ValueError("PLAN_SCOPE_UNRESOLVED")
    pending = []
    for plan in scope["plans"]:
        path, relative = resolve_artifact_path(repo, plan["path"])
        try:
            provider_artifact(repo, relative, provider_output_dirs((work.get("sdd") or {}).get("provider")))
        except ValueError as exc:
            raise ValueError("PLAN_SOURCE_INVALID: " + plan["path"]) from exc
        if not path.is_file():
            raise ValueError("PLAN_SOURCE_MISSING: " + relative)
        if path.name in SPRINT_STATUS_NAMES:
            ids = plan.get("item_ids") or []
            if not ids:
                raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + relative)
            states = _sprint_selected_states(path, ids)
            pending.extend(f"{relative}#{item} ({state})" for item, state in states.items()
                           if state not in SPRINT_DONE_STATES)
        elif path.name == "tasks.md" or ((work.get("sdd") or {}).get("provider") == "openspec" and path.suffix.lower() == ".md"):
            pending.extend(f"{relative}: {item}" for item in _pending_checkbox_tasks(path))
        else:
            raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + relative)
    return pending


def bmad_verification_plan_state(repo: Path, work: dict) -> dict:
    """Current requirement's aggregate BMAD execution gate, independent of SPEC status."""
    sdd = work.get("sdd") or {}
    if sdd.get("provider") != "bmad":
        return {"ready": True, "state": "not_applicable"}
    scope = sdd.get("plan_scope") or {}
    plans = [plan for plan in scope.get("plans", []) if Path(plan.get("path", "")).name in SPRINT_STATUS_NAMES]
    spec = spec_task_state(repo, work, provider_output_dirs("bmad"), _checkbox_tasks)
    if spec["state"] in {"pending", "done"}:
        candidates, _ = _native_candidates(repo, "bmad", work, str(sdd.get("native_ref") or ""))
        raw_status, _, _, _ = _read_native_status("bmad", candidates)
        canonical = _canonical_native_state(raw_status)
        if canonical == "blocked":
            spec.update(ready=False, state="pending", raw=raw_status, canonical=canonical,
                        reason="BMAD_SPEC_BLOCKED", next="Resolve the native SPEC blocker, then synchronize and verify.")
        elif not spec["ready"] and canonical == "in_progress":
            spec.update(raw=raw_status, canonical=canonical)
    decision = direct_build_decision(work)
    if decision and not plans and not any(item.get("role") == "sprint"
                                         for item in decision.get("required_artifacts", [])):
        return spec
    candidates = {repo / "_bmad-output" / name for name in SPRINT_STATUS_NAMES}
    candidates.update(repo / "_bmad-output/implementation-artifacts" / name for name in SPRINT_STATUS_NAMES)
    for ref in [sdd.get("native_ref"), ((sdd.get("spec_binding") or {}).get("spec") or {}).get("path"),
                *((sdd.get("workflow") or {}).get("artifacts") or [])]:
        if ref:
            candidates.update((repo / str(ref)).parent / name for name in SPRINT_STATUS_NAMES)
    if not plans and not any(path.exists() for path in candidates):
        return spec
    next_step = (f"Bind ALL stories or whole epics belonging to this requirement with `cao sdd plan bind --work {work['id']} "
                 "--plan-item <sprint-status.yaml-or-yml>#<story-id-or-epic-N>` (repeat for all associated items). "
                 "Continue the native workflow until every associated story is done, then run `cao verify`.")
    effective_scope = json.loads(json.dumps(scope))
    try:
        if scope.get("mode") != "bound" or not plans:
            raise ValueError("BMAD_PLAN_SCOPE_REQUIRED")
        if scope.get("requirement_sha256") not in {None, (work.get("requirement") or {}).get("sha256")}:
            raise ValueError("PLAN_SCOPE_REQUIREMENT_CHANGED")
        observations = {}
        pending = []
        for plan in plans:
            dirs = [directory for directory in provider_output_dirs("bmad")
                    if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
            path = provider_artifact(repo, plan["path"], dirs)
            if not path.is_file():
                raise ValueError("PLAN_SOURCE_MISSING: " + plan["path"])
            known = set(plan.get("item_ids") or [])
            observed = _sprint_states(path)
            for selector in list(known):
                if re.fullmatch(r"epic-\d+", selector):
                    prefix = selector.removeprefix("epic-") + "-"
                    known.update(key for key in observed if re.match(r"^" + re.escape(prefix) + r"\d+-", key))
            effective = next(item for item in effective_scope["plans"] if item["path"] == plan["path"])
            effective["item_ids"] = sorted(known)
            states = _sprint_selected_states(path, effective["item_ids"])
            observations[plan["path"]] = {"selectors": effective["item_ids"], "states": states}
            pending.extend(f"{plan['path']}#{item} ({state})" for item, state in states.items() if state != "done")
        values = [state for observation in observations.values() for state in observation["states"].values()]
        if not pending:
            raw = "done"
        elif all(state in {"backlog", "drafted", "ready-for-dev", "ready_for_dev"} for state in values):
            raw = "ready_for_dev"
        elif any(state in {"in-progress", "in_progress", "in_process", "review", "in-review", "done"} for state in values):
            raw = "in_progress"
        else:
            raw = None
        sprint = {"ready": not pending, "state": "pending" if pending else "done",
                "raw": raw, "canonical": _canonical_native_state(raw),
                "reason": "BMAD_PLAN_NOT_DONE" if pending else None,
                "scope": effective_scope, "pending_plan_items": pending, "observations": observations, "next": next_step if pending else None}
        return merge_plan_states(sprint, spec)
    except (ValueError, OSError, UnicodeError, RuntimeError) as exc:
        return {"ready": False, "state": "blocked", "reason": str(exc), "scope": effective_scope, "next": next_step}


def provider_verification_plan_state(repo: Path, work: dict) -> dict:
    """Aggregate all native execution plans associated with this requirement."""
    sdd = work.get("sdd") or {}
    provider = sdd.get("provider")
    if provider == "bmad":
        return bmad_verification_plan_state(repo, work)
    if provider not in {"openspec", "speckit"}:
        return {"ready": True, "state": "not_applicable"}
    scope = sdd.get("plan_scope") or {}
    next_step = (f"Finish every task in ALL this requirement's native plans before `cao verify`. "
                 f"Associate split changes/features with `cao sdd plan bind --work {work['id']} --plan <native-task-file>` "
                 "(repeat for every associated plan). For OpenSpec custom schemas, bind the actual apply.tracks outputs; "
                 "a quoted tracked-file glob also includes new shards. Do not guess unrelated changes or substitute a checklist.")
    effective_scope = None
    try:
        if scope.get("requirement_sha256") not in {None, (work.get("requirement") or {}).get("sha256")}:
            raise ValueError("PLAN_SCOPE_REQUIREMENT_CHANGED")
        paths = {plan["path"] for plan in scope.get("plans", [])}
        paths.update(_pattern_plan_paths(repo, provider, scope.get("patterns") or []))
        for root in _associated_task_roots(repo, work):
            path = root / "tasks.md"
            if path.exists() or path.is_symlink():
                paths.add(path.relative_to(repo.resolve()).as_posix())
        # A direct tasks reference remains a requirement association if its file vanishes.
        ref = sdd.get("native_ref")
        if ref and Path(ref).name == "tasks.md":
            paths.add(str(ref))
        if not paths:
            if scope.get("mode") == "bound":
                raise ValueError("PLAN_SCOPE_UNRESOLVED")
            return {"ready": True, "state": "not_applicable"}
        dirs = [directory for directory in provider_output_dirs(provider)
                if (repo.resolve() / directory).resolve() == repo.resolve() / directory]
        observations = {}
        pending = []
        plans = []
        for value in sorted(paths):
            path = provider_artifact(repo, value, dirs)
            relative = path.relative_to(repo.resolve()).as_posix()
            if not path.is_file():
                raise ValueError("PLAN_SOURCE_MISSING: " + relative)
            if (path.name != "tasks.md" and not (provider == "openspec" and path.suffix.lower() == ".md")):
                raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + relative)
            content = path.read_text(encoding="utf-8-sig")
            tasks = _checkbox_tasks(content)
            if not tasks:
                raise ValueError("PLAN_TASKS_UNRECOGNIZED: " + relative)
            ids = []
            for task in tasks:
                match = re.match(r"(T[0-9]+|[0-9]+(?:\.[0-9]+)+)(?=\s|[.:]|$)", task["description"], re.I)
                if match:
                    ids.append(match.group(1).upper())
            if len(ids) != len(set(ids)):
                raise ValueError("PLAN_TASK_ID_DUPLICATE: " + relative)
            previous = next((item for item in scope.get("plans", []) if item.get("path") == relative), {})
            if set(previous.get("task_ids") or []) - set(ids) or len(tasks) < int(previous.get("minimum_tasks") or 0):
                raise ValueError("PLAN_TASK_REMOVED: " + relative)
            plans.append({"path": relative, "item_ids": [], "task_ids": sorted(set(ids)),
                          "minimum_tasks": max(len(tasks), int(previous.get("minimum_tasks") or 0)),
                          **({"sha256": sha256_file(path)} if provider == "openspec" else {})})
            observations[relative] = {"sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                                      "total": len(tasks), "checked": sum(task["done"] for task in tasks)}
            pending.extend(f"{relative}:{task['line']}: {task['description'] or 'unnamed task'}"
                           for task in tasks if not task["done"])
        effective_scope = {"version": 1, "mode": "bound", "plans": plans,
                           "requirement_sha256": work["requirement"]["sha256"]}
        if scope.get("patterns"):
            effective_scope["patterns"] = sorted(set(scope["patterns"]))
        checked = sum(item["checked"] for item in observations.values())
        raw = "completed" if not pending else "in_progress" if checked else "ready_for_dev"
        return {"ready": not pending, "state": "pending" if pending else "done",
                "raw": raw, "canonical": _canonical_native_state(raw),
                "reason": "SDD_PLAN_NOT_DONE" if pending else None, "observations": observations,
                "pending_plan_items": pending, "scope": effective_scope, "next": next_step if pending else None}
    except (ValueError, OSError, UnicodeError, RuntimeError) as exc:
        return {"ready": False, "state": "blocked", "reason": str(exc), "next": next_step}


@locked_repo
def observe_plan_scope(repo: Path, work: dict) -> dict:
    """Persist pure provider scope deltas before any verification early return."""
    _refresh_work(repo, work)
    observation = provider_verification_plan_state(repo, work)
    scope = observation.get("scope")
    spec_scope = observation.get("spec_task_scope")
    if spec_scope and spec_scope != (work.get("sdd") or {}).get("spec_task_scope"):
        work.setdefault("sdd", {})["spec_task_scope"] = spec_scope
        save_work(repo, work)
    if scope and scope != (work.get("sdd") or {}).get("plan_scope"):
        work.setdefault("sdd", {})["plan_scope"] = scope
        save_work(repo, work)
    return observation


@locked_command
def cmd_sdd_plan_bind(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    try:
        sdd = work["sdd"]
        selections = {}
        for plan in (sdd.get("plan_scope") or {}).get("plans", []):
            selections[plan["path"]] = list(plan.get("item_ids") or [])
        for value in args.plan_item or []:
            path, item = value.rsplit("#", 1)
            if not path or not item:
                raise ValueError("PLAN_ITEM_INVALID")
            selections.setdefault(path, []).append(item)
        if not args.plan and not args.plan_item:
            raise ValueError("PLAN_SCOPE_REQUIRED")
        scope = bind_plan_scope(repo, work, list(selections) + (args.plan or []), selections, False)
        scope["requirement_sha256"] = work["requirement"]["sha256"]
    except (ValueError, OSError, UnicodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc).split(":", 1)[0], "reason": str(exc)})); return 2
    sdd["plan_scope"] = scope
    queue_event(work, {"event": "sdd.plan.bound", "work_item_id": work["id"], "source": "cao",
                       "plan_scope": scope, "occurred_at": now()})
    save_work(repo, work)
    reconcile_native_sdd_state(repo, work)
    print(json.dumps({"ok": True, "plan_scope": scope, "verification_plan": provider_verification_plan_state(repo, work)}, ensure_ascii=False)); return 0


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
    return output_paths(repo, declaration, provider_output_dirs(work["sdd"]["provider"]), work["id"], previous)


def sdd_acceptance_inputs(repo: Path, work: dict, cfg: dict) -> dict:
    record = (work.get("sdd") or {}).get("acceptance") or {}
    if not record.get("declaration"):
        raise ValueError("SDD_ACCEPTANCE_NOT_CONFIGURED")
    return {**verification_inputs(repo, work, cfg), "work_id": work["id"], "provider": work["sdd"]["provider"],
            "declaration_sha256": record["declaration_sha256"],
            "source_hashes": source_hashes(repo, record["declaration"], provider_output_dirs(work["sdd"]["provider"]))}


def sdd_acceptance_current_hashes(repo: Path, work: dict) -> dict:
    record = (work.get("sdd") or {}).get("acceptance") or {}
    run = record.get("run") or {}
    if run.get("status") != "passed":
        return {}
    return report_hashes(repo, run.get("results") or [], record["declaration"]["outputs"])


def sdd_acceptance_state(repo: Path, work: dict) -> dict:
    profile = (work.get("path_assessment") or {}).get("profile")
    if profile == "light":
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
    inputs = {"snapshot_format": SNAPSHOT_FORMAT, "requirement_sha256": sha256_file(repo / work["requirement"]["path"]),
            "code_snapshot": code_snapshot(repo, {work["requirement"]["path"]}),
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
    record = (work.get("sdd") or {}).get("acceptance") or {}
    if record.get("declaration") and (work.get("path_assessment") or {}).get("profile") != "light":
        sources = source_hashes(repo, record["declaration"], provider_output_dirs(work["sdd"]["provider"]))
        inputs["code_snapshot"] = code_snapshot(repo, {work["requirement"]["path"]} | sdd_acceptance_outputs(repo, work))
        inputs["rules_snapshot"] = acceptance_digest({"rules": inputs["rules_snapshot"],
                                                      "declaration": record["declaration_sha256"], "sources": sources})
    return inputs


def completion_status(repo: Path, work: dict[str, Any] | None) -> dict[str, Any]:
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
        spec_state = light_spec_state(repo, work, provider_output_dirs(provider))
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
    gates = gate_state(work, fresh, sdd_state["ready"])
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
                "Mark each observed gate with `cao gate satisfy --gate " + "|".join(manual) + " --evidence \"<what was done>\"`."
                if manual else "Run `cao verify` so the verification gates become fresh."
            ),
        }

    if profile in {"standard", "comprehensive"} and not sdd_state["ready"]:
        return {"ready": False, "reason": sdd_state["reason"], "next": sdd_state.get("next"), "sdd_acceptance": sdd_state}
    if fresh:
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


def resume_payload(repo: Path, work: dict[str, Any], session_id: str | None = None, host: str | None = None, *, next_action: dict | None = None) -> dict[str, Any]:
    c = completion_status(repo, work)
    current = next_action or current_work_action(repo, work, completion=c)
    summary = work_summary(work)
    summary["next_action"] = current
    handoff = latest_handoff(repo, work["id"]) or {}
    return {
        "work_item": summary,
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
        "Verification need not pass or be configured before start. After start, rerun bootstrap "
        "for verification setup and follow `next` for path assessment and the native SDD workflow. "
        "CAO commands, numbered requirement documents and native SDD output writes remain allowed; "
        "direct edits to CAO-derived state remain denied."
    )


def work_prerequisite_action(repo: Path, work: dict | None) -> dict | None:
    """One prerequisite order for CLI navigation, synchronization and host reminders."""
    command = cao_invocation()["command"]
    restart = restart_state(repo, detect_current_host())
    if not restart["ready"]:
        return action("human", restart["next"], stage="startup", reason=restart["reason"])
    if not work:
        status = sdd_status(repo)
        startup = not status["installed"] or status["selected"] == "auto" and len(status["installed"]) > 1
        return action("agent", f"Run `{command} bootstrap` and resolve SDD selection/installation first." if startup else work_intake_instruction(),
                      stage="startup" if startup else "intake", reason="SDD_SELECTION_REQUIRED" if startup else "WORK_REQUIRED")
    if work.get("status") == "completed":
        return action("none", "Work item is completed.", stage="completed")
    if not work_sdd_ready(repo, work)[0]:
        return action("human", f"Restore the Work's selected SDD, then run `{command} bootstrap`; do not restart intake.", stage="startup", reason="SDD_NOT_INSTALLED")
    if work.get("status") in {"todo", "suspended"}:
        return action("agent", f"Resume this work item with `{command} resume --work {work['id']} --reactivate`.", stage="intake", reason="WORK_NOT_ACTIVE")
    if work.get("blockers"):
        return action("human", "Resolve the active blocker before continuing.", stage="recovery", reason="active blocker exists")
    baseline = verification_baseline_state(repo, work)
    if not baseline["ready"]:
        return action("human" if baseline['reason'] == 'VERIFICATION_REVIEW_REQUIRED' else "agent",
                      baseline["next"], stage="verification_setup", reason=baseline["reason"])
    assessment = path_assessment_state(repo, work)
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
    restart = restart_state(repo, host or detect_current_host())
    if not restart["ready"]:
        return restart["reason"] + ": " + restart["next"]
    inspection = setup_state(config(repo))
    current = current_work_action(repo, work)
    navigation = "## Read before acting\n" + json.dumps(agent_guidance(repo, work, next_action=current), ensure_ascii=False)
    if not work or work.get("status") in {"todo", "suspended", "completed"}:
        instruction = (STARTUP_ORDER if current.get('stage') == 'startup' else work_intake_instruction(work))
        return "# Coding Agent Orchestrator Work Intake\n" + navigation + "\n" + instruction + request_context(repo)
    baseline = verification_baseline_state(repo, work)
    if not baseline["ready"]:
        # Preserve continuity without calculating completion from invalid declarations.
        return "\n".join([
            "# Coding Agent Orchestrator Verification Setup",
            navigation,
            baseline["next"],
            json.dumps({"verification_setup": inspection, "work": verification_setup_context(work),
                        "session_id": session_id, "host": host}, ensure_ascii=False),
        ])
    p = resume_payload(repo, work, session_id=session_id, host=host, next_action=current)
    e = p["execution"] or {}
    lines = [
        "# Coding Agent Orchestrator Resume Context",
        navigation,
        f"Work Item: {p['work_item']['title']} ({p['work_item']['id']})",
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
    return "\n".join(lines)


@locked_repo
def register_session(
    repo: Path,
    work: dict[str, Any] | None,
    session_id: str,
    host: str,
    *,
    event: str = "session_start",
    kind: str | None = None,
    parent_session_id: str | None = None,
) -> dict[str, Any]:
    """Create or reattach a Session using the host-native ID as its lookup key."""
    if work:
        _refresh_work(repo, work)
    ts = now()
    existing = load_session(repo, session_id, host=host)
    previous_internal_id = existing.get("id") if existing else None
    effective_host = existing.get("host") if host == "unknown" and existing else host
    effective_host = effective_host or "unknown"
    # If an earlier adapter registered the Session before it knew the Host,
    # re-key it once the concrete Host arrives so the ID invariant stays true.
    if existing and existing.get("host") == "unknown" and effective_host != "unknown":
        internal_id = stable_session_id(effective_host, session_id)
    else:
        internal_id = existing["id"] if existing else stable_session_id(effective_host, session_id)
    if existing and existing.get("status") == "ended" and event in {"session_start", "subagent_start", "resume"}:
        started_at = ts
    else:
        started_at = (existing or {}).get("started_at", ts)
    session = {
        "schema_version": 2,
        "id": internal_id,
        "host": effective_host,
        "host_session_id": session_id,
        "work_item_id": work.get("id") if work else (existing or {}).get("work_item_id"),
        "kind": kind if kind in {"primary", "subagent"} else (existing or {}).get("kind", "primary"),
        "parent_session_id": parent_session_id if parent_session_id is not None else (existing or {}).get("parent_session_id"),
        "status": "active",
        "started_at": started_at,
        "last_event": event,
        "last_event_at": ts,
        "updated_at": ts,
        "ended_at": None,
        "end_reason": None,
        "last_handoff_id": (existing or {}).get("last_handoff_id"),
    }
    save_session(repo, session)
    if previous_internal_id and previous_internal_id != internal_id:
        previous_path = session_path(repo, previous_internal_id)
        if previous_path.exists():
            try:
                previous_path.unlink()
            except OSError:
                pass
    if work:
        work.setdefault("resume", {})["last_agent"] = effective_host
        save_work(repo, work)
    return session


# Fields that define what a Handoff snapshot *says*. Everything else (id,
# created_at, sequence) is record metadata, so comparing only these collapses
# the identical snapshots that every Stop event produces.
HANDOFF_IDENTITY_FIELDS = (
    "work_item", "session_id", "host", "reason", "step", "next_action",
    "path_assessment", "summary", "completed", "pending", "decisions",
    "blockers", "verification",
)


def handoff_identity(handoff: dict[str, Any]) -> dict[str, Any]:
    return {key: handoff.get(key) for key in HANDOFF_IDENTITY_FIELDS}


@locked_repo
def create_handoff(repo: Path, work: dict[str, Any], session_id: str | None, host: str | None, reason: str = "session stop") -> dict[str, Any]:
    _refresh_work(repo, work)
    session = load_session(repo, session_id, host=host) if session_id else None
    existing_records = list_handoff_records(repo, work["id"])
    sequence = max((int(item.get("sequence", 0) or 0) for item in existing_records), default=0) + 1
    current = current_work_action(repo, work)
    pending = list(work.get('resume', {}).get('pending', []))
    if current.get('type') != 'none' and current.get('instruction') and current['instruction'] not in pending:
        pending.append(current['instruction'])
    handoff = {
        "version": 1,
        "sequence": sequence,
        "id": new_id("handoff"),
        "work_item": work["id"],
        "session_id": session.get("id") if session else None,
        "host_session_id": session.get("host_session_id") if session else session_id,
        "host": host if host and host != "unknown" else (session or {}).get("host"),
        "reason": reason,
        "created_at": now(),
        "step": work.get("execution", {}).get("step"),
        "next_action": current,
        "path_assessment": work.get("path_assessment"),
        "summary": work.get("resume", {}).get("summary"),
        "completed": work.get("resume", {}).get("completed", []),
        "pending": pending,
        "decisions": work.get("decisions", [])[-8:],
        "blockers": work.get("blockers", []),
        "verification": work.get("verification"),
    }
    # Each Handoff is an independent immutable record. There is no flat latest
    # projection; readers derive the latest record from this history directory.
    # Repeated identical snapshots are idempotent: Stop fires after every tool
    # call, so without this a single Work Item accumulated dozens of copies of
    # the same state and the history became unreadable.
    latest = max(existing_records, key=lambda item: int(item.get("sequence", 0) or 0), default=None)
    if latest is not None and handoff_identity(latest) == handoff_identity(handoff):
        if session:
            session["last_handoff_id"] = latest["id"]
            save_session(repo, session)
        return latest
    save_json(handoff_record_path(repo, work["id"], handoff["id"]), handoff)
    if session:
        session["last_handoff_id"] = handoff["id"]
        save_session(repo, session)
    return handoff


def resolve_requirement(repo: Path, path: str | None) -> Path | None:
    if path:
        candidate = Path(path)
        p = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
        return p if p.is_file() else None
    files = requirement_files(repo, config(repo))
    return files[0] if len(files) == 1 else None


def ensure_initialized(repo: Path) -> None:
    if not (repo / CONFIG_REL).exists():
        raise RuntimeError("Project is not initialized. Run `cao init` first.")


@locked_command
def cmd_requirement_next_id(args: argparse.Namespace) -> int:
    """Allocate the next numbered requirement path without writing any content.

    CAO allocates the number and creates the file with a skeleton; the Agent
    edits that file. Creating it here matters because a mutating tool call is
    gated until a Work Item exists, so an Agent that has to `write` a brand new
    requirement file can be blocked before `cao start` can ever run.
    """
    repo = repo_root(args.repo or os.getcwd())
    ensure_initialized(repo)
    title = args.title or "requirement"
    path = new_requirement_path(repo, title, args.date)
    rel_path = rel(path, repo)
    number = re.match(r"^(\d+)-", path.name)
    number = number.group(1) if number else "001"
    created = False
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(requirement_skeleton(title, number, args.date, config(repo)), encoding="utf-8")
        created = True
    print(json.dumps({
        "ok": True,
        "number": number,
        "title": title,
        "path": rel_path,
        "absolute_path": str(path.resolve()),
        "directory": REQUIREMENT_DIR.as_posix(),
        "created": created,
        "next": f"Edit `{rel_path}` with the requirement content, then run `cao start --requirement {rel_path}`",
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_requirement_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo or os.getcwd())
    ensure_initialized(repo)
    files = [p for p in requirement_files(repo, config(repo)) if REQUIREMENT_DIR.as_posix() in p.as_posix()]
    items = []
    for p in sorted(files)[: max(0, args.limit)]:
        items.append({
            "path": rel(p, repo),
            "name": p.name,
            "sha256": sha256_file(p),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime)),
        })
    print(json.dumps({"ok": True, "directory": REQUIREMENT_DIR.as_posix(), "requirements": items, "next": f"{next_requirement_number(repo):03d}"}, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Return the always-on startup context for an Agent.

    Bootstrap establishes CAO state, verifies that an SDD is installed in the
    project, and only then exposes the Work Item next action. It never creates
    a Work Item and never reruns requirement intake merely because a session or
    Agent changed.
    """
    repo = repo_root(args.repo)
    # Idempotent re-check: covers projects provisioned before this step existed,
    # and files the user deleted. Never overwrites an existing file.
    project_instructions = install_project_instruction_templates(repo, getattr(args, "host", None) or detect_current_host())
    if not (repo / CONFIG_REL).exists():
        save_json(repo / CONFIG_REL, json.loads(json.dumps(DEFAULT_CONFIG)))
        save_index(repo, load_index(repo))
        for d in (WORK_DIR, SESSION_DIR, HANDOFF_DIR):
            (repo / d).mkdir(parents=True, exist_ok=True)

    ensure_language_preference(repo)
    status = sdd_status(repo)
    cfg = config(repo)
    selected = status["selected"]
    installed = status["installed"]
    active_id = load_index(repo).get("active_work")
    work = load_work(repo, active_id) if active_id else None
    # Bootstrap is a state-read boundary too. Reconcile native SDD state before
    # constructing a new-session context so a previous Agent's phase cursor
    # cannot remain stale after the native workflow has advanced.
    if (work and work.get("status") in {"active", "suspended"}
            and restart_state(repo, getattr(args, "host", None) or detect_current_host())["ready"]
            and reconcile_native_sdd_state(repo, work)):
        work = load_work(repo, active_id) or work

    # Session continuity must not depend solely on the host adapter being
    # installed. When the runtime exposes a host and a native session id, attach
    # the Session here as well so `cao sessions list` is never empty.
    identity = host_session_identity(getattr(args, "host", None))
    bootstrap_session = register_session(repo, work, identity[1], identity[0], event="bootstrap") if identity else None

    # Report, do not install. Adapters load when the host process starts, so a
    # bootstrap-time install could never deliver this session's SessionStart.
    adapter_host = identity[0] if identity else getattr(args, "host", None)
    bootstrap_adapter = host_adapter_status(repo, adapter_host) if adapter_host else None

    restart = restart_state(repo, adapter_host or detect_current_host())
    if not restart["ready"]:
        next_action = action("human", restart["next"], reason=restart["reason"], stage="startup")
        print(json.dumps({"ok": True, "mode": "host_restart_required", "restart": restart,
                          "work_item": {**work_summary(work), "next_action": next_action} if work else None,
                          "next": next_action,
                          "host_adapter": bootstrap_adapter, "project_instructions": project_instructions,
                          "guidance": agent_guidance(repo, work, "host_restart_required")}, ensure_ascii=False, indent=2))
        return 0

    # Existing work owns its provider choice. A missing provider is repaired
    # before any resume/implementation action is exposed.
    work_provider = str(((work or {}).get("sdd") or {}).get("provider") or "auto")
    if work and work_provider in SDD_CATALOG and not detect_sdd(repo, work_provider)["installed"]:
        next_out = {
            "ok": True,
            "mode": "sdd_install_required",
            "work_item": work_summary(work),
            "sdd": {"selected": work_provider, "status": detect_sdd(repo, work_provider)},
            "options": sdd_options(status),
            "next": action("human", f"SDD `{work_provider}` is not installed in this project. Run `{sdd_install_instruction(work_provider, getattr(args, 'host', None))}`, then run `cao bootstrap` again."),
        }
        next_out["project_instructions"] = project_instructions
        next_out["guidance"] = agent_guidance(repo, work, next_out["mode"])
        next_out['next']['instruction'] += ' ' + STARTUP_ORDER
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    if not installed:
        next_out = {
            "ok": True,
            "mode": "sdd_select",
            "work_item": work_summary(work) if work else None,
            "sdd": {"status": "not_installed", "selected": selected},
            "selection_required": True,
            "selection_policy": "explicit_user_choice_only",
            "startup_order": STARTUP_ORDER,
            "pending_request": pending_request(repo) or None,
            "options": sdd_options(status),
            "next": action("human", "HARD STOP: no SDD is installed and no provider is authorized. Ask the user to explicitly choose OpenSpec, BMad Method, or GitHub Spec Kit and wait for their answer. Do not infer a choice from recommendations, popularity, existing global CLIs, Agent preference, or task type. Do not run any provider install/select command before the user answers. After an explicit answer only, run `cao sdd install --provider <chosen-provider> --yes`. The installer must detect the active host from the current Agent runtime and install only that host's integration. If detection is unavailable or ambiguous, stop and request `--host` (or a provider-specific explicit integration/tools option); never use an all-hosts default. If the interaction channel cannot ask the user, stop with SDD_SELECTION_REQUIRED. Do not start or modify production code before installation completes."),
        }
        next_out["project_instructions"] = project_instructions
        next_out["guidance"] = agent_guidance(repo, work, next_out["mode"])
        next_out['next']['instruction'] += ' ' + STARTUP_ORDER
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    # The configured user language applies to every installed SDD, regardless
    # of which provider is selected for the current Work Item.
    language_configuration = apply_installed_sdd_languages(repo)
    if language_configuration["failures"]:
        failed = {
            "ok": False,
            "mode": "language_configuration_failed",
            "error": "SDD_LANGUAGE_CONFIGURATION_FAILED",
            "language_configuration": language_configuration,
            "next": action("human", "Repair the reported SDD language configuration, then rerun `cao bootstrap`."),
            "project_instructions": project_instructions,
        }
        failed["guidance"] = agent_guidance(repo, work, failed["mode"])
        print(json.dumps(failed, ensure_ascii=False, indent=2))
        return 2

    # If exactly one SDD is already installed, make it the project selection
    # automatically. Multiple installed SDDs require an explicit choice.
    if selected == "auto" and len(installed) == 1:
        select_sdd_provider(repo, installed[0]["provider"])
        selected = installed[0]["provider"]
    elif selected == "auto" and len(installed) > 1:
        next_out = {
            "ok": True,
            "mode": "sdd_select",
            "work_item": work_summary(work) if work else None,
            "sdd": {"status": "multiple_installed", "selected": "auto"},
            "selection_required": True,
            "selection_policy": "explicit_user_choice_only",
            "startup_order": STARTUP_ORDER,
            "pending_request": pending_request(repo) or None,
            "options": sdd_options(status),
            "next": action("human", "HARD STOP: multiple SDDs are installed and no provider is authorized. Ask the user to explicitly choose one and wait for their answer. Do not infer a choice from recommendations, task type, or Agent preference. Only after the user answers, run `cao sdd select --provider <chosen-provider>` and bootstrap again. If unable to ask, stop with SDD_SELECTION_REQUIRED."),
            "language_configuration": language_configuration,
        }
        next_out["project_instructions"] = project_instructions
        next_out["guidance"] = agent_guidance(repo, work, next_out["mode"])
        next_out['next']['instruction'] += ' ' + STARTUP_ORDER
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    inspection = setup_state(cfg)
    current = current_work_action(repo, work)
    mode = "verification_setup" if current.get("stage") == "verification_setup" else "resume" if work else "select"
    next_out = {
        "ok": True, "mode": mode,
        "pending_request": pending_request(repo) if not work else None,
        "work_item": work_summary(work) if work else None,
        "sdd": sdd_status(repo),
        "context": verification_setup_context(work) if mode == "verification_setup" else resume_payload(repo, work, next_action=current) if work else None,
        "next": current,
    }
    if next_out["work_item"]:
        next_out["work_item"]["next_action"] = current
    next_out["verification_setup"] = inspection
    if bootstrap_session:
        next_out["session"] = bootstrap_session
    if bootstrap_adapter is not None:
        next_out["host_adapter"] = bootstrap_adapter
        if not bootstrap_adapter["installed"]:
            next_out["host_adapter"]["next"] = f"cao host install --host {adapter_host}  (takes effect from the next session)"
    next_out["language_configuration"] = language_configuration
    next_out["project_instructions"] = project_instructions
    next_out["guidance"] = agent_guidance(repo, work, next_out["mode"], next_action=current)
    print(json.dumps(next_out, ensure_ascii=False, indent=2))
    return 0


def cmd_sdd_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    if (repo / CONFIG_REL).exists():
        status = sdd_status(repo)
        status["providers"] = [detect_sdd(repo, name, include_cli_version=True) for name in SDD_CATALOG]
        status["installed"] = [x for x in status["providers"] if x["installed"]]
        selected = status["selected"]
        status["selected_status"] = detect_sdd(repo, selected, include_cli_version=True) if selected in SDD_CATALOG else None
    else:
        providers = [detect_sdd(repo, name, include_cli_version=True) for name in SDD_CATALOG]
        status = {
            "selected": "auto",
            "selected_status": None,
            "installed": [x for x in providers if x["installed"]],
            "providers": providers,
            "selection_required": True,
        }
    print(json.dumps({"ok": True, **status}, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_sdd_select(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    detected = detect_sdd(repo, args.provider)
    if not detected["installed"]:
        print(json.dumps({
            "ok": False,
            "error": "SDD_NOT_INSTALLED",
            "provider": args.provider,
            "status": detected,
            "next": f"cao sdd install --provider {args.provider} --yes",
        }, ensure_ascii=False, indent=2))
        return 2
    select_sdd_provider(repo, args.provider)
    try:
        language_configuration = apply_provider_language(repo, args.provider)
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": args.provider, "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "selected": args.provider, "status": detect_sdd(repo, args.provider), "language_configuration": language_configuration}, ensure_ascii=False, indent=2))
    return 0


def detect_current_host(environ: dict[str, str] | None = None) -> str | None:
    """Detect the active coding-agent host from its runtime environment.

    Explicit CLI arguments take precedence at call sites. Detection is
    intentionally conservative: if multiple hosts appear active, return None
    instead of installing integrations for all of them.
    """
    env = os.environ if environ is None else environ
    declared = str(env.get("CAO_HOST") or "").strip().lower()
    aliases = {
        "claude": "claude-code", "claude-code": "claude-code",
        "codex": "codex", "pi": "pi",
    }
    if declared in aliases:
        return aliases[declared]

    matches: set[str] = set()
    if any(env.get(key) for key in ("CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")):
        matches.add("claude-code")
    if any(env.get(key) for key in ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME")):
        matches.add("codex")
    if any(env.get(key) for key in ("PI_CODING_AGENT", "PI_SESSION_ID")):
        matches.add("pi")
    return next(iter(matches)) if len(matches) == 1 else None


HOST_SESSION_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "claude-code": ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"),
    "codex": ("CODEX_THREAD_ID",),
    "pi": ("PI_SESSION_ID",),
}


def host_session_identity(host: str | None, environ: dict[str, str] | None = None) -> tuple[str, str] | None:
    """Resolve (host, native session id) when the runtime exposes both.

    Host adapters are supposed to register the Session through `host-event`, but
    that only happens when the adapter is actually installed. Returning None
    here keeps CAO from inventing an `unknown` Session for every command.
    """
    env = os.environ if environ is None else environ
    resolved = host or detect_current_host(env)
    if not resolved:
        return None
    for key in HOST_SESSION_ENV_KEYS.get(resolved, ()):
        value = str(env.get(key) or "").strip()
        if value:
            return resolved, value
    return None


def openspec_tool_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return str((SDD_CATALOG["openspec"]["install"].get("tool_by_host") or {}).get(host) or "") or None


def build_openspec_project_command(args: argparse.Namespace) -> list[str]:
    tools = args.tools
    if not tools:
        host = args.host or detect_current_host()
        tools = openspec_tool_for_host(host)
    if not tools:
        raise ValueError(
            "OpenSpec installation requires a detectable current host or explicit "
            "--host <claude-code|codex|pi> / --tools <tool-id>. Refusing --tools all "
            "because it installs integrations for unrelated hosts."
        )
    return ["openspec", "init", "--tools", tools]


def speckit_integration_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return str((SDD_CATALOG["speckit"]["install"].get("integration_by_host") or {}).get(host) or "") or None


def bmad_tool_for_host(host: str | None) -> str | None:
    if not host:
        return None
    return str((SDD_CATALOG["bmad"]["install"].get("tool_by_host") or {}).get(host) or "") or None


def sdd_install_instruction(provider: str, host: str | None = None, tools: str | None = None) -> str:
    if provider == "speckit":
        if host:
            return f"cao sdd install --provider speckit --host {host} --yes"
        return "cao sdd install --provider speckit --host <claude-code|codex|pi> --yes"
    if provider == "bmad":
        if tools:
            return f"cao sdd install --provider bmad --tools {tools} --yes"
        if host:
            return f"cao sdd install --provider bmad --host {host} --yes"
        return "cao sdd install --provider bmad --host <claude-code|codex|pi> --yes"
    return f"cao sdd install --provider {provider} --yes"


def build_bmad_project_command(args: argparse.Namespace, repo: Path) -> list[str]:
    tools = args.tools or bmad_tool_for_host(args.host or detect_current_host())
    if not tools:
        raise ValueError("BMad installation requires --host <claude-code|codex|pi> or explicit --tools <tool-id>")
    template = list(SDD_CATALOG["bmad"]["install"].get("project_command") or [])
    if not template:
        raise ValueError("BMad installation command template is not configured")
    replacements = {"{repo}": str(repo), "{tool}": tools}
    return [replacements.get(part, part) for part in template]


def build_speckit_project_command(args: argparse.Namespace) -> list[str]:
    integration = args.integration or speckit_integration_for_host(args.host or detect_current_host())
    if not integration:
        raise ValueError("Spec Kit installation requires --host <claude-code|codex|pi> or --integration <spec-kit integration>")
    command = ["specify", "init", "--here", "--force", "--non-interactive", "--integration", integration, "--ignore-agent-tools"]
    if args.integration_options:
        command.append(f"--integration-options={args.integration_options}")
    return command


def install_restart_hosts(args: argparse.Namespace, provider: str) -> list[str]:
    explicit = getattr(args, "tools", None) or getattr(args, "integration", None)
    if explicit:
        meta = SDD_CATALOG[provider]["install"]
        mapping = meta.get("integration_by_host") if provider == "speckit" else meta.get("tool_by_host")
        targets = {value.strip() for value in explicit.split(",")}
        return sorted(host for host, tool in (mapping or {}).items() if tool in targets)
    host = getattr(args, "host", None) or detect_current_host()
    return [host] if host else []


def install_restart_result(repo: Path) -> dict:
    restart = restart_state(repo)
    return {"restart_required": not restart["ready"], "restart": restart,
            **({"next": action("human", restart["next"], reason=restart["reason"]),
                "guidance": agent_guidance(repo, None, "host_restart_required")} if not restart["ready"] else {})}


def cmd_sdd_install(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    provider = args.provider
    if provider not in SDD_CATALOG:
        print(json.dumps({"ok": False, "error": "SDD_PROVIDER_REQUIRED", "options": sdd_options(sdd_status(repo))}, ensure_ascii=False, indent=2))
        return 2

    detected = detect_sdd(repo, provider)
    if detected["installed"] and not getattr(args, "upgrade", False):
        select_sdd_provider(repo, provider)
        try:
            language_configuration = apply_provider_language(repo, provider)
        except (OSError, UnicodeError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": provider, "reason": str(exc), **install_restart_result(repo)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, "status": "already_installed", "selected": provider, "sdd": detected, "language_configuration": language_configuration, **install_restart_result(repo)}, ensure_ascii=False, indent=2))
        return 0

    prereq = sdd_prerequisite_check(provider)

    meta = SDD_CATALOG[provider]["install"]
    external = choose_external_install_command(provider, getattr(args, "upgrade", False))
    plan = []
    if external:
        plan.append({"scope": "machine", "command": shlex.join(external)})
    project_command = list(meta.get("project_command") or [])
    if provider == "openspec":
        try:
            project_command = build_openspec_project_command(args)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": "SDD_INSTALL_ARGUMENTS_REQUIRED", "provider": provider, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    elif provider == "bmad":
        try:
            project_command = build_bmad_project_command(args, repo)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": "SDD_INSTALL_ARGUMENTS_REQUIRED", "provider": provider, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 2
    elif provider == "speckit":
        project_command = build_speckit_project_command(args)
    if project_command:
        plan.append({"scope": "project", "command": shlex.join(project_command)})

    if not args.yes:
        print(json.dumps({
            "ok": True,
            "status": "confirmation_required",
            "provider": provider,
            "sdd": detected,
            "prerequisites": prereq,
            "install_plan": plan,
            "next": f"Review the install plan and rerun `cao sdd install --provider {provider} --yes` after the user confirms.",
        }, ensure_ascii=False, indent=2))
        return 0

    # A plan is inspectable even when a tool is missing; actual installation
    # remains blocked before any machine/project command runs.
    if not prereq["ok"]:
        print(json.dumps({"ok": False, "error": "SDD_PREREQUISITE_FAILED", "provider": provider, "prerequisites": prereq}, ensure_ascii=False, indent=2))
        return 2
    results = []
    if external:
        result = run_install_command(external, repo); results.append(result)
        if result["exit_code"] != 0:
            print(json.dumps({"ok": False, "error": "SDD_MACHINE_INSTALL_FAILED", "provider": provider, "results": results}, ensure_ascii=False, indent=2))
            return 1

    # Provider project installers are invoked only after explicit user
    # confirmation via --yes. Built-in commands are constructed in non-
    # interactive form so Bootstrap never gets trapped inside a provider prompt.
    if project_command:
        try:
            result = subprocess.run(project_command, cwd=repo, text=True)
            results.append({"command": shlex.join(project_command), "exit_code": result.returncode})
        except OSError as exc:
            results.append({"command": shlex.join(project_command), "exit_code": 127, "stderr": str(exc)})
            result = None
        if result is None or result.returncode != 0:
            print(json.dumps({"ok": False, "error": "SDD_PROJECT_INSTALL_FAILED", "provider": provider, "results": results}, ensure_ascii=False, indent=2))
            return 1

    final = detect_sdd(repo, provider)
    if not final["installed"]:
        print(json.dumps({"ok": False, "error": "SDD_INSTALL_NOT_DETECTED", "provider": provider, "results": results, "status": final}, ensure_ascii=False, indent=2))
        return 1
    select_sdd_provider(repo, provider)
    # Persist before language configuration: an installed provider still needs
    # its host resources reloaded if the language update subsequently fails.
    require_restart(repo, provider, install_restart_hosts(args, provider))
    try:
        language_configuration = apply_provider_language(repo, provider)
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": provider, "reason": str(exc), "sdd": final, "results": results, **install_restart_result(repo)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "status": "installed", "selected": provider, "sdd": final, "results": results, "language_configuration": language_configuration, **install_restart_result(repo)}, ensure_ascii=False, indent=2))
    return 0


def record_workflow_unverified(repo: Path, work: dict[str, Any], provider: str, name: str, reason: str, operation: str = "select") -> None:
    """Persist an auditable trace for a documented native-workflow exception."""
    identity = {"work_item_id": work["id"], "event": "workflow.unverified_exception", "workflow": name,
                "provider": provider, "requirement_sha256": work["requirement"]["sha256"], "reason": reason}
    key = uuid.uuid4().hex
    queue_event(work, {
        "event_id": "evt-" + key[:20],
        "idempotency_key": key,
        "event": "workflow.unverified_exception",
        "work_item_id": work["id"],
        "source": "cao",
        "provider": provider,
        "requirement_sha256": identity["requirement_sha256"],
        "workflow": name,
        "reason": reason,
        "evidence_source": "agent_declaration",
        "operation": operation,
        "occurred_at": now(),
    })


@locked_command
def cmd_sdd_workflow(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    sdd = work.setdefault("sdd", {})
    provider = str(sdd.get("provider") or "none")
    unverified_reason = str(getattr(args, "reason", "") or "").strip()
    allow_unverified = bool(getattr(args, "allow_unverified", False))
    if allow_unverified and not unverified_reason:
        print(json.dumps({"ok": False, "error": "UNVERIFIED_REASON_REQUIRED", "reason": "--allow-unverified requires --reason explaining why the native workflow cannot be verified."}, ensure_ascii=False, indent=2)); return 2

    if args.workflow_action == "select":
        name = str(args.name or "").strip()
        entrypoint = str(args.entrypoint or "").strip()
        if not name or not entrypoint:
            print(json.dumps({"ok": False, "error": "WORKFLOW_NAME_AND_ENTRYPOINT_REQUIRED"}, ensure_ascii=False, indent=2)); return 2
        skills = provider_native_skills(repo, provider)
        selected = workflow_skill_identity(repo, provider, entrypoint)
        explicit_skill = workflow_entrypoint_tokens(entrypoint)[:2] == ["invoke", "skill"]
        if not allow_unverified and not selected and (explicit_skill or (skills and provider == "bmad")):
            next_step = (f'Select an installed Skill: `cao sdd workflow select --name {skills[0]["name"]} --entrypoint "invoke skill {skills[0]["name"]}"`.'
                         if skills else "No matching native Skill is installed. Inspect the provider installation and its documented CLI/Skill entrypoint.")
            print(json.dumps({"ok": False, "error": "NATIVE_SKILL_REQUIRED", "skills": skills,
                              "next": next_step})); return 2
        if allow_unverified:
            check = {"ok": True, "reason": None}
        else:
            check = validate_workflow_entrypoint(provider, name, entrypoint)
        if not check["ok"]:
            print(json.dumps({
                "ok": False,
                "error": "NATIVE_WORKFLOW_ENTRYPOINT_INVALID",
                "reason": check["reason"],
                "provider": provider,
                "next": f"Record the provider's real native workflow entrypoint, or pass `--allow-unverified --reason \"<why>\"` to record a documented exception.",
            }, ensure_ascii=False, indent=2)); return 2
        sdd["workflow"] = {
            "name": name,
            "entrypoint": entrypoint,
            "status": "selected",
            "selected_at": now(),
            "requirement_sha256": work["requirement"]["sha256"],
            "provider": provider,
            "artifacts": [],
            "execution": None,
            "unverified": {"allowed": True, "reason": unverified_reason, "at": now(), "by": str(getattr(args, "assessed_by", "agent") or "agent")} if allow_unverified else None,
        }
        if selected == "bmad-build":
            sdd["native_route_required"] = True
        if allow_unverified:
            record_workflow_unverified(repo, work, provider, name, unverified_reason)
        work.setdefault("execution", {})["step"] = "native-sdd-workflow-selected"
        guidance = skill_invocation_guidance(repo, provider, entrypoint)
        if selected == "bmad-build":
            guidance += (" CAO quality profile and BMAD route are separate. After native planning creates a SPEC, "
                         "bind it immediately with `cao sdd artifact bind --artifact <spec>`; submit installed routing rules, "
                         "their hashes/quotes, required artifacts and omitted-planning replacements with "
                         "`cao sdd route propose --declaration <route.json>` before product implementation. "
                         "Direct Build does not lower standard gates. For dispatch, present the native SPEC summary, "
                         "run `cao sdd approval request`, and wait for explicit user approval; technical option answers "
                         "are not SPEC approval. Run `cao sdd sync` at native stage transitions.")
        work["execution"]["next_action"] = action("agent", f"Execute the selected native SDD workflow `{name}` via `{entrypoint}` now. Do not skip its native prerequisites, and do not implement the change yourself instead of running the workflow - `cao done` refuses completion until it has actually run and its artifacts are recorded.{guidance} Then record the execution with `cao sdd workflow execute --command \"<command actually run>\" --exit-code <code> --work {work['id']}`, and record the real output files with `cao sdd workflow complete --work {work['id']} --artifact <repo-relative-file>`.")
    elif args.workflow_action == "execute":
        workflow = sdd.get("workflow") or {}
        if not workflow.get("name") or not workflow.get("entrypoint"):
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_NOT_SELECTED", "next": f"Run `cao sdd workflow select --name \"<workflow name>\" --entrypoint \"<native workflow command or skill>\" --work {work['id']}` first."}, ensure_ascii=False, indent=2)); return 2
        command = str(getattr(args, "command", "") or "").strip()
        if not command:
            print(json.dumps({"ok": False, "error": "WORKFLOW_COMMAND_REQUIRED", "reason": "Record the command that was actually executed."}, ensure_ascii=False, indent=2)); return 2
        if re.search(r'\s->\s', command) or any(Path(identity).name == 'render_skill.py' for identity in workflow_command_identities(command)):
            print(json.dumps({'ok': False, 'error': 'RENDERER_IS_NOT_WORKFLOW_EXECUTION',
                              'next': 'A renderer only loads instructions. Follow the native workflow; record its actual session invocation (invoke skill <installed skill>) without explanatory pseudo-shell suffixes. Native review and artifacts are checked separately.'})); return 2
        selected_skill = workflow_skill_identity(repo, provider, str(workflow["entrypoint"]))
        executed_skill = workflow_skill_identity(repo, provider, command)
        if executed_skill and selected_skill != executed_skill:
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_IDENTITY_CHANGED",
                              "next": "Reselect the actual workflow with `cao sdd workflow select` before recording its execution."})); return 2
        if not allow_unverified:
            check = validate_workflow_entrypoint(provider, str(workflow.get("name") or ""), command)
            if not check["ok"]:
                print(json.dumps({
                    "ok": False,
                    "error": "NATIVE_WORKFLOW_EXECUTION_INVALID",
                    "reason": check["reason"],
                    "provider": provider,
                    "next": f"Record the command that actually ran the `{workflow.get('name')}` workflow, or pass `--allow-unverified --reason \"<why>\"`.",
                }, ensure_ascii=False, indent=2)); return 2
        # A new run requires a new completion and a separately accepted failure exception.
        workflow["status"] = "executed"
        workflow["requirement_sha256"] = work["requirement"]["sha256"]
        workflow["provider"] = provider
        workflow["artifacts"] = []
        for key in ("completed_at", "artifact_hashes", "failure_exception"):
            workflow.pop(key, None)
        workflow["execution"] = {
            "command": command,
            "exit_code": int(getattr(args, "exit_code", 0) or 0),
            "executed_at": now(),
            "output_tail": str(getattr(args, "output", "") or "").strip()[-2000:],
        }
        workflow["unverified"] = {"allowed": True, "reason": unverified_reason, "at": now(), "by": "agent"} if allow_unverified else workflow.get("unverified")
        if allow_unverified:
            record_workflow_unverified(repo, work, provider, workflow["name"], unverified_reason, "execute")
        work.setdefault("execution", {})["step"] = "native-sdd-workflow-executed"
        work["execution"]["next_action"] = action("agent", f"Record the real output files produced by `{workflow.get('name')}` with `cao sdd workflow complete --work {work['id']} --artifact <repo-relative-file>` (repeat --artifact for each file).")
    elif args.workflow_action == "complete":
        planning = planning_state(repo, work)
        if not planning['ready']:
            print(json.dumps({'ok': False, 'error': planning['reason'].split(':', 1)[0], **planning})); return 2
        workflow = sdd.get("workflow") or {}
        if not workflow.get("name") or not workflow.get("entrypoint"):
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_NOT_SELECTED", "next": f"Run `cao sdd workflow select --name \"<workflow name>\" --entrypoint \"<native workflow command or skill>\" --work {work['id']}` first."}, ensure_ascii=False, indent=2)); return 2
        if not workflow.get("execution") and not allow_unverified:
            print(json.dumps({
                "ok": False,
                "error": "NATIVE_WORKFLOW_EXECUTION_REQUIRED",
                "reason": f"`{workflow.get('name')}` has no recorded execution. Run the workflow, then record it with `cao sdd workflow execute --command \"<command actually run>\" --exit-code <code> --work {work['id']}`.",
                "workflow": workflow,
            }, ensure_ascii=False, indent=2)); return 2
        execution = workflow.get("execution") or {}
        if execution and (workflow.get("requirement_sha256") != work["requirement"]["sha256"]
                          or workflow.get("provider") != provider):
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_EVIDENCE_STALE",
                              "next": f"Select and execute the workflow for this requirement with `cao sdd workflow select` and `cao sdd workflow execute --work {work['id']}`."}, ensure_ascii=False)); return 2
        if execution and execution.get("exit_code") != 0 and not allow_unverified:
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_EXECUTION_FAILED",
                              "exit_code": execution.get("exit_code"),
                              "next": "Rerun the native workflow successfully; accepting its failed run requires complete --allow-unverified --reason \"<why>\"."}, ensure_ascii=False)); return 2
        paths = list(args.artifact or [])
        if not paths:
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_ARTIFACT_REQUIRED", "reason": "At least one actual output file must be recorded."}, ensure_ascii=False, indent=2)); return 2
        dir_check = validate_workflow_artifacts(repo, provider, paths)
        if not dir_check["ok"]:
            print(json.dumps({
                "ok": False,
                "error": "NATIVE_WORKFLOW_ARTIFACT_OUTSIDE_PROVIDER_DIRS",
                "reason": dir_check["reason"],
                "provider": provider,
                "offending": dir_check["offending"],
                "next": f"Record only files the native workflow produced under {', '.join(provider_output_dirs(provider)) or 'the provider directory'}; product source files are not SDD artifacts.",
            }, ensure_ascii=False, indent=2)); return 2
        refs = []
        try:
            for value in paths:
                ref = artifact_reference(repo, value, role="native-sdd-output", native_type=workflow["name"], native_ref=workflow["entrypoint"])
                refs.append(ref)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": "ARTIFACT_INVALID", "reason": str(exc)}, ensure_ascii=False, indent=2)); return 2
        artifacts = sdd.setdefault("artifacts", [])
        for ref in refs:
            artifacts[:] = [item for item in artifacts if item.get("path") != ref["path"]]
            artifacts.append(ref)
        workflow["artifacts"] = [ref["path"] for ref in refs]
        workflow["artifact_hashes"] = {ref["path"]: ref["sha256"] for ref in refs}
        workflow["requirement_sha256"] = work["requirement"]["sha256"]
        workflow["provider"] = provider
        workflow.pop("failure_exception", None)
        if execution and execution.get("exit_code") != 0:
            workflow["failure_exception"] = {"reason": unverified_reason, "at": now(),
                                              "exit_code": execution["exit_code"]}
            queue_event(work, {"event": "workflow.execution_failure_exception", "work_item_id": work["id"],
                               "source": "cao", "provider": provider, "workflow": workflow["name"],
                               "requirement_sha256": work["requirement"]["sha256"],
                               "reason": unverified_reason, "exit_code": execution["exit_code"], "occurred_at": now()})
        selections = {}
        try:
            for value in getattr(args, "plan_item", None) or []:
                path, item = value.rsplit("#", 1)
                if not path or not item:
                    raise ValueError("PLAN_ITEM_INVALID")
                selections.setdefault(path, []).append(item)
            scope = bind_plan_scope(repo, work, getattr(args, "plan", None) or [],
                                    selections, bool(getattr(args, "no_plan", False)))
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc).split(":", 1)[0], "reason": str(exc),
                              "next": "Supply associated --plan <path> and shared --plan-item <path>#<story-id>; --no-plan cannot remove an existing valid plan scope."}, ensure_ascii=False)); return 2
        sdd["plan_scope"] = scope
        if scope["mode"] == "none":
            queue_event(work, {"event": "workflow.no_plan_declared", "work_item_id": work["id"],
                               "source": "cao", "provider": provider, "workflow": workflow["name"],
                               "requirement_sha256": work["requirement"]["sha256"], "occurred_at": now()})
        workflow["status"] = "completed"
        workflow["completed_at"] = now()
        if allow_unverified and not (execution and execution.get("exit_code") != 0):
            workflow["unverified"] = {"allowed": True, "reason": unverified_reason, "at": now(), "by": "agent"}
            record_workflow_unverified(repo, work, provider, str(workflow.get("name") or ""), unverified_reason, "complete")
        work["execution"]["step"] = "native-sdd-workflow-completed"
        work["execution"]["next_action"] = action("agent", "Continue implementation/review/verification according to the selected path; record manual reviews with `cao gate satisfy`, record additional native workflow outputs if produced, then run configured `cao verify` for derived gates and `cao done`.")
    save_work(repo, work)
    print(json.dumps({"ok": True, "work_item": work_summary(work), "workflow": sdd.get("workflow")}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_sdd_spec(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    try:
        binding = make_binding(repo, work, args.spec, args.criteria, args.criteria_section,
                               provider_output_dirs(work["sdd"]["provider"]))
        previous = work["sdd"].get("spec_binding")
        if previous == binding:
            reconcile_native_sdd_state(repo, work)
            print(json.dumps({"ok": True, "status": "unchanged", "binding": binding, "native_sync": native_sync_state(repo, work)}, ensure_ascii=False)); return 0
        if previous and not args.replace:
            raise ValueError("LIGHT_SPEC_REPLACE_REQUIRED")
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); return 2
    work["sdd"]["spec_binding"] = binding
    for ref, role in ((binding["spec"], "spec"), (binding["criteria"], "acceptance-criteria")):
        artifacts = work["sdd"].setdefault("artifacts", [])
        if not any(item.get("path") == ref["path"] for item in artifacts):
            artifacts.append({**ref, "role": role})
    queue_event(work, {"event": "spec.bound", "work_item_id": work["id"], "source": "cao",
                       "provider": binding["provider"], "requirement_sha256": binding["requirement_sha256"],
                       "binding_sha256": binding_digest(binding), "occurred_at": now()})
    save_work(repo, work)
    reconcile_native_sdd_state(repo, work)
    print(json.dumps({"ok": True, "status": "bound", "binding": binding,
                      "native_sync": native_sync_state(repo, work),
                      "next": "Follow the native implementation/review workflow. After reviewing any SPEC changes, rebind if needed, run configured `cao verify`, then `cao done`."}, ensure_ascii=False)); return 0


@locked_command
def cmd_sdd_review(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") != "active":
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    try:
        records = work.setdefault("sdd", {}).setdefault("native_reviews", {})
        identity = host_session_identity(None)
        runtime_model = os.environ.get('PI_MODEL') if identity and identity[0] == 'pi' else None
        if args.review_action in {'begin', 'attach'}:
            args.model = runtime_model_name(args.model, runtime_model, os.environ.get('PI_PROVIDER'))
            if runtime_model and runtime_model != args.model:
                print(json.dumps({'ok': False, 'error': 'NATIVE_REVIEW_MODEL_MISMATCH',
                                  'expected_model': runtime_model, 'actual_model': args.model,
                                  'next': 'Use the current model in a new independent reviewer session; a different model is not required.'}))
                return 2
        if args.review_action == "begin":
            if not args.model.strip():
                raise ValueError("NATIVE_REVIEW_MODEL_REQUIRED")
            path = repo_file(repo, args.instructions)
            expected_name = "step-04-review.md" if dispatch_review(work) else "step-oneshot.md"
            if path.name != expected_name or not inside(path, repo / "_bmad/render") or (repo / "_bmad/render").resolve() != repo / "_bmad/render" or not path.is_file():
                raise ValueError("NATIVE_REVIEW_INSTRUCTIONS_INVALID")
            content = path.read_text()
            layers = sorted(set(re.findall(r"(?m)^####[^\n]*\(`?([a-z][a-z0-9-]*)`?\)", content)))
            if not layers or args.layer not in layers:
                raise ValueError("NATIVE_REVIEW_LAYER_NOT_ACTIVE")
            if dispatch_review(work) and layers != active_layers(repo, work):
                raise ValueError("NATIVE_REVIEW_CONTRACT_MISMATCH")
            records["_contract"] = {"sources": [path.relative_to(repo).as_posix()], "layers": layers}
            if any(record.get('model') != args.model for key, record in records.items() if key != '_contract'):
                # A model change starts a new review round; never mix layers
                # from two capability levels while retaining their old passes.
                for key in list(records):
                    if key != '_contract':
                        records.pop(key)
            inputs = review_inputs(repo, work, code_snapshot)
            primary_ids = [session.get("host_session_id") for session in list_sessions(repo)
                           if session.get("work_item_id") == work["id"] and session.get("kind") == "primary"]
            existing = records.get(args.layer) or {}
            matching_inputs = (existing.get('inputs') == inputs or (existing.get('status') == 'classified'
                               and all(existing.get('inputs', {}).get(k) == v for k, v in inputs.items() if k != 'code_snapshot')))
            reused = (dispatch_review(work) and not args.restart and existing.get('status') in {'running', 'classified'}
                      and matching_inputs and existing.get('model') == args.model)
            run = existing if reused else {"id": new_id("review"), "status": "running", "inputs": inputs,
                   "input_sha256": native_digest(inputs), "model": args.model,
                   "existing_session_ids": [session['id'] for session in list_sessions(repo)],
                   "primary_session_ids": primary_ids}
            records[args.layer] = run
            output = {"ok": True, "review": run, "active_layers": layers, 'reused': reused}
            output['reviewer_start'] = (f'{cao_invocation()["command"]} sdd review attach --work {work["id"]} '
                f'--layer {args.layer} --run {run["id"]} --input-sha256 {run["input_sha256"]} --model {shlex.quote(args.model)}')
            output['next'] = ('Begin ALL active layers before launching reviewers. Each new independent host session must first run reviewer_start '
                'to acknowledge this ticket and frozen input, then inspect that exact code without product writes. '
                + ('Preserve unmodified native Markdown/JSON findings for each layer; classify ALL layers before patches, then resolve ALL layers. '
                   'Use `cao sdd review classify --layer <layer> --report <triage.json>`, then `cao sdd review resolve --layer <layer> --report <resolved.json>`.'
                   if dispatch_review(work) else
                   'Preserve raw Markdown findings and submit triage with `cao sdd review submit --layer <layer> --report <report.json>`.')
                + ' Before launch, inspect the installed agent definitions: the reviewer needs shell access to execute reviewer_start and the CAO host adapter. '
                'Use an available agent name; do not assume task/general-purpose exist. Preserve native prompts/output formats, including Markdown. '
                'If no capable subagent is available, use a new independent external host session with its CAO adapter. Never reuse results from before this ticket.')
            if reused:
                output['next'] = dispatch_review_next(records, layers, inputs)
        elif args.review_action == 'attach':
            run = records.get(args.layer) or {}
            identity = host_session_identity(None)
            session = load_session(repo, identity[1], host=identity[0]) if identity else None
            if session and any(key != args.layer and (record.get('execution_binding') or {}).get('session_id') == session['id']
                               for key, record in records.items() if key != '_contract'):
                raise ValueError('NATIVE_REVIEW_SESSION_REUSED')
            attached = attach_reviewer(run, review_inputs(repo, work, code_snapshot), identity, session,
                                       args.model, args.run, args.input_sha256)
            output = {'ok': True, 'review': attached,
                      'next': 'Read the frozen review input and native instructions. Return actual findings; do not patch product code.'}
        elif args.review_action == 'findings':
            source = provider_artifact(repo, args.source, ['_bmad-output'])
            output = {'ok': True, 'source': source.relative_to(repo).as_posix(),
                      'source_sha256': sha256_file(source), 'findings': load_findings(source)}
            print(json.dumps(output, ensure_ascii=False)); return 0
        elif args.review_action == "classify":
            output = {"ok": True, "review": classify_review(repo, work, args.layer, args.report, code_snapshot),
                      "next": dispatch_review_next(records, active_layers(repo, work), review_inputs(repo, work, code_snapshot))}
        elif args.review_action == "resolve":
            output = {"ok": True, "review": resolve_review(repo, work, args.layer, args.report, code_snapshot)}
        else:
            output = {"ok": True, "review": submit_review(repo, work, args.layer, args.report, code_snapshot)}
        queue_event(work, {"event": "sdd.native_review." + args.review_action,
                           "work_item_id": work["id"], "layer": args.layer, "occurred_at": now()})
        save_work(repo, work)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); return 2
    print(json.dumps(output, ensure_ascii=False)); return 0


def cmd_verification_smoke(args: argparse.Namespace) -> int:
    try:
        result = run_smoke(repo_root(args.repo), args.server_command, args.path, args.method,
                           args.body, args.expect_status, args.timeout)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result)); return 0 if result["ok"] else 2


def cmd_sdd_acceptance(args: argparse.Namespace) -> int:
    from scripts.sdd_acceptance_commands import dispatch
    return dispatch(args, sys.modules[__name__])


def resolve_work(repo: Path, work_id: str | None = None) -> dict[str, Any] | None:
    index = load_index(repo)
    wid = work_id or index.get("active_work")
    return load_work(repo, wid) if wid else None


def resolve_artifact_path(repo: Path, value: str) -> tuple[Path, str]:
    resolved = repo_file(repo, value)
    return resolved, resolved.relative_to(repo.resolve()).as_posix()


def artifact_reference(repo: Path, value: str, role: str | None = None, native_type: str | None = None, native_ref: str | None = None) -> dict[str, Any]:
    path, relative = resolve_artifact_path(repo, value)
    if not path.is_file():
        raise ValueError(f"Artifact file not found: {relative}")
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "role": role,
        "native_type": native_type,
        "native_ref": native_ref,
    }


@locked_command
def cmd_artifact_add(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    try:
        ref = artifact_reference(repo, args.path, args.role, args.native_type, args.native_ref)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "ARTIFACT_INVALID", "reason": str(exc)}, ensure_ascii=False, indent=2)); return 2
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    artifacts[:] = [a for a in artifacts if a.get("path") != ref["path"]]
    artifacts.append(ref)
    save_work(repo, work)
    print(json.dumps({"ok": True, "artifact": ref, "work_item": work_summary(work)}, ensure_ascii=False, indent=2)); return 0


@locked_repo
def mark_artifact_confirmed(repo: Path, work: dict[str, Any], value: str, confirmed_by: str = "user") -> dict[str, Any]:
    """Record explicit confirmation for the current content hash of a native artifact."""
    _refresh_work(repo, work)
    sdd = work.setdefault("sdd", {})
    artifacts = sdd.setdefault("artifacts", [])
    role = None
    native_type = None
    native_ref = sdd.get("native_ref")
    for existing in artifacts:
        if existing.get("path") == resolve_artifact_path(repo, value)[1]:
            role = existing.get("role")
            native_type = existing.get("native_type")
            native_ref = existing.get("native_ref") or native_ref
            break
    ref = artifact_reference(repo, value, role=role, native_type=native_type, native_ref=native_ref)
    ref.update({"confirmed_at": int(time.time()), "confirmed_by": confirmed_by, "status": "confirmed"})
    artifacts[:] = [existing for existing in artifacts if existing.get("path") != ref["path"]]
    artifacts.append(ref)
    save_work(repo, work)
    return ref


@locked_command
def cmd_artifact_confirm(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    try:
        ref = mark_artifact_confirmed(repo, work, args.path, args.confirmed_by)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "ARTIFACT_INVALID", "reason": str(exc)}, ensure_ascii=False, indent=2)); return 2
    print(json.dumps({"ok": True, "artifact": ref, "work_item": work_summary(work)}, ensure_ascii=False, indent=2)); return 0


def cmd_artifact_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    print(json.dumps({"ok": True, "work_item": work["id"], "artifacts": work.get("sdd", {}).get("artifacts", [])}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_artifact_remove(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    _, relative = resolve_artifact_path(repo, args.path)
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    before = len(artifacts)
    work["sdd"]["artifacts"] = [a for a in artifacts if a.get("path") != relative]
    save_work(repo, work)
    print(json.dumps({"ok": True, "removed": before - len(work["sdd"]["artifacts"]), "artifacts": work["sdd"]["artifacts"]}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_artifact_refresh(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    artifacts = work.setdefault("sdd", {}).setdefault("artifacts", [])
    target = args.path
    refreshed = []
    for item in artifacts:
        if target:
            _, relative = resolve_artifact_path(repo, target)
            if item.get("path") != relative:
                continue
        p = repo / item["path"]
        if p.is_file():
            previous_sha = item.get("sha256")
            current_sha = sha256_file(p)
            item["sha256"] = current_sha
            if previous_sha and previous_sha != current_sha:
                item.pop("confirmed_at", None)
                item.pop("confirmed_by", None)
                item["status"] = "generated"
            refreshed.append(item)
    if target and not refreshed:
        print(json.dumps({"ok": False, "error": "ARTIFACT_NOT_FOUND", "path": target}, ensure_ascii=False, indent=2)); return 2
    save_work(repo, work)
    print(json.dumps({"ok": True, "refreshed": refreshed, "artifacts": artifacts}, ensure_ascii=False, indent=2)); return 0


@locked_command
def _initialize_state(args: argparse.Namespace):
    repo = repo_root(args.repo)
    cfg_path = repo / CONFIG_REL
    if cfg_path.exists() and not args.force:
        out = {"ok": True, "status": "already_initialized", "config": str(cfg_path)}
        pending = [h for h in HOST_ADAPTER_META if not host_adapter_status(repo, h)["installed"]]
        if pending:
            out["host_adapters"] = {"not_installed": pending, "next": f"cao host install --host {'|'.join(pending)}"}
        print(json.dumps(out, ensure_ascii=False, indent=2)); return 0

    # Host adapters are process-startup integrations, so they belong to
    # provisioning. The host must be explicit: guessing here would silently wire
    # the wrong host's hooks. An unknown host is reported as an unresolved
    # adapter instead of failing initialization, so a plain-shell `cao init`
    # (and CI) still works while the hook gap stays visible.
    host = args.host or detect_current_host()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if args.sdd:
        cfg["sdd"]["provider"] = args.sdd
    if getattr(args, "language", None):
        cfg.setdefault("language", {})["user"] = args.language.strip()
    verify_scripts = args.verify_script or args.verify_command or []
    test_scripts = getattr(args, "test_script", None) or []
    if verify_scripts or test_scripts:
        general = [{"name": f"script-{i}", "command": str(command).strip(), "required": True}
                   for i, command in enumerate(verify_scripts, 1)]
        tests = [{"name": f"test-{i}", "command": str(command).strip(), "required": True, "gates": ["tests"]}
                 for i, command in enumerate(test_scripts, 1)]
        cfg["verification"]["scripts"] = normalize_scripts(general + tests)
        # Informational time only; accepted configuration hashes are authoritative.
        cfg["verification"]["configured_at"] = now()
    save_json(cfg_path, cfg)
    index = load_index(repo)
    save_index(repo, index)
    for d in (WORK_DIR, SESSION_DIR, HANDOFF_DIR):
        (repo / d).mkdir(parents=True, exist_ok=True)
    return repo, cfg_path, host


@locked_command
def cmd_verification_configure(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    path = repo / CONFIG_REL
    scripts = []
    raw = {}
    try:
        scripts = declared_scripts(args.verify_script or [], args.test_script or [], getattr(args, 'security_script', None) or [])
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        candidate, changed = prepare_config(raw, scripts, args.replace, now())
        effective = json.loads(json.dumps(DEFAULT_CONFIG))
        deep_merge(effective, candidate)
        wid = getattr(args, "work", None) or load_index(repo).get("active_work")
        # An accepted standard cannot be silently reanchored. Only the first
        # valid baseline of a Work started with empty setup is established here.
        work = _read_work(repo, wid) if wid else None
        initial_baseline = bool(work and work.get("status") == "active" and
            not setup_state(config(repo))["ready"] and
            (work.get("acceptance") or {}).get("config_sha256") == verification_snapshot(config(repo), repo) and
            not work.get("verification") and not work.get("gate_status"))
        review = bool(work and work.get("status") == "active" and
                      not initial_baseline and
                      (work.get("acceptance") or {}).get("config_sha256") != verification_snapshot(effective, repo))
    except ValueError as exc:
        error = "VERIFICATION_CONFIG_INVALID" if isinstance(exc, json.JSONDecodeError) else str(exc).split(":", 1)[0]
        out = {"ok": False, "error": error, "reason": str(exc)}
        if error == "VERIFICATION_CONFIG_REPLACE_REQUIRED":
            out.update(current=setup_state(raw), requested=scripts,
                       next=action("human", "Review the existing and requested verification declarations; use --replace only to explicitly replace them."))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2
    if changed:
        save_json(path, candidate)
    if initial_baseline and setup_state(effective)["ready"]:
        anchor_verification(repo, work, effective)
        queue_event(work, {"event": "verification.baseline.established", "work_item_id": work["id"],
                           "source": "cao", "acceptance": work["acceptance"], "occurred_at": now()})
        save_work(repo, work)
    out = {"ok": True, "status": "configured" if changed else "unchanged", "config": str(path),
           "verification_setup": setup_state(effective), "acceptance_review_required": review,
           "initial_baseline_established": bool(initial_baseline and setup_state(effective)["ready"])}
    if review:
        out["next"] = action("human", f'Review changed acceptance and explicitly run `cao reopen --work {wid} --reanchor --reason "Reviewed acceptance changes"`.')
        if work["sdd"].get("native_route_required") or any(s["name"] == "bmad-build" for s in provider_native_skills(repo, work["sdd"]["provider"])):
            ensure_verification_review(repo, work, effective)
            out["next"] = action("human", f'Present the changed verification scripts and wait for explicit user `批准验证基线` (approve verification baseline) through the host. Then `cao reopen --work {wid} --reanchor --reason "Reviewed acceptance changes"`. Agent reason text is not human approval.')
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    prepared = _initialize_state(args)
    if isinstance(prepared, int):
        return prepared
    repo, cfg_path, host = prepared
    # Host installation may run an external package manager; never hold state locks.
    if host:
        adapters = install_host_adapters(repo, [host])
    else:
        adapters = {
            "hosts": [],
            "installed": [],
            "already_present": [],
            "failed": [],
            "status": "host_required",
        }
    project_instructions = install_project_instruction_templates(repo, host)
    out = {"ok": True, "status": "initialized", "config": str(cfg_path), "state": str(repo / INDEX_REL), "host": host, "host_adapters": adapters, "project_instructions": project_instructions}
    if not host:
        out["host_adapters"]["next"] = f"cao host install --host {'|'.join(sorted(HOST_ADAPTER_META))}"
        out["warning"] = (
            "No coding-agent host could be determined, so no host adapter was installed. Host events (Session, Handoff, completion gate) will not fire. "
            "CAO's bootstrap/start Session fallback still covers the current session."
        )
    elif adapters["failed"]:
        out["warning"] = (
            f"Host adapter for `{host}` could not be installed. Host events (Session, Handoff, completion gate) will not fire in this project "
            "for hosts started after this point. CAO's bootstrap/start Session fallback still covers the current session."
        )
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


def cmd_host(args: argparse.Namespace) -> int:
    """Wire or inspect host adapters for an already-initialized project."""
    repo = repo_root(args.repo)
    if args.host_command == "install":
        host = args.host or detect_current_host()
        if not host:
            print(json.dumps({
                "ok": False,
                "error": "HOST_REQUIRED",
                "reason": "Cannot determine the coding-agent host from this environment.",
                "hosts": sorted(HOST_ADAPTER_META),
                "next": "cao host install --host <claude-code|codex|pi>",
            }, ensure_ascii=False, indent=2)); return 2
        adapters = install_host_adapters(repo, [host])
        print(json.dumps({"ok": True, "host": host, "host_adapters": adapters}, ensure_ascii=False, indent=2))
        return 0 if not adapters["failed"] else 1
    hosts = [args.host] if args.host else sorted(HOST_ADAPTER_META)
    print(json.dumps({
        "ok": True,
        "hosts": [host_adapter_status(repo, host) for host in hosts],
        "note": "Host adapters load when the host process starts, so install them before the next session.",
    }, ensure_ascii=False, indent=2))
    return 0


@locked_command
def _prepare_start(args: argparse.Namespace):
    repo = repo_root(args.repo); ensure_initialized(repo)
    if args.sdd_provider:
        detected = detect_sdd(repo, args.sdd_provider)
        if not detected["installed"]:
            print(json.dumps({
                "ok": False,
                "error": "SDD_NOT_INSTALLED",
                "provider": args.sdd_provider,
                "status": detected,
                "next": f"cao sdd install --provider {args.sdd_provider} --yes",
            }, ensure_ascii=False, indent=2)); return 2
        select_sdd_provider(repo, args.sdd_provider)
    sdd_state = sdd_status(repo)
    installed = sdd_state["installed"]
    if not installed:
        print(json.dumps({"ok": False, "error": "SDD_SELECTION_REQUIRED", "options": sdd_options(sdd_state), "next": "cao bootstrap"}, ensure_ascii=False, indent=2)); return 2
    selected = str((config(repo).get("sdd") or {}).get("provider") or "auto")
    if selected == "auto" and len(installed) > 1:
        print(json.dumps({"ok": False, "error": "SDD_SELECTION_REQUIRED", "options": sdd_options(sdd_state), "next": "cao sdd select --provider <provider>"}, ensure_ascii=False, indent=2)); return 2
    if selected == "auto" and len(installed) == 1:
        select_sdd_provider(repo, installed[0]["provider"])
        selected = installed[0]["provider"]
    if selected in SDD_CATALOG:
        try:
            apply_provider_language(repo, selected)
        except (OSError, UnicodeError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": selected, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 1
    p = resolve_requirement(repo, args.requirement)
    if p is None:
        if args.requirement:
            # An explicit path that does not exist is a different failure from
            # "which requirement do you mean?": the Agent must write the file
            # first (native SDD output dirs are writable for exactly that).
            print(json.dumps({
                "ok": False,
                "error": "REQUIREMENT_FILE_MISSING",
                "requirement": args.requirement,
                "reason": "The requirement file does not exist yet. Create it first (inside the provider's native output directory), then rerun `cao start`.",
            }, ensure_ascii=False, indent=2)); return 2
        candidates = [rel(x, repo) for x in requirement_files(repo, config(repo))]
        print(json.dumps({"ok": False, "error": "REQUIREMENT_SELECTION_REQUIRED", "candidates": candidates}, ensure_ascii=False, indent=2)); return 2
    index = load_index(repo)
    wid = stable_work_id(repo, p)
    existing = load_work(repo, wid)
    is_new_work = existing is None
    if existing and existing.get("status") == "completed" and not args.reopen:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_COMPLETED", "work_item": work_summary(existing)}, ensure_ascii=False, indent=2))
        return 2
    active_id = index.get("active_work")
    active_to_suspend = None
    if active_id:
        active = load_work(repo, active_id)
        if active and active.get("status") == "active":
            if not args.replace:
                print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_ACTIVE", "work_item": work_summary(active)}, ensure_ascii=False, indent=2)); return 2
            if active_id != wid:
                active_to_suspend = active
    configured_provider = str((config(repo).get("sdd") or {}).get("provider") or "auto")
    provider = configured_provider if configured_provider != "auto" else infer_sdd_provider(repo, p, config(repo))
    work = existing or {
        "version": 1,
        "id": wid,
        "title": args.title or p.stem,
        "status": "todo",
        "requirement": requirement_evidence(repo, p),
        "sdd": {"provider": provider, "native_ref": args.sdd_ref, "artifacts": []},
        "execution": {"step": "path-assessment", "next_action": action("agent", path_assessment_instruction())},
        "path_assessment": None,
        "resume": {"summary": "Work item created. Requirement is ready for intake.", "completed": [], "pending": []},
        "decisions": [],
        "blockers": [],
        "verification": None,
        "created_at": now(),
    }
    if active_to_suspend is None and _activation_denied(repo, wid):
        return 2
    work["status"] = "active"
    work["started_at"] = work.get("started_at") or now()
    changed = reset_requirement(work, requirement_evidence(repo, p))
    if (work.get("sdd") or {}).get("provider") != provider:
        work["path_assessment"] = None
        work["gate_status"] = {}
        work["verification"] = None
        for field in ("workflow", "plan_scope", "native_state", "spec_binding", "acceptance",
                      "native_route_required", "route_decision", "spec_approval", "implementation_state", "spec_task_scope"):
            work["sdd"].pop(field, None)
        hint = work["sdd"].pop("native_ref", None)
        if hint:
            work.setdefault("resume", {})["native_ref_hint"] = hint
        if work["sdd"].get("artifacts"):
            work.setdefault("resume", {})["previous_sdd_artifacts"] = work["sdd"]["artifacts"]
            work["sdd"]["artifacts"] = []
    if is_new_work or changed:
        cfg = config(repo)
        anchor_verification(repo, work, cfg)
    if not isinstance(work.get("path_assessment"), dict):
        work.setdefault("execution", {})["step"] = "path-assessment"
        work.setdefault("execution", {})["next_action"] = action("agent", path_assessment_instruction(work))
    if args.title:
        work["title"] = args.title
    work["sdd"]["provider"] = provider
    if provider == "bmad" and any(s["name"] == "bmad-build" for s in provider_native_skills(repo, provider)):
        work["sdd"]["native_route_required"] = True
    if args.sdd_ref:
        work["sdd"]["native_ref"] = args.sdd_ref
    if is_new_work:
        queue_event(work, {"event": "task.created", "work_item_id": wid, "source": "cao",
                           "native_ref": work.get("sdd", {}).get("native_ref"),
                           "occurred_at": work.get("created_at") or now()})
    # Prepare and validate all target evidence before mutating the current Work.
    # A rejection must not leave it suspended or create a false handoff.
    if active_to_suspend:
        active_to_suspend["status"] = "suspended"
        active_to_suspend.setdefault("resume", {})["pending"] = list(dict.fromkeys(
            (active_to_suspend.get("resume", {}).get("pending") or [])
            + [f"Execution re-anchored to a new work item: {Path(p).stem}"]))
        save_work(repo, active_to_suspend)
        create_handoff(repo, active_to_suspend, None, None, reason="execution re-anchored to another work item")
    save_work(repo, work)
    # Associate the new work and Session in the same transaction as activation.
    identity = host_session_identity(getattr(args, "host", None))
    session = register_session(repo, work, identity[1], identity[0], event="start") if identity else None
    return repo, wid, rel(p, repo), session


def cmd_start(args: argparse.Namespace) -> int:
    prepared = _prepare_start(args)
    if isinstance(prepared, int):
        return prepared
    repo, wid, requirement, session = prepared
    native = run_sdd_command(repo, "inspect", [requirement])
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if work is None:
            raise RuntimeError("WORK_ITEM_NOT_FOUND")
        current = current_work_action(repo, work)
        consume_request(repo, work['id'])
        summary = work_summary(work)
        summary["next_action"] = current
        out = {"ok": True, "action": "VERIFICATION_SETUP" if current.get("stage") == "verification_setup" else "ASSESS_PATH", "work_item": summary, "next_action": current,
               "guidance": agent_guidance(repo, work, next_action=current), "sdd": native}
        if session:
            out["session"] = load_session(repo, session["id"]) or session
        out["verification_setup"] = setup_state(config(repo))
        if not out["verification_setup"]["ready"]:
            out["warning"] = setup_instruction(cao_invocation()["command"])
        print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    items: list[dict[str, Any]] = []
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        work = load_work(repo, p.stem)
        if work:
            if work.get("status") in {"active", "suspended"} and reconcile_native_sdd_state(repo, work):
                work = load_work(repo, work.get("id")) or work
            items.append(work_summary(work))
    index["work_items"] = items
    save_index(repo, index)
    print(json.dumps({"ok": True, "active_work": index.get("active_work"), "work_items": items}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_status(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if work and reconcile_native_sdd_state(repo, work):
        work = load_work(repo, wid) or work
    out = {"ok": True, "work": resume_payload(repo, work) if work else None, "work_items": []}
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        w = load_json(p, None)
        if w:
            out["work_items"].append(work_summary(w))
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_path_assess(args: argparse.Namespace) -> int:
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
    assessment = build_path_assessment(
        requested_profile=args.profile,
        reason=args.reason,
        risk_flags=args.risk_flag or [],
        provider=provider,
        requirement_sha256=current_sha,
        questions=args.question or [],
        assessed_by=args.assessed_by,
    )
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
            + f"Required gates: {gate_text}. Record observed manual reviews with `cao gate satisfy --gate <gate> --evidence \"<what was done>\"`; verification and tests gates come from fresh configured scripts, not manual claims. `cao done` blocks until all are satisfied. Reassess only if the scope, risk, or requirement materially changes.",
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
            "gates": gate_state(work, False),
        }, ensure_ascii=False, indent=2))
        return 0
    fresh = bound_verification_fresh(work, verification_inputs(repo, work, config(repo)))
    print(json.dumps({
        "ok": True,
        "work_item": work_summary(work),
        "required_gates": assessment.get("required_gates") or [],
        "gates": gate_state(work, fresh, sdd_acceptance_state(repo, work)["ready"]),
        "gate_status": work.get("gate_status") or {},
    }, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_next(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    if work and restart_state(repo, detect_current_host())["ready"] and reconcile_native_sdd_state(repo, work):
        work = load_work(repo, work["id"]) or work
    current = current_work_action(repo, work)
    out = {"ok": True, "action": current, "work_item": work_summary(work) if work else None,
           "guidance": agent_guidance(repo, work, next_action=current)}
    if out["work_item"]:
        out["work_item"]["next_action"] = current
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_checkpoint(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    if args.step:
        execution = work.setdefault("execution", {})
        phase = execution_phase({"step": args.step}) or execution_phase(execution)
        if phase:
            execution["phase"] = phase
        execution["step"] = args.step
    if args.next:
        work.setdefault("execution", {})["next_action"] = action(args.next_type, args.next, command=args.command) if args.next_type != "none" else action("none", args.next)
    if args.summary:
        work.setdefault("resume", {})["summary"] = args.summary
    if args.completed:
        work.setdefault("resume", {}).setdefault("completed", []).append(args.completed)
    if args.pending:
        work.setdefault("resume", {}).setdefault("pending", []).append(args.pending)
    work.setdefault("resume", {})["last_checkpoint_at"] = now()
    save_work(repo, work)
    if args.session:
        touch_session(repo, args.session, "checkpoint", work, host=args.host)
    print(json.dumps({"ok": True, "work_item": work_summary(work), "next": work.get("execution", {}).get("next_action")}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_decision(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    d = {"id": new_id("decision"), "decision": args.decision, "reason": args.reason, "scope": args.scope, "created_at": now(), "supersedes": args.supersedes}
    work.setdefault("decisions", []).append(d)
    save_work(repo, work)
    print(json.dumps({"ok": True, "decision": d}, ensure_ascii=False, indent=2)); return 0


def cmd_decision_list(args: argparse.Namespace) -> int:
    """Read back recorded decisions.

    Recording existed, but only reaching them was missing: an Agent could write
    decisions with `cao decision` yet had no way to look at the ones already
    made, so the recorded reasoning went unread and un superseded.
    """
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    decisions = work.get("decisions") or []
    print(json.dumps({
        "ok": True,
        "work_item": work["id"],
        "count": len(decisions),
        "decisions": decisions[-int(args.limit or 50):],
    }, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_block(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    repair = bool(args.work and args.suspend)
    # An explicit suspension must remain usable even with a historical conflict.
    if repair:
        wid = args.work
    else:
        index = load_index(repo)
        wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    b = {"id": new_id("blocker"), "reason": args.reason, "resolution": args.resolution, "created_at": now()}
    work.setdefault("blockers", []).append(b)
    work["status"] = "suspended" if args.suspend else work.get("status", "active")
    try:
        save_work(repo, work)
    except RuntimeError as exc:
        if not repair or not str(exc).startswith("MULTIPLE_ACTIVE_WORK_ITEMS:"):
            raise
        # Authority and its conflict projection committed; more explicit fixes remain.
    out = {"ok": True, "blocker": b, "status": work["status"]}
    remaining = other_active_work(repo, work["id"])
    if len(remaining) > 1:
        out["remaining_active_work_items"] = remaining
        out["next"] = "Continue resolving the conflict with `cao block --work <id> --suspend --reason <reason>`."
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_unblock(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = load_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_NOT_FOUND"}, ensure_ascii=False, indent=2)); return 2
    before = len(work.get("blockers") or [])
    if args.blocker:
        work["blockers"] = [b for b in work.get("blockers", []) if b.get("id") != args.blocker]
    else:
        work["blockers"] = []
    if work.get("status") == "suspended" and not work["blockers"]:
        if not other_active_work(repo, work["id"]):
            work["status"] = "active"
    save_work(repo, work)
    out = {"ok": True, "removed": before - len(work.get("blockers") or []), "work_item": work_summary(work)}
    if work.get("status") == "suspended" and not work["blockers"]:
        out["next"] = f"Explicitly switch with `cao start --requirement {work['requirement']['path']} --replace`."
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


def cmd_handoff_list(args: argparse.Namespace, repo: Path) -> int:
    if args.limit < 1:
        print(json.dumps({"ok": False, "error": "INVALID_LIMIT", "reason": "--limit must be >= 1"}, ensure_ascii=False, indent=2))
        return 2
    records = list_handoff_records(repo, args.work)[:args.limit]
    summaries = [{
        "id": record.get("id"),
        "sequence": record.get("sequence"),
        "work_item": record.get("work_item"),
        "host": record.get("host"),
        "session_id": record.get("session_id"),
        "reason": record.get("reason"),
        "created_at": record.get("created_at"),
        "step": record.get("step"),
        "summary": record.get("summary"),
    } for record in records]
    print(json.dumps({"ok": True, "count": len(summaries), "handoffs": summaries}, ensure_ascii=False, indent=2))
    return 0


def cmd_handoff_show(args: argparse.Namespace, repo: Path) -> int:
    if not args.handoff_id:
        print(json.dumps({"ok": False, "error": "HANDOFF_ID_REQUIRED", "next": "cao handoff show --id <handoff-id>"}, ensure_ascii=False, indent=2))
        return 2
    record = find_handoff(repo, args.handoff_id)
    if not record:
        print(json.dumps({"ok": False, "error": "HANDOFF_NOT_FOUND", "id": args.handoff_id}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, "handoff": record}, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_handoff(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    if args.handoff_action == "list":
        return cmd_handoff_list(args, repo)
    if args.handoff_action == "show":
        return cmd_handoff_show(args, repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    if args.summary:
        work.setdefault("resume", {})["summary"] = args.summary
    save_work(repo, work)
    handoff = create_handoff(repo, work, args.session, args.host, args.reason)
    if args.end_session and args.session:
        end_session(repo, args.session, args.host, args.reason, event="stop")
    print(json.dumps({"ok": True, "handoff": handoff}, ensure_ascii=False, indent=2)); return 0


def choose_resume_work(repo: Path, work_id: str | None) -> dict[str, Any] | None:
    if work_id:
        return load_work(repo, work_id)
    index = load_index(repo); active = index.get("active_work")
    if active:
        return load_work(repo, active)
    candidates = []
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        w = load_json(p, None)
        if w and w.get("status") in {"active", "suspended"}:
            candidates.append(w)
    return candidates[0] if len(candidates) == 1 else None


def work_sdd_ready(repo: Path, work: dict[str, Any] | None) -> tuple[bool, dict[str, Any] | None]:
    if not work:
        return False, None
    provider = str(((work.get("sdd") or {}).get("provider") or "auto"))
    if provider not in SDD_CATALOG:
        return True, None
    detected = detect_sdd(repo, provider)
    return bool(detected["installed"]), detected


@locked_command
def cmd_resume(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = choose_resume_work(repo, args.work)
    if not work:
        candidates = []
        for p in sorted((repo / WORK_DIR).glob("*.json")):
            w = load_json(p, None)
            if w and w.get("status") in {"active", "suspended"}:
                candidates.append(work_summary(w))
        if candidates:
            print(json.dumps({"ok": False, "error": "WORK_ITEM_SELECTION_REQUIRED", "candidates": candidates}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"ok": False, "error": "NO_RESUMABLE_WORK"}, ensure_ascii=False, indent=2))
        return 2
    if reconcile_native_sdd_state(repo, work):
        work = load_work(repo, work["id"]) or work
    ready, detected = work_sdd_ready(repo, work)
    if not ready:
        provider = ((work.get("sdd") or {}).get("provider") or "auto")
        print(json.dumps({"ok": False, "error": "SDD_NOT_INSTALLED", "provider": provider, "status": detected, "next": f"cao sdd install --provider {provider} --yes"}, ensure_ascii=False, indent=2))
        return 2
    if work.get("status") == "suspended" and args.reactivate:
        if _activation_denied(repo, work["id"]):
            return 2
        work["status"] = "active"
        save_work(repo, work)
    elif work.get("status") == "suspended" and work.get("blockers"):
        # Resume can inspect suspended work without silently changing its status.
        pass
    session_id = args.session or new_id(args.host or "session")
    session = register_session(repo, work, session_id, args.host or "unknown", event="resume")
    print(render_context(repo, work, session_id=session_id, host=args.host))
    print("\n## Session\n" + json.dumps(session, ensure_ascii=False, indent=2))
    return 0


def verification_status_for_exit_code(exit_code: int) -> str:
    if exit_code == 0:
        return "passed"
    if exit_code == 1:
        return "failed"
    if exit_code == 2:
        return "blocked"
    return "error"


def verification_reason(stdout: str, stderr: str, status: str) -> str:
    for stream in ((stdout, stderr) if status == "passed" else (stderr, stdout)):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1][-1000:]
    defaults = {
        "passed": "Verification script passed.",
        "failed": "Verification script failed.",
        "blocked": "Verification could not be completed.",
        "error": "Verification script execution failed.",
    }
    return defaults[status]


def overall_verification_status(results: list[dict[str, Any]]) -> str:
    return required_verification_status(results)


@locked_command
def _prepare_verification(args: argparse.Namespace):
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2

    if work.get("status") != "active":
        recovery = (f'cao reopen --work {wid} --reason "<why>"' if work.get("status") == "completed"
                    else f'cao resume --work {wid} --reactivate')
        print(json.dumps({"ok": False, "error": "VERIFICATION_WORK_NOT_ACTIVE", "status": work.get("status"), "next": recovery})); return 2

    route = native_route_state(repo, work)
    if not route["ready"]:
        print(json.dumps({"ok": False, "error": route["reason"], "next": route["next"]})); return 2

    plan_state = observe_plan_scope(repo, work)
    if not plan_state["ready"]:
        print(json.dumps({"ok": False, "error": str(plan_state["reason"]).split(":", 1)[0],
                          "verification_plan": plan_state, "next": plan_state["next"]}, ensure_ascii=False, indent=2)); return 2
    review = native_review_state(repo, work, code_snapshot)
    if not review["ready"]:
        print(json.dumps({"ok": False, "error": review["reason"], "native_review": review, "next": review.get("next")}, ensure_ascii=False)); return 2
    planning = planning_state(repo, work)
    if not planning['ready']:
        print(json.dumps({'ok': False, 'error': planning['reason'].split(':', 1)[0], **planning})); return 2

    cfg = config(repo)
    cfg_scripts = verification_scripts(cfg)
    cli_values = args.script or args.command or []
    cli_override = normalize_scripts([
        {"name": f"script-{i}", "command": str(command).strip(), "required": True}
        for i, command in enumerate(cli_values, 1) if str(command).strip()
    ])
    source = "cli" if cli_override else "config"
    scripts = cli_override or cfg_scripts
    if not scripts:
        print(json.dumps({
            "ok": False,
            "error": "VERIFICATION_SCRIPT_REQUIRED",
            "next": "Configure required verification with `cao verification configure --test-script \"<real test command>\" --replace`, then explicitly re-anchor the Work Item."
        }, ensure_ascii=False, indent=2)); return 2

    if source == "config":
        baseline = verification_baseline_state(repo, work)
        if not baseline["ready"]:
            print(json.dumps({"ok": False, "error": baseline["reason"], "next": baseline["next"]})); return 2
        required_gates = ((work.get("path_assessment") or {}).get("required_gates")) or []
        if "security_verification" in required_gates and not any(item['required'] and 'security_verification' in item['gates'] for item in scripts):
            print(json.dumps({'ok': False, 'error': 'VERIFICATION_GATE_UNBOUND',
                              'reason': 'security_verification needs a required script declaring that capability.',
                              'next': 'Use verification configure --security-script <actual security checks>; the same test wrapper may also declare --test-script. Review and reanchor changed standards.'})); return 2
        if "tests" in required_gates and not any(item["required"] and "tests" in item["gates"] for item in scripts):
            print(json.dumps({
                "ok": False, "error": "VERIFICATION_GATE_UNBOUND",
                "reason": "The tests gate needs a required verification script explicitly declaring gates: [tests].",
                "next": "Use `cao verification configure --test-script \"<real test command>\" --replace`, then `cao reopen --reanchor --reason \"<why>\"` and rerun `cao verify`.",
            }, ensure_ascii=False, indent=2)); return 2

    req = repo / work["requirement"]["path"]
    if not req.exists():
        print(json.dumps({"ok": False, "error": "REQUIREMENT_FILE_MISSING"}, ensure_ascii=False, indent=2)); return 2
    inputs = verification_inputs(repo, work, cfg)
    run = {"run_id": "verify-" + uuid.uuid4().hex,
           "sequence": int(work.get("verification_sequence", 0)) + 1,
           "generation": int(work.get("execution_generation", 0)), "status": "running", "at": now(),
           "inputs": inputs, "source": source}
    previous = work.get("verification") or {}
    if previous:
        work.setdefault("verification_history", []).append(previous)
    work["verification_sequence"] = run["sequence"]
    work["verification"] = run
    save_work(repo, work)
    return repo, work["id"], scripts, source, inputs, verification_timeout(cfg), run


def commit_verification(repo, wid, inputs, scripts, results, source, started, run=None):
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if work is None:
            raise RuntimeError("WORK_ITEM_NOT_FOUND")
        identity = work.get("verification") or {}
        eligible = (run is not None and work.get("status") == "active"
                    and identity.get("run_id") == run["run_id"] and identity.get("status") == "running"
                    and int(work.get("execution_generation", 0)) == run["generation"])
        if not eligible:
            rejected = {"status": "superseded", "run_status": overall_verification_status(results),
                        "run_id": (run or {}).get("run_id"), "results": results,
                        "reason": "VERIFICATION_RUN_CHANGED", "source": source, "at": started}
            # Completed history remains immutable. Do not change its progress/official result.
            if work.get("status") != "completed":
                work.setdefault("verification_history", []).append(rejected)
                save_work(repo, work)
            return rejected
        try:
            current = verification_inputs(repo, work, config(repo))
        except (ValueError, OSError, RuntimeError):
            current = None
        changed = current != inputs
        plan_state = observe_plan_scope(repo, work)
        if plan_state.get("scope"):
            work.setdefault("sdd", {})["plan_scope"] = plan_state["scope"]
        status = overall_verification_status(results)
        verification = {"version": 2, "run_id": run["run_id"], "sequence": run["sequence"], "generation": run["generation"], "source": source, "at": started, **inputs,
                        "scripts": scripts, "results": results,
                        "status": "stale" if changed else status, "run_status": status}
        work["verification"] = verification
        if not changed:
            work["execution"]["step"] = "verification"
            work["execution"]["phase"] = "verification"
            completion = completion_status(repo, work) if status == "passed" else None
            instruction = ("Run `cao done`." if completion and completion["ready"] else
                           (completion.get("next") or completion_next_instruction(repo, work, completion)) if completion else
                           "Fix verification and rerun `cao verify`.")
            work["execution"]["next_action"] = action("agent", instruction)
            work["resume"]["summary"] = "Verification passed." if status == "passed" else f"Verification {status}. See verification.results for script status and reasons."
        save_work(repo, work)
        return verification


def cmd_verify(args: argparse.Namespace) -> int:
    prepared = _prepare_verification(args)
    if isinstance(prepared, int):
        return prepared
    repo, wid, scripts, source, inputs, timeout, run = prepared
    started = now()
    results: list[dict[str, Any]] = []
    for item in scripts:
        command = item["command"]
        command_started = now()
        try:
            result = run_script(command, repo, timeout=timeout)
        except Exception as exc:
            result = {"exit_code": 127, "stdout_tail": "", "stderr_tail": f"VERIFICATION_EXECUTION_ERROR: {exc}", "timed_out": False}
        exit_code = result["exit_code"]
        stdout, stderr = result["stdout_tail"], result["stderr_tail"]
        status_for_script = verification_status_for_exit_code(exit_code)
        results.append({
            "name": item["name"],
            "command": command,
            "required": item["required"],
            "gates": list(item["gates"]),
            "exit_code": exit_code,
            "status": status_for_script,
            "timed_out": result["timed_out"],
            "reason": verification_reason(stdout, stderr, status_for_script),
            "started_at": command_started,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        })

    verification = commit_verification(repo, wid, inputs, scripts, results, source, started, run)
    status = verification["status"]
    with repo_transaction(repo):
        work = load_work(repo, wid)
        next_action = work["execution"]["next_action"]
    cao_exit = {"passed": 0, "failed": 1, "blocked": 2, "error": 3, "stale": 2, "superseded": 2}[status]
    print(json.dumps({"ok": status == "passed", "verification": verification, "next": next_action}, ensure_ascii=False, indent=2)); return cao_exit


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


@locked_command
def cmd_reopen(args: argparse.Namespace) -> int:
    """Return a completed Work Item to active.

    demo11 closed a requirement that was never implemented: the Agent satisfied
    every gate with self-description, defined "compiles" as the verification
    standard, and `cao done` accepted it. `done` is append-only, so reopening is
    the honest correction path: previously satisfied gates and verification are
    cleared because they were earned by the same evidence that was wrong.
    """
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    reanchored = bool(getattr(args, "reanchor", False))
    status = str(work.get("status") or "")
    # Explicit standard acceptance also applies to active work, not only completed work.
    allowed = {"active", "completed"} if reanchored else {"completed"}
    if status not in allowed:
        print(json.dumps({
            "ok": False,
            "error": "WORK_ITEM_NOT_COMPLETED" if not reanchored else "WORK_ITEM_NOT_REANCHORABLE",
            "status": status,
            "reason": (
                f"Only a completed Work Item can be reopened; this one is `{status}`. Active work continues as-is."
                if not reanchored else
                f"Only an active or completed Work Item can be re-anchored; this one is `{status}`."
            ),
        }, ensure_ascii=False, indent=2)); return 2

    if _activation_denied(repo, work["id"]):
        return 2
    previous_execution = json.loads(json.dumps(work.get("execution") or {}))
    relocate_acceptance_sources(work.get("sdd") or {})
    if reanchored:
        review = work.get("verification_review") or {}
        cfg = config(repo)
        current_hash = verification_snapshot(cfg, repo)
        if ((work.get("acceptance") or {}).get("config_sha256") != current_hash
                and (review or work["sdd"].get("native_route_required"))):
            review = ensure_verification_review(repo, work, cfg)
            if review.get("status") != "approved":
                print(json.dumps({"ok": False, "error": "VERIFICATION_REVIEW_REQUIRED",
                                  "next": "Present changed scripts and wait for explicit user 批准验证基线 through the host."}))
                return 2
        p = repo / work["requirement"]["path"]
        if not p.is_file():
            print(json.dumps({"ok": False, "error": "REQUIREMENT_FILE_MISSING"}, ensure_ascii=False)); return 2
        reset_requirement(work, requirement_evidence(repo, p))
        cfg = config(repo)
        anchor_verification(repo, work, cfg)
        work["reanchored_at"] = now()
        record = work["sdd"].get("acceptance") or {}
        if record.get("declaration") and (work.get("path_assessment") or {}).get("profile") != "light":
            sources = source_hashes(repo, record["declaration"], provider_output_dirs(work["sdd"]["provider"]))
            reanchor_record(work, sources)
    reopening_message = (
        "The active Work Item's verification standard was re-anchored after explicit review."
        if status == "active" else
        "The Work Item was reopened: it was marked completed but the requirement is not actually satisfied."
    )
    work["execution_generation"] = int(work.get("execution_generation", 0)) + 1
    work["status"] = "active"
    work["completed_at"] = None
    work["execution"]["step"] = "reopened"
    work["execution"]["next_action"] = action(
        "agent",
        reopening_message
        + (f" Reopen reason: {args.reason}." if args.reason else "")
        + (" The current verification configuration was explicitly accepted as the new standard."
           if reanchored else "")
        + " Continue the requirement from where it truly stands. Gates and verification were cleared and must be re-earned with real evidence.",
    )
    work["resume"]["summary"] = args.reason or (
        "Re-anchored: the current verification standard was accepted; gates and verification must be re-earned."
        if reanchored else "Reopened after being completed without the requirement actually being satisfied."
    )
    work["gate_status"] = {}
    work["verification"] = {}
    if (work.get("sdd") or {}).get("acceptance"):
        work["sdd"]["acceptance"]["run"] = None
    queue_event(work, {
        "event": "verification.baseline.reanchored" if status == "active" and reanchored else "task.reopened",
        "work_item_id": work["id"],
        "source": "cao",
        "to_state": "active",
        "occurred_at": now(),
        "reason": args.reason or "not specified",
        "reanchored": reanchored,
    })
    if status == "active" and reanchored:
        work["execution"] = previous_execution
        # Keep the business cursor and reconstruct native prerequisites below.
    save_work(repo, work)
    reconcile_native_sdd_state(repo, work)
    print(json.dumps({
        "ok": True,
        "status": "active",
        "reanchored": reanchored,
        "work_item": work_summary(work),
    }, ensure_ascii=False, indent=2)); return 0


def _completion_blocked(repo: Path, work: dict[str, Any] | None, c: dict[str, Any]) -> int:
    blocked: dict[str, Any] = {"ok": False, "error": "COMPLETION_BLOCKED", "reason": c["reason"]}
    # Report concrete items, not just an unfinished category.
    for key in ("pending_plan_items", "missing_artifacts", "unmet_gates", "pending_gates", "gates", "workflow"):
        if c.get(key):
            blocked[key] = c[key]
    blocked["next"] = c.get("next") or completion_next_instruction(repo, work, c)
    print(json.dumps(blocked, ensure_ascii=False, indent=2)); return 2


def cmd_done(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    with repo_transaction(repo):
        index = load_index(repo)
        wid = args.work or index.get("active_work")
        work = load_work(repo, wid) if wid else None
        if work and work.get("status") == "active":
            observe_plan_scope(repo, work)
        c = completion_status(repo, work)
        if not c["ready"]:
            return _completion_blocked(repo, work, c)
    transition = sdd_transition(repo, "done")
    if transition is not None and transition["exit_code"] != 0:
        print(json.dumps({"ok": False, "error": "SDD_TRANSITION_FAILED", "transition": transition}, ensure_ascii=False, indent=2)); return 1
    # Native transitions can change both state and freshness. Judge again before closing.
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if work and work.get("status") == "active":
            observe_plan_scope(repo, work)
        c = completion_status(repo, work)
        if not c["ready"]:
            return _completion_blocked(repo, work, c)
        work["status"] = "completed"; work["completed_at"] = now(); work["execution"]["step"] = "done"; work["execution"]["next_action"] = action("none", "Work item is completed.")
        work["resume"]["summary"] = "Work item completed and verification was fresh."
        queue_event(work, {"event": "task.closed", "work_item_id": work.get("id"),
                           "source": "cao", "to_state": "closed", "occurred_at": work.get("completed_at")})
        save_work(repo, work)
        print(json.dumps({"ok": True, "status": "completed", "work_item": work_summary(work), "sdd_transition": transition}, ensure_ascii=False, indent=2)); return 0


def cmd_context(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    print(render_context(repo, work, session_id=args.session, host=args.host)); return 0


def native_output_dirs() -> list[str]:
    """Every provider-owned SDD output directory across the catalog."""
    dirs: list[str] = []
    for meta in SDD_CATALOG.values():
        for out in meta.get("output_dirs") or []:
            if out not in dirs:
                dirs.append(out)
    return dirs


# Compared against lower-cased keys, so every entry must be lower-case too.
WRITE_TOOL_PATH_KEYS = ("path", "file_path", "filepath", "filename", "notebook_path",
                       "destination", "destination_path", "dest", "new_path", "newpath",
                       "target_path", "targetpath", "source_path", "old_path", "oldpath")

# Tools whose target lives in a command string rather than a path argument.
SHELL_TOOLS = frozenset({"bash", "shell", "exec", "command", "exec_command", "shell_command", "run_shell_command", "run_command"})


def _collect_path_strings(node: Any, depth: int = 0) -> list[str]:
    """Recursively collect path-like string values.

    Hosts disagree on where a tool call's target path lives: `tool_input.path`,
    `input.filePath`, `params.path`, or nested further. Reading only the first
    level silently disabled the intake exemption in Pi, whose event shape does
    not match any single-level guess, and that reproduced the intake deadlock.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            k = str(key).lower()
            if isinstance(value, str):
                if k in WRITE_TOOL_PATH_KEYS and value.strip():
                    found.append(value.strip())
            elif isinstance(value, (dict, list)):
                found.extend(_collect_path_strings(value, depth + 1))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_path_strings(item, depth + 1))
    return found


def write_targets_protected_state(repo: Path, raw: dict[str, Any], work: dict | None = None) -> list[str]:
    """Protect both the lexical state link and its resolved destination."""
    repo = repo.resolve()
    state, requirements = repo / ORCH, repo / REQUIREMENT_DIR
    hits = []
    for target in tool_write_targets(raw, repo):
        value = Path(target)
        lexical = Path(os.path.abspath(value if value.is_absolute() else repo / value))
        resolved = lexical.resolve()
        # Resolve repository aliases (e.g. macOS /var) without dereferencing a state link.
        lexical_state = next((ancestor for ancestor in (lexical, *lexical.parents)
                              if ancestor.name == ORCH.name and ancestor.parent.resolve() == repo), None)
        state_target = lexical_state is not None or inside(resolved, state)
        writable = (inside(resolved, repo) and inside(resolved, requirements)
                    and inside(resolved, state)
                    and (lexical_state is None or lexical.is_relative_to(lexical_state / "requirements")))
        writable = writable or writable_input(repo, target, work)
        if state_target and not writable and target not in hits:
            hits.append(target)
    return hits


def protected_state_advice(targets: list[str]) -> str:
    """Point at the command that owns this state instead of a hand edit."""
    joined = " ".join(targets).lower()
    if "requirements" in joined:
        return "Requirement documents are Agent-authored; use `cao requirement next-id` to create one."
    if "/work/" in joined:
        return ("Work Item state is CAO-derived. Use its commands instead: `cao checkpoint --summary ...`, "
                "`cao decision --decision ...`, `cao path assess ...`, `cao sdd workflow <select|execute|complete>`, "
                "`cao verify`, `cao done`.")
    if "config.json" in joined:
        return "Project config is written by `cao verification configure`, `cao init`, `cao language set`, and provider install commands."
    if "index.json" in joined:
        return "`index.json` is regenerated from `work/*.json` after every Work Item mutation; it never needs editing."
    if "sessions/" in joined or "handoffs/" in joined:
        return "Session and Handoff records are written by the host hooks automatically."
    if "lifecycle/" in joined:
        return "`lifecycle/events.jsonl` is an append-only audit log written by CAO."
    return "CAO state is derived from its own commands; hand edits would be overwritten."


def _event_key(raw: Any, *keys: str) -> str:
    """Read a top-level string key case-insensitively.

    Hosts spell the tool name differently (`tool_name`, `toolName`, `tool`), and
    `dict.get("toolname")` misses `toolName`, which silently made every Pi shell
    call look targetless.
    """
    if not isinstance(raw, dict):
        return ""
    lowered = {str(key).lower(): value for key, value in raw.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def tool_cwd(raw: dict, repo: Path | None = None) -> Path | None:
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments") or raw.get("params") or {}
    session_value = _event_key(raw, "cwd", "workdir", "working_directory")
    session = Path(session_value) if session_value else repo
    if session is not None and not session.is_absolute() and repo is not None: session = repo / session
    value = _event_key(payload, "workdir", "cwd", "working_directory")
    if value:
        path = Path(value)
        if path.is_absolute(): return path
        return session / path if session is not None else None
    return session


def tool_effect(raw: dict, repo: Path | None = None) -> dict:
    """One bounded analysis supplies mutation classification and write targets."""
    tool = _event_key(raw, "tool_name", "toolname", "tool", "name").lower()
    if not tool:
        tool = next(iter(_collect_values_for_keys(raw, TOOL_NAME_KEYS)), "").lower()
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments") or raw.get("params") or raw
    cwd = tool_cwd(raw, repo)
    if cwd is not None and cwd.is_absolute(): cwd = cwd.resolve()
    targets, diagnostics = [], []
    effect = "write" if tool in MUTATING_TOOLS or _collect_values_for_keys(payload, MUTATION_PAYLOAD_KEYS) else "read"
    if tool not in SHELL_TOOLS:
        targets = _collect_path_strings(raw)
        patches = _collect_values_for_keys(raw, {"patch", "patch_text", "input"})
        if tool in {"apply_patch", "patch"}:
            patches.extend(_collect_values_for_keys(raw, {"command", "cmd"}))
        if isinstance(payload, str):
            if tool in {"apply_patch", "patch"}: patches.append(payload)
            else: targets.append(payload)
        for patch in patches: targets.extend(patch_targets(patch))
    else:
        commands = _collect_values_for_keys(raw, {"command", "cmd"})
        if isinstance(payload, str): commands.append(payload)
        effect = "read" if commands else "unknown"
        for command in commands:
            directory = cwd or Path(".")
            try:
                segments = _shell_segments(command)
            except ValueError:
                effect = "unknown" if effect != "write" else effect
                diagnostics.append("SHELL_PARSE_UNKNOWN")
                continue
            for tokens in segments:
                if tokens[0] == "cd" and len(tokens) == 2 and not any(c in tokens[1] for c in "$`~"):
                    path = Path(tokens[1]); directory = path if path.is_absolute() else directory / path
                    if directory.is_absolute(): directory = directory.resolve()
                    continue
                if _readonly_tokens(tokens, directory): continue
                operands = shell_write_operands(tokens)
                redirects = any(token in {">", ">>", "&>", ">|", "<>"} for token in tokens)
                known_command = tokens[0] in {"rm", "mv", "cp", "touch", "mkdir", "rmdir", "chmod", "tee", "find", "sed", "git", "diff", "echo", "printf", "cat", "head", "tail", "ls", "grep", "rg"}
                known = known_command and (bool(operands) or redirects)
                if known: effect = "write"
                elif effect != "write": effect = "unknown"
                if not known_command or (tokens[0] == "sed" and sed_effect(tokens)[0] == "unknown"):
                    diagnostics.append("SHELL_EFFECT_UNKNOWN")
                if tokens[0] == "find":
                    for flag in ("-exec", "-execdir", "-ok", "-okdir"):
                        if flag in tokens:
                            nested = [t for t in tokens[tokens.index(flag) + 1:] if t not in {"{}", ";", "+"}]
                            if nested and not _readonly_tokens(nested):
                                if nested[0] not in {"rm", "mv", "cp", "touch", "mkdir", "rmdir", "chmod", "tee", "sed"} or (nested[0] == "sed" and sed_effect(nested)[0] == "unknown"):
                                    diagnostics.append("SHELL_EFFECT_UNKNOWN")
                if any(c in operand for operand in operands for c in "$`~"):
                    diagnostics.append("SHELL_TARGET_UNKNOWN")
                target_cwd = directory
                if tokens[0] == "git":
                    for i, token in enumerate(tokens[:-1]):
                        if token == "-C":
                            path = Path(tokens[i + 1]); target_cwd = path if path.is_absolute() else target_cwd / path
                for operand in operands:
                    path = Path(operand)
                    targets.append(str(path if path.is_absolute() else target_cwd / path))
    # Retain lexical paths as well as resolving links in the protection layer.
    if tool not in SHELL_TOOLS:
        targets = [str(Path(value) if Path(value).is_absolute() or cwd is None else cwd / value) for value in targets]
    if cwd is None and any(not Path(value).is_absolute() for value in targets):
        diagnostics.append("TOOL_CWD_UNKNOWN")
    if repo is not None:
        # Preserve the lexical target for state-link protection, and include the
        # actual target resolved before lexical .. collapses a directory alias.
        actual = [str(Path(value).resolve()) for value in targets]
        targets = actual + [os.path.abspath(value) for value in targets]
    return {"effect": effect, "targets": list(dict.fromkeys(targets)),
            "cwd": str(cwd) if cwd is not None else None, "diagnostics": diagnostics}


def tool_write_targets(raw: dict[str, Any], repo: Path | None = None) -> list[str]:
    return tool_effect(raw, repo)["targets"]


def sed_effect(tokens: list[str]) -> tuple[str, list[str]]:
    expressions, files = [], []
    explicit, unknown, skip = False, False, False
    for i, token in enumerate(tokens[1:], 1):
        if skip: skip = False; continue
        if token in {"-e", "--expression"} and i + 1 < len(tokens):
            expressions.append(tokens[i + 1]); explicit = skip = True
        elif token in {"-f", "--file"}:
            unknown = explicit = skip = True
        elif token.startswith(("--expression=", "-e")):
            expressions.append(token.split("=", 1)[1] if "=" in token else token[2:]); explicit = True
        elif token.startswith(("--file=", "-f")):
            unknown = explicit = True
        elif not token.startswith("-"):
            files.append(token)
    if not explicit and files: expressions.append(files.pop(0))
    targets = []
    if any(token.startswith(("-i", "--in-place")) for token in tokens[1:]): targets.extend(files)
    for expression in expressions:
        # w/W commands, address-prefixed w, and substitution w flags.
        for match in re.finditer(r"(?:^|[;}]|/[gIp0-9]*)\s*(?:[0-9,$!/]+\s*)?[wW]\s+([^;\n]+)", expression):
            targets.append(match.group(1).strip())
        execution = re.search(r"(?:^|;)\s*(?:[0-9,$!/]+\s*)?e(?:\s+([^;\n]+)|$)", expression)
        if execution:
            unknown = True
            if execution.group(1):
                try:
                    for segment in _shell_segments(execution.group(1)):
                        targets.extend(shell_write_operands(segment))
                except ValueError: pass
        if re.search(r"/[^;]*e(?:\s|$)", expression): unknown = True
        # Unsupported expression grammar is unknown rather than proven read.
        if not re.match(r"^(?:[0-9,$!/]+\s*)?(?:[spdqnaic=hrwWyY]|$)", expression): unknown = True
    return ("unknown" if unknown else "write" if targets else "read"), targets


def git_output_targets(tokens: list[str]) -> list[str]:
    targets = []
    for i, token in enumerate(tokens[1:], 1):
        if token in {"--output", "-o"} and i + 1 < len(tokens): targets.append(tokens[i + 1])
        elif token.startswith("--output="): targets.append(token.split("=", 1)[1])
    return targets


def shell_write_operands(tokens: list[str]) -> list[str]:
    """Keep read inputs and command data out of native-output exemptions."""
    redirects = {">", ">>", "&>", ">|", "<>"}
    outputs = [tokens[i + 1] for i, token in enumerate(tokens[:-1]) if token in redirects]
    if tokens[0] in {"echo", "printf", "cat", "head", "tail", "ls", "grep", "rg", "diff"}:
        return outputs
    operands = []
    skip = False
    for token in tokens[1:]:
        if skip:
            skip = False
            continue
        if token in redirects | {"<", "<<", "<<<", "<&", ">&"}:
            skip = True
            continue
        if token.startswith("-") or token in {"2", "&"}:
            continue
        operands.append(token)
    if tokens[0] == "chmod":
        # The symbolic/numeric mode is command data, not a write destination.
        # Reference forms remain conservative; setup allows only literal modes.
        mode = next((t for t in tokens[1:] if re.fullmatch(r"(?:[0-7]{3,4}|[ugoa]*[+=-][rwxXstugo]*(?:,[ugoa]*[+=-][rwxXstugo]*)*)", t)), None)
        if not any(t.startswith("--reference") for t in tokens) and operands and operands[0] == mode:
            operands = operands[1:]
        return outputs + operands
    if tokens[0] == "cp":
        target_dir = next((tokens[i + 1] for i, token in enumerate(tokens[:-1])
                           if token in {"-t", "--target-directory"}), None)
        target_dir = target_dir or next((token.split("=", 1)[1] for token in tokens
                                        if token.startswith("--target-directory=")), None)
        return outputs + ([target_dir] if target_dir else operands[-1:])
    if tokens[0] == "find":
        roots = []
        for token in tokens[1:]:
            if token.startswith("-") or token in {"(", ")", "!"}: break
            roots.append(token)
        if "-delete" in tokens: outputs.extend(roots or ["."])
        for i, token in enumerate(tokens[:-1]):
            if token in {"-fprint", "-fprint0", "-fprintf"}: outputs.append(tokens[i + 1])
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in tokens:
                nested = tokens[tokens.index(flag) + 1:]
                substitutes = "{}" in nested
                nested = [token for token in nested if token not in {"{}", ";", "+"}]
                if nested:
                    outputs.extend(shell_write_operands(nested))
                    if substitutes and not _readonly_tokens(nested): outputs.extend(roots or ["."])
        return outputs
    if tokens[0] == "git": return outputs + git_output_targets(tokens)
    if tokens[0] == "sed": return outputs + sed_effect(tokens)[1]
    return outputs + operands


def verification_baseline_state(repo: Path, work: dict) -> dict:
    cfg = config(repo)
    state = setup_state(cfg)
    if not state["ready"]:
        return {"ready": False, "reason": "VERIFICATION_BASELINE_REQUIRED", "next": setup_instruction(cao_invocation()["command"])}
    anchor = work.get("acceptance") or {}
    if anchor.get("version") != 2 or anchor.get("config_sha256") != verification_snapshot(cfg, repo):
        review = work.get('verification_review') or {}
        if review.get('status') == 'pending' and review.get('config_sha256') == verification_snapshot(cfg, repo):
            return {'ready': False, 'reason': 'VERIFICATION_REVIEW_REQUIRED', 'approval_id': review['id'],
                    'next': f'Present the changed verification baseline for request {review["id"]}. '
                    'Ask once through a bound ask_user_question or a direct user reply (批准验证基线 / approve verification baseline). '
                    f'After approval run `cao reopen --work {work["id"]} --reanchor --reason "Reviewed verification baseline"`.'}
        return {"ready": False, "reason": "VERIFICATION_STANDARD_CHANGED",
                "next": f'Review the configured scripts, then `cao reopen --work {work["id"]} --reanchor --reason "Reviewed verification baseline"` before feature implementation. Tests need not pass yet.'}
    return {"ready": True}


def verification_capability_state(repo: Path, work: dict) -> dict:
    required = (work.get('path_assessment') or {}).get('required_gates', [])
    scripts = setup_state(config(repo))['scripts']
    if 'security_verification' in required and not any(
            script['required'] and 'security_verification' in script['gates'] for script in scripts):
        return {'ready': False, 'reason': 'VERIFICATION_GATE_UNBOUND',
                'next': 'The selected risk requires security verification before implementation. '
                'Configure a real security check with `cao verification configure --security-script <wrapper>`; '
                'preserve existing test/general declarations with --replace when needed. The same test wrapper may declare both capabilities. '
                'Review and reanchor a changed baseline, then continue the existing native workflow.'}
    return {'ready': True}


def verification_scaffold_write(repo: Path, raw: dict) -> bool:
    analysis = tool_effect(raw, repo)
    targets = [] if analysis["diagnostics"] else analysis["targets"]
    if _event_key(raw, "tool_name", "toolname", "tool", "name").lower() in SHELL_TOOLS:
        try:
            commands = _collect_values_for_keys(raw, {"command", "cmd"})
            payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments")
            if isinstance(payload, str):
                commands.append(payload)
            for command in commands:
                for tokens in _shell_segments(command):
                    if tokens[0] == "chmod":
                        if (len(tokens) < 3 or tokens[1] not in {"+x", "u+x", "a+x", "ug+x", "700", "750", "755"}
                                or any(t.startswith("-") and t != "--" for t in tokens[2:])):
                            return False
                        if not all(repo_file(repo, p).is_file() for p in targets):
                            return False
        except (ValueError, OSError, RuntimeError):
            return False
    if targets:
        valid = True
        for value in targets:
            try:
                path = repo_file(repo, value)
                relative = path.relative_to(repo.resolve()).as_posix()
                allowed = relative in SCAFFOLD_MANIFESTS or any(inside(path, repo.resolve() / root) and
                          (repo.resolve() / root).resolve() == repo.resolve() / root for root in SCAFFOLD_ROOTS)
                valid = valid and allowed
            except (ValueError, OSError, RuntimeError):
                valid = False
        if valid:
            return True
    if _event_key(raw, "tool_name", "toolname", "tool", "name").lower() in SHELL_TOOLS:
        commands = _collect_values_for_keys(raw, {"command", "cmd"})
        try:
            for command in commands:
                for tokens in _shell_segments(command):
                    if _readonly_tokens(tokens): continue
                    if any(char in shlex.join(tokens) for char in "$`") or any(t in {">", ">>", "<<", "&>"} for t in tokens): return False
                    name = Path(tokens[0]).name
                    if tokens[:2] == ["git", "init"]: continue
                    if tokens[:2] in (["uv", "venv"], ["uv", "run"]):
                        if tokens[:2] == ["uv", "run"]:
                            runner = tokens[2:]
                            while runner and runner[0].startswith("--"):
                                runner = runner[1:]
                            if not runner or not (Path(runner[0]).name == "pytest" or
                                    (Path(runner[0]).name.startswith("python") and runner[1:3] in (["-m", "pytest"], ["-m", "unittest"]))): return False
                        continue
                    if tokens[:3] == ["uv", "pip", "install"] or (name in {"pip", "pip3", "npm"} and "install" in tokens): continue
                    if name.startswith("python") and tokens[1:3] == ["-m", "pip"] and "install" in tokens: continue
                    if name.startswith("python") and tokens[1:3] in (["-m", "pytest"], ["-m", "unittest"], ["-m", "venv"]): continue
                    return False
            return bool(commands)
        except ValueError:
            return False
    return False


def intake_writable_dirs() -> list[str]:
    """Directories that stay writable before a Work Item exists.

    - provider-owned SDD output dirs: requirement/PRD/spec artifacts, which are
      also the only places native workflow artifacts may live;
    - `.orchestrator/requirements/`: CAO's own numbered requirement documents.

    Blocking writes to either deadlocks intake: the hook demands an active Work
    Item, `cao start` demands a requirement file, and the file can never be
    written. Only `.orchestrator/requirements/` is exempt — the rest of
    `.orchestrator/` holds CAO state the Agent must not hand-edit.
    """
    return native_output_dirs() + [f"{REQUIREMENT_DIR.as_posix()}/"]


def write_targets_native_sdd_output(repo: Path, raw: dict[str, Any]) -> bool:
    """Only an all-native write is exempt from the no-Work gate and reminders."""
    analysis = tool_effect(raw, repo)
    # A literal-looking operand is not proof of the effects of an unknown
    # program, or of an expanded variable target inside a native directory.
    if analysis["diagnostics"]: return False
    targets = analysis["targets"]
    if not targets:
        return False
    for target in targets:
        try:
            path = repo_file(repo, target)
        except ValueError:
            return False
        if not any((repo.resolve() / directory).resolve() == repo.resolve() / directory
                   and inside(path, repo.resolve() / directory) for directory in intake_writable_dirs()):
            return False
    return True


def native_intake_shell(repo: Path, raw: dict[str, Any]) -> bool:
    """Trust native metadata commands, not general implementation scripts."""
    if _event_key(raw, "tool_name", "toolname", "tool", "name").lower() not in SHELL_TOOLS:
        return False
    commands = _collect_values_for_keys(raw, {"command", "cmd"})
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments")
    if isinstance(payload, str):
        commands.append(payload)
    if not commands:
        return False
    for command in commands:
        try:
            segments = _shell_segments(command)
        except ValueError:
            return False
        for tokens in segments:
            if _readonly_tokens(tokens):
                continue
            if any(any(char in token for char in "$`") or token in {">", ">>", "&>", "<", "<<", "(", ")"} for token in tokens):
                return False
            for i, token in enumerate(tokens):
                if token in {"--output", "--output-dir", "--out", "-o"} and i + 1 < len(tokens):
                    output = tokens[i + 1]
                elif token.startswith(("--output=", "--output-dir=", "--out=")):
                    output = token.split("=", 1)[1]
                else:
                    continue
                if not write_targets_native_sdd_output(repo, {"tool_name": "write", "tool_input": {"path": output}}):
                    return False
            if tokens[:2] in (["openspec", "status"], ["openspec", "instructions"], ["openspec", "list"], ["openspec", "show"], ["openspec", "validate"]):
                continue
            if bmad_preparation(repo, tokens):
                continue
            if tokens[:3] == ["openspec", "new", "change"] and detect_sdd(repo, "openspec")["installed"]:
                continue
            if (tokens[:2] == ["openspec", "archive"]
                    and (repo / "openspec").is_dir()
                    and (repo / "openspec").resolve() == repo.resolve() / "openspec"
                    and len([arg for arg in tokens[2:] if not arg.startswith("-")]) == 1
                    and all(arg in {"--yes", "-y", "--json", "--skip-specs"}
                            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", arg) for arg in tokens[2:])):
                continue
            identities = workflow_command_identities(shlex.join(tokens))
            if Path(tokens[0]).stem.lower() in {"pwsh", "powershell"} and len(tokens) > 2 and tokens[1].lower() == "-file":
                identities.append(tokens[2])
            helpers = [repo / "_bmad/scripts/render_skill.py"] + [
                repo / ".specify/scripts" / directory / (name + suffix)
                for directory, suffix in (("bash", ".sh"), ("powershell", ".ps1"))
                for name in ("check-prerequisites", "create-new-feature", "setup-plan")
            ]
            if any(helper.is_file() and helper.parent.resolve() == repo.resolve() / helper.parent.relative_to(repo)
                   and inside(helper.resolve(), helper.parent)
                   and any((Path(identity) if Path(identity).is_absolute() else repo / identity).resolve() == helper.resolve()
                           for identity in identities) for helper in helpers):
                continue
            return False
    return True


MUTATING_TOOLS = {"write", "edit", "apply_patch", "str_replace", "strreplace", "insert", "delete", "mv", "move", "write_file", "create_file", "patch"}
# `command`/`cmd` are deliberately absent: shell calls are judged by
# `readonly_shell` below, and treating any command as a mutation would block
# read-only shell usage entirely.
MUTATION_PAYLOAD_KEYS = {"new_string", "newstring", "old_string", "patch", "content"}
TOOL_NAME_KEYS = {"tool_name", "toolname", "tool", "name"}


def _collect_values_for_keys(node: Any, keys: frozenset[str] | set[str], depth: int = 0) -> list[str]:
    """Collect string values stored under `keys`, without silently truncating targets."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            k = str(key).lower()
            if isinstance(value, str):
                if k in keys and value.strip():
                    found.append(value.strip())
            elif isinstance(value, (dict, list)):
                found.extend(_collect_values_for_keys(value, keys, depth + 1))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_values_for_keys(item, keys, depth + 1))
    return found


def tool_is_mutating(raw: dict[str, Any]) -> bool:
    return tool_effect(raw)["effect"] != "read"


def _shell_segments(command: str) -> list[list[str]]:
    # Heredoc bodies are input data, not additional mutating commands. Keep
    # subsequent commands after the terminator visible to the gate.
    lines = []
    pending: list[tuple[str, bool]] = []
    for line in command.splitlines(keepends=True):
        if pending:
            delimiter, quoted = pending[0]
            if line.rstrip("\r\n") == delimiter:
                pending.pop(0)
            elif not quoted and any(char in line for char in "$`"):
                # Unquoted heredocs execute command substitutions. Preserve
                # those bodies for target checking instead of treating them
                # as literal native document content.
                lines = None
                break
            continue
        lines.append(line)
        try:
            header = shlex.shlex(line, posix=False, punctuation_chars=";&|<>()")
            header.whitespace_split = True
            tokens = list(header)
        except ValueError:
            # A quoted string may span lines; do not interpret any of its
            # later lines as a heredoc operator.
            lines = None
            break
        for i, token in enumerate(tokens[:-1]):
            if token == "<<":
                marker = tokens[i + 1]
                quoted = re.fullmatch(r"(['\"])([A-Za-z_][A-Za-z0-9_-]*)\1", marker)
                if quoted:
                    pending.append((quoted.group(2), True))
                elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", marker):
                    pending.append((marker, False))
                else:
                    # Unsupported delimiters must not hide later commands.
                    lines = None
                    break
        if lines is None:
            break
    if lines is not None:
        command = "".join(lines)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"  # Preserve unquoted newlines as command boundaries.
    segments = [[]]
    for token in lexer:
        if token and all(char in ";&|\n" for char in token):
            if segments[-1]:
                segments.append([])
        else:
            segments[-1].append(token)
    return [tokens for tokens in segments if tokens]


def readonly_shell(command: str, cwd: Path | None = None) -> bool:
    try:
        directory = cwd or Path.cwd()
        for tokens in _shell_segments(command):
            if not _readonly_tokens(tokens, directory):
                return False
            if tokens[0] == 'cd' and len(tokens) == 2:
                directory = (directory / tokens[1]).resolve()
        return True
    except ValueError:
        return False


def diagnostic_shell_write(repo: Path, raw: dict) -> bool:
    """Allow literal diff artifacts in the system temp directory, not product writes."""
    if _event_key(raw, 'tool_name', 'toolname', 'tool', 'name').lower() not in SHELL_TOOLS:
        return False
    analysis = tool_effect(raw, repo)
    if analysis['effect'] != 'write' or analysis['diagnostics'] or not analysis['targets']:
        return False
    root = Path(tempfile.gettempdir()).resolve()
    if any(not inside(Path(target).resolve(), root) or inside(Path(target).resolve(), repo.resolve())
           or Path(target).resolve() == root for target in analysis['targets']):
        return False
    commands = _collect_values_for_keys(raw, {'command', 'cmd'})
    if not commands:
        return False
    for command in commands:
        for tokens in _shell_segments(command):
            if _readonly_tokens(tokens, tool_cwd(raw, repo)):
                continue
            if tokens[0] == 'mkdir' and all(t == '-p' or not t.startswith('-') for t in tokens[1:]):
                continue
            if tokens[0] != 'diff':
                return False
            stripped = list(tokens)
            for i in range(len(stripped) - 2, 0, -1):
                if stripped[i] in {'>', '>>'}:
                    del stripped[i:i + 2]
            if not _readonly_tokens(stripped, tool_cwd(raw, repo)):
                return False
    return True


def _readonly_checksum_tokens(tokens: list[str]) -> bool:
    """Recognize literal SHA probes without accepting arbitrary runner flags."""
    algorithms = {"1", "224", "256", "384", "512", "512224", "512256"}
    options = {"-b", "--binary", "-t", "--text", "--tag", "-z", "--zero", "--help", "--version"}
    shasum = Path(tokens[0]).name.lower() == "shasum"
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            return True  # Remaining literal arguments are filenames.
        if shasum and token in {"-a", "--algorithm"}:
            i += 1
            if i >= len(tokens) or tokens[i] not in algorithms:
                return False
        elif shasum and token.startswith("--algorithm="):
            if token.partition("=")[2] not in algorithms:
                return False
        elif token.startswith("-") and token not in options:
            return False
        i += 1
    return True


def _readonly_tokens(tokens: list[str], cwd: Path | None = None) -> bool:
    tokens = list(tokens)
    # Descriptor duplication has no filesystem target. Keep arbitrary redirects
    # and substitutions visible to the write-target guard.
    index = 1
    while index + 2 < len(tokens):
        if tokens[index:index + 3] in (["2", ">&", "1"], ["1", ">&", "2"]):
            del tokens[index:index + 3]
        else:
            index += 1
    # Exit status expansion is data, not command substitution. Restrict it to
    # diagnostic echo; do not broadly exempt dollar expansions.
    if tokens and tokens[0] == "echo":
        tokens = [token.replace("$?", "STATUS") for token in tokens]
    # Literal output to the real null device cannot modify project files. Keep
    # every other redirection visible, and never remove the command itself.
    if Path("/dev/null").is_char_device():
        index = 1
        while index + 1 < len(tokens):
            if tokens[index] in {">", ">>"} and tokens[index + 1] == "/dev/null":
                start = index - 1 if index > 1 and tokens[index - 1] in {"1", "2"} else index
                del tokens[start:index + 2]
                index = max(1, start)
            else:
                index += 1
    operators = {">", ">>", "<", "<<", "<>", ">|", "&>", ">&", "<&", ";", "&", "|", "&&", "||", "(", ")", "$", "`"}
    if not tokens or any(token in operators for token in tokens):
        return False
    name = tokens[0].lower()
    if any(char in token for token in tokens for char in "$`"): return False
    if re.fullmatch(r"NPM_CONFIG_PREFIX=.+", tokens[0]):
        # Only a literal npm prefix on the real CAO installer is exempt. Never
        # accept interpreter/preload/PATH overrides or arbitrary assigned commands.
        rest = tokens[1:]
        return bool(rest and rest[1:3] == ["sdd", "install"] and _readonly_tokens(rest))
    if tokens in (["true"], ["false"]):
        return True
    if tokens in (["set", "-o", "pipefail"], ["set", "-e"], ["set", "-u"], ["set", "-euo", "pipefail"]):
        return True
    if name == "sort":
        safe = {"--", "-r", "-n", "-u", "-f", "-b", "-R", "-V", "-h", "--reverse",
                "--numeric-sort", "--unique", "--ignore-case"}
        return all(not token.startswith("-") or token in safe for token in tokens[1:])
    if name == "npm" and (tokens[1:] in (["root"], ["root", "-g"], ["root", "--global"])
                          or (tokens[1:3] == ["config", "get"] and len(tokens) == 4
                              and tokens[3] in {"prefix", "cache", "registry"})):
        return True
    if name == "openspec":
        if tokens[1:] in (["--help"], ["-h"], ["--version"], ["-V"]):
            return True
        if len(tokens) == 3 and tokens[1] in {"archive", "init", "new", "status", "instructions", "validate", "list", "show"} and tokens[2] in {"--help", "-h"}:
            return True
        if (len(tokens) > 1 and tokens[1] in {"status", "instructions", "list", "show", "validate", "context", "schemas"}
                and not any(token in {"--output", "--out", "--output-dir", "-o"}
                            or token.startswith(("--output=", "--out=", "--output-dir=")) for token in tokens)):
            return True
    if name == "find":
        return not any(token in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf"} for token in tokens)
    if name == 'date':
        return all(token in {'-u', '--utc', '--universal', '-I', '--iso-8601', '--rfc-3339=seconds'}
                   or token.startswith('+') for token in tokens[1:])
    if name in {'diff', 'printenv'}:
        return True
    if name in {"ls", "dir", "cat", "type", "pwd", "grep", "rg", "head", "tail", "echo", "printf", "cd", "exit", "which", "file", "stat", "wc"}:
        return True
    if name == "command" and len(tokens) > 1 and tokens[1] in {"-v", "-V"}:
        return True
    probe_name = Path(tokens[0]).name.lower()
    python_probe = re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", probe_name)
    if len(tokens) == 2:
        if python_probe and tokens[1] in {"--version", "-V"}:
            return True
        if probe_name in {"java", "javac", "mvn", "gradle", "node", "npm", "uv"} and tokens[1] in {"--version", "-version", "-v"}:
            return True
    if probe_name in {"sha256sum", "shasum"}:
        return _readonly_checksum_tokens(tokens)
    if name == "git":
        args = tokens[1:]
        while len(args) >= 2 and args[0] == "-C": args = args[2:]
        if args and args[0] == "rev-parse":
            return bool(args[1:]) and all(token in {"--show-toplevel", "--is-inside-work-tree", "--git-dir", "--show-prefix", "--verify", "HEAD"} for token in args[1:])
        if args and args[0] in {"status", "diff", "log", "show", "branch", "ls-files"}:
            unsafe = {"--ext-diff", "--textconv", "-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy"}
            return not git_output_targets(tokens) and not any(token in unsafe for token in args[1:]) and (args[0] != "branch" or all(token.startswith("-") for token in args[1:]))
    if name == "sed":
        return sed_effect(tokens)[0] == "read"
    if name == "pytest" or tokens[:3] in (["python", "-m", "unittest"],):
        return True
    if tokens[:2] in (["mvn", "test"], ["gradle", "test"], ["npm", "test"]):
        return True
    # Standalone CAO commands own their state mutations; their arguments are not hand edits.
    command_tokens = tokens
    if python_probe:
        runner = tokens[1:]
        while runner and runner[0] in {"-u", "-B", "-E", "-I", "-s", "-S"}:
            runner = runner[1:]
        if len(runner) < 2:
            return False
        try:
            if Path(runner[0]).resolve() != Path(__file__).resolve():
                return False
        except (OSError, ValueError, RuntimeError):
            return False
        command_tokens = runner[1:]
    elif tokens[0] == "cao":
        command_tokens = tokens[1:]
    else:
        # Templates and the Pi fallback use this package's absolute launcher.
        # A matching basename alone must not exempt an unrelated script.
        index = 1 if tokens[0] == "sh" else 0
        if len(tokens) <= index:
            return False
        root = package_root_dir()
        launchers = {str((root / "cao").resolve())} if root else set()
        console = shutil.which("cao")
        if console:
            launchers.add(str(Path(console).resolve()))
        try:
            target = str(((cwd or Path.cwd()) / tokens[index]).resolve())
        except (OSError, RuntimeError):
            return False
        if target not in launchers:
            return False
        command_tokens = tokens[index + 1:]
    safe_control_commands = {
        "init", "bootstrap", "sdd", "start", "path", "list", "status", "next", "resume",
        "checkpoint", "decision", "block", "unblock", "handoff", "sessions", "verify", "done",
        "context", "artifact", "host", "host-event", "requirement", "language", "gate", "reopen", "verification", "instructions",
    }
    return bool(command_tokens and (command_tokens[0] in safe_control_commands or command_tokens in (["--help"], ["-h"])))


@locked_command
def host_event(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo or os.getcwd())
    raw = json.load(sys.stdin)
    raw.setdefault("cwd", str(Path(args.repo or os.getcwd()).resolve()))
    event = str(raw.get("event") or raw.get("hook_event_name") or raw.get("event_name") or "").lower()
    mapping = {
        "sessionstart": "session_start", "session_start": "session_start",
        "sessionend": "session_end", "session_end": "session_end",
        "userpromptsubmit": "prompt_submit", "before_agent_start": "prompt_submit",
        "pretooluse": "pre_tool", "tool_call": "pre_tool",
        "posttooluse": "post_tool", "tool_result": "post_tool",
        "subagentstart": "subagent_start", "subagentstop": "subagent_stop",
        "agent_end": "turn_end",
    }
    event = mapping.get(event, event)
    index = load_index(repo) if (repo / CONFIG_REL).exists() else empty_index()
    wid = index.get("active_work")
    work = load_work(repo, wid) if wid else None
    candidate_id = _event_key(raw, "subagent_id", "agent_id", "session_id", "sessionId", "thread_id")
    known_session = load_session(repo, candidate_id, host=args.host) if candidate_id else None
    kind = "subagent" if (event in {"subagent_start", "subagent_stop"} or raw.get("kind") == "subagent"
                           or (known_session or {}).get("kind") == "subagent") else "primary"
    if work and restart_state(repo, args.host)["ready"] and event in {"session_start", "subagent_start", "prompt_submit", "pre_tool", "stop", "turn_end", "before_settle"}:
        # Refresh before exposing context or writing a handoff, including edits
        # performed outside this host. Derived observations never reanchor input hashes.
        reconcile_native_sdd_state(repo, work)
    if kind == "subagent":
        host_session_id = _event_key(raw, "subagent_id", "agent_id")
        if not host_session_id:
            candidate = _event_key(raw, "session_id", "sessionId", "thread_id")
            known = load_session(repo, candidate, host=args.host) if candidate else None
            host_session_id = candidate if known and known.get("kind") == "subagent" else ""
        existing = load_session(repo, host_session_id, host=args.host) if host_session_id else None
        if existing and existing.get("kind") != "subagent":
            host_session_id = ""  # Never reinterpret a primary ID as a child shutdown.
    else:
        host_session_id = _event_key(raw, "session_id", "sessionId", "thread_id")
    parent_native = _event_key(raw, "parent_session_id", "parentSessionId", "parent_session")
    if kind == "subagent" and not parent_native:
        parent_native = _event_key(raw, "session_id", "sessionId")
    parent_id = stable_session_id(args.host, parent_native) if parent_native and parent_native != host_session_id else None

    def respond(result: dict[str, Any]) -> int:
        if not host_session_id:
            result["diagnostic"] = "CAO_SESSION_ID_MISSING: no distinct native session ID; lifecycle tracking skipped"
            if args.host != "pi":
                print(result["diagnostic"], file=sys.stderr)
        print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False))
        return 0

    if event == "session_start":
        observe_start(repo, args.host, host_session_id, primary=kind == "primary",
                      source=_event_key(raw, "reason", "source"))

    session = None
    if host_session_id:
        if event in {"session_start", "subagent_start"}:
            session = register_session(repo, work, host_session_id, args.host, event=event, kind=kind, parent_session_id=parent_id)
        else:
            session = update_session_event(repo, host_session_id, args.host, event, work, kind=kind, parent_session_id=parent_id)

    if event == 'prompt_submit' and not work and kind == 'primary' and (repo / CONFIG_REL).exists():
        capture_request(repo, raw.get('prompt'), args.host, host_session_id)

    restart = restart_state(repo, args.host)
    if not restart["ready"] and event not in {"session_end", "subagent_stop"}:
        context = restart["reason"] + ": " + restart["next"]
        if event == "pre_tool" and tool_is_mutating(raw):
            return respond({"decision": "deny", "reason": context, "additional_context": context})
        # Allow settling: a restart reminder must never force another Agent turn.
        return respond({"decision": "allow", "additional_context": context})

    if event in {"session_start", "subagent_start"}:
        if kind == "primary":
            expose_approval_context(repo, work, args.host, host_session_id)
        context = render_context(repo, work, session_id=host_session_id, host=args.host)
        result = {"decision": "allow", "additional_context": context}
        if host_session_id:
            result["session_id"] = host_session_id
        return respond(result)

    if event == "prompt_submit":
        if work and kind == "primary":
            review = None
            if work.get("verification_review"):
                try:
                    review = approve_baseline_response(work, raw, event, args.host, host_session_id,
                                                      verification_snapshot(config(repo), repo), now())
                except ValueError:
                    pass  # Invalid setup must remain recoverable through host context.
            if review:
                queue_event(work, {"event": "verification.baseline.approved", "work_item_id": work["id"],
                                   "review": review, "occurred_at": now()})
                save_work(repo, work)
            approval = approve_observed_response(repo, work, raw, event, args.host, host_session_id,
                provider_output_dirs(work["sdd"]["provider"]), now())
            if approval:
                queue_event(work, {"event": "sdd.spec.approved", "work_item_id": work["id"],
                                   "approval": approval, "occurred_at": now()})
                save_work(repo, work)
                reconcile_native_sdd_state(repo, work)
            expose_approval_context(repo, work, args.host, host_session_id)
        return respond({"decision": "allow", "additional_context": render_context(repo, work, session_id=host_session_id, host=args.host)})

    if event == "pre_tool":
        if work and kind == "primary":
            question_text = json.dumps(raw.get('tool_input') or raw.get('input') or raw.get('arguments') or {}, ensure_ascii=False).lower()
            baseline_question = '验证基线' in question_text or 'verification baseline' in question_text
            candidates = [work.get('verification_review') or {}] if baseline_question else [
                (work.get('sdd') or {}).get('spec_approval') or {}]
            for approval in candidates:
                if ((approval.get("status") == "pending" or approval.get("stop_after_approval"))
                        and bind_approval_question(approval, work["id"], raw, args.host, host_session_id)):
                    save_work(repo, work)
        # No-Work product writes and hand edits to managed state are denied.
        result: dict[str, Any] = {"decision": "allow"}
        # CAO's own state narrow enough that it can never deadlock intake:
        # `requirements/` stays writable (the Agent authors those), and CAO
        # itself writes state through commands, not through this hook.
        protected = write_targets_protected_state(repo, raw, work)
        if protected and tool_is_mutating(raw):
            result = {
                "decision": "deny",
                "reason": (
                    f"Do not edit CAO-managed state by hand ({', '.join(protected[:3])}). "
                    f"{protected_state_advice(protected)}"
                ),
            }
            return respond(result)
        analysis = tool_effect(raw, repo)
        route_input_write = (not analysis["diagnostics"] and bool(analysis["targets"])
                             and all(writable_input(repo, target, work) for target in analysis["targets"]))
        native_write = (route_input_write or write_targets_native_sdd_output(repo, raw)
                        or native_intake_shell(repo, raw) or diagnostic_shell_write(repo, raw))
        if tool_is_mutating(raw) and not native_write:
            if not work or work.get("status") != "active":
                advice_work = work
                if not advice_work and session and session.get("work_item_id"):
                    advice_work = load_work(repo, session["work_item_id"])
                startup = work_prerequisite_action(repo, advice_work)
                instruction = (STARTUP_ORDER if startup and startup.get('stage') == 'startup' else work_intake_instruction(advice_work))
                return respond({"decision": "deny", "reason": "CAO: no active Work; product writes are blocked. " + instruction})
            else:
                baseline = verification_baseline_state(repo, work)
                if not baseline["ready"] and not verification_scaffold_write(repo, raw):
                    return respond({"decision": "deny", "reason": baseline["reason"] + ": " + baseline["next"]})
                if not verification_scaffold_write(repo, raw):
                    preflight = implementation_preflight(repo, work)
                    if not preflight["ready"]:
                        return respond({"decision": "deny", "reason": str(preflight["reason"]) + ": " + preflight["next"] + " " + scaffold_instruction()})
                    capabilities = verification_capability_state(repo, work)
                    if not capabilities['ready']:
                        return respond({'decision': 'deny', 'reason': capabilities['reason'] + ': ' + capabilities['next']})
                    implementation = implementation_write_guard(repo, work, raw, host_session_id, kind)
                    if not implementation['ready']:
                        return respond({'decision': 'deny', 'reason': implementation['reason'] + ': ' + implementation['next']})
                review_patches = dispatch_patch_state(repo, work, code_snapshot)
                if not review_patches["ready"]:
                    return respond({"decision": "deny", "reason": review_patches["reason"] + ": classify every active native layer before patching; follow native loopback for intent/spec gaps."})
                path_state = path_assessment_state(repo, work)
                if not path_state["ready"]:
                    next_step = path_assessment_instruction(work)
                    if path_state["reason"] == "requirement changed since work started":
                        next_step = completion_next_instruction(repo, work, path_state) + " " + next_step
                    result["additional_context"] = f"CAO reminder: {path_state['reason']}. {next_step} Clarify open questions before reassessment."
                elif (work.get("path_assessment") or {}).get("profile") == "light":
                    state = light_spec_state(repo, work, provider_output_dirs((work.get("sdd") or {}).get("provider")))
                    if not state["ready"]:
                        result["additional_context"] = "CAO light reminder: " + (state.get("next") or state["reason"])
                else:
                    workflow = ((work.get("sdd") or {}).get("workflow") or {})
                    if not (workflow.get("execution") or {}).get("executed_at") and not (workflow.get("unverified") or {}).get("allowed"):
                        name = workflow.get("name") or "<native workflow>"
                        result["additional_context"] = (
                            f"CAO reminder: native SDD workflow `{name}` is selected but has NOT been executed. "
                            f"`cao done` requires native workflow evidence. Run it, then record it with "
                            f"`cao sdd workflow execute --work {work['id']} --command \"<command actually run>\"`."
                        )
        if tool_is_mutating(raw) and not native_write and not setup_state(config(repo))["ready"]:
            result["additional_context"] = (result.get("additional_context", "") + "\n" + setup_instruction(cao_invocation()["command"])).strip()
        commands = _collect_values_for_keys(raw, {"command", "cmd"})
        payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments")
        if isinstance(payload, str) and _event_key(raw, "tool_name", "toolname", "tool", "name").lower() in SHELL_TOOLS:
            commands.append(payload)
        for command in commands:
            dynamic = any(char in command for char in "$`")
            try:
                segments = _shell_segments(command)
                dynamic = dynamic or any(tokens[0] in {"eval", "source", "."} or "-c" in tokens for tokens in segments)
            except ValueError:
                dynamic = True
            if dynamic:
                result["additional_context"] = (result.get("additional_context", "") + "\nCAO risk reminder: dynamic or unparseable Shell targets cannot be reliably checked; this guard is not a Shell sandbox.").strip()
                break
        return respond(result)

    if event in {"post_tool", "file_changed"}:
        if work:
            observe_implementation_read(repo, work, raw, host_session_id)
        if work and kind == "primary":
            review = None
            if work.get('verification_review'):
                try:
                    review = approve_baseline_response(work, raw, event, args.host, host_session_id,
                                                       verification_snapshot(config(repo), repo), now())
                except ValueError:
                    pass  # An invalid declaration must remain repairable through the host.
            if review:
                queue_event(work, {'event': 'verification.baseline.approved', 'work_item_id': work['id'],
                                   'review': review, 'occurred_at': now()})
                save_work(repo, work)
            approval = approve_observed_response(repo, work, raw, event, args.host, host_session_id,
                provider_output_dirs(work["sdd"]["provider"]), now())
            if approval:
                queue_event(work, {"event": "sdd.spec.approved", "work_item_id": work["id"],
                                   "approval": approval, "occurred_at": now()})
                save_work(repo, work)
        if work and tool_is_mutating(raw):
            if (work.get("verification") or {}).get("status") == "passed":
                work.setdefault("resume", {})["summary"] = "Code changed after the last verification; verification is now stale."
            save_work(repo, work)
        if work:
            reconcile_native_sdd_state(repo, work)
        if work:
            prerequisite = work_prerequisite_action(repo, work)
            route = native_route_state(repo, work)
            if prerequisite or not route["ready"]:
                current = prerequisite or route_preparation_action(repo, work, route)
                return respond({"decision": "allow", "additional_context": runtime_action_context(repo, work, current)})
        return respond({"decision": "allow"})

    if event in {"session_end", "subagent_stop"}:
        if session and session.get("status") != "ended":
            if work and session.get("kind") != "subagent":
                create_handoff(repo, work, host_session_id, args.host, reason=event)
            end_session(repo, host_session_id, args.host, reason=_event_key(raw, "reason") or event, event=event)
        return respond({"decision": "allow"})

    if event in {"stop", "turn_end", "before_settle"}:
        if event != "before_settle" and work and session and session.get("kind") != "subagent":
            create_handoff(repo, work, host_session_id, args.host, reason=event)
        result = {"decision": "allow"}
        if work and work.get("status") == "active":
            current = current_work_action(repo, work)
            result["additional_context"] = "CAO completion reminder: " + runtime_action_context(repo, work, current)
        return respond(result)

    return respond({"decision": "allow"})


def native_hook_output(host: str, event: str, result: dict[str, Any]) -> dict[str, Any]:
    if host == "pi":
        return result
    hook_name = {"session_start": "SessionStart", "session_end": "SessionEnd", "prompt_submit": "UserPromptSubmit", "pre_tool": "PreToolUse", "post_tool": "PostToolUse", "subagent_start": "SubagentStart", "subagent_stop": "SubagentStop", "stop": "Stop", "turn_end": "Stop"}.get(event, event)
    out: dict[str, Any] = {}
    context = result.get("additional_context")
    if context:
        out["hookSpecificOutput"] = {"hookEventName": hook_name, "additionalContext": context}
    if event == "pre_tool":
        specific = out.setdefault("hookSpecificOutput", {"hookEventName": hook_name})
        specific["permissionDecision"] = "deny" if result.get("decision") == "deny" else "allow"
        if result.get("decision") == "deny":
            specific["permissionDecisionReason"] = result.get("reason") or "Blocked by CAO"
        return out
    if result.get("decision") == "deny":
        out["decision"] = "block"; out["reason"] = result.get("reason") or "Blocked by CAO"
    return out


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the read-only local orchestration dashboard."""
    from scripts.dashboard import serve_dashboard
    return int(serve_dashboard(args))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cao", description="Coding Agent Orchestrator")
    sub = p.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("init"); sp.add_argument("--repo", default="."); sp.add_argument("--sdd", default=None); sp.add_argument("--language", default=None, help="User language / locale for conversation and all SDD artifacts, e.g. zh-CN or en-US"); sp.add_argument("--verify-script", action="append", default=None, help="Project verification script command; repeat for multiple scripts"); sp.add_argument("--test-script", action="append", default=None, help="Required verification script declaring the tests gate; repeat for multiple test suites"); sp.add_argument("--verify-command", action="append", default=None, help=argparse.SUPPRESS); sp.add_argument("--host", choices=sorted(HOST_ADAPTER_META), default=None, help="Coding-agent host whose hooks are wired during initialization"); sp.add_argument("--force", action="store_true"); sp.set_defaults(fn=cmd_init)
    host = sub.add_parser("host", help="Wire or inspect the coding-agent host hooks for this project")
    host_sub = host.add_subparsers(dest="host_command", required=True)
    sp = host_sub.add_parser("install", help="Install the host adapter (SessionStart/PreToolUse/Stop hooks)")
    sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=sorted(HOST_ADAPTER_META), default=None, help="Host to wire; auto-detected from the environment when omitted")
    sp.set_defaults(fn=cmd_host)
    sp = host_sub.add_parser("status", help="Show whether each host adapter is wired")
    sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=sorted(HOST_ADAPTER_META), default=None, help="Host to inspect; defaults to all")
    sp.set_defaults(fn=cmd_host)
    sp = sub.add_parser("bootstrap"); sp.add_argument("--repo", default="."); sp.add_argument("--host", default=None, choices=["claude-code", "codex", "pi"]); sp.set_defaults(fn=cmd_bootstrap)
    sp = sub.add_parser("dashboard", help="Launch the read-only local orchestration dashboard")
    sp.add_argument("--repo", default=".", help="Project repository root")
    sp.add_argument("--host", default="127.0.0.1", help="Bind address; non-loopback requires --allow-remote")
    sp.add_argument("--port", type=int, default=8765, help="HTTP port (0 selects an available port)")
    sp.add_argument("--refresh", type=int, default=10, help="Browser refresh interval in seconds, minimum 3")
    sp.add_argument("--open", action="store_true", help="Open the dashboard URL in a browser")
    sp.add_argument("--allow-remote", action="store_true", help="Allow binding beyond localhost; exposes project metadata without authentication")
    sp.set_defaults(fn=cmd_dashboard)
    language = sub.add_parser("language", help="Configure user communication and SDD artifact language")
    language_sub = language.add_subparsers(dest="language_command", required=True)
    sp = language_sub.add_parser("show", help="Show the configured user language and installed SDDs")
    sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_language_show)
    sp = language_sub.add_parser("set", help="Set language and apply it to every installed SDD")
    sp.add_argument("--repo", default="."); sp.add_argument("--language", required=True, help="Locale or language name, e.g. zh-CN, en-US, Japanese")
    sp.set_defaults(fn=cmd_language_set)
    sp = language_sub.add_parser("apply", help="Reapply the configured language to installed SDDs")
    sp.add_argument("--repo", default="."); sp.add_argument("--provider", choices=sorted(SDD_CATALOG), help="Apply only to one provider; default applies to all installed SDDs")
    sp.set_defaults(fn=cmd_language_apply)
    sdd = sub.add_parser("sdd", help="Detect, select, and install a project SDD")
    sdd_sub = sdd.add_subparsers(dest="sdd_command", required=True)
    route = sdd_sub.add_parser("route", help="Record evidence-backed native routes separately from CAO quality profiles")
    route_sub = route.add_subparsers(dest="route_action", required=True)
    for name in ("propose", "show", "prepare", "rule", "check"):
        sp = route_sub.add_parser(name)
        sp.add_argument("--repo", default="."); sp.add_argument("--work")
        if name in {"propose", "check"}: sp.add_argument("--declaration", required=name == "propose", metavar="JSON_FILE", help="Existing declaration JSON path; new inputs use .orchestrator/inputs/WORK_ID/route.json")
        if name == "prepare": sp.add_argument("--from", dest="from_declaration", metavar="JSON_FILE", help="Copy an existing declaration into the new input location without overwriting a current draft")
        if name == "rule":
            sp.add_argument("--source", required=True, help="Actual installed native rule file")
            sp.add_argument("--start-line", required=True, type=int)
            sp.add_argument("--end-line", required=True, type=int)
            sp.add_argument("--index", type=int, help="Zero-based rule index to replace; default replaces the same source path or appends")
        sp.set_defaults(fn=cmd_sdd_route)
    artifact = sdd_sub.add_parser("artifact", help="Bind a running native artifact without certifying completion")
    artifact_sub = artifact.add_subparsers(dest="artifact_action", required=True)
    sp = artifact_sub.add_parser("bind")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--artifact", required=True)
    sp.set_defaults(fn=cmd_sdd_artifact_bind)
    approval = sdd_sub.add_parser("approval", help="Request native SPEC approval and inspect host-observed evidence")
    approval_sub = approval.add_subparsers(dest="approval_action", required=True)
    for name in ("request", "show"):
        sp = approval_sub.add_parser(name)
        sp.add_argument("--repo", default="."); sp.add_argument("--work")
        sp.set_defaults(fn=cmd_sdd_approval)
    sp = sdd_sub.add_parser("sync", help="Synchronize native progress and implementation blockers")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_sdd_sync)
    review = sdd_sub.add_parser("review", help="Record independent native reviews and pre-patch dispatch classification")
    review_sub = review.add_subparsers(dest="review_action", required=True)
    for name in ("begin", "attach", "submit", "classify", "resolve", "findings"):
        sp = review_sub.add_parser(name)
        sp.add_argument("--repo", default="."); sp.add_argument("--work")
        if name != 'findings':
            sp.add_argument("--layer", required=True)
        if name == "begin":
            sp.add_argument("--model", required=True); sp.add_argument("--instructions", required=True)
            sp.add_argument('--restart', action='store_true', help='Explicitly replace a failed current dispatch ticket')
        elif name == 'findings':
            sp.add_argument('--source', required=True, help='Unmodified native Markdown list or JSON findings under _bmad-output')
        elif name == 'attach':
            sp.add_argument('--run', required=True); sp.add_argument('--input-sha256', required=True)
            sp.add_argument('--model', required=True)
        else:
            sp.add_argument("--report", required=True)
        sp.set_defaults(fn=cmd_sdd_review)
    acceptance = sdd_sub.add_parser("acceptance", help="Independent SDD final acceptance for full paths")
    acceptance_sub = acceptance.add_subparsers(dest="acceptance_action", required=True)
    for name in ("configure", "show", "run", "begin", "submit", "protocol"):
        sp = acceptance_sub.add_parser(name)
        sp.add_argument("--repo", default="."); sp.add_argument("--work")
        if name == "configure":
            sp.add_argument("--declaration", required=True); sp.add_argument("--replace", action="store_true")
        if name == "submit":
            sp.add_argument("--run", required=True); sp.add_argument("--report", required=True); sp.add_argument("--exit-code", type=int, default=0)
        sp.set_defaults(fn=cmd_sdd_acceptance)
    spec = sdd_sub.add_parser("spec", help="Bind a minimal native SPEC and explicit criteria")
    spec_sub = spec.add_subparsers(dest="spec_command", required=True)
    sp = spec_sub.add_parser("bind")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--spec", required=True)
    sp.add_argument("--criteria"); sp.add_argument("--criteria-section"); sp.add_argument("--replace", action="store_true")
    sp.set_defaults(fn=cmd_sdd_spec)
    plan = sdd_sub.add_parser("plan", help="Associate native execution items with this requirement")
    plan_sub = plan.add_subparsers(dest="plan_action", required=True)
    sp = plan_sub.add_parser("bind")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.add_argument("--plan", action="append", help="Associated native task file or quoted OpenSpec tracked-file glob; repeat for ALL changes/features")
    sp.add_argument("--plan-item", action="append", help="BMAD plan path#story-id or path#epic-N; repeat for ALL requirement items; adds to existing scope")
    sp.set_defaults(fn=cmd_sdd_plan_bind)
    sp = sdd_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_sdd_list)
    sp = sdd_sub.add_parser("select"); sp.add_argument("--repo", default="."); sp.add_argument("--provider", required=True, choices=sorted(SDD_CATALOG)); sp.set_defaults(fn=cmd_sdd_select)
    sp = sdd_sub.add_parser("install"); sp.add_argument("--repo", default="."); sp.add_argument("--provider", choices=sorted(SDD_CATALOG)); sp.add_argument("--tools", default=None, help="OpenSpec init tool ids, or all/none"); sp.add_argument("--host", choices=["claude-code", "codex", "pi"], default=None, help="Current coding-agent host; used by Spec Kit to select its integration"); sp.add_argument("--integration", default=None, help="Spec Kit integration key, e.g. claude, codex, pi, copilot"); sp.add_argument("--integration-options", default=None, help="Spec Kit integration options passed through as one value"); sp.add_argument("--yes", action="store_true", help="Confirm installation of external/project software"); sp.set_defaults(fn=cmd_sdd_install)
    sp.add_argument("--upgrade", action="store_true", help="Explicitly upgrade the machine CLI instead of reusing an installed OpenSpec CLI")
    workflow = sdd_sub.add_parser("workflow", help="Record and enforce native SDD workflow execution")
    workflow_sub = workflow.add_subparsers(dest="workflow_action", required=True)
    sp = workflow_sub.add_parser("select", help="Record the provider-native workflow before executing it")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--name", required=True); sp.add_argument("--entrypoint", required=True)
    sp.add_argument("--allow-unverified", action="store_true", help="Record a documented exception when the entrypoint cannot be verified; requires --reason")
    sp.add_argument("--reason", default=None, help="Why this workflow cannot be verified natively; required with --allow-unverified")
    sp.set_defaults(fn=cmd_sdd_workflow)
    sp = workflow_sub.add_parser("execute", help="Record that the selected native workflow was actually executed")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--command", required=True, help="The command or skill invocation that was actually run")
    sp.add_argument("--exit-code", type=int, default=0, help="Exit code returned by the executed workflow")
    sp.add_argument("--output", default=None, help="Tail of the workflow output to keep as evidence")
    sp.add_argument("--allow-unverified", action="store_true", help="Record a documented exception; requires --reason")
    sp.add_argument("--reason", default=None, help="Why the executed command cannot be verified; required with --allow-unverified")
    sp.set_defaults(fn=cmd_sdd_workflow)
    sp = workflow_sub.add_parser("complete", help="Record native workflow completion and actual output files")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--artifact", action="append", required=True, help="Existing repository-relative output file; repeat for each file")
    plan = sp.add_mutually_exclusive_group()
    plan.add_argument("--plan", action="append", help="Associated plan path; repeat for related changes/features")
    plan.add_argument("--no-plan", action="store_true", help="Explicitly declare that this workflow produces no plan")
    sp.add_argument("--plan-item", action="append", help="Shared plan path#story-id or path#epic-N; repeat for every associated story/epic")
    sp.add_argument("--allow-unverified", action="store_true", help="Record a documented native-entrypoint or failed-execution exception; output ownership is still required")
    sp.add_argument("--reason", default=None, help="Why this completion needs an exception; required with --allow-unverified")
    sp.set_defaults(fn=cmd_sdd_workflow)
    lifecycle = sub.add_parser("lifecycle", help="Inspect persisted CAO lifecycle events")
    lifecycle_sub = lifecycle.add_subparsers(dest="lifecycle_command", required=True)
    sp = lifecycle_sub.add_parser("events", help="List lifecycle events from native SDD reconciliation and CAO transitions")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--limit", type=int, default=100); sp.set_defaults(fn=cmd_lifecycle_events)
    sp = sub.add_parser("start"); sp.add_argument("--repo", default="."); sp.add_argument("--requirement"); sp.add_argument("--title", default=None, help="Human-readable Work Item title; overrides the requirement filename"); sp.add_argument("--sdd-ref"); sp.add_argument("--sdd-provider", choices=sorted(SDD_CATALOG)); sp.add_argument("--host", choices=["claude-code", "codex", "pi"], default=None, help="Current coding-agent host; used to attach the runtime Session"); sp.add_argument("--replace", action="store_true"); sp.add_argument("--reopen", action="store_true"); sp.set_defaults(fn=cmd_start)
    path = sub.add_parser("path", help="Assess and inspect the minimum-safe execution path")
    path_sub = path.add_subparsers(dest="path_command", required=True)
    sp = path_sub.add_parser("assess", help="Record an Agent's lightweight scope/risk assessment")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.add_argument("--profile", required=True, choices=EXECUTION_PROFILES)
    sp.add_argument("--reason", required=True, help="Short explanation for the selected profile")
    risk_cli_names = sorted(set(PATH_RISK_MINIMUM) | {name.replace("_", "-") for name in PATH_RISK_MINIMUM})
    sp.add_argument("--risk-flag", action="append", default=[], choices=risk_cli_names, help="Risk requiring a minimum profile; repeat as needed")
    sp.add_argument("--question", action="append", default=[], help="Unresolved requirement question; repeat as needed")
    sp.add_argument("--assessed-by", default="agent", help="Who performed the assessment")
    sp.set_defaults(fn=cmd_path_assess)
    sp = path_sub.add_parser("show", help="Show the current Work Item's path assessment")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.set_defaults(fn=cmd_path_show)
    gate = sub.add_parser("gate", help="Satisfy and inspect the execution gates of the selected path")
    gate_sub = gate.add_subparsers(dest="gate_command", required=True)
    sp = gate_sub.add_parser("satisfy", help="Record that a required gate has actually been satisfied")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--gate", required=True, help="Gate name from `cao path show`, e.g. scope_review")
    sp.add_argument("--evidence", required=True, help="What was actually done to satisfy this gate")
    sp.add_argument("--satisfied-by", default="agent", help="Who satisfied the gate")
    sp.set_defaults(fn=cmd_gate)
    sp = gate_sub.add_parser("show", help="Show the required gates and their current status")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.set_defaults(fn=cmd_gate)
    artifact = sub.add_parser("artifact", help="Manage SDD artifact references")
    artifact_sub = artifact.add_subparsers(dest="artifact_command", required=True)
    sp = artifact_sub.add_parser("add"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path", required=True); sp.add_argument("--role"); sp.add_argument("--native-type", dest="native_type"); sp.add_argument("--native-ref", dest="native_ref"); sp.set_defaults(fn=cmd_artifact_add)
    sp = artifact_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_artifact_list)
    sp = artifact_sub.add_parser("confirm", help="Mark an SDD artifact as reviewed and confirmed"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path", required=True); sp.add_argument("--confirmed-by", default="user"); sp.set_defaults(fn=cmd_artifact_confirm)
    sp = artifact_sub.add_parser("remove"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path", required=True); sp.set_defaults(fn=cmd_artifact_remove)
    sp = artifact_sub.add_parser("refresh"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path"); sp.set_defaults(fn=cmd_artifact_refresh)
    sp = sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_list)
    sp = sub.add_parser("status"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_status)
    instructions = sub.add_parser("instructions", help="Preview or apply a safe CAO project-instruction upgrade")
    instructions_sub = instructions.add_subparsers(dest="instructions_command", required=True)
    sp = instructions_sub.add_parser("update", help="Preview managed/known legacy instructions; --apply backs up and replaces only CAO content")
    sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=["claude-code", "codex", "pi"])
    sp.add_argument("--apply", action="store_true"); sp.set_defaults(fn=cmd_instructions_update)
    sp = sub.add_parser("next"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_next)
    sp = sub.add_parser("resume"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--host", default="unknown"); sp.add_argument("--session"); sp.add_argument("--reactivate", action="store_true"); sp.set_defaults(fn=cmd_resume)
    sp = sub.add_parser("checkpoint"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--step"); sp.add_argument("--next"); sp.add_argument("--next-type", choices=sorted(ACTION_TYPES), default="agent"); sp.add_argument("--command"); sp.add_argument("--summary"); sp.add_argument("--completed"); sp.add_argument("--pending"); sp.set_defaults(fn=cmd_checkpoint)
    sp = sub.add_parser("decision"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--decision", required=True); sp.add_argument("--reason"); sp.add_argument("--scope"); sp.add_argument("--supersedes"); sp.set_defaults(fn=cmd_decision)
    sp = sub.add_parser("block"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--reason", required=True); sp.add_argument("--resolution"); sp.add_argument("--suspend", action="store_true"); sp.set_defaults(fn=cmd_block)
    sp = sub.add_parser("unblock"); sp.add_argument("--repo", default="."); sp.add_argument("--work", required=True); sp.add_argument("--blocker"); sp.set_defaults(fn=cmd_unblock)
    sp = sub.add_parser("handoff", help="Create or inspect Handoff records"); sp.add_argument("handoff_action", nargs="?", choices=["list", "show"]); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host"); sp.add_argument("--reason", default="manual checkpoint"); sp.add_argument("--summary"); sp.add_argument("--end-session", action="store_true", help="End the referenced Session after creating the handoff"); sp.add_argument("--id", dest="handoff_id"); sp.add_argument("--limit", type=int, default=20); sp.set_defaults(fn=cmd_handoff)
    sp = sub.add_parser("decisions", help="List recorded decisions for a Work Item"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--limit", type=int, default=50); sp.set_defaults(fn=cmd_decision_list)
    sessions = sub.add_parser("sessions", help="Inspect Agent Sessions")
    sessions_sub = sessions.add_subparsers(dest="sessions_command", required=True)
    sp = sessions_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--work"); sp.add_argument("--status", choices=["active", "ended"]); sp.add_argument("--stale-after-minutes", type=int, default=30); sp.set_defaults(fn=cmd_sessions_list)
    sp = sessions_sub.add_parser("show"); sp.add_argument("--repo", default="."); sp.add_argument("--id", required=True); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--stale-after-minutes", type=int, default=30); sp.set_defaults(fn=cmd_sessions_show)
    verification = sub.add_parser("verification", help="Configure project verification without resetting other state")
    verification_sub = verification.add_subparsers(dest="verification_command", required=True)
    sp = verification_sub.add_parser("smoke", help="HTTP smoke with a fresh port and owned server process")
    sp.add_argument("--repo", default="."); sp.add_argument("--server-command", required=True)
    sp.add_argument("--path", default="/"); sp.add_argument("--method", choices=["GET", "POST"], default="GET")
    sp.add_argument("--body"); sp.add_argument("--expect-status", type=int, default=200)
    sp.add_argument("--timeout", type=float, default=10)
    sp.set_defaults(fn=cmd_verification_smoke)
    sp = verification_sub.add_parser("configure", help="Declare required project scripts without installing or executing anything")
    sp.add_argument("--work", help="Work to inspect for reanchor; scripts remain project-wide")
    sp.add_argument("--repo", default="."); sp.add_argument("--test-script", action="append"); sp.add_argument("--verify-script", action="append")
    sp.add_argument("--security-script", action="append", help="Required security checks; reuse the same test wrapper to declare both capabilities without running it twice")
    sp.add_argument("--replace", action="store_true", help="Explicitly replace different existing verification declarations")
    sp.set_defaults(fn=cmd_verification_configure)
    sp = sub.add_parser("verify"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--script", action="append", help="Engineering diagnostic only, never grants done eligibility; repeat for multiple scripts"); sp.add_argument("--command", action="append", help=argparse.SUPPRESS); sp.set_defaults(fn=cmd_verify)
    sp = sub.add_parser("done"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_done)
    sp = sub.add_parser("reopen", help="Reopen a completed Work Item that should not have been closed"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--reason", help="Why the completion was wrong, e.g. which part of the requirement is missing"); sp.add_argument("--reanchor", action="store_true", help="Explicitly accept the current verification standard and clear gates/verification; also supports active Work Items"); sp.set_defaults(fn=cmd_reopen)
    sp = sub.add_parser("context"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host", default="unknown"); sp.set_defaults(fn=cmd_context)
    requirement = sub.add_parser("requirement", help="Allocate numbered CAO requirement documents")
    requirement_sub = requirement.add_subparsers(dest="requirement_action")
    sp = requirement_sub.add_parser("next-id", help="Reserve the next numbered requirement path"); sp.add_argument("--repo", default="."); sp.add_argument("--title", help="Requirement title, used for the filename slug"); sp.add_argument("--date", help="Override the date segment (YYYY-MM-DD)"); sp.set_defaults(fn=cmd_requirement_next_id)
    sp = requirement_sub.add_parser("list", help="List CAO requirement documents"); sp.add_argument("--repo", default="."); sp.add_argument("--limit", type=int, default=50); sp.set_defaults(fn=cmd_requirement_list)
    sp = sub.add_parser("host-event"); sp.add_argument("--repo", default="."); sp.add_argument("--host", default="unknown"); sp.set_defaults(fn=host_event)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        # Lifecycle observation and repair/inspection remain usable while the
        # installed host resources await reload. Work advancement must wait.
        # Dispatch by handler: several existing subcommands also use the
        # argparse destination `command` for a shell command or a list of them.
        repair_or_read = args.fn in {
            cmd_init, cmd_bootstrap, cmd_host, host_event, cmd_language_show,
            cmd_language_set, cmd_language_apply, cmd_instructions_update,
            cmd_list, cmd_status, cmd_next, cmd_context, cmd_sessions_list,
            cmd_sessions_show, cmd_decision_list, cmd_handoff, cmd_dashboard,
            cmd_sdd_list, cmd_sdd_install, cmd_sdd_select,
        }
        if not repair_or_read:
            host = getattr(args, "host", None)
            restart = restart_state(repo_root(args.repo), host if host in HOST_ADAPTER_META else detect_current_host())
            if not restart["ready"]:
                print(json.dumps({"ok": False, "error": restart["reason"], "restart": restart,
                                  "next": action("human", restart["next"], reason=restart["reason"])}, ensure_ascii=False, indent=2))
                return 2
        return int(args.fn(args))
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "error": str(exc)}
        if str(exc).startswith("SNAPSHOT_"):
            result["reason_code"] = str(exc).split(":", 1)[0]
            result["next"] = "Restore readable repository-owned inputs and executable permissions; replace external business links with repository-owned sources, repair broken/cyclic links or reduce the linked scan scope, then run `cao verify`. Old evidence is not reusable."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
