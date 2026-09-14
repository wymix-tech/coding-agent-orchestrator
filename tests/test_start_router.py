import argparse
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import coding_orchestrator as cli
import execution_state_manager as sm
import project_bootstrap
import requirement_discovery
import start_router


def git_init(repo: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)


class StartRouterTests(unittest.TestCase):
    def test_exact_start_intents_only(self):
        for text in ["开始", "开始！", "继续", "接着做", "start", "continue", "resume"]:
            self.assertTrue(start_router.is_start_intent(text), text)
        for text in ["开始实现登录功能", "start the API", "继续修改 UserService", ""]:
            self.assertFalse(start_router.is_start_intent(text), text)

    def test_empty_bootstrapped_project_requests_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="none")
            result = start_router.resolve(repo, ensure_bootstrap=False)
            self.assertEqual("READY_FOR_INTAKE", result["status"])
            self.assertEqual("REQUEST_REQUIREMENT", result["route"])
            self.assertFalse((repo / ".orchestrator/execution-state.yaml").exists())

    def test_skill_docs_are_not_requirement_sources(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            p = repo / ".agents/skills/coding-agent-orchestrator/requirements.md"
            p.parent.mkdir(parents=True)
            p.write_text("# Requirements\nThe system must do things.\nAcceptance criteria: pass.\n", encoding="utf-8")
            result = requirement_discovery.discover(repo, "generic")
            self.assertEqual("NONE", result["status"])

    def test_normal_readme_does_not_auto_become_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "README.md").write_text("# Demo\n\nInstall with pip. Run tests with pytest.\n", encoding="utf-8")
            project_bootstrap.initialize(repo, host="none")
            result = start_router.resolve(repo, ensure_bootstrap=False)
            self.assertEqual("REQUEST_REQUIREMENT", result["route"])

    def test_explicit_requirement_document_is_single_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "requirements.md").write_text(
                "# Requirements\n\nThe service must expose a user lookup API.\nAcceptance criteria: unknown users return 404.\n",
                encoding="utf-8",
            )
            project_bootstrap.initialize(repo, host="none")
            result = start_router.resolve(repo, ensure_bootstrap=False)
            self.assertEqual("AUTO_INTAKE_CANDIDATE", result["route"])
            self.assertEqual("requirements.md", result["candidate"]["path"])

    def test_multiple_requirement_documents_require_selection(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            for name, topic in [("requirements.md", "users"), ("spec.md", "payments")]:
                (repo / name).write_text(
                    f"# Requirements\n\nThe system must support {topic}.\nAcceptance criteria: the {topic} behavior is testable.\n",
                    encoding="utf-8",
                )
            project_bootstrap.initialize(repo, host="none")
            result = start_router.resolve(repo, ensure_bootstrap=False)
            self.assertEqual("ACTION_REQUIRED", result["status"])
            self.assertEqual("SELECT_REQUIREMENT", result["route"])
            self.assertEqual(2, result["requirements"]["count"])

    def test_active_work_item_resumes_without_requirement_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="none")
            state_path = repo / ".orchestrator/execution-state.yaml"
            state = sm.create_state("W1", "Existing work", "FAST", provider="generic", authority_mode="orchestrator")
            sm.initialize(state_path, state, "test")
            result = start_router.resolve(repo, ensure_bootstrap=False)
            self.assertEqual("READY_FOR_WORK", result["status"])
            self.assertEqual("RESUME_CURRENT_WORK", result["route"])

    def test_cli_start_auto_intakes_unique_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "requirements.md").write_text(
                "# Requirements\n\nThe service must expose a user lookup API.\nAcceptance criteria: unknown users return 404.\n",
                encoding="utf-8",
            )
            project_bootstrap.initialize(repo, host="none")
            args = cli.build_parser().parse_args(["--repo", str(repo), "start"]); args.repo = repo
            fake_result = {"status": "NEEDS_EVIDENCE", "work_item": {"id": "W"}}
            with mock.patch.object(cli, "cmd_intake", return_value=(1, fake_result, "Intake: NEEDS_EVIDENCE")) as intake:
                code, result, text = cli.cmd_start(args)
            self.assertEqual(1, code)
            self.assertEqual("AUTO_INTAKE", result["route"])
            self.assertIn("requirements.md", text)
            self.assertEqual(1, intake.call_count)

    def test_cli_start_does_not_create_fake_work_item_without_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="none")
            args = cli.build_parser().parse_args(["--repo", str(repo), "start"]); args.repo = repo
            code, result, _ = cli.cmd_start(args)
            self.assertEqual(0, code)
            self.assertEqual("REQUEST_REQUIREMENT", result["route"])
            self.assertFalse((repo / ".orchestrator/execution-state.yaml").exists())


if __name__ == "__main__":
    unittest.main()
