"""Provider-owned artifacts, native Skills, and workflow entrypoint validation."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from scripts.cao_defaults import PLACEHOLDER_ENTRYPOINT_COMMANDS, SDD_CATALOG
from scripts.path_guard import provider_artifact, repo_file


def provider_output_dirs(provider: str, repo: Path | None = None) -> list[str]:
    if provider == "bmad" and repo is not None:
        from scripts.bmad_project import output_dirs
        try:
            return output_dirs(repo)
        except (ValueError, OSError, RuntimeError):
            # Health/bootstrap expose the configuration diagnostic. Consumers
            # get no ownership grants, rather than crashing or using an unsafe root.
            return []
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
    from scripts.cao_tool_policy import _shell_segments

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
            elif head in {'node', 'nodejs'}:
                tokens = tokens[1:]
                while tokens and tokens[0] in {'--enable-source-maps', '--no-warnings'}:
                    tokens = tokens[1:]
                if tokens and tokens[0].startswith('-'):
                    tokens = []  # Inline -e/-p code and unknown options are not file identities.
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
    from scripts.cao_tool_policy import _shell_segments

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
    dirs = provider_output_dirs(provider, repo)
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
