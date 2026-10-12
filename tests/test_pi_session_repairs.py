"""Regressions from the supplied real Pi session, using the real CAO CLI."""
import json
import os
import shlex
import subprocess
import sys
from unittest import mock

from repair_helpers import RepairCase, ROOT
from scripts import cao


class CompoundEntrypointTests(RepairCase):
    entry = "mkdir -p _bmad-output && echo '# Login System Specification' > _bmad-output/spec.md"

    def test_logged_compound_placeholder_does_not_advance_workflow(self):
        wid = self.start()
        before = self.read_work(wid)["sdd"].get("workflow")
        rc, out = self.run_cli("sdd", "workflow", "select", "--name", "specify", "--entrypoint", self.entry)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_WORKFLOW_ENTRYPOINT_INVALID")
        self.assertEqual(self.read_work(wid)["sdd"].get("workflow"), before)

    def test_provider_data_is_not_a_native_command(self):
        for provider, hint in (("bmad", "bmad"), ("openspec", "openspec"), ("speckit", "speckit")):
            for entry in (f"mkdir -p {hint}-output && echo spec > {hint}-output/spec.md",
                          f"mkdir -p output; printf '{hint} workflow' > output/spec.md",
                          f"cp {hint}-spec.md output.md", f"touch {hint}-spec.md",
                          f"unrelated --provider {hint}", f"echo spec | tee {hint}-spec.md",
                          f"project-local {hint} workflow/skill", f"invoke skill unrelated --provider {hint}",
                          f"python {hint}-output/helper.py", f"sh {hint}-output/spec.md",
                          f"/tmp/{hint}-project/unrelated",
                          f"env PROVIDER={hint} echo placeholder",
                          f"PROVIDER={hint} echo placeholder",
                          f"python -c \"print('{hint}')\"",
                          f"npx --package {hint} echo placeholder",
                          f"sh -c \"mkdir -p {hint}-output && echo placeholder\"",
                          f"sh -c '$NOT_A_{hint.upper()}_CLI build'", f"echo {hint}; true"):
                with self.subTest(provider=provider, entry=entry):
                    self.assertFalse(cao.validate_workflow_entrypoint(provider, "native-flow", entry)["ok"])

    def test_actual_cli_skill_and_runner_positions_remain_valid(self):
        cases = {
            "bmad": ("bmad workflow implement", "bmad-bmm-create-prd", "invoke skill bmad-build", "mkdir -p _bmad-output && bmad-build",
                     "echo preparing && bmad-build", "npx --yes bmad-method workflow implement",
                     "npx -p bmad-method bmad workflow implement", "python3 -u bmad-fixture.py",
                     "python3 -m bmad.workflow", "uv run --project app python bmad-fixture.py",
                     "uvx --from bmad-method bmad workflow implement", "env NODE_ENV=test bmad-build",
                     "sh -c 'mkdir -p _bmad-output && bmad-build'", "sh _bmad/scripts/native.sh"),
            "openspec": ("openspec apply", "npx --yes @fission-ai/openspec@latest apply",
                         "mkdir -p openspec/changes && openspec apply", "python openspec-fixture.py"),
            "speckit": ("/speckit.specify", "specify init --here", "uvx --from specify-cli specify init",
                        "python speckit-fixture.py", "sh .specify/scripts/bash/create-new-feature.sh"),
        }
        for provider, entries in cases.items():
            for entry in entries:
                with self.subTest(provider=provider, entry=entry):
                    self.assertTrue(cao.validate_workflow_entrypoint(provider, "native-flow", entry)["ok"])

    def test_declared_workflow_name_does_not_certify_placeholder(self):
        with mock.patch.dict(cao.SDD_CATALOG["bmad"], {"workflows": {"planning": ["specify"]}}):
            self.assertFalse(cao.validate_workflow_entrypoint("bmad", "specify", self.entry)["ok"])
            self.assertTrue(cao.validate_workflow_entrypoint("bmad", "specify", "bmad create-spec")["ok"])
            self.assertFalse(cao.validate_workflow_entrypoint("bmad", "unlisted", "bmad create-spec")["ok"])

    def test_setup_utility_in_provider_script_directory_is_not_a_workflow(self):
        for provider, marker in (("bmad", "_bmad"), ("openspec", "openspec"), ("speckit", ".specify")):
            for command in ("mkdir", "cp", "tee", "mkdir.exe"):
                with self.subTest(provider=provider, command=command):
                    entry = f"sh {marker}/scripts/{command} output"
                    self.assertFalse(cao.validate_workflow_entrypoint(provider, "native-flow", entry)["ok"])

    def test_malformed_and_deep_shell_do_not_certify_workflow(self):
        self.assertFalse(cao.validate_workflow_entrypoint("bmad", "build", "bmad-build '")["ok"])
        entry = "bmad-build"
        for _ in range(10):
            entry = "sh -c " + shlex.quote(entry)
        self.assertFalse(cao.validate_workflow_entrypoint("bmad", "build", entry)["ok"])

    def test_explicit_exception_still_requires_reason_and_is_audited(self):
        self.start()
        args = ("sdd", "workflow", "select", "--name", "legacy", "--entrypoint", self.entry, "--allow-unverified")
        rc, out = self.run_cli(*args)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "UNVERIFIED_REASON_REQUIRED")
        rc, out = self.run_cli(*args, "--reason", "explicit fixture exception")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["workflow"]["unverified"]["allowed"])
        events = [json.loads(line) for line in (self.repo / ".orchestrator/lifecycle/events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(event["event"] == "workflow.unverified_exception" for event in events))

    def test_real_provider_script_fixture_can_complete_standard_contract(self):
        self.start(profile="standard")
        script = self.repo / "bmad-fixture.py"
        script.write_text("from pathlib import Path\np=Path('_bmad-output/spec.md')\n"
                          "p.parent.mkdir(exist_ok=True)\np.write_text('Explicit local fixture')\n", encoding="utf-8")
        command = shlex.join([sys.executable, str(script)])
        rc, out = self.run_cli("sdd", "workflow", "select", "--name", "native-fixture", "--entrypoint", command)
        self.assertEqual(rc, 0, out)
        result = subprocess.run([sys.executable, str(script)], cwd=self.repo, timeout=10)
        self.assertEqual(result.returncode, 0)
        for args in (("sdd", "workflow", "execute", "--command", command, "--exit-code", "0"),
                     ("sdd", "workflow", "complete", "--artifact", "_bmad-output/spec.md", "--no-plan"),
                     ("gate", "satisfy", "--gate", "scope_review", "--evidence", "local fixture scope"),
                     ("gate", "satisfy", "--gate", "implementation_review", "--evidence", "local fixture implementation"),
                     ("verify",), ("done",)):
            rc, out = self.run_cli(*args)
            self.assertEqual(rc, 0, out)
        self.assertEqual(out["status"], "completed")


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
