import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliEntryTests(unittest.TestCase):
    def test_project_declares_cao_console_script(self):
        project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(
            project_text,
            r'(?ms)^\[project\.scripts\]\s+cao\s*=\s*\"scripts\.cao:main\"',
        )

    def test_project_instruction_templates_are_packaged_as_data_files(self):
        project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('[tool.setuptools.data-files]', project_text)
        self.assertTrue((ROOT / "templates" / "AGENTS.md").is_file())
        self.assertTrue((ROOT / "templates" / "CLAUDE.md").is_file())
        self.assertFalse((ROOT / "AGENTS.md").exists())
        self.assertFalse((ROOT / "CLAUDE.md").exists())
        self.assertFalse((ROOT / ".agents").exists())

    def test_core_is_under_scripts_directory(self):
        self.assertTrue((ROOT / "scripts" / "cao.py").is_file())
        self.assertFalse((ROOT / "cao.py").exists())
        self.assertTrue((ROOT / "scripts" / "__init__.py").is_file())

    def test_source_launcher_prints_help(self):
        result = subprocess.run(
            [str(ROOT / "cao"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Coding Agent Orchestrator", result.stdout)

    def test_dashboard_command_is_registered(self):
        result = subprocess.run(
            [str(ROOT / "cao"), "dashboard", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: cao dashboard", result.stdout)
        self.assertIn("--allow-remote", result.stdout)

    def test_direct_python_script_dashboard_import_works_outside_repo_cwd(self):
        # Running scripts/cao.py sets sys.path[0] to `scripts/`, not the project
        # root. Dashboard must still import its sibling package modules.
        missing_repo = ROOT / "this-repo-does-not-exist-for-dashboard-test"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "cao.py"), "dashboard", "--repo", str(missing_repo)],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertIn("Repository directory does not exist", result.stdout)

    def test_repair_modules_and_parameters_are_available(self):
        from scripts import completion_policy, path_guard, state_store, verification_runner, windows_supervisor
        self.assertTrue(callable(verification_runner.run_script))
        self.assertTrue(callable(state_store.repo_transaction))
        self.assertTrue(callable(completion_policy.acceptance_hash))
        self.assertTrue(callable(path_guard.repo_file))
        self.assertTrue(callable(windows_supervisor.run_windows_command))
        for command, flags in ((["init"], ["--test-script"]),
                               (["sdd", "workflow", "complete"], ["--plan", "--plan-item", "--no-plan"]),
                               (["reopen"], ["--reanchor"])):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/cao.py"), *command, "--help"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            for flag in flags:
                self.assertIn(flag, result.stdout)

    def test_user_docs_describe_reminders_and_bound_completion(self):
        stale_claims = ("Host Hooks deny code mutation", "Missing or stale assessments block mutating Host events",
                        "CAO blocks code-mutating", "main-session stop events create a Handoff and then close that Session",
                        "Full repository code snapshots are not recalculated on every browser refresh")
        for name in ("README.md", "DESIGN.md", "SKILL.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(document=name):
                for claim in stale_claims:
                    self.assertNotIn(claim, text)
                if name == "SKILL.md":
                    text += "\n" + "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "references").glob("*.md"))
                for term in ("--test-script", "--plan-item", "--no-plan", "--reanchor", "timeout_seconds"):
                    self.assertIn(term, text)

    def test_pi_metadata_only_claims_verified_host_api(self):
        import json
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["peerDependencies"]["@earendil-works/pi-coding-agent"], "1.1.0")
        self.assertEqual(package["engines"]["node"], ">=22.18.0")

    def test_hooks_use_shared_runner(self):
        claude = (ROOT / "hosts/claude-code/settings.fragment.json").read_text()
        codex = (ROOT / "hosts/codex/hooks.json").read_text()
        pi = (ROOT / "hosts/pi/coding-orchestrator.ts").read_text()
        self.assertIn("hooks/host-event.sh", claude)
        self.assertIn("hooks/host-event.sh", codex)
        # Invocation behavior is exercised by test_pi_adapter.mjs, not a synchronous spelling.
        self.assertIn('invoke("cao", args', pi)
        self.assertIn('invoke("sh", [path.join(ROOT, "cao"), ...args]', pi)
        self.assertIn('["host-event", "--repo", cwd, "--host", "pi"]', pi)


if __name__ == "__main__":
    unittest.main()
