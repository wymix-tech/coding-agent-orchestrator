import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap_guard
import enforcement_kernel as ek


def git_init(repo: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)


class BootstrapGuardTests(unittest.TestCase):
    def test_missing_config_safe_auto_bootstraps_once(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            first = bootstrap_guard.ensure(repo, host="none")
            self.assertTrue(first["performed"])
            self.assertEqual("BOOTSTRAPPED", first["status"])
            self.assertTrue((repo / ".orchestrator/config.yaml").exists())
            second = bootstrap_guard.ensure(repo, host="none")
            self.assertFalse(second["performed"])
            self.assertEqual("BOOTSTRAPPED", second["status"])

    def test_invalid_existing_config_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            cfg = repo / ".orchestrator/config.yaml"; cfg.parent.mkdir(parents=True)
            original = "this: [is: invalid\\n"
            cfg.write_text(original, encoding="utf-8")
            result = bootstrap_guard.ensure(repo, host="none")
            self.assertFalse(result["performed"])
            self.assertEqual("ACTION_REQUIRED", result["status"])
            self.assertEqual(original, cfg.read_text(encoding="utf-8"))

    def test_ambiguous_sdd_returns_action_required_without_fake_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "openspec").mkdir()
            (repo / "_bmad").mkdir(); (repo / "_bmad/sprint-status.yaml").write_text("status: active\\n", encoding="utf-8")
            result = bootstrap_guard.ensure(repo, host="none")
            self.assertTrue(result["performed"])
            self.assertEqual("ACTION_REQUIRED", result["status"])
            self.assertFalse((repo / ".orchestrator/execution-state.yaml").exists())

    def test_session_start_self_bootstraps_and_injects_ready_for_intake(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            result = ek.handle(repo, "codex", "session_start", {"session_id": "s1"})
            self.assertTrue((repo / ".orchestrator/config.yaml").exists())
            self.assertFalse((repo / ".orchestrator/execution-state.yaml").exists())
            self.assertTrue(result["metadata"]["auto_bootstrap_performed"])
            self.assertIn("READY_FOR_INTAKE", result["additional_context"])

    def test_first_prompt_can_self_bootstrap_without_second_user_turn(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            result = ek.handle(repo, "codex", "prompt_submit", {"session_id": "s1", "prompt": "开始"})
            self.assertTrue(result["metadata"]["auto_bootstrap_performed"])
            self.assertIn("READY_FOR_INTAKE", result["additional_context"])
            self.assertIn("Safe Auto project bootstrap completed", result["additional_context"])


if __name__ == "__main__":
    unittest.main()
