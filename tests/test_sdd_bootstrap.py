import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from helpers import configure_verification

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


def run(repo, *args, input_text=None, env=None):
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True, input=input_text, timeout=20, env=process_env)
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
            self.assertTrue(out["selection_required"])
            self.assertEqual(out["selection_policy"], "explicit_user_choice_only")
            self.assertEqual({x["provider"] for x in out["options"]}, {"openspec", "bmad", "speckit"})
            instruction = out["next"]["instruction"]
            self.assertIn("wait for their answer", instruction)
            self.assertIn("Do not run any provider install/select command before the user answers", instruction)
            self.assertIn("If the interaction channel cannot ask the user, stop", instruction)

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
            self.assertFalse(out["verification_setup"]["ready"])
            self.assertEqual(out["sdd"]["selected"], "openspec")
            config = json.loads((repo / ".orchestrator/config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["sdd"]["provider"], "openspec")

    def test_openspec_install_refuses_all_hosts_when_host_is_unknown(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "install", "--provider", "openspec", env={"CAO_HOST": "", "CLAUDE_CODE": "", "CLAUDE_CODE_SESSION_ID": "", "CODEX_THREAD_ID": "", "CODEX_SANDBOX": "", "CODEX_HOME": "", "PI_CODING_AGENT": "", "PI_SESSION_ID": ""})
            if rc == 2 and out.get("error") == "SDD_PREREQUISITE_FAILED":
                self.skipTest("OpenSpec runtime prerequisites unavailable on this host")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "SDD_INSTALL_ARGUMENTS_REQUIRED")
            self.assertIn("Refusing --tools all", out["reason"])

    def test_openspec_install_uses_explicit_host_only(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "install", "--provider", "openspec", "--host", "claude-code")
            if rc == 2 and out.get("error") == "SDD_PREREQUISITE_FAILED":
                self.skipTest("OpenSpec runtime prerequisites unavailable on this host")
            self.assertEqual(rc, 0)
            commands = [x["command"] for x in out["install_plan"]]
            self.assertIn("openspec init --tools claude", commands)
            self.assertNotIn("openspec init --tools all", commands)

    def test_openspec_install_detects_runtime_host(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "install", "--provider", "openspec", env={"CLAUDE_CODE_SESSION_ID": "test-session", "CODEX_THREAD_ID": "", "CODEX_SANDBOX": "", "CODEX_HOME": "", "PI_CODING_AGENT": "", "PI_SESSION_ID": ""})
            if rc == 2 and out.get("error") == "SDD_PREREQUISITE_FAILED":
                self.skipTest("OpenSpec runtime prerequisites unavailable on this host")
            self.assertEqual(rc, 0)
            commands = [x["command"] for x in out["install_plan"]]
            self.assertIn("openspec init --tools claude", commands)
            self.assertNotIn("openspec init --tools all", commands)

    def test_openspec_install_respects_explicit_tools_override(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "install", "--provider", "openspec", "--tools", "claude,codex")
            if rc == 2 and out.get("error") == "SDD_PREREQUISITE_FAILED":
                self.skipTest("OpenSpec runtime prerequisites unavailable on this host")
            self.assertEqual(rc, 0)
            commands = [x["command"] for x in out["install_plan"]]
            self.assertIn("openspec init --tools claude,codex", commands)

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
            self.assertFalse(out["verification_setup"]["ready"])
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
            configure_verification(repo)
            rc, _, = run(repo, "start", "--requirement", "requirements/login.md")
            self.assertEqual(rc, 0)
            import shutil
            shutil.rmtree(repo / "openspec")
            payload = {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": "app.py", "content": "x"}}
            rc, out = run(repo, "host-event", "--host", "claude-code", input_text=json.dumps(payload))
            self.assertEqual(rc, 0)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_sdd_select_requires_project_installation(self):
        td, repo = self.init_repo()
        with td:
            rc, out = run(repo, "sdd", "select", "--provider", "bmad")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "SDD_NOT_INSTALLED")
            self.assertIn("sdd install", out["next"])


if __name__ == "__main__":
    unittest.main()
