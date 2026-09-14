import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import project_activation
import project_bootstrap
import coding_orchestrator as cli


def git_init(repo: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)


class ProjectActivationTests(unittest.TestCase):
    def test_init_creates_managed_agents_activation_block(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            result = project_bootstrap.initialize(repo, host="none")
            agents = repo / "AGENTS.md"
            self.assertTrue(agents.exists())
            text = agents.read_text(encoding="utf-8")
            self.assertIn(project_activation.START, text)
            self.assertIn(project_activation.END, text)
            self.assertIn("开始", text)
            self.assertIn("continue", text)
            self.assertIn("Lazy Reference Loading Contract", text)
            self.assertIn("continue the same user request", text)
            self.assertIn("--repo . start", text)
            self.assertIn("activation", result)

    def test_activation_merge_preserves_existing_project_instructions(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            agents = repo / "AGENTS.md"
            agents.write_text("# Existing\n\nKeep this project rule.\n", encoding="utf-8")
            project_bootstrap.initialize(repo, host="none")
            text = agents.read_text(encoding="utf-8")
            self.assertIn("Keep this project rule.", text)
            self.assertEqual(1, text.count(project_activation.START))

    def test_activation_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="none")
            first = (repo / "AGENTS.md").read_text(encoding="utf-8")
            project_bootstrap.initialize(repo, host="none")
            second = (repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(first, second)
            self.assertEqual(1, second.count(project_activation.START))

    def test_claude_install_creates_claude_activation_stub(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="claude-code")
            claude = repo / "CLAUDE.md"
            self.assertTrue(project_activation.has_managed_block(claude))
            self.assertTrue((repo / ".claude" / "settings.json").exists())

    def test_no_activation_flag_path_can_disable_stubs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="none", activation=False)
            self.assertFalse((repo / "AGENTS.md").exists())

    def test_doctor_checks_activation_stub(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="none")
            checks = {x["name"]: x for x in cli._doctor(repo)["checks"]}
            self.assertEqual("PASS", checks["activation:AGENTS"]["status"])
            (repo / "AGENTS.md").unlink()
            checks = {x["name"]: x for x in cli._doctor(repo)["checks"]}
            self.assertEqual("FAIL", checks["activation:AGENTS"]["status"])

    def test_skill_metadata_targets_start_resume_continue(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: coding-agent-orchestrator", text)
        for phrase in ["start/resume/continue", "开始", "继续", "resume"]:
            self.assertIn(phrase, text)

    def test_readme_language_layout(self):
        english = ROOT / "README.md"
        chinese = ROOT / "README-zh.md"
        self.assertTrue(english.exists())
        self.assertTrue(chinese.exists())
        self.assertFalse((ROOT / "README.en.md").exists())
        self.assertIn("Project Positioning", english.read_text(encoding="utf-8"))
        self.assertIn("项目定位", chinese.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
