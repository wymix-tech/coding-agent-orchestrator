import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


HOST_ENV_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)
# With no host detectable, every template is installed as a fallback. That is the
# state this test needs to exercise both install and skip in one repo.
NO_HOST_ENV = {key: "" for key in HOST_ENV_KEYS}


def run(repo, *args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True, timeout=20, env=env)
    try:
        out = json.loads(p.stdout)
    except Exception:
        out = {"raw": p.stdout}
    return p.returncode, out


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_initializes_without_creating_work(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
            rc, out = run(repo, "bootstrap", "--host", "codex")
            self.assertEqual(rc, 0)
            self.assertEqual(out["mode"], "sdd_select")
            self.assertIsNone(out["work_item"])
            self.assertTrue((repo / ".orchestrator/config.json").exists())
            self.assertIn("sdd install", out["next"]["instruction"])
            # Only the file this host reads. Asserting both made the test depend
            # on whatever host this machine happens to report.
            self.assertEqual(out["project_instructions"]["installed"], ["AGENTS.md"])
            self.assertTrue((repo / "AGENTS.md").is_file())
            self.assertFalse((repo / "CLAUDE.md").exists())

    def test_bootstrap_installs_only_missing_templates_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
            custom_agents = "# Existing team guidance\nKeep this content.\n"
            (repo / "AGENTS.md").write_text(custom_agents, encoding="utf-8")

            rc, first = run(repo, "bootstrap", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0)
            self.assertEqual((repo / "AGENTS.md").read_text(encoding="utf-8"), custom_agents)
            self.assertTrue((repo / "CLAUDE.md").is_file())
            self.assertEqual(first["project_instructions"]["installed"], ["CLAUDE.md"])
            self.assertEqual(first["project_instructions"]["skipped_existing"], ["AGENTS.md"])

            (repo / "CLAUDE.md").write_text("# User customized Claude instructions\n", encoding="utf-8")
            rc, second = run(repo, "bootstrap", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0)
            self.assertEqual((repo / "CLAUDE.md").read_text(encoding="utf-8"), "# User customized Claude instructions\n")
            self.assertEqual(set(second["project_instructions"]["skipped_existing"]), {"AGENTS.md", "CLAUDE.md"})

    def test_templates_live_in_templates_directory_not_package_root(self):
        self.assertTrue((ROOT / "templates" / "AGENTS.md").is_file())
        self.assertTrue((ROOT / "templates" / "CLAUDE.md").is_file())
        self.assertFalse((ROOT / "AGENTS.md").exists())
        self.assertFalse((ROOT / "CLAUDE.md").exists())
        self.assertFalse((ROOT / ".agents").exists())

    def test_bootstrap_prefers_existing_work_and_does_not_restart_intake(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
            (repo / "requirements").mkdir()
            (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            (repo / "openspec").mkdir()
            (repo / "openspec/config.yaml").write_text("context: test\n", encoding="utf-8")
            run(repo, "init")
            run(repo, "start", "--requirement", "requirements/login.md")
            run(repo, "path", "assess", "--profile", "light", "--reason", "Small local change")
            run(repo, "checkpoint", "--step", "implement-login", "--next", "run tests", "--summary", "API implemented")
            rc, out = run(repo, "bootstrap")
            self.assertEqual(rc, 0)
            self.assertEqual(out["mode"], "resume")
            self.assertEqual(out["context"]["execution"]["step"], "implement-login")
            self.assertEqual(out["next"]["instruction"], "run tests")
            self.assertEqual(out["next"]["instruction"], "run tests")


if __name__ == "__main__":
    unittest.main()
