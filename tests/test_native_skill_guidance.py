"""Skill-based native workflows must tell the Agent how to actually run them.

Session evidence (demo10): BMad v6 installs its workflows as agent skills under
`.agents/skills/bmad-<name>/SKILL.md`. The Agent selected `bmad-build` correctly,
then said "BMad build 可能是一个通用的构建流程" and started writing product code
directly — because nothing told it that "executing" this workflow means invoking
the skill, and CAO's validation had accepted the entrypoint without explaining
the invocation form. These tests pin the guidance that closes that gap.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from tests.helpers import configure_verification

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"

spec = importlib.util.spec_from_file_location("cao", CAO)
cao = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cao)

HOST_ENV_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)
NO_HOST_ENV = {key: "" for key in HOST_ENV_KEYS}


def run(repo, *args, env_extra=None, stdin=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True, env=env, timeout=60, input=stdin)
    try:
        if args and args[0] == "init" and p.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(repo)
        return p.returncode, json.loads(p.stdout), p.stderr
    except json.JSONDecodeError:
        if args and args[0] == "init" and p.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(repo)
        return p.returncode, {"raw": p.stdout}, p.stderr


def bmad_project(repo: pathlib.Path) -> None:
    run(repo, "init", "--host", "pi", env_extra=NO_HOST_ENV)
    run(repo, "sdd", "select", "--provider", "bmad")
    (repo / "_bmad").mkdir(exist_ok=True)


def install_skill(repo: pathlib.Path, name: str, root: str = ".agents/skills") -> None:
    skill = repo / root / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\nDo the {name} workflow.\n", encoding="utf-8")


class SkillDiscovery(unittest.TestCase):
    """Discovery must not assume one provider's layout."""

    def test_discovers_bmad_skills_in_agent_skill_roots(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            install_skill(repo, "bmad-build")
            install_skill(repo, "bmad-build-auto")
            install_skill(repo, "coding-agent-orchestrator")  # another skill, not BMad's
            found = [s["name"] for s in cao.provider_native_skills(repo, "bmad")]
            self.assertIn("bmad-build", found)
            self.assertIn("bmad-build-auto", found)
            self.assertNotIn("coding-agent-orchestrator", found)

    def test_discovery_works_for_every_provider_and_every_skill_root(self):
        """OpenSpec and Spec Kit ship skills too; roots differ per host."""
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            install_skill(repo, "openspec-ff", root=".agents/skills")
            install_skill(repo, "speckit-plan", root=".claude/skills")
            install_skill(repo, "bmad-build", root="skills")
            cases = [
                ("openspec", ".agents/skills/openspec-ff/SKILL.md"),
                ("speckit", ".claude/skills/speckit-plan/SKILL.md"),
                ("bmad", "skills/bmad-build/SKILL.md"),
            ]
            for provider, expected in cases:
                skills = cao.provider_native_skills(repo, provider)
                self.assertEqual([s["rel_path"] for s in skills], [expected], provider)

    def test_paths_point_at_where_the_skill_really_is(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            install_skill(repo, "bmad-build", root=".claude/skills")
            guidance = cao.skill_invocation_guidance(repo, "bmad", "bmad-build")
            self.assertIn(".claude/skills/bmad-build/SKILL.md", guidance)
            self.assertNotIn(".agents/skills/bmad-build", guidance)

    def test_cli_setup_is_distinct_from_agent_workflow_guidance(self):
        """No installed skills: say how the provider actually runs workflows."""
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            openspec = cao.skill_invocation_guidance(repo, "openspec", "openspec-ff")
            speckit = cao.skill_invocation_guidance(repo, "speckit", "specify")
            self.assertIn("CLI (`openspec`)", openspec)
            self.assertIn("CLI (`specify`)", speckit)
            for provider in ("openspec", "speckit"):
                self.assertIn("agent", cao.skill_invocation_guidance(repo, provider, "x"))

    def test_an_unlisted_entrypoint_lists_the_installed_skills(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            install_skill(repo, "bmad-build")
            install_skill(repo, "bmad-build-auto")
            guidance = cao.skill_invocation_guidance(repo, "bmad", "not-installed")
            self.assertIn("`bmad-build`", guidance)
            self.assertIn("`bmad-build-auto`", guidance)
            self.assertIn("Do not substitute your own implementation", guidance)


class SelectGuidance(unittest.TestCase):
    def _reached_workflow_select(self, repo: pathlib.Path) -> tuple[int, dict]:
        rc, out, _ = run(repo, "requirement", "next-id", "--title", "login", env_extra=NO_HOST_ENV)
        path = out["path"]
        (repo / path).write_text("# Fixture\nGenerate a local status report.\n", encoding="utf-8")
        configure_verification(repo)
        rc, out, _ = run(repo, "start", "--requirement", path, env_extra=NO_HOST_ENV)
        assert rc == 0, out
        rc, out, _ = run(repo, "path", "assess", "--profile", "standard", "--reason", "clear scope", env_extra=NO_HOST_ENV)
        assert rc == 0, out
        return run(repo, "sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build", env_extra=NO_HOST_ENV)

    def test_select_tells_the_agent_to_invoke_the_skill(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            bmad_project(repo)
            install_skill(repo, "bmad-build")
            rc, out, _ = self._reached_workflow_select(repo)
            self.assertEqual(rc, 0, out)
            instruction = out["work_item"]["next_action"]["instruction"]
            self.assertIn("invoke the `bmad-build` skill", instruction)
            self.assertIn(".agents/skills/bmad-build/SKILL.md", instruction)
            self.assertIn("Do not substitute your own implementation", instruction)

    def test_select_without_installed_skills_still_records_the_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            bmad_project(repo)
            rc, out, _ = self._reached_workflow_select(repo)
            self.assertEqual(rc, 0, out)
            instruction = out["work_item"]["next_action"]["instruction"]
            self.assertNotIn("agent skill", instruction)


class PreToolReminder(SelectGuidance):
    """Installed Build requires a route decision even after a workflow run is recorded."""

    def test_missing_route_blocks_product_write(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            bmad_project(repo)
            install_skill(repo, "bmad-build")
            self._reached_workflow_select(repo)
            raw = {"event": "pre_tool", "toolName": "write", "params": {"path": "src/App.java", "content": "x"}}
            rc, out, _ = run(repo, "host-event", "--repo", ".", "--host", "pi", env_extra=NO_HOST_ENV, stdin=json.dumps(raw))
            self.assertEqual(out["decision"], "deny")
            self.assertIn("NATIVE_ROUTE_REQUIRED", out["reason"])

    def test_execution_record_does_not_replace_native_route(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            bmad_project(repo)
            install_skill(repo, "bmad-build")
            self._reached_workflow_select(repo)
            rc, out, _ = run(repo, "sdd", "workflow", "execute", "--command", "invoke skill bmad-build", "--exit-code", "0", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0, out)
            raw = {"event": "pre_tool", "toolName": "write", "params": {"path": "src/App.java", "content": "x"}}
            rc, out, _ = run(repo, "host-event", "--repo", ".", "--host", "pi", env_extra=NO_HOST_ENV, stdin=json.dumps(raw))
            self.assertEqual(out["decision"], "deny")
            self.assertNotIn("has NOT been executed", out.get("additional_context", ""))
            self.assertNotIn("verification setup", out.get("additional_context", "").lower())
