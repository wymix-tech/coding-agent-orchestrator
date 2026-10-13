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
from scripts.path_guard import inside, repo_file, provider_artifact, patch_targets
from scripts.completion_policy import (
    acceptance_hash, anchor_acceptance, reset_requirement, evidence_matches,
    verification_fresh as bound_verification_fresh, normalize_scripts, tests_satisfied, workflow_ready, normalize_timeout,
)
from scripts.verification_runner import run_script
from scripts.light_spec_policy import make_binding, binding_digest, light_spec_state
from scripts.sdd_acceptance_policy import digest as acceptance_digest
from scripts.sdd_acceptance_files import source_hashes, output_paths, report_hashes
from scripts.sdd_acceptance_runtime import acceptance_status, reanchor_record
from scripts.verification_setup import (
    configured_scripts, setup_state, declared_scripts, prepare_config, setup_instruction, verification_setup_context,
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
            "light": "For a clear small OpenSpec change, invoke the installed /opsx:propose then /opsx:apply agent commands (host syntax varies), and sync/archive after checks. Expanded /opsx:ff is available only if installed/enabled and creates artifacts, not a skip. Follow the active schema: default apply requires tasks and upstream artifacts; keep them concise rather than deleting them. A smaller custom schema must be explicitly configured and validated. CAO light adds no workflow/plan registration requirement.",
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
    pending: list[tuple[str, bool]] = []
    for gate in gates:
        if gate == "sdd_acceptance":
            ok = bool(sdd_ready)
        elif gate == "tests":
            ok = bool(verification_fresh and tests_satisfied(work.get("verification") or {}))
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
                content = source.read_text(encoding="utf-8")
                # Resolve the host placeholder when provisioning knows the host,
                # so the Agent never has to guess it.
                if host:
                    content = content.replace("--host <host>", f"--host {host}")
                # Render the real CAO invocation so the Agent never invents a path.
                content = content.replace("{{CAO_COMMAND}}", cao_invocation()["command"])
                stream.write(content)
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
    return result


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
        packages = [str(p) for p in (settings.get("packages") or [])]
        installed = any("coding-agent-orchestrator" in p for p in packages)
        return {**info, "installed": installed, "target": meta["target"], "packages": packages}
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
                "next": "pi install --local <path-to-coding-agent-orchestrator>",
            }
        result = subprocess.run(
            ["pi", "install", "--local", str(root)],
            cwd=repo, text=True, capture_output=True, timeout=120,
        )
        return {
            "host": host,
            "status": "installed" if result.returncode == 0 else "error",
            "target": meta["target"],
            "command": f"pi install --local {root}",
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


def choose_external_install_command(provider: str) -> list[str] | None:
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


def language_policy(language: str) -> str:
    label = language_label(language)
    return (
        f"Communicate with the user in {label}. Write all SDD artifacts and project documentation "
        f"in {label}, including requirements/specifications, proposals, designs, plans, tasks, "
        "architecture documents, review summaries, and handoffs. Keep code symbols, API names, "
        "commands, standardized keywords, and file names unchanged where required. If the user "
        "explicitly requests a different language for a specific artifact or conversation, follow "
        "that explicit request for the requested scope."
    )


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
    """Update language keys where present and ensure canonical values in [core]."""
    lines = text.splitlines()
    changed = False
    patterns = {key: re.compile(r"^(\s*)" + re.escape(key) + r"(\s*=).*$") for key in values}
    out: list[str] = []
    for line in lines:
        updated = line
        for key, pattern in patterns.items():
            match = pattern.match(line)
            if match:
                updated = f"{match.group(1)}{key}{match.group(2)} {_toml_quote(values[key])}"
                break
        changed = changed or updated != line
        out.append(updated)
    interim = "\n".join(out) + ("\n" if text.endswith("\n") or out else "")
    canonical, canonical_changed = _set_toml_table_values(interim, "core", values)
    return canonical, changed or canonical_changed


def configure_bmad_language(repo: Path, language: str) -> dict[str, Any]:
    """Apply language with a high-precedence BMAD user override plus legacy YAML support.

    Current BMAD merges central TOML layers. CAO writes only its two managed keys
    to the highest-precedence custom user layer rather than rewriting team-owned
    base config. Older installations that still use module YAML receive the same
    two-key update in those legacy files.
    """
    bmad_root = repo / "_bmad"
    if not bmad_root.is_dir():
        raise ValueError("BMAD is not installed: expected _bmad/ in the project")
    label = language_label(language)
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
        # This is the highest-precedence supported layer; it overrides language
        # defaults without mutating committed team configuration.
        targets.append((bmad_root / "custom" / "config.user.toml", "toml"))
    elif yaml_candidates:
        targets.extend((path, "yaml") for path in yaml_candidates)
    else:
        # A partially initialized/new BMAD directory still gets a valid modern
        # user-level config rather than failing after installation.
        targets.append((bmad_root / "custom" / "config.user.toml", "toml"))

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
            "configured": True, "changed_files": changed, "unchanged_files": unchanged}


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
    cfg = config(repo)
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
            f"## 验收标准\n\n<!-- 完成时必须满足的条件。 -->\n\n"
            f"## 范围外\n\n<!-- 明确不属于本次需求的内容（可选）。 -->\n"
        )
    return (
        f"# {title}\n\n"
        f"- Requirement ID: {number}\n"
        f"- Created: {day}\n"
        f"- Status: draft\n\n"
        f"## Description\n\n<!-- Describe the requirement here. -->\n\n"
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


def code_snapshot(repo: Path, excluded: set[str] | None = None) -> str:
    """Prune ignored directories; hash internal file links by their lexical names.

    Directory links are not followed. Unreadable or external file inputs cannot certify freshness.
    """
    excluded = excluded or set()
    h = hashlib.sha256()
    files = []
    def walk_error(exc):
        raise RuntimeError("SNAPSHOT_READ_FAILED: " + str(exc.filename or repo)) from exc
    for base, dirs, names in os.walk(repo, topdown=True, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(directory for directory in dirs if directory not in SNAPSHOT_IGNORE_DIRS)
        files.extend(Path(base) / name for name in names if name not in SNAPSHOT_IGNORE_DIRS)
    for p in sorted(files):
        rp = p.relative_to(repo).as_posix()
        if rp in excluded:
            continue
        if not p.resolve().is_relative_to(repo.resolve()):
            raise RuntimeError("SNAPSHOT_EXTERNAL_SYMLINK: " + rp)
        try:
            if not stat.S_ISREG(p.stat().st_mode):
                continue
            data = p.read_bytes()
        except OSError as exc:
            raise RuntimeError("SNAPSHOT_READ_FAILED: " + rp) from exc
        h.update(rp.encode()); h.update(b"\0"); h.update(hashlib.sha256(data).digest()); h.update(b"\0")
    return h.hexdigest()


def verification_timeout(cfg: dict[str, Any]) -> float:
    return normalize_timeout((cfg.get("verification") or {}).get("timeout_seconds", 300.0))


def verification_snapshot(cfg: dict[str, Any]) -> str:
    return acceptance_hash(verification_scripts(cfg), verification_timeout(cfg))


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


def _native_candidates(repo: Path, provider: str, work: dict[str, Any], native_ref: str) -> tuple[list[Path], Path | None]:
    """Return candidate artifact paths scoped to this Work Item, never the whole SDD tree by default."""
    requirement_path = str((work.get("requirement") or {}).get("path") or "")
    title = str(work.get("title") or "")
    slugs = [_slug(Path(requirement_path).stem), _slug(title)]
    slugs = [value for value in dict.fromkeys(slugs) if value]
    ref_path = (repo / native_ref).resolve() if native_ref else None
    try:
        ref_path.relative_to(repo.resolve())
    except (ValueError, AttributeError):
        ref_path = None
    if ref_path and ref_path.exists():
        if ref_path.is_file():
            if provider == "bmad":
                return [ref_path], ref_path
            parent = ref_path.parent
            return sorted(p for p in parent.rglob("*.md") if p.is_file()), parent
        if ref_path.is_dir():
            return sorted(p for p in ref_path.rglob("*.md") if p.is_file()), ref_path

    if provider == "bmad":
        root = repo / "_bmad-output/implementation-artifacts"
        if not root.is_dir():
            return [], None
        all_specs = sorted((p for p in root.glob("spec-*.md") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        def bmad_matches(path: Path) -> bool:
            file_slug = _slug(path.stem.removeprefix("spec-"))
            return bool(file_slug and any(file_slug == wanted or file_slug in wanted or wanted in file_slug for wanted in slugs))
        selected = [p for p in all_specs if bmad_matches(p)]
        # Prefer an exact identity match before considering partial slug matches.
        # When more than one candidate remains, fail closed: none of them may
        # drive lifecycle state until the current Work Item is unambiguously bound.
        exact = [p for p in selected if _slug(p.stem.removeprefix("spec-")) in slugs]
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
    matching = [p for p in dirs if any(_slug(p.name) == wanted or _slug(p.name) in wanted or wanted in _slug(p.name) for wanted in slugs)]
    exact = [p for p in matching if _slug(p.name) in slugs]
    if len(exact) == 1:
        matching = exact
    elif exact or len(matching) != 1:
        return [], None
    feature_dir = matching[0]
    return sorted(p for p in feature_dir.rglob("*.md") if p.is_file()), feature_dir


def _read_native_status(provider: str, candidates: list[Path]) -> tuple[str | None, str | None, str | None, dict[str, Any] | None]:
    """Extract only explicit machine status or task-checkbox evidence from work-specific files."""
    if provider == "bmad":
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
            match = re.search(r"(?im)^\s*status\s*:\s*['\"]?([a-z_-]+)", frontmatter)
            if match:
                raw = match.group(1).lower().replace("-", "_")
                return raw, path.name, hashlib.sha256(content.encode("utf-8")).hexdigest(), None
        return None, None, None, None

    tasks = next((p for p in candidates if p.name.lower() == "tasks.md"), None)
    if not tasks:
        return None, None, None, None
    try:
        content = tasks.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None, None, None, None
    checked = len(re.findall(r"(?m)^\s*[-*+]\s+\[[xX]\]", content))
    unchecked = len(re.findall(r"(?m)^\s*[-*+]\s+\[ \]", content))
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
    old_native = sdd.get("native_state") if isinstance(sdd.get("native_state"), dict) else {}
    old_raw = old_native.get("raw")
    old_canonical = old_native.get("canonical")
    new_snapshot = {
        "raw": raw_state,
        "canonical": canonical,
        "source": status_source,
        "evidence_sha256": evidence_hash,
        "task_counts": task_counts,
    }
    if canonical is not None and (old_native.get("canonical") != canonical or old_native.get("raw") != raw_state or old_native.get("evidence_sha256") != evidence_hash):
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
        elif old_native.get("evidence_sha256") != evidence_hash:
            queue_event(work, {**base_event, "event": "sdd.native_artifact.changed", "idempotency_key": idem + ":artifact", "event_id": "evt-" + idem[:16] + "artifact"})
        sdd["native_state"] = new_snapshot
        changed = True
        work.setdefault("resume", {})["last_native_sync_at"] = new_snapshot["observed_at"]

    # Reconcile the CAO phase on every read, not only on a newly observed native
    # transition. This repairs stale CAO state even if an earlier sync was missed.
    if canonical is not None:
        execution = work.setdefault("execution", {})
        current_step = str(execution.get("step") or "").lower()
        if canonical == "ready" and current_step in {"path-selected", "planning", "plan-selected", "sdd-planning"}:
            execution["step"] = "ready-for-dev"
            execution["next_action"] = action("agent", f"Native {provider} work is ready; begin implementation using the bound native work item.")
            work.setdefault("resume", {})["summary"] = f"Native SDD reports ready for implementation ({provider})."
            changed = True
        elif canonical == "in_progress" and current_step not in {"implementation", "review", "verification", "release", "done"}:
            execution["step"] = "implementation"
            execution["next_action"] = action("agent", f"Continue implementation using the native {provider} work item; phase synchronized from explicit native status/task progress.")
            work.setdefault("resume", {})["summary"] = f"Native SDD reports implementation in progress ({provider}); CAO phase reconciled."
            changed = True
        elif canonical in {"completed", "in_review"} and current_step in {"path-selected", "planning", "plan-selected", "sdd-planning", "ready-for-dev", "implementation"}:
            execution["step"] = "review"
            execution["next_action"] = action("agent", "Native implementation is ready for review; complete implementation review and CAO verification before closing the Work Item.")
            work.setdefault("resume", {})["summary"] = f"Native SDD reports review/implementation completion ({provider}); CAO moved to review, not closed."
            changed = True
        elif canonical == "blocked":
            blockers = work.setdefault("blockers", [])
            if not any(isinstance(item, dict) and item.get("id") == "native-sdd-state" for item in blockers):
                blockers.append({"id": "native-sdd-state", "reason": f"Native {provider} state is blocked", "source": status_source, "observed_at": now()})
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
    if work:
        _refresh_work(repo, work)
    session = load_session(repo, session_ref, host=host)
    if session is None:
        return register_session(repo, work, session_ref, host, event=event, kind=kind, parent_session_id=parent_session_id)
    if session.get("host") == "unknown" and host and host != "unknown":
        # Re-register to re-key the stored file using the now-known Host identity.
        return register_session(
            repo,
            work,
            session_ref,
            host,
            event=event,
            kind=kind if kind else session.get("kind", "primary"),
            parent_session_id=parent_session_id if parent_session_id is not None else session.get("parent_session_id"),
        )
    if host and host != "unknown":
        session["host"] = host
    session["work_item_id"] = work.get("id") if work else session.get("work_item_id")
    if session.get("status") != "ended" and event not in {"session_end", "subagent_stop"}:
        session["status"] = "active"
    session["last_event"] = event
    session["last_event_at"] = now()
    if event in {"session_start", "subagent_start"} and session.get("status") == "ended":
        session["started_at"] = now()
        session["ended_at"] = None
        session["end_reason"] = None
    save_session(repo, session)
    if work:
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


def current_work_id(repo: Path, index: dict[str, Any] | None = None) -> str | None:
    index = index or load_index(repo)
    return index.get("active_work")


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
        "Perform a lightweight scope/risk assessment, choose light, standard, or comprehensive, "
        "then record it with `cao path assess --profile <profile> --reason \"<short reason>\"" +
        " [--risk-flag <flag>]" + work_arg + "`. Do not build a separate evidence model or scorecard."
    )


# Each provider records its own plan, and each format says "not done yet"
# differently: BMAD keeps epic/story states in `sprint-status.yaml`, OpenSpec
# and Spec Kit keep checkbox lists in `tasks.md`. A Work Item is a single unit,
# so without reading these an Agent can finish one story of thirteen and close
# the requirement as complete (demo11 did exactly that).
PLAN_ARCHIVE_MARKERS = ("/archive/",)

# `sprint-status.yaml` line: an indented `name: state` pair whose value is a bare
# state word (so metadata like `generated: 10-10-2026 23:30` does not match).
SPRINT_STATUS_LINE = re.compile(r"^\s+(?P<name>[^#\s][^:]*?):\s*(?P<state>[a-z][a-z-]*)\s*$")
SPRINT_DONE_STATES = frozenset({"done"})
# Retrospectives are optional by definition, and an epic's state is derived from
# its stories, so counting both would double-report the same work.
SPRINT_IGNORED_NAMES = ("epic-", "-retrospective")

TASK_PENDING_LINE = re.compile(r"^\s*[-*]\s*\[\s\]\s*(?P<task>\S.*?)\s*$")


def _pending_checkbox_tasks(path: Path) -> list[str]:
    """Unchecked tasks, as OpenSpec and Spec Kit write them."""
    pending: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = TASK_PENDING_LINE.match(line)
        if match:
            task = match.group("task").strip()
            # Strip a leading task id so the message stays readable.
            pending.append(task[:80])
    return pending


def _sprint_states(path: Path) -> dict[str, str]:
    states = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SPRINT_STATUS_LINE.match(line)
        if match:
            states[match.group("name").strip().strip("\"'")] = match.group("state").strip()
    return states


def bind_plan_scope(repo: Path, work: dict, plan_paths: list[str],
                    selections: dict[str, list[str]], no_plan: bool) -> dict:
    repo = repo.resolve()
    sdd = work.get("sdd") or {}
    roots = [(repo / directory).resolve() for directory in provider_output_dirs(sdd.get("provider"))]
    paths = {resolve_artifact_path(repo, value)[1] for value in plan_paths}
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
    discovered = set()
    ambiguous = False
    public_roots = set(roots) | {(repo / "openspec/changes").resolve(), (repo / "specs").resolve()}
    for anchor in anchors:
        for name in ("tasks.md", "sprint-status.yaml"):
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
    if not paths and old.get("mode") == "bound":
        # Keep prior associations and add newly produced specific changes, never a public root.
        paths.update(value for value in discovered if (repo / value).parent not in public_roots)
        for plan in old.get("plans", []):
            paths.add(plan["path"])
            normalized[plan["path"]] = list(plan.get("item_ids") or [])
    if not paths and any(Path(value).name == "sprint-status.yaml" for value in discovered):
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
        if path.name == "sprint-status.yaml":
            states = _sprint_states(path)
            if not ids:
                raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + relative)
            if any(item not in states or any(marker in item.lower() for marker in SPRINT_IGNORED_NAMES) for item in ids):
                raise ValueError("PLAN_ITEM_INVALID: " + relative)
        elif path.name != "tasks.md" or ids:
            raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + relative)
        plans.append({"path": relative, "item_ids": ids})
    return {"version": 1, "mode": "bound", "plans": plans}


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
        if any(marker in f"/{relative}" for marker in PLAN_ARCHIVE_MARKERS):
            continue
        if path.name == "sprint-status.yaml":
            ids = plan.get("item_ids") or []
            if not ids:
                raise ValueError("SHARED_PLAN_ITEM_BINDING_REQUIRED: " + relative)
            states = _sprint_states(path)
            missing = [item for item in ids if item not in states]
            if missing:
                raise ValueError("PLAN_ITEM_MISSING: " + relative + "#" + ",".join(missing))
            pending.extend(f"{relative}#{item} ({states[item]})" for item in ids
                           if states[item] not in SPRINT_DONE_STATES and not states[item].startswith("optional"))
        elif path.name == "tasks.md":
            pending.extend(f"{relative}: {item}" for item in _pending_checkbox_tasks(path))
        else:
            raise ValueError("PLAN_FORMAT_UNSUPPORTED: " + relative)
    return pending


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
    inputs = {"requirement_sha256": sha256_file(repo / work["requirement"]["path"]),
            "code_snapshot": code_snapshot(repo, {work["requirement"]["path"]}),
            "rules_snapshot": rules_snapshot(repo, cfg),
            "verification_config_sha256": verification_snapshot(cfg),
            "assessment_revision": (work.get("path_assessment") or {}).get("revision")}
    binding = (work.get("sdd") or {}).get("spec_binding")
    if binding:
        inputs["rules_snapshot"] = binding_digest({"rules": inputs["rules_snapshot"], "spec_binding": binding})
    record = (work.get("sdd") or {}).get("acceptance") or {}
    if record.get("declaration") and (work.get("path_assessment") or {}).get("profile") != "light":
        sources = source_hashes(repo, record["declaration"], provider_output_dirs(work["sdd"]["provider"]))
        inputs["code_snapshot"] = code_snapshot(repo, {work["requirement"]["path"]} | sdd_acceptance_outputs(repo, work))
        inputs["rules_snapshot"] = acceptance_digest({"rules": inputs["rules_snapshot"],
                                                      "declaration": record["declaration_sha256"], "sources": sources})
    return inputs


def completion_status(repo: Path, work: dict[str, Any] | None) -> dict[str, Any]:
    if not work:
        return {"ready": False, "reason": "no work item"}
    if work.get("status") != "active":
        return {"ready": False, "reason": f"work status is {work.get('status')}"}
    if work.get("blockers"):
        return {"ready": False, "reason": "active blocker exists"}
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
    current = verification_inputs(repo, work, cfg)
    code_sha = current["code_snapshot"]
    rules_sha = current["rules_snapshot"]
    verification_cfg_sha = current["verification_config_sha256"]
    anchor = work.get("acceptance") or {}
    if anchor.get("version") != 2 or anchor.get("config_sha256") != verification_cfg_sha:
        return {"ready": False, "reason": "verification standard is missing or changed",
                "next": f"Review the standard, then run `cao reopen --work {work['id']} --reanchor --reason \"<why>\"`."}
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
    return {
        "ready": fresh,
        "reason": None if fresh else "verification is missing or stale",
        "gates": gates,
        "sdd_acceptance": sdd_state,
        "current_code_snapshot": code_sha,
        "current_rules_snapshot": rules_sha,
        "current_verification_config_sha256": verification_cfg_sha,
    }


def resume_payload(repo: Path, work: dict[str, Any], session_id: str | None = None, host: str | None = None) -> dict[str, Any]:
    c = completion_status(repo, work)
    handoff = latest_handoff(repo, work["id"]) or {}
    return {
        "work_item": work_summary(work),
        "requirement": work.get("requirement"),
        "execution": work.get("execution"),
        "path_assessment": work.get("path_assessment"),
        "path_assessment_state": path_assessment_state(repo, work),
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
        return f'Resume the existing Work before product writes with `{command} resume --work {work["id"]}`; do not create a replacement requirement.'
    return (
        "Create an active Work before product code, configuration, tests or verification scaffolding. "
        "Honor bootstrap's SDD authorization/install instructions first. "
        f"Inspect `{command} list` and resume existing suspended/todo work instead of repeating intake. "
        f'For an inline requirement run `{command} requirement next-id --title "<short title>"`, '
        "fill the returned numbered file with the user's confirmed requirement, "
        f"then run `{command} start --requirement <returned-path>`. "
        "If a requirement file already exists, use it directly; do not invent another requirement. "
        "Verification need not pass or be configured before start. After start, rerun bootstrap "
        "for verification setup and follow `next` for path assessment and the native SDD workflow. "
        "CAO commands, numbered requirement documents and native SDD output writes remain allowed; "
        "direct edits to CAO-derived state remain denied."
    )


def render_context(repo: Path, work: dict[str, Any] | None, *, session_id: str | None = None, host: str | None = None) -> str:
    inspection = setup_state(config(repo))
    if not work or work.get("status") in {"todo", "suspended", "completed"}:
        return "# Coding Agent Orchestrator Work Intake\n" + work_intake_instruction(work)
    if not inspection["ready"]:
        # Preserve continuity without calculating completion from invalid declarations.
        return "\n".join([
            "# Coding Agent Orchestrator Verification Setup",
            setup_instruction(cao_invocation()["command"]),
            json.dumps({"verification_setup": inspection, "work": verification_setup_context(work),
                        "session_id": session_id, "host": host}, ensure_ascii=False),
        ])
    p = resume_payload(repo, work, session_id=session_id, host=host)
    e = p["execution"] or {}
    lines = [
        "# Coding Agent Orchestrator Resume Context",
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
        json.dumps(e.get("next_action") or action("none", "No next action recorded."), ensure_ascii=False),
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
    kind: str = "primary",
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
        "next_action": work.get("execution", {}).get("next_action"),
        "path_assessment": work.get("path_assessment"),
        "summary": work.get("resume", {}).get("summary"),
        "completed": work.get("resume", {}).get("completed", []),
        "pending": work.get("resume", {}).get("pending", []),
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
    if work and work.get("status") in {"active", "suspended"} and reconcile_native_sdd_state(repo, work):
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
            "options": sdd_options(status),
            "next": action("human", "HARD STOP: no SDD is installed and no provider is authorized. Ask the user to explicitly choose OpenSpec, BMad Method, or GitHub Spec Kit and wait for their answer. Do not infer a choice from recommendations, popularity, existing global CLIs, Agent preference, or task type. Do not run any provider install/select command before the user answers. After an explicit answer only, run `cao sdd install --provider <chosen-provider> --yes`. The installer must detect the active host from the current Agent runtime and install only that host's integration. If detection is unavailable or ambiguous, stop and request `--host` (or a provider-specific explicit integration/tools option); never use an all-hosts default. If the interaction channel cannot ask the user, stop with SDD_SELECTION_REQUIRED. Do not start or modify production code before installation completes."),
        }
        next_out["project_instructions"] = project_instructions
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
            "options": sdd_options(status),
            "next": action("human", "HARD STOP: multiple SDDs are installed and no provider is authorized. Ask the user to explicitly choose one and wait for their answer. Do not infer a choice from recommendations, task type, or Agent preference. Only after the user answers, run `cao sdd select --provider <chosen-provider>` and bootstrap again. If unable to ask, stop with SDD_SELECTION_REQUIRED."),
            "language_configuration": language_configuration,
        }
        next_out["project_instructions"] = project_instructions
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    inspection = setup_state(cfg)
    if not inspection["ready"] and work:
        next_out = {
            "ok": True, "mode": "verification_setup",
            "work_item": work_summary(work) if work else None,
            "sdd": sdd_status(repo), "context": verification_setup_context(work),
            "next": action("agent", setup_instruction(cao_invocation()["command"])),
        }
    elif work:
        next_out = {
            "ok": True,
            "mode": "resume",
            "work_item": work_summary(work),
            "sdd": sdd_status(repo),
            "context": resume_payload(repo, work),
            "next": None,
        }
        execution = work.get("execution") or {}
        configured = execution.get("next_action")
        completion = completion_status(repo, work)
        if work.get("status") in {"todo", "suspended"}:
            next_out["next"] = action("agent", f"Resume this work item with `cao resume --work {work['id']}`.")
        elif work.get("status") == "completed":
            next_out["next"] = action("none", "Work item is completed.")
        elif work.get("blockers"):
            next_out["next"] = action("human", "Resolve the active blocker before continuing.")
        elif not path_assessment_state(repo, work)["ready"]:
            path_state = path_assessment_state(repo, work)
            if path_state["reason"] == "major requirement ambiguity requires clarification":
                next_out["next"] = action("human", "Clarify the material requirement ambiguity, then rerun `cao path assess` without `--risk-flag major-ambiguity`.")
            elif path_state["reason"] == "requirement changed since work started":
                next_out["next"] = action("human", completion_next_instruction(repo, work, {"ready": False, "reason": path_state["reason"]}))
            else:
                next_out["next"] = action("agent", path_assessment_instruction(work))
        elif (work.get("verification") or {}).get("status") == "failed":
            next_out["next"] = action("agent", "Fix the verification failure, then run `cao verify`.")
        elif completion["ready"]:
            next_out["next"] = action("agent", "Run `cao done` to close the work item.")
        else:
            next_out["next"] = configured or action("agent", "Continue the current work and record a checkpoint before changing requirements or switching agents.")
    else:
        next_out = {
            "ok": True,
            "mode": "select",
            "work_item": None,
            "sdd": sdd_status(repo),
            "context": None,
            "next": action("agent", work_intake_instruction()),
        }
    next_out["verification_setup"] = inspection
    if bootstrap_session:
        next_out["session"] = bootstrap_session
    if bootstrap_adapter is not None:
        next_out["host_adapter"] = bootstrap_adapter
        if not bootstrap_adapter["installed"]:
            next_out["host_adapter"]["next"] = f"cao host install --host {adapter_host}  (takes effect from the next session)"
    next_out["language_configuration"] = language_configuration
    next_out["project_instructions"] = project_instructions
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


def cmd_sdd_install(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    provider = args.provider
    if provider not in SDD_CATALOG:
        print(json.dumps({"ok": False, "error": "SDD_PROVIDER_REQUIRED", "options": sdd_options(sdd_status(repo))}, ensure_ascii=False, indent=2))
        return 2

    detected = detect_sdd(repo, provider)
    if detected["installed"]:
        select_sdd_provider(repo, provider)
        try:
            language_configuration = apply_provider_language(repo, provider)
        except (OSError, UnicodeError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": provider, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"ok": True, "status": "already_installed", "selected": provider, "sdd": detected, "language_configuration": language_configuration}, ensure_ascii=False, indent=2))
        return 0

    prereq = sdd_prerequisite_check(provider)
    if not prereq["ok"]:
        print(json.dumps({"ok": False, "error": "SDD_PREREQUISITE_FAILED", "provider": provider, "prerequisites": prereq}, ensure_ascii=False, indent=2))
        return 2

    meta = SDD_CATALOG[provider]["install"]
    external = choose_external_install_command(provider)
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
    try:
        language_configuration = apply_provider_language(repo, provider)
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": provider, "reason": str(exc), "sdd": final, "results": results}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"ok": True, "status": "installed", "selected": provider, "sdd": final, "results": results, "language_configuration": language_configuration}, ensure_ascii=False, indent=2))
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
        if allow_unverified:
            record_workflow_unverified(repo, work, provider, name, unverified_reason)
        work.setdefault("execution", {})["step"] = "native-sdd-workflow-selected"
        guidance = skill_invocation_guidance(repo, provider, entrypoint)
        work["execution"]["next_action"] = action("agent", f"Execute the selected native SDD workflow `{name}` via `{entrypoint}` now. Do not skip its native prerequisites, and do not implement the change yourself instead of running the workflow - `cao done` refuses completion until it has actually run and its artifacts are recorded.{guidance} Then record the execution with `cao sdd workflow execute --command \"<command actually run>\" --exit-code <code> --work {work['id']}`, and record the real output files with `cao sdd workflow complete --work {work['id']} --artifact <repo-relative-file>`.")
    elif args.workflow_action == "execute":
        workflow = sdd.get("workflow") or {}
        if not workflow.get("name") or not workflow.get("entrypoint"):
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_NOT_SELECTED", "next": f"Run `cao sdd workflow select --name \"<workflow name>\" --entrypoint \"<native workflow command or skill>\" --work {work['id']}` first."}, ensure_ascii=False, indent=2)); return 2
        command = str(getattr(args, "command", "") or "").strip()
        if not command:
            print(json.dumps({"ok": False, "error": "WORKFLOW_COMMAND_REQUIRED", "reason": "Record the command that was actually executed."}, ensure_ascii=False, indent=2)); return 2
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
            print(json.dumps({"ok": True, "status": "unchanged", "binding": binding}, ensure_ascii=False)); return 0
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
    print(json.dumps({"ok": True, "status": "bound", "binding": binding,
                      "next": "Run configured `cao verify`, then `cao done`."}, ensure_ascii=False)); return 0


def cmd_sdd_acceptance(args: argparse.Namespace) -> int:
    from scripts.sdd_acceptance_commands import dispatch
    return dispatch(args, sys.modules[__name__])


def cmd_sdd(args: argparse.Namespace) -> int:
    return int(args.fn(args))


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
        scripts = declared_scripts(args.verify_script or [], args.test_script or [])
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        candidate, changed = prepare_config(raw, scripts, args.replace, now())
        effective = json.loads(json.dumps(DEFAULT_CONFIG))
        deep_merge(effective, candidate)
        wid = load_index(repo).get("active_work")
        # Configuring verification must not upgrade or reanchor a Work record.
        work = _read_work(repo, wid) if wid else None
        review = bool(work and work.get("status") == "active" and
                      (work.get("acceptance") or {}).get("config_sha256") != verification_snapshot(effective))
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
    out = {"ok": True, "status": "configured" if changed else "unchanged", "config": str(path),
           "verification_setup": setup_state(effective), "acceptance_review_required": review}
    if review:
        out["next"] = action("human", f'Review changed acceptance and explicitly run `cao reopen --work {wid} --reanchor --reason "Reviewed acceptance changes"`.')
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
    active_id = index.get("active_work")
    if active_id:
        active = load_work(repo, active_id)
        if active and active.get("status") == "active":
            if not args.replace:
                print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_ACTIVE", "work_item": work_summary(active)}, ensure_ascii=False, indent=2)); return 2
            active["status"] = "suspended"
            active.setdefault("resume", {})["pending"] = list(dict.fromkeys((active.get("resume", {}).get("pending") or []) + [f"Execution re-anchored to a new work item: {Path(p).stem}"]))
            save_work(repo, active)
            create_handoff(repo, active, None, None, reason="execution re-anchored to another work item")
    wid = stable_work_id(repo, p)
    existing = load_work(repo, wid)
    is_new_work = existing is None
    if existing and existing.get("status") == "completed" and not args.reopen:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_COMPLETED", "work_item": work_summary(existing)}, ensure_ascii=False, indent=2)); return 2
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
    if _activation_denied(repo, wid):
        return 2
    work["status"] = "active"
    work["started_at"] = work.get("started_at") or now()
    changed = reset_requirement(work, requirement_evidence(repo, p))
    if (work.get("sdd") or {}).get("provider") != provider:
        work["path_assessment"] = None
        work["gate_status"] = {}
        work["verification"] = None
        for field in ("workflow", "plan_scope", "native_state", "spec_binding", "acceptance"):
            work["sdd"].pop(field, None)
        hint = work["sdd"].pop("native_ref", None)
        if hint:
            work.setdefault("resume", {})["native_ref_hint"] = hint
        if work["sdd"].get("artifacts"):
            work.setdefault("resume", {})["previous_sdd_artifacts"] = work["sdd"]["artifacts"]
            work["sdd"]["artifacts"] = []
    if is_new_work or changed:
        cfg = config(repo)
        anchor_acceptance(work, verification_scripts(cfg), verification_timeout(cfg))
    if not isinstance(work.get("path_assessment"), dict):
        work.setdefault("execution", {})["step"] = "path-assessment"
        work.setdefault("execution", {})["next_action"] = action("agent", path_assessment_instruction(work))
    if args.title:
        work["title"] = args.title
    work["sdd"]["provider"] = provider
    if args.sdd_ref:
        work["sdd"]["native_ref"] = args.sdd_ref
    if is_new_work:
        queue_event(work, {"event": "task.created", "work_item_id": wid, "source": "cao",
                           "native_ref": work.get("sdd", {}).get("native_ref"),
                           "occurred_at": work.get("created_at") or now()})
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
        out = {"ok": True, "action": "ASSESS_PATH", "work_item": work_summary(work), "next_action": work.get("execution", {}).get("next_action"), "sdd": native}
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
        work = load_json(p, None)
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
    print(json.dumps({"ok": True, "work_item": work_summary(work), "path_assessment": assessment, "next_action": execution["next_action"]}, ensure_ascii=False, indent=2))
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
    if not work:
        out = {"ok": True, "action": action("agent", "Select a requirement and run `cao start --requirement <path>`."), "work_item": None}
        print(json.dumps(out, ensure_ascii=False, indent=2)); return 0
    if reconcile_native_sdd_state(repo, work):
        work = load_work(repo, work["id"]) or work
    if not work_sdd_ready(repo, work)[0]:
        provider = ((work.get("sdd") or {}).get("provider") or "auto")
        out = {"ok": True, "action": action("human", f"Install the work item's SDD `{provider}` before continuing: `cao sdd install --provider {provider} --yes`."), "work_item": work_summary(work)}
    elif work.get("status") in {"todo", "suspended"}:
        out = {"ok": True, "action": action("agent", "Resume this work item with `cao resume --work %s`." % work["id"]), "work_item": work_summary(work)}
    elif work.get("status") == "completed":
        out = {"ok": True, "action": action("none", "Work item is completed."), "work_item": work_summary(work)}
    elif work.get("blockers"):
        out = {"ok": True, "action": action("human", "Resolve the active blocker before continuing."), "work_item": work_summary(work)}
    elif not path_assessment_state(repo, work)["ready"]:
        path_state = path_assessment_state(repo, work)
        if path_state["reason"] == "major requirement ambiguity requires clarification":
            nxt = action("human", "Clarify the material requirement ambiguity, then rerun `cao path assess` without `--risk-flag major-ambiguity`.")
        elif path_state["reason"] == "requirement changed since work started":
            nxt = action("human", completion_next_instruction(repo, work, {"ready": False, "reason": path_state["reason"]}))
        else:
            nxt = action("agent", path_assessment_instruction(work))
        out = {"ok": True, "action": nxt, "work_item": work_summary(work)}
    else:
        execution = work.get("execution") or {}
        configured = execution.get("next_action")
        c = completion_status(repo, work)
        if (work.get("verification") or {}).get("status") == "failed":
            nxt = action("agent", "Fix the verification failure, then run `cao verify`.")
        elif c["ready"]:
            nxt = action("agent", "Run `cao done` to close the work item.")
        else:
            nxt = configured or action("agent", "Continue implementation and update progress with `cao checkpoint`.")
        out = {"ok": True, "action": nxt, "work_item": work_summary(work)}
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_checkpoint(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    if args.step:
        work.setdefault("execution", {})["step"] = args.step
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
    required = [r for r in results if r["required"]]
    if not required:
        return "passed"
    statuses = {r["status"] for r in required}
    if "error" in statuses:
        return "error"
    if "blocked" in statuses:
        return "blocked"
    if "failed" in statuses:
        return "failed"
    return "passed"


@locked_command
def _prepare_verification(args: argparse.Namespace):
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2

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
        # Compare content, not seconds: legacy evidence needs an explicit anchor too.
        anchor = work.get("acceptance") or {}
        if anchor.get("version") != 2 or anchor.get("config_sha256") != verification_snapshot(cfg):
            print(json.dumps({
                "ok": False,
                "error": "VERIFICATION_STANDARD_CHANGED",
                "reason": "The configured verification standard is not the Work Item's accepted standard.",
                "next": f"Review the standard, then run `cao reopen --work {work['id']} --reanchor --reason \"<why>\"`; re-earn gates and run `cao verify`.",
            }, ensure_ascii=False, indent=2)); return 2
        required_gates = ((work.get("path_assessment") or {}).get("required_gates")) or []
        if "tests" in required_gates and not any(item["required"] and "tests" in item["gates"] for item in scripts):
            print(json.dumps({
                "ok": False, "error": "VERIFICATION_GATE_UNBOUND",
                "reason": "The tests gate needs a required verification script explicitly declaring gates: [tests].",
                "next": "Use `cao verification configure --test-script \"<real test command>\" --replace`, then `cao reopen --reanchor --reason \"<why>\"` and rerun `cao verify`.",
            }, ensure_ascii=False, indent=2)); return 2

    req = repo / work["requirement"]["path"]
    if not req.exists():
        print(json.dumps({"ok": False, "error": "REQUIREMENT_FILE_MISSING"}, ensure_ascii=False, indent=2)); return 2
    return repo, work["id"], scripts, source, verification_inputs(repo, work, cfg), verification_timeout(cfg)


def commit_verification(repo, wid, inputs, scripts, results, source, started):
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if work is None:
            raise RuntimeError("WORK_ITEM_NOT_FOUND")
        current = verification_inputs(repo, work, config(repo))
        changed = current != inputs
        status = overall_verification_status(results)
        verification = {"version": 2, "source": source, "at": started, **inputs,
                        "scripts": scripts, "results": results,
                        "status": "stale" if changed else status, "run_status": status}
        work["verification"] = verification
        if not changed:
            work["execution"]["step"] = "verification"
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
    repo, wid, scripts, source, inputs, timeout = prepared
    started = now()
    results: list[dict[str, Any]] = []
    for item in scripts:
        command = item["command"]
        command_started = now()
        result = run_script(command, repo, timeout=timeout)
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

    verification = commit_verification(repo, wid, inputs, scripts, results, source, started)
    status = verification["status"]
    with repo_transaction(repo):
        work = load_work(repo, wid)
        next_action = work["execution"]["next_action"]
    cao_exit = {"passed": 0, "failed": 1, "blocked": 2, "error": 3, "stale": 2}[status]
    print(json.dumps({"ok": status == "passed", "verification": verification, "next": next_action}, ensure_ascii=False, indent=2)); return cao_exit


def sdd_transition(repo: Path, target: str) -> dict[str, Any] | None:
    return run_sdd_command(repo, "transition", [target])


def completion_next_instruction(repo: Path, work: dict[str, Any] | None, completion: dict[str, Any]) -> str:
    """Return a concrete, executable next step for a blocked completion."""
    reason = str(completion.get("reason") or "")
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
    if reanchored:
        p = repo / work["requirement"]["path"]
        if not p.is_file():
            print(json.dumps({"ok": False, "error": "REQUIREMENT_FILE_MISSING"}, ensure_ascii=False)); return 2
        reset_requirement(work, requirement_evidence(repo, p))
        cfg = config(repo)
        anchor_acceptance(work, verification_scripts(cfg), verification_timeout(cfg))
        work["started_at"] = now()
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
        "event": "task.reopened",
        "work_item_id": work["id"],
        "source": "cao",
        "to_state": "active",
        "occurred_at": now(),
        "reason": args.reason or "not specified",
        "reanchored": reanchored,
    })
    save_work(repo, work)
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
        c = completion_status(repo, work)
        if not c["ready"]:
            return _completion_blocked(repo, work, c)
    transition = sdd_transition(repo, "done")
    if transition is not None and transition["exit_code"] != 0:
        print(json.dumps({"ok": False, "error": "SDD_TRANSITION_FAILED", "transition": transition}, ensure_ascii=False, indent=2)); return 1
    # Native transitions can change both state and freshness. Judge again before closing.
    with repo_transaction(repo):
        work = load_work(repo, wid)
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


def cmd_path(args: argparse.Namespace) -> int:
    if args.path_command == "assess":
        return cmd_path_assess(args)
    if args.path_command == "show":
        return cmd_path_show(args)
    raise ValueError(f"unsupported path command: {args.path_command}")


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


def write_targets_protected_state(repo: Path, raw: dict[str, Any]) -> list[str]:
    """Protect both the lexical state link and its resolved destination."""
    repo = repo.resolve()
    state, requirements = repo / ORCH, repo / REQUIREMENT_DIR
    hits = []
    for target in tool_write_targets(raw):
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
                "`cao decision add --text ...`, `cao path assess ...`, `cao sdd workflow <select|execute|complete>`, "
                "`cao verify`, `cao done`.")
    if "config.json" in joined:
        return "Project config is written by `cao init`, `cao language set`, and provider install commands."
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
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def tool_write_targets(raw: dict[str, Any]) -> list[str]:
    """Best-effort file targets of a mutating tool call, at any nesting depth."""
    tool = _event_key(raw, "tool_name", "toolname", "tool", "name").lower()
    if not tool:
        tool = next(iter(_collect_values_for_keys(raw, TOOL_NAME_KEYS)), "").lower()
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments") or raw
    targets = _collect_path_strings(raw)
    patches = _collect_values_for_keys(raw, {"patch", "patch_text", "input"})
    if tool in {"apply_patch", "patch"}:
        patches.extend(_collect_values_for_keys(raw, {"command", "cmd"}))
    if isinstance(payload, str):
        if tool in SHELL_TOOLS:
            patches = []
        elif tool in {"apply_patch", "patch"}:
            patches.append(payload)
        else:
            targets.append(payload)
    for text in patches:
        targets.extend(patch_targets(text))
    if tool in SHELL_TOOLS:
        commands = _collect_values_for_keys(raw, {"command", "cmd"})
        if isinstance(payload, str):
            commands.append(payload)
        for command in commands:
            try:
                segments = _shell_segments(command)
            except ValueError:
                continue  # Unparseable commands get a risk reminder, not a sandbox guarantee.
            cwd = Path(".")
            for tokens in segments:
                if tokens[0] == "cd" and len(tokens) == 2 and not any(char in tokens[1] for char in "$`"):
                    directory = Path(tokens[1])
                    cwd = directory if directory.is_absolute() else cwd / directory
                elif not _readonly_tokens(tokens):
                    for token in shell_write_operands(tokens):
                        path = Path(token)
                        targets.append(str(path if path.is_absolute() else cwd / path))
    return list(dict.fromkeys(target for target in targets if target))


def shell_write_operands(tokens: list[str]) -> list[str]:
    """Keep read inputs and command data out of native-output exemptions."""
    redirects = {">", ">>", "&>", ">|", "<>"}
    outputs = [tokens[i + 1] for i, token in enumerate(tokens[:-1]) if token in redirects]
    if tokens[0] in {"echo", "printf", "cat", "head", "tail", "ls", "grep", "rg"}:
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
    if tokens[0] == "cp":
        target_dir = next((tokens[i + 1] for i, token in enumerate(tokens[:-1])
                           if token in {"-t", "--target-directory"}), None)
        target_dir = target_dir or next((token.split("=", 1)[1] for token in tokens
                                        if token.startswith("--target-directory=")), None)
        return outputs + ([target_dir] if target_dir else operands[-1:])
    if tokens[0] == "sed":
        if not any(token.startswith("-i") or token.startswith("--in-place") for token in tokens[1:]):
            return outputs
        # -e expressions and -f scripts are inputs; without them the first
        # positional operand is the expression, followed by edited files.
        args = []
        skip = False
        explicit_script = False
        for token in tokens[1:]:
            if skip:
                skip = False
                continue
            if token in {"-e", "--expression", "-f", "--file"}:
                explicit_script = skip = True
            elif token.startswith(("--expression=", "--file=")) or token.startswith(("-e", "-f")):
                explicit_script = True
            elif not token.startswith("-"):
                args.append(token)
        return outputs + (args if explicit_script else args[1:])
    return outputs + operands


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
    targets = tool_write_targets(raw)
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
            if tokens[:3] == ["openspec", "new", "change"] and detect_sdd(repo, "openspec")["installed"]:
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
    """Whether a tool call changes files.

    Hosts nest tool arguments differently (`tool_input`, `input`, `params`, ...).
    Reading only the top level silently disabled the whole gate for Pi-shaped
    events: nothing looked mutating, so every write was allowed through. The
    checks below therefore look at any depth.
    """
    tool = str(raw.get("tool_name") or raw.get("tool") or raw.get("name") or "").lower()
    if not tool:
        for candidate in _collect_values_for_keys(raw, TOOL_NAME_KEYS):
            tool = candidate.lower()
            break
    if tool in MUTATING_TOOLS:
        return True
    payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments") or raw
    if _collect_values_for_keys(payload if payload is not raw else raw, MUTATION_PAYLOAD_KEYS):
        return True
    if tool in SHELL_TOOLS:
        commands = _collect_values_for_keys(payload if payload is not raw else raw, {"command", "cmd"})
        if isinstance(payload, str):
            commands.append(payload)
        return any(not readonly_shell(command) for command in commands)
    return False


def _shell_segments(command: str) -> list[list[str]]:
    # Heredoc bodies are input data, not additional mutating commands. Keep
    # subsequent commands after the terminator visible to the gate.
    lines = []
    pending: list[str] = []
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


def readonly_shell(command: str) -> bool:
    try:
        return all(_readonly_tokens(tokens) for tokens in _shell_segments(command))
    except ValueError:
        return False


def _readonly_tokens(tokens: list[str]) -> bool:
    operators = {">", ">>", "<", "<<", "<>", ">|", "&>", ">&", "<&", ";", "&", "|", "&&", "||", "(", ")", "$", "`"}
    if not tokens or any(token in operators for token in tokens):
        return False
    name = tokens[0].lower()
    if name in {"ls", "dir", "cat", "type", "pwd", "find", "grep", "rg", "head", "tail", "echo", "printf", "cd", "exit", "which", "file", "stat", "wc"}:
        return True
    if name == "command" and len(tokens) > 1 and tokens[1] in {"-v", "-V"}:
        return True
    if len(tokens) == 2 and tokens[1] in {"--version", "-version", "-v"} and name in {"java", "javac", "mvn", "gradle", "node", "npm", "uv", "python", "python3"}:
        return True
    if name == "git" and len(tokens) > 1 and tokens[1] in {"status", "diff", "log", "show", "branch"}:
        return True
    if name == "sed":
        return not any(token.startswith("-i") or token.startswith("--in-place") for token in tokens[1:])
    if name == "pytest" or tokens[:3] in (["python", "-m", "unittest"],):
        return True
    if tokens[:2] in (["mvn", "test"], ["gradle", "test"], ["npm", "test"]):
        return True
    # Standalone CAO commands own their state mutations; their arguments are not hand edits.
    command_tokens = tokens
    if tokens[0] == "python" or re.fullmatch(r"python3(?:\.\d+)?", tokens[0]):
        script_index = next((i for i, token in enumerate(tokens[1:], 1) if Path(token).as_posix().endswith("scripts/cao.py")), None)
        if script_index is None or script_index + 1 >= len(tokens):
            return False
        command_tokens = tokens[script_index + 1:]
    elif tokens[0] == "./cao":
        command_tokens = tokens[1:]
    elif tokens[0] == "cao":
        command_tokens = tokens[1:]
    else:
        # Templates and the Pi fallback use this package's absolute launcher.
        # A matching basename alone must not exempt an unrelated script.
        index = 1 if tokens[0] == "sh" else 0
        if len(tokens) <= index or not Path(tokens[index]).is_absolute():
            return False
        root = package_root_dir()
        launchers = {str((root / "cao").resolve())} if root else set()
        console = shutil.which("cao")
        if console:
            launchers.add(str(Path(console).resolve()))
        try:
            target = str(Path(tokens[index]).resolve())
        except (OSError, RuntimeError):
            return False
        if target not in launchers:
            return False
        command_tokens = tokens[index + 1:]
    safe_control_commands = {
        "init", "bootstrap", "sdd", "start", "path", "list", "status", "next", "resume",
        "checkpoint", "decision", "block", "unblock", "handoff", "sessions", "verify", "done",
        "context", "artifact", "host-event", "requirement", "language", "gate", "reopen", "verification",
    }
    return bool(command_tokens and command_tokens[0] in safe_control_commands)


@locked_command
def host_event(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo or os.getcwd())
    raw = json.load(sys.stdin)
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
    kind = "subagent" if event in {"subagent_start", "subagent_stop"} else "primary"
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

    session = None
    if host_session_id:
        if event in {"session_start", "subagent_start"}:
            session = register_session(repo, work, host_session_id, args.host, event=event, kind=kind, parent_session_id=parent_id)
        else:
            session = update_session_event(repo, host_session_id, args.host, event, work, kind=kind, parent_session_id=parent_id)

    if event in {"session_start", "subagent_start"}:
        context = render_context(repo, work, session_id=host_session_id, host=args.host)
        result = {"decision": "allow", "additional_context": context}
        if host_session_id:
            result["session_id"] = host_session_id
        return respond(result)

    if event == "prompt_submit":
        return respond({"decision": "allow", "additional_context": render_context(repo, work, session_id=host_session_id, host=args.host)})

    if event == "pre_tool":
        # No-Work product writes and hand edits to managed state are denied.
        result: dict[str, Any] = {"decision": "allow"}
        # CAO's own state narrow enough that it can never deadlock intake:
        # `requirements/` stays writable (the Agent authors those), and CAO
        # itself writes state through commands, not through this hook.
        protected = write_targets_protected_state(repo, raw)
        if protected and tool_is_mutating(raw):
            result = {
                "decision": "deny",
                "reason": (
                    f"Do not edit CAO-managed state by hand ({', '.join(protected[:3])}). "
                    f"{protected_state_advice(protected)}"
                ),
            }
            return respond(result)
        native_write = write_targets_native_sdd_output(repo, raw) or native_intake_shell(repo, raw)
        if tool_is_mutating(raw) and not native_write:
            if not work or work.get("status") != "active":
                advice_work = work
                if not advice_work and session and session.get("work_item_id"):
                    advice_work = load_work(repo, session["work_item_id"])
                return respond({"decision": "deny", "reason": "CAO: no active Work; product writes are blocked. " + work_intake_instruction(advice_work)})
            else:
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
        if work and tool_is_mutating(raw):
            if (work.get("verification") or {}).get("status") == "passed":
                work.setdefault("resume", {})["summary"] = "Code changed after the last verification; verification is now stale."
            save_work(repo, work)
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
        completion = completion_status(repo, work)
        result = {"decision": "allow"}
        if work and work.get("status") == "active":
            result["additional_context"] = (
                f"CAO completion reminder: {completion['reason']}. Next: {completion_next_instruction(repo, work, completion)}"
                if not completion["ready"] else "CAO acceptance is ready; close the work explicitly with `cao done`."
            )
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
    acceptance = sdd_sub.add_parser("acceptance", help="Independent SDD final acceptance for full paths")
    acceptance_sub = acceptance.add_subparsers(dest="acceptance_action", required=True)
    for name in ("configure", "show", "run", "begin", "submit"):
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
    sp = sdd_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_sdd_list)
    sp = sdd_sub.add_parser("select"); sp.add_argument("--repo", default="."); sp.add_argument("--provider", required=True, choices=sorted(SDD_CATALOG)); sp.set_defaults(fn=cmd_sdd_select)
    sp = sdd_sub.add_parser("install"); sp.add_argument("--repo", default="."); sp.add_argument("--provider", choices=sorted(SDD_CATALOG)); sp.add_argument("--tools", default=None, help="OpenSpec init tool ids, or all/none"); sp.add_argument("--host", choices=["claude-code", "codex", "pi"], default=None, help="Current coding-agent host; used by Spec Kit to select its integration"); sp.add_argument("--integration", default=None, help="Spec Kit integration key, e.g. claude, codex, pi, copilot"); sp.add_argument("--integration-options", default=None, help="Spec Kit integration options passed through as one value"); sp.add_argument("--yes", action="store_true", help="Confirm installation of external/project software"); sp.set_defaults(fn=cmd_sdd_install)
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
    sp.add_argument("--plan-item", action="append", help="Shared plan path#story-id; repeat for every associated story")
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
    sp = verification_sub.add_parser("configure", help="Declare required project scripts without installing or executing anything")
    sp.add_argument("--repo", default="."); sp.add_argument("--test-script", action="append"); sp.add_argument("--verify-script", action="append")
    sp.add_argument("--replace", action="store_true", help="Explicitly replace different existing verification declarations")
    sp.set_defaults(fn=cmd_verification_configure)
    sp = sub.add_parser("verify"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--script", action="append", help="One-off verification script command; repeat to run multiple scripts"); sp.add_argument("--command", action="append", help=argparse.SUPPRESS); sp.set_defaults(fn=cmd_verify)
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
        return int(args.fn(args))
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
