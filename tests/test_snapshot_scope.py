"""Source scope and generated output declarations must not hide changed inputs."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from repair_helpers import RepairCase
from scripts.cao_runtime import config
from scripts.cao_snapshot import code_snapshot, verification_snapshot


class ScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = pathlib.Path(temporary.name)

    def write(self, relative, content="one"):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def configure(self, outputs=None, excludes=None):
        value = {"verification": {"scripts": [], "outputs": outputs or []},
                 "snapshot": {"exclude_paths": excludes or []}}
        self.write(".orchestrator/config.json", json.dumps(value))

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_directories_are_not_ignored_in_git_or_plain_projects(self):
        for git in (False, True):
            if git:
                self.git("init", "-q")
                self.write(".gitignore", "src/\n")
            for directory in ("build", "dist", "target"):
                path = self.write(f"src/{directory}/logic.py")
                before = code_snapshot(self.repo)
                path.write_text("changed")
                self.assertNotEqual(before, code_snapshot(self.repo))

    def test_tracked_files_override_cache_and_explicit_exclusions(self):
        self.git("init", "-q")
        self.configure(excludes=["generated/"])
        for relative in ("node_modules/custom/code.js", "generated/code.py"):
            path = self.write(relative)
            self.git("add", "-f", relative)
            before = code_snapshot(self.repo)
            path.write_text("changed")
            self.assertNotEqual(before, code_snapshot(self.repo))

    def test_explicit_exclusions_are_root_relative_and_bound(self):
        self.configure(excludes=["generated/"])
        before = code_snapshot(self.repo)
        self.write("generated/data.bin")
        self.assertEqual(before, code_snapshot(self.repo))
        self.write("src/generated/logic.py")
        self.assertNotEqual(before, code_snapshot(self.repo))
        anchor = verification_snapshot(config(self.repo), self.repo)
        self.configure(excludes=["other/"])
        self.assertNotEqual(anchor, verification_snapshot(config(self.repo), self.repo))

    def test_tracked_cache_does_not_force_scanning_unrelated_dependencies(self):
        from unittest import mock
        import os
        self.git("init", "-q")
        self.write("node_modules/owned/code.js")
        self.git("add", "-f", "node_modules/owned/code.js")
        self.write("node_modules/unrelated/deep/code.js")
        calls = []
        original = os.scandir
        def scan(path):
            calls.append(str(path))
            return original(path)
        with mock.patch("scripts.cao_snapshot.os.scandir", side_effect=scan):
            code_snapshot(self.repo)
        self.assertFalse(any("unrelated" in item for item in calls))

    def test_output_path_validation_and_tracked_conflicts(self):
        self.git("init", "-q")
        self.write("reports/input.json")
        self.git("add", "reports/input.json")
        for output in ("../outside.xml", "/tmp/report.xml", "./", "**", "src/", "tests/",
                       "scripts/", ".orchestrator/", "requirements/", "AGENTS.md", "run_checks.py",
                       "reports/"):
            with self.subTest(output=output):
                self.configure(outputs=[output])
                with self.assertRaisesRegex((ValueError, RuntimeError), "SNAPSHOT_"):
                    code_snapshot(self.repo)

    def test_output_symlink_escape_is_rejected_before_pruning(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        try:
            (self.repo / "reports").symlink_to(outside.name, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        self.configure(outputs=["reports/"])
        with self.assertRaisesRegex((ValueError, RuntimeError), "SNAPSHOT_"):
            code_snapshot(self.repo)

    def test_declared_outputs_do_not_hide_changed_source(self):
        self.configure(outputs=["coverage.xml", "reports/"])
        before = code_snapshot(self.repo)
        self.write("coverage.xml", "timestamp 1")
        self.write("reports/junit.xml", "timestamp 1")
        self.assertEqual(before, code_snapshot(self.repo))
        self.write("src/logic.py", "changed")
        self.assertNotEqual(before, code_snapshot(self.repo))

    def test_native_commit_uses_declared_outputs_but_not_for_tracked_changes(self):
        from scripts.cao_defaults import SNAPSHOT_IGNORE_DIRS
        from scripts.native_obligations import commit_state
        from test_provider_light_adaptation import ONESHOT
        self.git("init", "-q")
        self.configure(outputs=["coverage.xml"])
        self.write("_bmad-output/spec.md", ONESHOT)
        source = self.write("node_modules/owned/source.js")
        self.git("add", "-f", "_bmad-output/spec.md", "node_modules/owned/source.js")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "Fixture")
        work = {"sdd": {"provider": "bmad", "spec_binding": {"spec": {"path": "_bmad-output/spec.md"}}}}
        self.write("coverage.xml", "timestamp")
        self.assertTrue(commit_state(self.repo, work, SNAPSHOT_IGNORE_DIRS)["ready"])
        source.write_text("changed")
        state = commit_state(self.repo, work, SNAPSHOT_IGNORE_DIRS)
        self.assertEqual(state["reason"], "NATIVE_COMMIT_REQUIRED")
        self.assertIn("node_modules/owned/source.js", state["changed"])
        from unittest import mock
        with mock.patch("scripts.snapshot_scope.git_inputs", side_effect=RuntimeError("SNAPSHOT_GIT_INDEX_FAILED")):
            state = commit_state(self.repo, work, SNAPSHOT_IGNORE_DIRS)
        self.assertEqual(state["reason"], "NATIVE_COMMIT_CHECK_FAILED")


class OutputVerificationTests(RepairCase):
    def setup_wrapper(self, declared=True, mutate=False):
        wrapper = self.repo / "run_checks.py"
        wrapper.write_text("from pathlib import Path\nimport time, subprocess, sys\n"
                           "result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'acceptance-tests'])\n"
                           "Path('coverage.xml').write_text(str(time.time_ns()))\n" +
                           ("Path('product.py').write_text(str(time.time_ns()))\n" if mutate else "") +
                           "raise SystemExit(result.returncode)\n")
        command = f'"{sys.executable}" run_checks.py'
        args = ["verification", "configure", "--replace", "--test-script", command]
        if declared:
            args.extend(["--output", "coverage.xml"])
        rc, out = self.run_cli(*args)
        self.assertEqual(rc, 0, out)
        self.start(profile="standard")

    def test_declared_report_passes_repeated_verification(self):
        self.setup_wrapper()
        for _ in range(2):
            rc, out = self.run_cli("verify")
            self.assertEqual(rc, 0, out)
            self.assertEqual(out["verification"]["status"], "passed")

    def test_undeclared_report_or_changed_source_remains_stale(self):
        self.setup_wrapper(declared=False)
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["verification"]["reason_code"], "VERIFICATION_SOURCE_CHANGED")

    def test_source_mutation_is_not_accepted_with_declared_report(self):
        self.setup_wrapper(mutate=True)
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["verification"]["status"], "stale")

    def test_output_change_requires_explicit_replace_and_reanchor(self):
        self.setup_wrapper()
        rc, out = self.run_cli("verification", "configure", "--output", "junit.xml")
        self.assertEqual(rc, 2, out)
        rc, out = self.run_cli("verification", "configure", "--replace", "--output", "junit.xml")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["acceptance_review_required"])
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)


    def test_clear_outputs_and_exclusions_preserve_scripts(self):
        self.setup_wrapper()
        rc, out = self.run_cli("verification", "configure", "--replace", "--exclude-path", "generated/")
        self.assertEqual(rc, 0, out)
        before = config(self.repo)["verification"]["scripts"]
        rc, out = self.run_cli("verification", "configure", "--replace", "--clear-outputs", "--clear-exclude-paths")
        self.assertEqual(rc, 0, out)
        self.assertEqual(config(self.repo)["verification"]["outputs"], [])
        self.assertEqual(config(self.repo)["snapshot"]["exclude_paths"], [])
        self.assertEqual(config(self.repo)["verification"]["scripts"], before)

    def test_invalid_output_does_not_write_configuration(self):
        before = (self.repo / ".orchestrator/config.json").read_bytes()
        rc, out = self.run_cli("verification", "configure", "--replace", "--output", "src/")
        self.assertEqual(rc, 2, out)
        self.assertEqual((self.repo / ".orchestrator/config.json").read_bytes(), before)

    def test_old_snapshot_format_never_reuses_new_evidence(self):
        from scripts.cao_completion import verification_inputs
        from scripts.completion_policy import verification_fresh
        self.setup_wrapper()
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        works = list((self.repo / ".orchestrator/work").glob("*.json"))
        work = json.loads(works[0].read_text())
        inputs = verification_inputs(self.repo, work, config(self.repo))
        self.assertTrue(verification_fresh(work, inputs))
        work["verification"]["snapshot_format"] = 3
        self.assertFalse(verification_fresh(work, inputs))
