"""Host adapter provisioning tests.

Host hooks (SessionStart / PreToolUse / Stop) are process-startup integrations:
Claude Code reads its settings file and Pi loads its extensions when the host
process starts. They must therefore be wired during project provisioning
(`cao init`), not from `cao bootstrap`, which already runs inside a session that
has missed its own SessionStart.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"

spec = __import__("importlib.util").util.spec_from_file_location("cao", CAO)
cao = __import__("importlib.util").util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cao)

# Empty every variable `detect_current_host` inspects so tests can simulate a
# plain shell / CI environment where no host is visible.
HOST_ENV_KEYS = (
    "CAO_HOST", "CLAUDE_CODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_HOME", "PI_SESSION_ID", "PI_CODING_AGENT",
)
NO_HOST_ENV = {key: "" for key in HOST_ENV_KEYS}


def run(repo, *args, env_extra=None):
    env = __import__("os").environ.copy()
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True, env=env, timeout=60)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        out = {"raw": p.stdout}
    if args and args[0] == "init" and p.returncode == 0:
        from tests.helpers import provision_fixture_background
        provision_fixture_background(repo)
    return p.returncode, out, p.stderr


def hook_commands(settings: dict, event: str) -> list[str]:
    return [
        h.get("command", "")
        for entry in (settings.get("hooks", {}).get(event) or [])
        for h in (entry.get("hooks") or [])
    ]


class ClaudeCodeAdapterTests(unittest.TestCase):
    def test_init_wires_the_adapter_into_project_local_settings(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            rc, out, _ = run(repo, "init", "--host", "claude-code")
            self.assertEqual(rc, 0)
            self.assertEqual(out["host_adapters"]["installed"], ["claude-code"])
            target = repo / ".claude" / "settings.local.json"
            self.assertTrue(target.is_file())
            settings = json.loads(target.read_text(encoding="utf-8"))
            for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
                self.assertTrue(hook_commands(settings, event), f"missing hook for {event}")

    def test_placeholders_are_resolved_to_the_real_install_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", "--host", "claude-code")
            settings = json.loads((repo / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
            commands = hook_commands(settings, "SessionStart")
            self.assertTrue(commands)
            self.assertNotIn("/PATH/TO/", commands[0])
            self.assertTrue(commands[0].endswith("hooks/host-event.sh --host claude-code"))
            # `${CLAUDE_PROJECT_DIR}` is expanded by the host at runtime, so CAO must leave it alone.
            self.assertIn("${CLAUDE_PROJECT_DIR}", commands[0])

    def test_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", "--host", "claude-code")
            rc, out, _ = run(repo, "init", "--host", "claude-code", "--force")
            self.assertEqual(rc, 0)
            self.assertEqual(out["host_adapters"]["already_present"], ["claude-code"])
            settings = json.loads((repo / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
            self.assertEqual(len(hook_commands(settings, "Stop")), 1)

    def test_existing_host_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            target_dir = repo / ".claude"
            target_dir.mkdir(parents=True)
            (target_dir / "settings.local.json").write_text(json.dumps({
                "permissions": {"allow": ["Bash(git:*)"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo custom"}]}]},
            }), encoding="utf-8")
            run(repo, "init", "--host", "claude-code")
            settings = json.loads((target_dir / "settings.local.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["permissions"], {"allow": ["Bash(git:*)"]})
            self.assertIn("echo custom", hook_commands(settings, "Stop"))
            self.assertEqual(len(hook_commands(settings, "Stop")), 2)


class CodexAdapterTests(unittest.TestCase):
    def test_init_wires_codex_hooks(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            rc, out, _ = run(repo, "init", "--host", "codex", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0)
            self.assertEqual(out["host_adapters"]["installed"], ["codex"])
            target = repo / ".codex" / "hooks.json"
            self.assertTrue(target.is_file())
            settings = json.loads(target.read_text(encoding="utf-8"))
            commands = hook_commands(settings, "PreToolUse")
            self.assertTrue(commands)
            self.assertNotIn("${PLUGIN_ROOT}", commands[0])


class HostRequiredTests(unittest.TestCase):
    def test_init_reports_the_hook_gap_without_failing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            rc, out, _ = run(repo, "init", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "initialized")
            self.assertEqual(out["host_adapters"]["status"], "host_required")
            self.assertIn("warning", out)
            self.assertIn("cao host install --host", out["host_adapters"]["next"])

    def test_host_install_demands_an_explicit_host_when_undetectable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            rc, out, _ = run(repo, "host", "install", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "HOST_REQUIRED")

    def test_host_install_backfills_an_existing_project(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", env_extra=NO_HOST_ENV)
            rc, out, _ = run(repo, "host", "install", "--host", "claude-code", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0)
            self.assertEqual(out["host_adapters"]["installed"], ["claude-code"])
            self.assertTrue((repo / ".claude" / "settings.local.json").is_file())

    def test_repeated_init_reports_pending_adapters(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", env_extra=NO_HOST_ENV)
            rc, out, _ = run(repo, "init", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "already_initialized")
            self.assertIn("claude-code", out["host_adapters"]["not_installed"])


class HostStatusTests(unittest.TestCase):
    def test_status_reflects_installed_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            run(repo, "init", "--host", "claude-code", env_extra=NO_HOST_ENV)
            rc, out, _ = run(repo, "host", "status", env_extra=NO_HOST_ENV)
            self.assertEqual(rc, 0)
            by_host = {entry["host"]: entry for entry in out["hosts"]}
            self.assertTrue(by_host["claude-code"]["installed"])
            self.assertFalse(by_host["codex"]["installed"])

    def test_pi_status_reads_the_project_package_list(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            pi_dir = repo / ".pi"
            pi_dir.mkdir()
            (pi_dir / "settings.json").write_text(json.dumps({"packages": ["npm:pi-subagents"]}), encoding="utf-8")
            self.assertFalse(cao.host_adapter_status(repo, "pi")["installed"])
            (pi_dir / "settings.json").write_text(json.dumps({"packages": ["npm:pi-subagents", "/opt/coding-agent-orchestrator"]}), encoding="utf-8")
            self.assertTrue(cao.host_adapter_status(repo, "pi")["installed"])


class MergePurityTests(unittest.TestCase):
    def test_merge_does_not_mutate_the_existing_config(self):
        existing = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo old"}]}]}}
        incoming = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo new"}]}]}}
        merged, changed = cao.merge_hook_config(existing, incoming)
        self.assertTrue(changed)
        self.assertEqual(hook_commands(existing, "Stop"), ["echo old"])
        self.assertEqual(sorted(hook_commands(merged, "Stop")), ["echo new", "echo old"])

    def test_merge_is_idempotent_for_identical_commands(self):
        existing = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo same"}]}]}}
        incoming = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo same"}]}]}}
        _, changed = cao.merge_hook_config(existing, incoming)
        self.assertFalse(changed)

    def test_merge_keeps_matchers_separate(self):
        incoming = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Write", "hooks": [{"type": "command", "command": "cao a"}]},
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "cao b"}]},
                ]
            }
        }
        merged, _ = cao.merge_hook_config({}, incoming)
        entries = merged["hooks"]["PreToolUse"]
        self.assertEqual({e.get("matcher") for e in entries}, {"Write", "Bash"})


if __name__ == "__main__":
    unittest.main()
