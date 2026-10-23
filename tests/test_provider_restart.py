"""Installation-to-host-loading boundaries, using real CAO commands and hooks."""
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from scripts import cao
from repair_helpers import HOST_KEYS
from scripts.provider_restart import require_restart, restart_state


class ProviderRestartTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = pathlib.Path(temp.name)
        host = patch.dict(cao.os.environ, {key: "" for key in HOST_KEYS})
        host.start()
        self.addCleanup(host.stop)
        code, out = self.cli("init", "--host", "pi", "--language", "zh-CN")
        self.assertEqual(code, 0, out)

    def cli(self, *args, event=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("sys.stdin", io.StringIO(json.dumps(event))):
            code = cao.main([*args, "--repo", str(self.repo)])
        if args and args[0] == "init" and code == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(self.repo)
        return code, json.loads(output.getvalue())

    def hook(self, event, host="pi", **data):
        code, out = self.cli("host-event", "--host", host,
                             event={"event": event, "session_id": "original-session", "reason": "startup", **data})
        self.assertEqual(code, 0, out)
        return out

    def install(self, provider="bmad", host="pi", upgrade=False, fail=False):
        def project_install(*args, **kwargs):
            if not fail:
                root = self.repo / {"bmad": "_bmad", "openspec": "openspec", "speckit": ".specify"}[provider]
                root.mkdir(exist_ok=True)
                if provider == "bmad":
                    (root / "config.user.toml").write_text(
                        '[core]\nuser_name = "Dev"\ncommunication_language = "English"\n'
                        'document_output_language = "English"\n', encoding="utf-8")
            return cao.subprocess.CompletedProcess(args[0], 1 if fail else 0)

        # Only external provider execution is faked. State, configuration,
        # detection, command routing and lifecycle hooks are the real core.
        with patch("scripts.cao_providers.sdd_prerequisite_check", return_value={"ok": True}), \
             patch("scripts.cao_providers.choose_external_install_command", return_value=None), \
             patch("scripts.cao_providers.repo_root", return_value=self.repo), \
             patch.object(cao.subprocess, "run", side_effect=project_install):
            return self.cli("sdd", "install", "--provider", provider, "--host", host, "--yes",
                            *(["--upgrade"] if upgrade else []))

    def test_new_install_stops_bootstrap_hooks_and_work_commands_until_host_start(self):
        self.hook("session_start")  # A start before installation is insufficient.
        code, out = self.install()
        self.assertEqual(code, 0, out)
        self.assertTrue(out["restart_required"])
        self.assertEqual(out["next"]["type"], "human")
        self.assertEqual(out["next"]["reason"], "HOST_RESTART_REQUIRED")
        self.assertEqual(out["language_configuration"]["language_values"],
                         {"communication_language": "Chinese", "document_output_language": "Chinese"})
        for _ in range(2):
            code, out = self.cli("bootstrap", "--host", "pi")
            self.assertEqual(code, 0, out)
            self.assertEqual(out["mode"], "host_restart_required")
            self.assertEqual(out["guidance"]["stage"], "startup")
        code, out = self.cli("next")
        self.assertEqual(out["action"]["reason"], "HOST_RESTART_REQUIRED")
        code, out = self.cli("start", "--requirement", "not-created.md")
        self.assertEqual((code, out["error"]), (2, "HOST_RESTART_REQUIRED"))
        code, out = self.cli("verify", "--command", "echo must-not-run")
        self.assertEqual((code, out["error"]), (2, "HOST_RESTART_REQUIRED"))
        for event in ("post_tool", "prompt_submit", "turn_end", "before_settle"):
            out = self.hook(event)
            self.assertEqual(out["decision"], "allow")
            self.assertIn("HOST_RESTART_REQUIRED", out["additional_context"])
            self.assertNotIn("CAO completion reminder", out["additional_context"])
        for target in ("product.py", "_bmad-output/SPEC.md", ".orchestrator/requirements/new.md"):
            out = self.hook("pre_tool", toolName="write", input={"path": target, "content": "new"})
            self.assertEqual(out["decision"], "deny", out)
        out = self.hook("pre_tool", toolName="bash", input={"command": "cao host install --host pi"})
        self.assertEqual(out["decision"], "allow", out)
        self.hook("subagent_start", subagent_id="child")
        self.hook("session_start", session_id="new-id", kind="subagent", subagent_id="child")
        self.hook("session_start", session_id="")
        self.hook("session_start", host="codex")
        for reason in ("new", "resume", "fork", ""):
            self.hook("session_start", reason=reason, session_id="another-id")
        self.assertFalse(restart_state(self.repo, "pi")["ready"])
        self.hook("session_start")  # Restart/resume can reuse the conversation ID.
        code, out = self.cli("bootstrap", "--host", "pi")
        self.assertEqual(out["mode"], "select", out)
        self.assertTrue(restart_state(self.repo)["ready"])

    def test_unchanged_install_does_not_create_or_clear_boundary_and_upgrade_requires_one(self):
        code, out = self.install()
        code, out = self.install()
        self.assertEqual(out["status"], "already_installed")
        self.assertTrue(out["restart_required"])
        self.hook("session_start")
        code, out = self.install()
        self.assertEqual(out["status"], "already_installed")
        self.assertFalse(out["restart_required"])
        code, out = self.install(upgrade=True)
        self.assertEqual(out["status"], "installed")
        self.assertTrue(out["restart_required"])

    def test_all_providers_and_hosts_require_restart_but_failed_installs_do_not(self):
        for provider, host in (("openspec", "claude-code"), ("speckit", "codex")):
            code, out = self.install(provider, host, fail=True)
            self.assertEqual(code, 1, out)
            self.assertTrue(restart_state(self.repo)["ready"])
            code, out = self.install(provider, host)
            self.assertEqual(code, 0, out)
            self.assertEqual(out["restart"]["pending"], [{"provider": provider, "host": host}])
            self.hook("session_start", host=host)
            self.assertTrue(restart_state(self.repo)["ready"])

    def test_language_failure_retains_restart_boundary_and_repair_remains_available(self):
        with patch("scripts.cao_providers.apply_provider_language", side_effect=ValueError("broken language config")):
            code, out = self.install()
        self.assertEqual(code, 1, out)
        self.assertEqual(out["error"], "SDD_LANGUAGE_CONFIGURATION_FAILED")
        self.assertTrue(out["restart_required"])
        code, out = self.cli("language", "apply", "--provider", "bmad")
        self.assertEqual(code, 0, out)
        self.assertFalse(restart_state(self.repo)["ready"])

    def test_existing_work_and_requirement_survive_restart(self):
        (self.repo / "_bmad").mkdir()
        req = self.repo / ".orchestrator/requirements/001-login.md"
        req.parent.mkdir(exist_ok=True)
        req.write_text("# Login\nImplement confirmed login scope.\n", encoding="utf-8")
        code, out = self.cli("start", "--requirement", str(req))
        self.assertEqual(code, 0, out)
        wid = out["work_item"]["id"]
        work_path = self.repo / f".orchestrator/work/{wid}.json"
        before = json.loads(work_path.read_text())
        require_restart(self.repo, "bmad", ["pi"])
        code, out = self.cli("bootstrap", "--host", "pi")
        self.assertEqual(out["next"], out["work_item"]["next_action"])
        code, out = self.cli("next")
        self.assertEqual(out["action"], out["work_item"]["next_action"])
        code, out = self.cli("verify")
        self.assertEqual((code, out["error"]), (2, "HOST_RESTART_REQUIRED"))
        self.hook("turn_end")
        self.hook("session_start")
        code, out = self.cli("bootstrap", "--host", "pi")
        self.assertEqual(out["work_item"]["id"], wid)
        after = json.loads(work_path.read_text())
        for key in ("requirement", "path_assessment", "verification", "acceptance", "status"):
            self.assertEqual(before.get(key), after.get(key), key)


if __name__ == "__main__":
    unittest.main()
