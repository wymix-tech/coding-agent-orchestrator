"""Phase A / PR2: the boundary between a native fact and a governed decision.

"Native has advanced" is an observation that is always recorded. "Governance has advanced" is an
authorization that still has to be earned. These tests pin that boundary for implementation /
review / closed, and re-check that verification / release keep the rules they already had: the
native path is a new way to *observe*, never a new whitelist around them.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
for _path in (str(ROOT / "scripts"), str(TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import action_guard  # noqa: E402
import execution_state_manager as sm  # noqa: E402
import evidence_factory  # noqa: E402
from governance_fixture import attach_fixture_analysis  # noqa: E402


NATIVE_REF = "docs/sprint-status.yaml"


class BoundaryFixture:
    """A repository whose native source is real content, and whose analysis is real analysis."""

    def __init__(self, *, flow: str = "FAST", mode: str = "native", native_status: str = "in-progress"):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        self.state_path = self.repo / ".orchestrator" / "execution-state.yaml"
        # The code a claim may depend on: a file that exists before anything is claimed about it.
        evidence_factory.write_source(self.repo, "src/app.py")
        self.write_native(native_status)
        state = sm.create_state("W-1", "Example work", flow, provider="generic",
                                authority_mode=mode, native_state_ref=NATIVE_REF)
        sm.initialize(self.state_path, state, "test")

    # --- real material -----------------------------------------------------------------

    def write_native(self, status: str) -> str:
        path = self.repo / NATIVE_REF
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"development_status:\n  W-1: {status}\n", encoding="utf-8")
        return NATIVE_REF

    def reanalyze(self) -> None:
        """The native source is repository content: moving it must be re-analyzed, not assumed."""
        attach_fixture_analysis(sm, self.state_path)

    def enter_implementation(self) -> None:
        for key in ("behavior_change", "acceptance_criteria_present", "sdd_ready"):
            evidence_factory.establish_readiness(self.state_path, key)
        self.reanalyze()
        if self.state()["phase"] == "implementation":
            return
        if self.state()["authority"]["mode"] in {"native", "hybrid"}:
            self.sync("implementation", "in_progress")
        else:
            self.transition("implementation", "in_progress", "sdd ready")

    def readiness(self, key: str) -> None:
        evidence_factory.establish_readiness(self.state_path, key)

    # --- entry points ------------------------------------------------------------------

    def state(self) -> dict:
        return sm._load(self.state_path)

    def sync(self, phase: str, status: str) -> dict:
        return sm.sync_native(self.state_path, phase, status, "agent", NATIVE_REF)

    def transition(self, phase: str, status: str = "in_progress", reason: str = "governance") -> dict:
        state = self.state()
        return sm.transition(self.state_path, phase, status, "agent", reason, state["revision"])

    def authorize(self, action: str, **kwargs) -> dict:
        return action_guard.authorize(self.repo, self.state(), action, **kwargs)

    def close(self) -> None:
        self.tmp.cleanup()


class GovernedBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fx = BoundaryFixture()
        self.addCleanup(self.fx.close)

    # --- review -------------------------------------------------------------------------

    def test_native_review_waits_for_governance_tasks(self):
        self.fx.enter_implementation()
        self.fx.write_native("review")
        self.fx.reanalyze()

        result = self.fx.sync("review", "in_progress")
        self.assertFalse(result["governance_applied"])
        after = self.fx.state()
        self.assertEqual("implementation", after["phase"])
        self.assertIn("TASKS_INCOMPLETE", after["authority"]["native_divergence"]["reason_codes"])
        # the native fact is still recorded, even though governance refused to follow it
        self.assertEqual("review", after["authority"]["native_observation"]["phase"])

    def test_native_review_follows_once_tasks_are_really_complete(self):
        self.fx.enter_implementation()
        self.fx.readiness("implementation_tasks_complete")
        self.fx.write_native("review")
        self.fx.reanalyze()

        result = self.fx.sync("review", "in_progress")
        self.assertTrue(result["governance_applied"], result)
        self.assertEqual("review", self.fx.state()["phase"])

    # --- closed -------------------------------------------------------------------------

    def test_native_done_does_not_close_governance_without_verification(self):
        self.fx.enter_implementation()
        self.fx.readiness("implementation_tasks_complete")
        self.fx.readiness("acceptance_satisfied")
        self.fx.write_native("done")
        self.fx.reanalyze()

        result = self.fx.sync("closed", "completed")
        self.assertFalse(result["governance_applied"])
        after = self.fx.state()
        self.assertIsNone(after.get("completion_record"))
        self.assertNotEqual("closed", after["phase"])
        self.assertIn("VERIFICATION_NOT_PASSED", after["authority"]["native_divergence"]["reason_codes"])
        self.assertEqual("closed", after["authority"]["native_observation"]["phase"])

    # --- verification / release keep the rules they already had ---------------------------

    def _in_verification(self) -> BoundaryFixture:
        fx = BoundaryFixture(mode="orchestrator")
        self.addCleanup(fx.close)
        fx.enter_implementation()
        fx.readiness("implementation_tasks_complete")
        fx.transition("verification", "in_progress", "tasks complete")
        return fx

    def test_verifier_cannot_finish_without_a_recorded_outcome(self):
        fx = self._in_verification()
        denied = fx.authorize("finish_role", role="verifier")
        self.assertFalse(denied["allowed"])
        self.assertIn("ROLE_OUTCOME_MISSING", denied["reason_codes"])

    def test_release_still_requires_a_fresh_passed_verification(self):
        fx = self._in_verification()
        denied = fx.authorize("advance", target_phase="release", target_status="in_progress")
        self.assertFalse(denied["allowed"])
        self.assertIn("VERIFICATION_NOT_PASSED", denied["reason_codes"])

        report = evidence_factory.write_report(fx.repo, "final-verification.json",
                                               target="verification")
        state = fx.state()
        sm.set_execution_snapshot(fx.state_path, "snap-1", "agent", "final code", state["revision"])
        state = fx.state()
        sm.record_verification(fx.state_path, "passed", "verifier", "snap-1", report["path"],
                               state["revision"], report_path=report["path"], exit_code=0)
        fx.readiness("acceptance_satisfied")

        allowed = fx.authorize("advance", target_phase="release", target_status="in_progress")
        self.assertTrue(allowed["allowed"], allowed["reasons"])


if __name__ == "__main__":
    unittest.main()
