import json
import os
import shlex
import subprocess
import sys
import unittest

from repair_helpers import RepairCase
from scripts import cao, completion_policy as policy


class NormalizationTests(unittest.TestCase):
    def normalize(self, raw):
        self.assertTrue(callable(getattr(policy, "normalize_scripts", None)), "normalize_scripts is missing")
        return policy.normalize_scripts(raw)

    def gate_result(self, verification):
        self.assertTrue(callable(getattr(policy, "tests_satisfied", None)), "tests_satisfied is missing")
        return policy.tests_satisfied(verification)

    def test_wrapper_keeps_declared_gate_and_deduplicates_capabilities(self):
        scripts = self.normalize([{"name": "unit", "command": "./scripts/verify-unit-tests.sh",
                                   "required": True, "gates": ["tests", "tests"]}])
        self.assertEqual(scripts[0]["gates"], ["tests"])

    def test_legacy_strings_remain_general_verification(self):
        scripts = self.normalize(["python -m unittest --help", {"command": "./checks", "required": False}])
        self.assertEqual([script["gates"] for script in scripts], [[], []])
        self.assertEqual([script["name"] for script in scripts], ["script-1", "script-2"])

    def test_invalid_scripts_and_capabilities_are_rejected(self):
        for raw in (None, {}, [3], [{"command": ""}], [{"command": "test", "required": "yes"}],
                    [{"command": "test", "gates": "tests"}], [{"command": "test", "gates": ["lint"]}],
                    [{"command": "test", "gates": [["tests"]]}],
                    [{"name": "a", "command": "one"}, {"name": "a", "command": "two"}]):
            with self.subTest(raw=raw):
                self.assertTrue(callable(getattr(policy, "normalize_scripts", None)), "normalize_scripts is missing")
                with self.assertRaises(ValueError):
                    policy.normalize_scripts(raw)

    def test_optional_failed_tests_do_not_satisfy_gate(self):
        self.assertFalse(self.gate_result({"results": [
            {"required": True, "gates": [], "status": "passed"},
            {"required": False, "gates": ["tests"], "status": "failed"}]}))

    def test_all_required_test_bindings_must_pass(self):
        for status in ("failed", "blocked", "error", "stale"):
            with self.subTest(status=status):
                self.assertFalse(self.gate_result({"results": [
                    {"required": True, "gates": ["tests"], "status": "passed"},
                    {"required": True, "gates": ["tests"], "status": status}]}))
        self.assertTrue(self.gate_result({"results": [
            {"required": True, "gates": ["tests"], "status": "passed"},
            {"required": False, "gates": ["tests"], "status": "failed"}]}))
        self.assertFalse(self.gate_result({"results": []}))


class VerificationTests(RepairCase):
    def set_scripts(self, scripts):
        path = self.repo / ".orchestrator/config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["verification"]["scripts"] = scripts
        path.write_text(json.dumps(cfg), encoding="utf-8")
        from scripts.verification_setup import setup_state
        if setup_state(cfg)['ready']:
            from tests.helpers import seed_project_preparation
            seed_project_preparation(self.repo)

    def python_command(self, code):
        argv = [sys.executable, "-c", code]
        return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)

    def wrapper(self):
        # Python launcher exercises the same opaque-wrapper contract on Windows and POSIX.
        path = self.repo / "run_checks.py"
        path.write_text(
            "import subprocess, sys\n"
            "result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'acceptance-tests', '-v'])\n"
            "raise SystemExit(result.returncode)\n", encoding="utf-8")
        argv = [sys.executable, "run_checks.py"]
        return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)

    def test_opaque_real_test_wrapper_satisfies_tests(self):
        self.set_scripts([{"name": "unit", "command": self.wrapper(), "required": True, "gates": ["tests"]}])
        self.start(profile="standard")
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        result = out["verification"]["results"][0]
        self.assertEqual(result["gates"], ["tests"])
        self.assertIn("Ran 1 test", result["stderr_tail"])
        rc, out = self.run_cli("gate", "show")
        self.assertEqual(rc, 0, out)
        self.assertIn("tests", out["gates"]["satisfied"])

    def test_keyword_without_binding_does_not_grant_tests(self):
        self.set_scripts([{"command": self.test_command, "required": True}])
        self.start(profile="standard")
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_GATE_UNBOUND")

    def test_optional_test_binding_is_insufficient(self):
        self.set_scripts([
            {"name": "general", "command": self.test_command, "required": True, "gates": []},
            {"name": "optional", "command": self.test_command, "required": False, "gates": ["tests"]},
        ])
        self.start(profile="standard")
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_GATE_UNBOUND")

    def test_tests_cannot_be_manually_satisfied(self):
        self.start(profile="standard")
        rc, out = self.run_cli("gate", "satisfy", "--gate", "tests", "--evidence", "claimed")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "GATE_IS_AUTO_DERIVED")

    def test_required_failed_tests_leave_gate_pending(self):
        (self.repo / "acceptance-tests/test_acceptance.py").write_text(
            "import unittest\nclass Acceptance(unittest.TestCase):\n"
            "    def test_fails(self): self.fail('real test failed')\n", encoding="utf-8")
        self.start(profile="standard")
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 1, out)
        self.assertEqual(out["verification"]["results"][0].get("gates"), ["tests"])
        rc, out = self.run_cli("gate", "show")
        self.assertIn("tests", out["gates"]["pending"])

    def test_required_test_failure_and_optional_failure_remain_distinct(self):
        failed = self.repo / "failed-tests"
        failed.mkdir()
        (failed / "test_failure.py").write_text(
            "import unittest\nclass Failure(unittest.TestCase):\n"
            "    def test_fail(self): self.fail('expected actual test failure')\n", encoding="utf-8")
        failing_command = self.test_command.replace("acceptance-tests", "failed-tests")
        self.set_scripts([
            {"name": "unit", "command": self.test_command, "required": True, "gates": ["tests"]},
            {"name": "second", "command": failing_command, "required": True, "gates": ["tests"]},
        ])
        self.start(profile="standard")
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 1, out)
        self.assertEqual([r["status"] for r in out["verification"]["results"]], ["passed", "failed"])
        rc, out = self.run_cli("gate", "show")
        self.assertIn("tests", out["gates"]["pending"])
        cfg = cao.config(self.repo)
        cfg["verification"]["scripts"][1]["required"] = False
        self.set_scripts(cfg["verification"]["scripts"])
        rc, out = self.run_cli("reopen", "--reanchor", "--reason", "second check is now informational")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["verification"]["results"][1]["status"], "failed")
        rc, out = self.run_cli("gate", "show")
        self.assertIn("tests", out["gates"]["satisfied"])

    def test_cli_override_is_engineering_only(self):
        self.start(profile="standard")
        rc, out = self.run_cli("verify", "--script", self.test_command)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["verification"]["source"], "cli")
        rc, out = self.run_cli("gate", "show")
        self.assertIn("tests", out["gates"]["pending"])
        self.assertIn("verification", out["gates"]["pending"])

    def test_same_second_capability_change_invalidates_standard(self):
        self.start()
        cfg = cao.config(self.repo)
        cfg["verification"]["scripts"][0]["gates"] = []
        (self.repo / ".orchestrator/config.json").write_text(json.dumps(cfg), encoding="utf-8")
        from tests.helpers import seed_project_preparation
        seed_project_preparation(self.repo)
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_STANDARD_CHANGED")

    def test_gate_show_does_not_trust_passed_after_code_change(self):
        self.start(profile="standard")
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        (self.repo / "app.py").write_text("changed = True\n", encoding="utf-8")
        rc, out = self.run_cli("gate", "show")
        self.assertIn("verification", out["gates"]["pending"])
        self.assertIn("tests", out["gates"]["pending"])

    def test_verification_preserves_decision_made_during_execution(self):
        from unittest import mock
        import contextlib
        import io
        wid = self.start()
        original = cao.run_script
        def concurrent(*args, **kwargs):
            # The bounded runner replaced subprocess.run as the lock-free execution boundary.
            rc, out = self.run_cli("decision", "--decision", "during verification")
            self.assertEqual(rc, 0, out)
            return original(*args, **kwargs)
        args = cao.build_parser().parse_args(["verify", "--repo", str(self.repo)])
        with mock.patch("scripts.cao_verification.run_script", side_effect=concurrent), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cao.cmd_verify(args), 0)
        self.assertEqual(self.read_work(wid)["decisions"][0]["decision"], "during verification")

    def test_changed_verification_inputs_are_committed_as_stale(self):
        changes = {
            "code": "from pathlib import Path; Path('app.py').write_text('changed = True')",
            "requirement": "from pathlib import Path; Path('.orchestrator/requirements/a.md').write_text('# changed')",
            "config": "import json; from pathlib import Path; p=Path('.orchestrator/config.json'); c=json.loads(p.read_text()); c['verification']['scripts'][0]['command'] += ' changed'; p.write_text(json.dumps(c))",
            "assessment": f"import subprocess, sys; subprocess.run([sys.executable, {cao.__file__!r}, 'path', 'assess', '--profile', 'standard', '--reason', 'during verification'], check=True)",
            "rules": "from pathlib import Path; Path('AGENTS.md').write_text('# Changed rules')",
        }
        for name, change in changes.items():
            with self.subTest(input=name):
                # Each subcase owns a fresh real repository and test process.
                case = VerificationTests()
                case.setUp()
                try:
                    command = case.test_command + " && " + case.python_command(change)
                    case.set_scripts([{"name": "unit", "command": command, "required": True, "gates": ["tests"]}])
                    wid = case.start(profile="standard")
                    rc, out = case.run_cli("verify")
                    if name in {"requirement", "assessment"}:
                        self.assertEqual(rc, 0, out)
                        self.assertEqual(out["verification"]["status"], "passed")
                        self.assertFalse(case.run_cli("acceptance", "status")[1]["ready"])
                        continue
                    self.assertEqual(rc, 2, out)
                    self.assertEqual(out["verification"]["status"], "stale")
                    self.assertEqual(out["verification"]["run_status"], "passed")
                    self.assertEqual(case.read_work(wid)["execution"]["step"], "path-selected")
                finally:
                    case.doCleanups()

    def test_init_test_script_exposes_required_capabilities_and_help(self):
        rc, out = self.run_cli("init", "--force", "--host", "codex", "--sdd", "bmad",
                               "--verify-script", self.python_command("print('general')"),
                               "--test-script", self.test_command, "--test-script", self.test_command)
        self.assertEqual(rc, 0, out)
        scripts = cao.verification_scripts(cao.config(self.repo))
        self.assertEqual([script["gates"] for script in scripts], [[], ["tests"], ["tests"]])
        self.assertEqual(len({script["name"] for script in scripts}), 3)
        self.assertTrue(all(script["required"] for script in scripts))
        rc, out = self.run_cli("init", "--help")
        self.assertEqual(rc, 0, out)
        self.assertIn("--test-script", out["raw"])
