"""Phase A / PR3: a destructive-revision confirmation is bound to what it destroys."""
from __future__ import annotations

import importlib.util
import pathlib
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("requirement_identity", ROOT / "scripts" / "requirement_identity.py")
ri = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader
_SPEC.loader.exec_module(ri)

REQ = "generic:source:docs/spec.md"


class RevisionConfirmationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        docs = self.repo / "docs"
        docs.mkdir()
        (docs / "spec.md").write_text("# Spec\n\nlogin with jwt\n", encoding="utf-8")
        self.source_revision = ri.requirement_content_revision(self.repo, "docs/spec.md")["source_revision"]
        ri.record(self.repo, requirement_id=REQ, revision_id="rev-active", work_item_id="W-1",
                  provider="generic", source_path="docs/spec.md", status="active",
                  source_revision=self.source_revision)

    def _check(self, confirmation, *, phase="implementation", status="in_progress",
               active_revision="rev-active", source_revision=None, state_revision="st-1"):
        return ri.check_revision_confirmation(
            self.repo, requirement_id=REQ,
            source_revision=source_revision or self.source_revision,
            phase=phase, status=status, confirmation=confirmation,
            active_revision=active_revision, state_revision=state_revision)

    def _full(self, **overrides):
        full = {"requirement_id": REQ, "source_revision": "rev-active",
                "incoming_source_revision": self.source_revision, "state_revision": "st-1",
                "phase": "implementation", "status": "in_progress"}
        full.update(overrides)
        return full

    def test_missing_confirmation_is_refused(self):
        result = self._check(None)
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_REQUIRED", result["error"])
        # A refusal has to be actionable: the command carries every binding it needs.
        self.assertIn("--confirm-state-revision", result["confirm_command"])

    def test_confirmation_for_another_requirement_is_not_reusable(self):
        result = self._check({**self._full(), "requirement_id": "generic:source:docs/other.md"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_MISMATCH", result["error"])

    def test_every_required_binding_is_checked(self):
        for missing in ("source_revision", "incoming_source_revision", "state_revision"):
            confirmation = self._full()
            confirmation.pop(missing)
            result = self._check(confirmation)
            self.assertFalse(result["allowed"], missing)
            self.assertEqual("REVISION_CONFIRMATION_REQUIRED", result["error"])
            self.assertIn(missing, result["missing_bindings"])

    def test_confirmation_must_be_reissued_after_the_phase_moves(self):
        result = self._check({**self._full(), "phase": "discovery"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_SUPERSEDED", result["error"])
        self.assertEqual("confirm_requirement_revision", result["next_action"])

    def test_confirmation_must_be_reissued_after_the_status_moves(self):
        result = self._check({**self._full(), "status": "blocked"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_SUPERSEDED", result["error"])

    def test_stale_source_revision_is_not_accepted(self):
        result = self._check({**self._full(), "source_revision": "rev-stale-old"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_SUPERSEDED", result["error"])

    def test_an_old_confirmation_cannot_approve_a_later_source_change(self):
        """A confirmation approves exactly one incoming revision, not every future one."""
        self._write_spec("login with jwt and refresh tokens")
        moved = ri.requirement_content_revision(self.repo, "docs/spec.md")["source_revision"]
        result = ri.check_revision_confirmation(
            self.repo, requirement_id=REQ, source_revision=moved, phase="implementation",
            status="in_progress", active_revision="rev-active", state_revision="st-1",
            confirmation={"requirement_id": REQ, "source_revision": "rev-active",
                          "incoming_source_revision": self.source_revision,
                          "state_revision": "st-1", "phase": "implementation",
                          "status": "in_progress"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_SUPERSEDED", result["error"])
        self.assertEqual(moved, result["current_source_revision"])

    def test_progress_inside_the_same_phase_still_needs_reconfirmation(self):
        """Phase and status are diagnostics; the state revision is what binds the decision."""
        result = self._check({**self._full(), "state_revision": "st-old"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_SUPERSEDED", result["error"])
        self.assertEqual("st-1", result["current_state_revision"])

    def test_phase_may_be_omitted_when_the_state_revision_is_bound(self):
        confirmation = self._full()
        confirmation.pop("phase")
        result = self._check(confirmation, phase=None)
        self.assertTrue(result["allowed"])

    def _write_spec(self, requirement_text):
        (self.repo / "docs" / "spec.md").write_text(f"# Spec\n\n{requirement_text}\n", encoding="utf-8")

    def test_a_properly_bound_confirmation_is_accepted(self):
        result = self._check(self._full())
        self.assertTrue(result["allowed"])


class PrintedConfirmationCommandTests(unittest.TestCase):
    """R7: the command a refusal prints has to be a command that runs.

    A recovery command that cannot be executed -- a wrong `--request` option, `--repo` after
    the subcommand, a relative entry point -- leaves no legal way forward. So the printed
    command is executed here, from a working directory that is not the project.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        docs = self.repo / "docs"
        docs.mkdir()
        (docs / "spec.md").write_text("# Spec\n\nlogin with jwt\n", encoding="utf-8")
        self.source_revision = ri.requirement_content_revision(self.repo, "docs/spec.md")["source_revision"]
        ri.record(self.repo, requirement_id=REQ, revision_id="rev-active", work_item_id="W-1",
                  provider="generic", source_path="docs/spec.md", status="active",
                  source_revision=self.source_revision)

    def _refusal_command(self) -> str:
        result = ri.check_revision_confirmation(
            self.repo, requirement_id=REQ, source_revision=self.source_revision,
            phase="implementation", status="in_progress", confirmation=None,
            active_revision="rev-active", state_revision="st-1",
            request="Continue the login implementation.")
        self.assertFalse(result["allowed"])
        return str(result["confirm_command"])

    def test_the_printed_command_runs_from_another_directory(self):
        command = self._refusal_command()
        with tempfile.TemporaryDirectory() as elsewhere:
            run = subprocess.run(shlex.split(command), cwd=elsewhere, capture_output=True, text=True)
        # A usage error is not a recovery: argparse would have rejected the printed flags.
        self.assertNotIn("unrecognized arguments", run.stderr)
        self.assertNotIn("invalid choice", run.stderr)
        self.assertNotIn("the following arguments are required", run.stderr)
        self.assertNotIn("Traceback", run.stderr)
        # It reached the intake that owns the confirmation instead of stopping at the parser.
        self.assertIn("Intake:", run.stdout)
        self.assertIn("Work item:", run.stdout)

    def test_the_printed_command_is_well_formed_before_it_is_run(self):
        """The entry point, the repo option and the request are placed where the parser wants them."""
        command = self._refusal_command()
        parts = shlex.split(command)
        self.assertTrue(pathlib.Path(parts[1]).is_absolute(), parts[1])
        self.assertEqual("--repo", parts[2])
        self.assertEqual(str(self.repo.resolve()), parts[3])
        self.assertEqual("intake", parts[4])
        self.assertNotIn("--request", parts)  # not an option of this CLI
        self.assertIn("Continue the login implementation.", parts)


if __name__ == "__main__":
    unittest.main()
