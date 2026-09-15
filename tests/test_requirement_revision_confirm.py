"""Phase A / PR3: a destructive-revision confirmation is bound to what it destroys."""
from __future__ import annotations

import importlib.util
import pathlib
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

    def _check(self, confirmation, *, phase="implementation", status="in_progress", active_revision="rev-active"):
        return ri.check_revision_confirmation(
            self.repo, requirement_id=REQ, source_revision=self.source_revision,
            phase=phase, status=status, confirmation=confirmation, active_revision=active_revision)

    def test_missing_confirmation_is_refused(self):
        result = self._check(None)
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_REQUIRED", result["error"])

    def test_confirmation_for_another_requirement_is_not_reusable(self):
        result = self._check({"requirement_id": "generic:source:docs/other.md",
                              "source_revision": "rev-active",
                              "phase": "implementation", "status": "in_progress"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_MISMATCH", result["error"])

    def test_confirmation_must_be_reissued_after_the_phase_moves(self):
        result = self._check({"requirement_id": REQ, "source_revision": "rev-active",
                              "phase": "discovery", "status": "in_progress"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_SUPERSEDED", result["error"])
        self.assertEqual("confirm_requirement_revision", result["next_action"])

    def test_confirmation_must_be_reissued_after_the_status_moves(self):
        result = self._check({"requirement_id": REQ, "source_revision": "rev-active",
                              "phase": "implementation", "status": "blocked"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_SUPERSEDED", result["error"])

    def test_stale_source_revision_is_not_accepted(self):
        (self.repo / "docs" / "spec.md").write_text("# Spec\n\nlogin with jwt and refresh tokens\n", encoding="utf-8")
        moved = ri.requirement_content_revision(self.repo, "docs/spec.md")["source_revision"]
        result = ri.check_revision_confirmation(
            self.repo, requirement_id=REQ, source_revision=moved, phase="implementation", status="in_progress",
            confirmation={"requirement_id": REQ, "source_revision": "rev-stale-old",
                          "phase": "implementation", "status": "in_progress"})
        self.assertFalse(result["allowed"])
        self.assertEqual("REVISION_CONFIRMATION_SUPERSEDED", result["error"])

    def test_a_properly_bound_confirmation_is_accepted(self):
        result = self._check({"requirement_id": REQ, "source_revision": "rev-active",
                              "phase": "implementation", "status": "in_progress"})
        self.assertTrue(result["allowed"])


if __name__ == "__main__":
    unittest.main()
