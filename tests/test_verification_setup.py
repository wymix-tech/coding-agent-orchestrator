"""First-project setup and lossless configuration, using the real CLI."""
import argparse
from contextlib import redirect_stdout
import importlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
from unittest import mock

from repair_helpers import RepairCase, ROOT
from scripts import cao


class VerificationSetupTests(RepairCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.verification_setup"))
        return importlib.import_module("scripts.verification_setup")

    def configure(self, *args):
        return self.run_cli("verification", "configure", *args)

    def raw(self):
        return json.loads((self.repo / cao.CONFIG_REL).read_text(encoding="utf-8"))

    def test_setup_state_distinguishes_configuration_from_pass(self):
        module = self.module()
        for verification, ready, tests_bound in (
            ({}, False, False), ({"scripts": []}, False, False),
            ({"scripts": [{"name": "optional", "command": "check", "required": False}]}, False, False),
            ({"scripts": ["project check"]}, True, False),
            ({"commands": ["project check"]}, True, False),
            ({"command": "project check"}, True, False),
            ({"scripts": [{"name": "suite", "command": "missing-wrapper", "required": True, "gates": ["tests"]}]}, True, True),
            ({"scripts": [{"name": "bad", "command": "", "required": True}]}, False, False),
            ({"scripts": [{"name": "bad", "command": "check", "required": "yes"}]}, False, False),
            ("invalid-section", False, False),
        ):
            with self.subTest(verification=verification):
                out = module.setup_state({"verification": verification})
                self.assertEqual(out["ready"], ready)
                self.assertEqual(out["tests_bound"], tests_bound)
                self.assertNotIn("passed", out)

    def test_configure_preserves_unrelated_values_and_declares_capabilities(self):
        cfg = self.raw()
        cfg["language"]["user"] = "ja"
        cfg["verification"]["timeout_seconds"] = 19
        cfg["verification"]["custom"] = {"keep": "verification metadata"}
        cfg["custom"] = {"nested": [1, 2]}
        cao.save_json(self.repo / cao.CONFIG_REL, cfg)
        rc, out = self.configure("--replace", "--verify-script", "project lint", "--test-script", self.test_command)
        self.assertEqual(rc, 0, out)
        current = self.raw()
        for key in cfg:
            if key != "verification":
                self.assertEqual(current[key], cfg[key])
        self.assertEqual(current["verification"]["timeout_seconds"], 19)
        self.assertEqual(current["verification"]["custom"], cfg["verification"]["custom"])
        self.assertEqual([item["gates"] for item in current["verification"]["scripts"]], [[], ["tests"]])
        self.assertTrue(out["verification_setup"]["ready"])
        self.assertTrue(out["verification_setup"]["tests_bound"])

    def test_identical_configuration_does_not_write_file(self):
        before = (self.repo / cao.CONFIG_REL).read_bytes()
        rc, out = self.configure("--test-script", self.test_command)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["status"], "unchanged")
        self.assertEqual((self.repo / cao.CONFIG_REL).read_bytes(), before)

    def test_different_nonempty_configuration_requires_explicit_replace(self):
        before = (self.repo / cao.CONFIG_REL).read_bytes()
        rc, out = self.configure("--test-script", "new project suite")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_CONFIG_REPLACE_REQUIRED")
        self.assertIn("current", out)
        self.assertIn("requested", out)
        self.assertEqual((self.repo / cao.CONFIG_REL).read_bytes(), before)

    def test_invalid_old_script_requires_replace(self):
        cfg = self.raw()
        cfg["verification"]["scripts"] = [{"name": "broken", "command": ""}]
        cao.save_json(self.repo / cao.CONFIG_REL, cfg)
        rc, out = self.configure("--test-script", self.test_command)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_CONFIG_REPLACE_REQUIRED")
        self.assertEqual(self.configure("--replace", "--test-script", self.test_command)[0], 0)

    def test_invalid_parameters_do_not_change_configuration(self):
        before = (self.repo / cao.CONFIG_REL).read_bytes()
        for args in ((), ("--test-script", "   "), ("--verify-script", "")):
            with self.subTest(args=args):
                rc, out = self.configure(*args)
                self.assertEqual(rc, 2, out)
                self.assertIn("error", out)
                self.assertEqual((self.repo / cao.CONFIG_REL).read_bytes(), before)

    def test_malformed_whole_config_cannot_be_silently_replaced(self):
        path = self.repo / cao.CONFIG_REL
        path.write_bytes(b'{"language":')
        rc, out = self.configure("--replace", "--test-script", self.test_command)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_CONFIG_INVALID")
        self.assertEqual(path.read_bytes(), b'{"language":')

    def test_active_anchor_and_work_are_not_modified_and_review_is_reported(self):
        wid = self.start()
        path = self.repo / cao.WORK_DIR / (wid + ".json")
        before = path.read_bytes()
        rc, out = self.configure("--replace", "--test-script", "new project suite")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["acceptance_review_required"])
        self.assertIn("--reanchor", out["next"]["instruction"])
        self.assertEqual(path.read_bytes(), before)
        rc, out = self.configure("--test-script", "new project suite")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["status"], "unchanged")
        self.assertTrue(out["acceptance_review_required"])
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_active_record_is_not_upgraded_by_configuration(self):
        wid = self.start()
        path = self.repo / cao.WORK_DIR / (wid + ".json")
        work = self.read_work(wid)
        work.pop("acceptance")
        work.pop("revision", None)
        path.write_text(json.dumps(work), encoding="utf-8")
        before = path.read_bytes()
        rc, out = self.configure("--replace", "--test-script", "reviewed suite")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["acceptance_review_required"])
        self.assertEqual(path.read_bytes(), before)

    def test_non_object_config_is_rejected_without_write(self):
        path = self.repo / cao.CONFIG_REL
        for raw in (b"[]", b"null", b"17"):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                rc, out = self.configure("--replace", "--test-script", self.test_command)
                self.assertEqual(rc, 2, out)
                self.assertEqual(out.get("error"), "VERIFICATION_CONFIG_INVALID")
                self.assertEqual(path.read_bytes(), raw)

    def test_first_configure_does_not_create_work_or_host_files(self):
        new_repo = self.repo / "new-project"
        new_repo.mkdir()
        env = dict(os.environ)
        env["PI_CODING_AGENT"] = "1"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/cao.py"), "verification", "configure",
                                 "--repo", str(new_repo), "--test-script", self.test_command],
                                env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        cfg = json.loads((new_repo / cao.CONFIG_REL).read_text(encoding="utf-8"))
        self.assertEqual(set(cfg), {"verification"})
        self.assertFalse((new_repo / ".pi").exists())
        self.assertFalse((new_repo / ".codex").exists())
        self.assertFalse((new_repo / "AGENTS.md").exists())
        self.assertEqual(list((new_repo / cao.WORK_DIR).glob("*.json")), [])

    def test_handler_does_not_install_or_execute_and_storage_failure_is_not_success(self):
        self.assertTrue(hasattr(cao, "cmd_verification_configure"))
        args = argparse.Namespace(repo=str(self.repo), verify_script=["check"], test_script=[], replace=True)
        before = (self.repo / cao.CONFIG_REL).read_bytes()
        # Repository discovery already uses read-only git; isolate that common boundary.
        with mock.patch("scripts.cao_verification.repo_root", return_value=self.repo), \
                mock.patch("scripts.cao_runtime.repo_root", return_value=self.repo), \
                mock.patch("scripts.cao_bootstrap.install_host_adapters") as install, \
                mock.patch.object(cao.subprocess, "run") as run:
            with mock.patch("scripts.cao_verification.save_json", side_effect=OSError("write denied")):
                with self.assertRaises(OSError):
                    cao.cmd_verification_configure(args)
            self.assertEqual((self.repo / cao.CONFIG_REL).read_bytes(), before)
            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(cao.cmd_verification_configure(args), 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "configured")
            install.assert_not_called()
            run.assert_not_called()

    def test_concurrent_language_and_configuration_changes_survive(self):
        commands = (["language", "set", "--language", "ja"],
                    ["verification", "configure", "--replace", "--test-script", "new suite"])
        processes = [subprocess.Popen([sys.executable, str(ROOT / "scripts/cao.py"), *command, "--repo", str(self.repo)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        cfg = self.raw()
        self.assertEqual(cfg["language"]["user"], "ja")
        self.assertEqual(cfg["verification"]["scripts"][0]["command"], "new suite")

    def test_configure_is_a_standalone_control_command(self):
        self.assertTrue(cao.readonly_shell("cao verification configure --test-script 'project suite'"))
        self.assertFalse(cao.readonly_shell("cao verification configure --test-script suite > .orchestrator/config.json"))
