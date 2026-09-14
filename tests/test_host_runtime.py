import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap_guard
import host_runtime
import install_host_adapter
import project_bootstrap


def git_init(repo: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)


class HostRuntimeTests(unittest.TestCase):
    def test_pi_runtime_environment_selects_pi(self):
        result = host_runtime.detect_current_host(env={"PI_CODING_AGENT": "true"}, process_chain=[])
        self.assertEqual("pi", result["host"])
        self.assertFalse(result["fallback"])

    def test_codex_thread_environment_selects_codex(self):
        result = host_runtime.detect_current_host(env={"CODEX_THREAD_ID": "019d-test"}, process_chain=[])
        self.assertEqual("codex", result["host"])
        self.assertEqual("env:CODEX_THREAD_ID", result["source"])

    def test_nearest_process_ancestry_wins_over_inherited_parent_environment(self):
        result = host_runtime.detect_current_host(
            env={"CODEX_THREAD_ID": "inherited", "PI_CODING_AGENT": "true"},
            process_chain=[{"command": "/usr/local/bin/pi --mode rpc"}, {"command": "/usr/local/bin/codex"}],
        )
        self.assertEqual("pi", result["host"])
        self.assertEqual("process_ancestry", result["source"])

    def test_unknown_runtime_falls_back_to_claude(self):
        result = host_runtime.detect_current_host(env={}, process_chain=[{"command": "python3 tool.py"}])
        self.assertEqual("claude-code", result["host"])
        self.assertTrue(result["fallback"])

    def test_shell_script_text_does_not_create_false_host_match(self):
        result = host_runtime.detect_current_host(
            env={"PI_CODING_AGENT": "true"},
            process_chain=[{"command": "bash -lc 'echo claude-code; echo codex; run later'"}],
        )
        self.assertEqual("pi", result["host"])
        self.assertEqual("env:PI_CODING_AGENT", result["source"])

    def test_explicit_host_overrides_runtime(self):
        result = host_runtime.select_hosts(
            "codex", env={"PI_CODING_AGENT": "true"}, process_chain=[{"command": "/usr/local/bin/pi"}]
        )
        self.assertEqual(["codex"], result["hosts"])
        self.assertEqual("explicit", result["source"])

    def test_bootstrapped_project_reconciles_new_current_host_without_reinitializing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            project_bootstrap.initialize(repo, host="claude-code")
            config_before = (repo / ".orchestrator/config.yaml").read_text(encoding="utf-8")
            self.assertTrue(install_host_adapter.is_installed(repo, "claude-code"))
            self.assertFalse(install_host_adapter.is_installed(repo, "pi"))

            selection = {
                "requested": "auto", "host": "pi", "hosts": ["pi"],
                "source": "env:PI_CODING_AGENT", "confidence": "high", "fallback": False,
                "evidence": ["PI_CODING_AGENT=true"],
            }
            with mock.patch("host_runtime.select_hosts", return_value=selection):
                result = bootstrap_guard.ensure(repo, host="auto")

            self.assertFalse(result["performed"], "project bootstrap itself must not rerun")
            self.assertTrue(result["host_reconcile"]["performed"], "current host adapter should be added")
            self.assertTrue(install_host_adapter.is_installed(repo, "pi"))
            self.assertTrue((repo / ".orchestrator/config.yaml").exists())
            self.assertFalse((repo / ".orchestrator/execution-state.yaml").exists())
            self.assertNotEqual("", config_before)


if __name__ == "__main__":
    unittest.main()
