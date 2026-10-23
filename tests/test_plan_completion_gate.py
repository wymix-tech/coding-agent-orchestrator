"""A Work Item cannot be closed while the provider's own plan is unfinished.

A single Work Item spans whatever the provider planned: BMAD epics/stories,
OpenSpec change tasks, Spec Kit tasks. demo11 closed a thirteen-story
requirement after finishing story 1-1, because nothing compared the Work Item
against the provider's plan.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import configure_verification, complete_acceptance

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"

HOST_ENV_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)
NO_HOST_ENV = {key: "" for key in HOST_ENV_KEYS}


def run(repo, *args):
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True,
                       capture_output=True, env=dict(os.environ, **NO_HOST_ENV), timeout=60)
    try:
        if args and args[0] == "init" and p.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(repo)
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        if args and args[0] == "init" and p.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(repo)
        return p.returncode, {"raw": p.stdout, "stderr": p.stderr}


def _write(rel_path: str, content: str):
    """Return a plan-writer for `ready_to_close` that writes a provider file."""
    def writer(repo: pathlib.Path) -> None:
        target = repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return writer


# `cao sdd workflow select` requires the entrypoint to name the provider, so
# each provider gets a plausible one.
PROVIDER_ENTRYPOINTS = {
    "bmad": "bmad-build",
    "openspec": "openspec-apply",
    "speckit": "speckit-implement",
}


def ready_to_close(repo: pathlib.Path, provider: str, artifact: str, profile: str = "standard", plan=None, expect_verify_blocked=False) -> str:
    """Drive a Work Item to the point where only `cao done` remains."""
    entrypoint = PROVIDER_ENTRYPOINTS[provider]
    run(repo, "init", "--host", "pi")
    configure_verification(repo)
    run(repo, "sdd", "select", "--provider", provider)
    (repo / pathlib.Path(artifact).parent).mkdir(parents=True, exist_ok=True)
    pathlib.Path(repo / artifact).write_text("# artifact\n", encoding="utf-8")
    if provider == "bmad":
        (repo / "_bmad").mkdir(exist_ok=True)
    if provider == "openspec":
        (repo / "openspec").mkdir(exist_ok=True)
    if provider == "speckit":
        (repo / ".specify").mkdir(exist_ok=True)
    rc, out = run(repo, "requirement", "next-id", "--title", "login")
    req = repo / out["path"]
    # Plan completion is independent of domain-specific security capabilities.
    req.write_text("# Fixture\n\nGenerate a bounded local status report.\n", encoding="utf-8")
    rc, out = run(repo, "start", "--requirement", out["path"])
    wid = out["work_item"]["id"]
    run(repo, "path", "assess", "--profile", profile, "--reason", "auth system", "--work", wid)
    run(repo, "sdd", "workflow", "select", "--name", entrypoint, "--entrypoint", entrypoint, "--work", wid)
    run(repo, "sdd", "workflow", "execute", "--command", entrypoint, "--exit-code", "0", "--work", wid)
    if plan is not None:
        plan(repo)
    plan_args = []
    directory = (repo / artifact).parent
    for name in ("tasks.md", "sprint-status.yaml"):
        source = directory / name
        if source.is_file():
            relative = source.relative_to(repo).as_posix()
            plan_args += ["--plan", relative]
            if name == "sprint-status.yaml":
                # This fixture owns every numeric story in the written requirement plan.
                for line in source.read_text(encoding="utf-8").splitlines():
                    story = line.strip().split(":", 1)[0]
                    if story and story[0].isdigit():
                        plan_args += ["--plan-item", relative + "#" + story]
    rc, out = run(repo, "sdd", "workflow", "complete", "--artifact", artifact, "--work", wid,
                  *(plan_args or ["--no-plan"]))
    assert rc == 0, f"workflow completion failed: {out}"
    rc, out = run(repo, "verify", "--work", wid)
    if expect_verify_blocked:
        assert rc == 2 and out["error"] == ("BMAD_PLAN_NOT_DONE" if provider == "bmad" else "SDD_PLAN_NOT_DONE"), out
        return wid
    assert rc == 0, f"verify failed: {out}"
    for gate in ("scope_review", "implementation_review"):
        rc, out = run(repo, "gate", "satisfy", "--gate", gate, "--evidence", "done", "--work", wid)
        assert rc == 0, f"gate {gate} failed: {out}"
    return wid


class BmadPlan(unittest.TestCase):
    def test_done_refuses_while_stories_are_still_backlog(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = ready_to_close(repo, "bmad", "_bmad-output/implementation_artifacts/story.md", expect_verify_blocked=True, plan=_write(
                "_bmad-output/implementation_artifacts/sprint-status.yaml", 
                "generated: 10-10-2026\n"
                "development_status:\n"
                "  epic-1: backlog\n"
                "  1-1-项目初始化与依赖配置: backlog\n"
                "  1-2-配置属性绑定: backlog\n"
                "  2-1-登录接口: in-progress\n"
                "  2-3-jwt-token: review\n"
                "  epic-1-retrospective: optional\n"))
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual((rc, out["error"]), (2, "BMAD_PLAN_NOT_DONE"))
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "COMPLETION_BLOCKED")
            self.assertEqual(out["reason"], "BMAD_PLAN_NOT_DONE")
            # Epics are aggregates of their stories and retrospectives are
            # optional, so only the four real stories count.
            self.assertEqual(len(out["pending_plan_items"]), 4, out["pending_plan_items"])

    def test_done_accepts_once_every_story_is_done(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = ready_to_close(repo, "bmad", "_bmad-output/implementation_artifacts/story.md", plan=_write(
                "_bmad-output/implementation_artifacts/sprint-status.yaml", 
                "development_status:\n"
                "  epic-1: done\n"
                "  1-1-项目初始化: done\n"
                "  epic-1-retrospective: optional\n"))
            complete_acceptance(repo, run, wid)
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 0, out)


class OpenSpecPlan(unittest.TestCase):
    def test_done_refuses_while_tasks_are_unchecked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = ready_to_close(repo, "openspec", "openspec/changes/login/proposal.md", expect_verify_blocked=True, plan=_write(
                "openspec/changes/login/tasks.md", 
                "# Tasks\n\n- [x] 1.1 created project\n- [ ] 1.2 configure yml\n- [ ] 2.1 jwt service\n"))
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual((rc, out["error"]), (2, "SDD_PLAN_NOT_DONE"))
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(len(out["pending_plan_items"]), 2, out["pending_plan_items"])

    def test_done_accepts_once_all_tasks_are_checked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = ready_to_close(repo, "openspec", "openspec/changes/login/proposal.md", plan=_write(
                "openspec/changes/login/tasks.md",
                "# Tasks\n\n- [x] 1.1 created project\n- [x] 1.2 configure yml\n"))
            complete_acceptance(repo, run, wid)
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 0, out)

    def test_archived_changes_are_not_counted_as_pending(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            def plan(r: pathlib.Path) -> None:
                archive = r / "openspec" / "changes" / "archive"
                archive.mkdir(parents=True, exist_ok=True)
                (archive / "tasks.md").write_text("- [ ] long archived task\n", encoding="utf-8")
                (r / "openspec" / "changes" / "login" / "tasks.md").write_text("- [x] done\n", encoding="utf-8")

            wid = ready_to_close(repo, "openspec", "openspec/changes/login/proposal.md", plan=plan)
            complete_acceptance(repo, run, wid)
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 0, out)


class SpecKitPlan(unittest.TestCase):
    def test_done_refuses_while_spec_kit_tasks_are_unchecked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            wid = ready_to_close(repo, "speckit", "specs/login/plan.md", expect_verify_blocked=True, plan=_write(
                "specs/login/tasks.md", "- [ ] 1.1 create project\n- [ ] 1.2 add jwt\n"))
            rc, out = run(repo, "verify", "--work", wid)
            self.assertEqual((rc, out["error"]), (2, "SDD_PLAN_NOT_DONE"))
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["reason"], "SDD_PLAN_NOT_DONE")


class NoFalsePositives(unittest.TestCase):
    def test_a_tasks_md_outside_the_provider_directory_is_ignored(self):
        """Product code may contain its own TODO files; those are not the plan."""
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            def plan(r: pathlib.Path) -> None:
                (r / "docs").mkdir(exist_ok=True)
                (r / "docs" / "tasks.md").write_text("- [ ] unrelated docs todo\n", encoding="utf-8")
                (r / "openspec" / "changes" / "login" / "tasks.md").write_text("- [x] done\n", encoding="utf-8")

            wid = ready_to_close(repo, "openspec", "openspec/changes/login/proposal.md", plan=plan)
            complete_acceptance(repo, run, wid)
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 0, out)

    def test_a_provider_without_a_plan_file_is_not_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            # No plan file at all: the provider simply has nothing to check.
            wid = ready_to_close(repo, "openspec", "openspec/changes/login/proposal.md")
            self.assertFalse(list((repo / "openspec").rglob("tasks.md")), "test needs a provider with no plan file")
            complete_acceptance(repo, run, wid)
            rc, out = run(repo, "done", "--work", wid)
            self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
