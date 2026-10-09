import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


def run(repo, *args, input_text=None):
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True, input=input_text, timeout=20)
    try:
        out = json.loads(p.stdout)
    except Exception:
        out = {"raw": p.stdout}
    return p.returncode, out


class SddBootstrapTests(unittest.TestCase):
    def init_repo(self):
        td = tempfile.TemporaryDirectory()
        repo = pathlib.Path(td.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
        rc, out = run(repo, "init")
        self.assertEqual(rc, 0)
        return td, repo

    def test_bootstrap_requires_sdd_before_requirement_selection(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir()
            (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            rc, out = run(repo, "bootstrap")
            self.assertEqual(rc, 0)
            self.assertEqual(out["mode"], "sdd_select")
            self.assertEqual(out["sdd"]["status"], "not_installed")
            self.assertEqual({x["provider"] for x in out["options"]}, {"openspec", "bmad", "speckit"})
            self.assertIn("sdd install", out["next"]["instruction"])

    def test_start_is_blocked_when_sdd_is_missing(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir()
            (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            rc, out = run(repo, "start", "--requirement", "requirements/login.md")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "SDD_SELECTION_REQUIRED")

    def test_existing_openspec_project_is_detected_and_selected(self):
        td, repo = self.init_repo()
        with td:
            (repo / "openspec").mkdir()
            (repo / "openspec/config.yaml").write_text("context: demo\n", encoding="utf-8")
            (repo / "requirements").mkdir()
            (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            rc, out = run(repo, "bootstrap")
            self.assertEqual(rc, 0)
            self.assertEqual(out["mode"], "select")
            self.assertEqual(out["sdd"]["selected"], "openspec")
            config = json.loads((repo / ".orchestrator/config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["sdd"]["provider"], "openspec")

    def test_sdd_install_without_yes_returns_plan(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "install", "--provider", "openspec")
            # This may fail the runtime prerequisite check on a stripped test host.
            if rc == 0:
                self.assertEqual(out["status"], "confirmation_required")
                self.assertEqual(out["provider"], "openspec")
                self.assertTrue(out["install_plan"])
            else:
                self.assertEqual(out["error"], "SDD_PREREQUISITE_FAILED")

    def test_existing_speckit_project_is_detected_and_inferred(self):
        td, repo = self.init_repo()
        with td:
            (repo / ".specify").mkdir()
            (repo / ".specify/integration.json").write_text('{"default_integration":"codex"}', encoding="utf-8")
            (repo / "specs").mkdir()
            (repo / "specs/001-login").mkdir()
            req = repo / "specs/001-login/spec.md"
            req.write_text("Add login", encoding="utf-8")
            rc, out = run(repo, "bootstrap")
            self.assertEqual(rc, 0)
            self.assertEqual(out["mode"], "select")
            self.assertEqual(out["sdd"]["selected"], "speckit")
            rc, out = run(repo, "start", "--requirement", "specs/001-login/spec.md")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work_item"]["sdd"]["provider"], "speckit")

    def test_speckit_install_plan_uses_current_host(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "install", "--provider", "speckit", "--host", "claude-code")
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "confirmation_required")
            commands = [x["command"] for x in out["install_plan"]]
            self.assertIn("uv tool install specify-cli", commands)
            self.assertIn("specify init --here --force --non-interactive --integration claude --ignore-agent-tools", commands)

    def test_speckit_install_plan_accepts_explicit_integration(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "install", "--provider", "speckit", "--integration", "copilot")
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "confirmation_required")
            commands = [x["command"] for x in out["install_plan"]]
            self.assertIn("specify init --here --force --non-interactive --integration copilot --ignore-agent-tools", commands)

    def test_host_blocks_mutation_when_work_sdd_is_removed(self):
        td, repo = self.init_repo()
        with td:
            (repo / "openspec").mkdir()
            (repo / "openspec/config.yaml").write_text("context: test\n", encoding="utf-8")
            (repo / "requirements").mkdir()
            (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            run(repo, "init")
            rc, _, = run(repo, "start", "--requirement", "requirements/login.md")
            self.assertEqual(rc, 0)
            import shutil
            shutil.rmtree(repo / "openspec")
            payload = {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": "app.py", "content": "x"}}
            rc, out = run(repo, "host-event", "--host", "claude-code", input_text=json.dumps(payload))
            self.assertEqual(rc, 0)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_sdd_select_requires_project_installation(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "select", "--provider", "bmad")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "SDD_NOT_INSTALLED")
            self.assertIn("sdd install", out["next"])


if __name__ == "__main__":
    unittest.main()
