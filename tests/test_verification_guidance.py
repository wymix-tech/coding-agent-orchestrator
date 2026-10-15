"""Guidance contracts; these do not prove a fresh model follows the instructions."""
import pathlib
import shlex
import sys
import unittest

from tests.repair_helpers import RepairCase

ROOT = pathlib.Path(__file__).resolve().parents[1]


class VerificationGuidanceDocuments(unittest.TestCase):
    def test_templates_start_before_preflight_and_preserve_sdd_authorization(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(template=name):
                text = (ROOT / "templates" / name).read_text(encoding="utf-8")
                self.assertIn("mode: verification_setup", text)
                self.assertLess(text.index("mode: sdd_select"), text.index("mode: verification_setup"))
                self.assertLess(text.index("start --requirement"), text.index("mode: verification_setup"))
                for token in ("product manifests", "user's explicit", "ask the user", "Zero tests",
                              "missing tools", "missing dependencies", "JUnit5", "Surefire",
                              "{{CAO_COMMAND}} verification configure", "init --force", "reanchor",
                              "not verification success", ".agents", ".pi", "requirements remain relevant"):
                    self.assertIn(token, text)

    def test_skill_explains_work_before_setup_and_no_false_pass(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("mode: verification_setup", text)
        self.assertLess(text.index("### Landing"), text.index("mode: verification_setup"))
        for token in ("product manifests", "ask the user", "Zero tests", "JUnit5", "Surefire",
                      "verification configure", "--replace", "init --force", "reanchor",
                      "not verification success", "existing scripts", "requirements remain relevant"):
            self.assertIn(token, text)

    def test_readme_and_design_document_safe_configuration_contract(self):
        for name in ("README.md", "DESIGN.md"):
            with self.subTest(document=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                for token in ("verification_setup", "verification configure", "--replace", "unchanged",
                              "not verification success", "zero tests", "init --force", "reanchor"):
                    self.assertIn(token, text)


class VerificationGuidanceRuntime(RepairCase):
    def test_existing_project_instructions_are_not_overwritten_by_new_guidance(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            (self.repo / name).write_text("# Existing project policy\n", encoding="utf-8")
        rc, out = self.run_cli("bootstrap")
        self.assertEqual(rc, 0, out)
        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertEqual((self.repo / name).read_text(encoding="utf-8"), "# Existing project policy\n")

    def test_project_owned_zero_test_wrapper_fails_without_core_report_parsing(self):
        # The project wrapper, not CAO, defines the zero-test policy.
        (self.repo / "empty-tests").mkdir()
        wrapper = self.repo / "check_tests.py"
        wrapper.write_text(
            "import unittest\n"
            "suite = unittest.TestLoader().discover('empty-tests')\n"
            "if suite.countTestCases() == 0: raise SystemExit('Zero tests collected')\n"
            "raise SystemExit(not unittest.TextTestRunner().run(suite).wasSuccessful())\n",
            encoding="utf-8",
        )
        command = shlex.join([sys.executable, wrapper.name])
        rc, out = self.run_cli("verification", "configure", "--test-script", command, "--replace")
        self.assertEqual(rc, 0, out)
        wid = self.start()
        rc, out = self.run_cli("verify")
        self.assertNotEqual(rc, 0, out)
        work = self.read_work(wid)
        self.assertEqual(work["verification"]["status"], "failed")
        self.assertIn("Zero tests collected", str(work["verification"]))
        rc, done = self.run_cli("done")
        self.assertNotEqual(rc, 0, done)


if __name__ == "__main__":
    unittest.main()
