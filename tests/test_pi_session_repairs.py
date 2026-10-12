"""Regressions from the supplied real Pi session, using the real CAO CLI."""
import os
import shlex
from unittest import mock

from repair_helpers import RepairCase, ROOT
from scripts import cao


class AbsoluteControlInvocationTests(RepairCase):
    def assert_control(self, argv):
        command = shlex.join(argv + ["path", "assess", "--profile", "standard"])
        raw = {"toolName": "bash", "input": {"command": command}}
        self.assertTrue(cao.readonly_shell(command), command)
        self.assertFalse(cao.tool_is_mutating(raw), command)
        rc, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": "pre_tool", "session_id": "native-pi", **raw,
        })
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "allow")
        self.assertNotIn("additional_context", out)

    def test_owned_absolute_launcher_is_a_control_invocation(self):
        self.assert_control([str(ROOT / "cao")])

    def test_owned_sh_fallback_is_a_control_invocation(self):
        self.assert_control(["sh", str(ROOT / "cao")])

    def test_canonical_alias_with_spaces_is_a_control_invocation(self):
        alias = self.repo / "CAO alias"
        try:
            alias.symlink_to(ROOT / "cao")
        except OSError:
            self.skipTest("host cannot create symlinks")
        self.assert_control([str(alias)])

    def test_resolved_path_console_is_a_control_invocation(self):
        binary = self.repo / ("cao.cmd" if os.name == "nt" else "cao")
        binary.write_text("# console identity fixture", encoding="utf-8")
        binary.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(self.repo) + os.pathsep + os.environ["PATH"]}):
            self.assert_control([str(binary)])

    def test_unknown_launcher_and_subcommand_are_not_exempt(self):
        fake = self.repo / "cao"
        fake.write_text("# unregistered launcher", encoding="utf-8")
        for argv in ([str(fake), "next"], ["sh", str(fake), "next"],
                     [str(ROOT / "cao"), "unknown-command"]):
            with self.subTest(argv=argv):
                self.assertFalse(cao.readonly_shell(shlex.join(argv)))
        for command in ("cao next", "./cao next"):
            self.assertTrue(cao.readonly_shell(command))

    def test_redirection_and_mixed_shell_still_protect_internal_state(self):
        launcher = shlex.quote(str(ROOT / "cao"))
        for command in (f"{launcher} next > .orchestrator/index.json",
                        f"{launcher} next && echo '{{}}' > .orchestrator/index.json"):
            with self.subTest(command=command):
                self.assertFalse(cao.readonly_shell(command))
                rc, out = self.run_cli("host-event", "--host", "pi", payload={
                    "event": "pre_tool", "session_id": "native-pi",
                    "toolName": "bash", "input": {"command": command},
                })
                self.assertEqual(rc, 0, out)
                self.assertEqual(out["decision"], "deny", out)
                self.assertIn("CAO-managed", out["reason"])

    def test_symlink_loop_remains_mutating_instead_of_crashing(self):
        alias = self.repo / "loop"
        try:
            alias.symlink_to(alias)
        except OSError:
            self.skipTest("host cannot create symlinks")
        self.assertFalse(cao.readonly_shell(shlex.join([str(alias), "next"])))


class PostToolVerificationTests(RepairCase):
    def event(self, name):
        rc, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": name, "session_id": "native-pi",
            "toolName": "write", "input": {"path": "src.py", "content": "x = 1"},
        })
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "allow")
        self.assertEqual(cao.load_session(self.repo, "native-pi", host="pi")["status"], "active")

    def test_null_verification_survives_post_tool_and_file_changed(self):
        wid = self.start()
        self.assertIsNone(self.read_work(wid)["verification"])
        for name in ("post_tool", "file_changed"):
            with self.subTest(event=name):
                self.event(name)
                self.assertIsNone(self.read_work(wid)["verification"])

    def test_missing_legacy_verification_survives_post_tool(self):
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work.pop("verification")
        cao.save_work(self.repo, work)
        self.event("post_tool")
        self.assertNotIn("verification", self.read_work(wid))

    def test_pending_verification_remains_pending(self):
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work["verification"] = {"status": "pending"}
        cao.save_work(self.repo, work)
        self.event("post_tool")
        self.assertEqual(self.read_work(wid)["verification"]["status"], "pending")

    def test_passed_verification_still_records_stale_summary(self):
        wid = self.start()
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        (self.repo / "src.py").write_text("x = 1", encoding="utf-8")
        self.event("post_tool")
        work = self.read_work(wid)
        self.assertIn("verification is now stale", work["resume"]["summary"])
        self.assertFalse(cao.bound_verification_fresh(work, cao.verification_inputs(self.repo, work, cao.config(self.repo))))
