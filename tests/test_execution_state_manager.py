import importlib.util
import json
import pathlib
import tempfile
import unittest

from governance_fixture import attach_fixture_analysis
import evidence_factory

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("execution_state_manager", ROOT / "scripts" / "execution_state_manager.py")
sm = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(sm)

DSPEC = importlib.util.spec_from_file_location("state_provider_detector", ROOT / "scripts" / "state_provider_detector.py")
detector = importlib.util.module_from_spec(DSPEC)
assert DSPEC.loader
DSPEC.loader.exec_module(detector)


def _write_native(root: pathlib.Path, work_item_id: str, status: str, *,
                  completed: int | None = None, total: int | None = None,
                  rel: str = "sprint-status.yaml") -> pathlib.Path:
    """Write a real native status source; native authority is parsed, never restated."""
    path = root / rel
    lines = ["development_status:", f"  {work_item_id}:"]
    if completed is None:
        path.write_text(
            "development_status:\n" + f"  {work_item_id}: {status}\n", encoding="utf-8")
        return path
    tasks = [f"      task-{i}: {'true' if i <= completed else 'false'}" for i in range(1, total + 1)]
    path.write_text(
        "development_status:\n" + f"  {work_item_id}:\n    status: {status}\n    tasks:\n"
        + "\n".join(tasks) + "\n", encoding="utf-8")
    return path


class StateFixture:
    def __init__(self, flow="STANDARD", provider="generic", mode="orchestrator"):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state_path = self.root / ".orchestrator" / "execution-state.yaml"
        state = sm.create_state("W-1", "Example work", flow, provider=provider, authority_mode=mode)
        sm.initialize(self.state_path, state, "test")

    def close(self):
        self.tmp.cleanup()

    def state(self):
        return sm._load(self.state_path)

    def set_ready_for_impl(self):
        s = self.state()
        evidence_factory.establish_readiness(self.state_path, "behavior_change")
        s = self.state()
        evidence_factory.establish_readiness(self.state_path, "acceptance_criteria_present")
        s = self.state()
        evidence_factory.establish_readiness(self.state_path, "sdd_ready")
        attach_fixture_analysis(sm, self.state_path)


class ExecutionStateTests(unittest.TestCase):
    def test_init_standard_requires_review(self):
        fx = StateFixture(flow="STANDARD")
        try:
            s = fx.state()
            self.assertTrue(s["review"]["required"])
            self.assertEqual("pending", s["review"]["status"])
        finally:
            fx.close()

    def test_blocker_is_modifier_and_preserves_phase(self):
        fx = StateFixture()
        try:
            before = fx.state()
            sm.add_blocker(fx.state_path, "dependency", "waiting on API", "agent", expected_revision=before["revision"])
            s = fx.state()
            self.assertTrue(s["blocked"])
            self.assertEqual("discovery", s["phase"])
            self.assertEqual("in_progress", s["status"])
        finally:
            fx.close()

    def test_implementation_requires_sdd_ready_and_acceptance_for_behavior(self):
        fx = StateFixture()
        try:
            s = fx.state()
            evidence_factory.establish_readiness(fx.state_path, "behavior_change")
            s = fx.state()
            with self.assertRaises(sm.TransitionDenied):
                sm.transition(fx.state_path, "implementation", "in_progress", "agent", "start", s["revision"])
            fx.set_ready_for_impl()
            s = fx.state()
            sm.transition(fx.state_path, "implementation", "in_progress", "agent", "ready", s["revision"])
            self.assertEqual("implementation", fx.state()["phase"])
        finally:
            fx.close()

    def test_standard_cannot_skip_required_review(self):
        fx = StateFixture(flow="STANDARD")
        try:
            fx.set_ready_for_impl()
            s = fx.state()
            sm.transition(fx.state_path, "implementation", "in_progress", "agent", "start", s["revision"])
            s = fx.state()
            evidence_factory.establish_readiness(fx.state_path, "implementation_tasks_complete")
            s = fx.state()
            with self.assertRaises(sm.TransitionDenied):
                sm.transition(fx.state_path, "verification", "in_progress", "agent", "skip review", s["revision"])
            s = fx.state()
            sm.transition(fx.state_path, "review", "in_progress", "agent", "review", s["revision"])
            s = fx.state()
            sm.record_review(fx.state_path, "passed", "reviewer", 0, "review:1", s["revision"])
            s = fx.state()
            sm.transition(fx.state_path, "verification", "in_progress", "agent", "review passed", s["revision"])
            self.assertEqual("verification", fx.state()["phase"])
        finally:
            fx.close()

    def test_close_denied_when_required_gate_failed(self):
        fx = StateFixture(flow="FAST")
        try:
            fx.set_ready_for_impl()
            s = fx.state()
            sm.transition(fx.state_path, "implementation", "in_progress", "agent", "start", s["revision"])
            s = fx.state()
            evidence_factory.establish_readiness(fx.state_path, "implementation_tasks_complete")
            s = fx.state()
            sm.transition(fx.state_path, "verification", "in_progress", "agent", "verify", s["revision"])
            s = fx.state()
            evidence_factory.establish_readiness(fx.state_path, "acceptance_satisfied")
            s = fx.state()
            sm.record_gate(fx.state_path, "unit_test", True, "failed", "ci", evidence_ref="ci:1", expected_revision=s["revision"])
            s = fx.state()
            sm.set_execution_snapshot(fx.state_path, "snap-1", "agent", "final code", s["revision"])
            s = fx.state()
            sm.record_verification(fx.state_path, "passed", "verifier", "snap-1", "verify:1", s["revision"])
            s = fx.state()
            with self.assertRaises(sm.TransitionDenied) as ctx:
                sm.transition(fx.state_path, "closed", "completed", "agent", "done", s["revision"])
            self.assertIn("unit_test", str(ctx.exception))
        finally:
            fx.close()

    def test_required_gate_cannot_be_skipped(self):
        fx = StateFixture()
        try:
            s = fx.state()
            with self.assertRaises(sm.StateError):
                sm.record_gate(fx.state_path, "semgrep", True, "skipped", "agent", expected_revision=s["revision"])
        finally:
            fx.close()

    def test_snapshot_change_invalidates_fresh_verification(self):
        fx = StateFixture(flow="FAST")
        try:
            s = fx.state()
            sm.set_execution_snapshot(fx.state_path, "snap-1", "agent", "code", s["revision"])
            s = fx.state()
            sm.record_verification(fx.state_path, "passed", "verifier", "snap-1", "v1", s["revision"])
            self.assertTrue(fx.state()["verification"]["fresh"])
            s = fx.state()
            sm.set_execution_snapshot(fx.state_path, "snap-2", "agent", "code changed", s["revision"])
            self.assertFalse(fx.state()["verification"]["fresh"])
        finally:
            fx.close()

    def test_optimistic_revision_rejects_stale_agent(self):
        fx = StateFixture()
        try:
            stale = fx.state()["revision"]
            sm.assign_role(fx.state_path, "implementer", "agent-a", "orchestrator", stale)
            with self.assertRaises(sm.RevisionConflict):
                sm.assign_role(fx.state_path, "reviewer", "agent-b", "orchestrator", stale)
        finally:
            fx.close()

    def test_history_is_append_only_and_contains_revision_edges(self):
        fx = StateFixture()
        try:
            s = fx.state()
            sm.assign_role(fx.state_path, "implementer", "agent-a", "orchestrator", s["revision"])
            s = fx.state()
            sm.add_blocker(fx.state_path, "dependency", "x", "agent-a", expected_revision=s["revision"])
            hp = sm._history_path(fx.state_path)
            events = [json.loads(x) for x in hp.read_text().splitlines()]
            self.assertEqual(3, len(events))  # initialize + 2 mutations
            self.assertEqual("STATE_INITIALIZED", events[0]["event"])
            self.assertEqual("ROLE_ASSIGNED", events[1]["event"])
            self.assertEqual(0, events[1]["revision_before"])
            self.assertEqual(1, events[1]["revision_after"])
        finally:
            fx.close()

    def test_native_authority_rejects_direct_transition(self):
        fx = StateFixture(provider="bmad", mode="native")
        try:
            fx.set_ready_for_impl()
            s = fx.state()
            with self.assertRaises(sm.NativeAuthorityRequired):
                sm.transition(fx.state_path, "implementation", "in_progress", "agent", "direct", s["revision"])
            s = fx.state()
            # The native advance is itself a repository change, so the analysis is refreshed
            # before governance is allowed to follow it.
            _write_native(fx.root, s["work_item"]["id"], "in-progress", completed=1, total=3)
            attach_fixture_analysis(sm, fx.state_path)
            s = fx.state()
            sm.sync_native(fx.state_path, "implementation", "in_progress", "bmad-adapter", "sprint-status.yaml", None, 1, 3, s["revision"])
            self.assertEqual("implementation", fx.state()["phase"])
            self.assertEqual({"completed": 1, "total": 3}, fx.state()["work_item"]["progress"])
        finally:
            fx.close()

    def test_native_closed_does_not_mean_governance_done(self):
        fx = StateFixture(provider="bmad", mode="native")
        try:
            s = fx.state()
            _write_native(fx.root, s["work_item"]["id"], "done", completed=1, total=1)
            sm.sync_native(fx.state_path, "closed", "completed", "bmad-adapter", "sprint-status.yaml", None, 1, 1, s["revision"])
            summary = sm.resume_summary(fx.state(), fx.root)
            self.assertTrue(summary["completion"]["native_or_canonical_closed"])
            self.assertFalse(summary["completion"]["done"])
            self.assertTrue(summary["completion"]["close_guard_failures"])
        finally:
            fx.close()

    def test_resume_summary_contains_next_action(self):
        fx = StateFixture(flow="FAST")
        try:
            fx.set_ready_for_impl()
            summary = sm.resume_summary(fx.state(), fx.root)
            self.assertEqual("transition_to_implementation", summary["next_action"])
            self.assertIn("revision", summary)
            self.assertIn("authority", summary)
        finally:
            fx.close()

    def test_fast_can_skip_optional_review_but_not_verification(self):
        fx = StateFixture(flow="FAST")
        try:
            fx.set_ready_for_impl()
            s = fx.state()
            sm.transition(fx.state_path, "implementation", "in_progress", "agent", "start", s["revision"])
            s = fx.state()
            evidence_factory.establish_readiness(fx.state_path, "implementation_tasks_complete")
            s = fx.state()
            sm.transition(fx.state_path, "verification", "in_progress", "agent", "review optional", s["revision"])
            self.assertEqual("verification", fx.state()["phase"])
            s = fx.state()
            evidence_factory.establish_readiness(fx.state_path, "acceptance_satisfied")
            s = fx.state()
            with self.assertRaises(sm.TransitionDenied):
                sm.transition(fx.state_path, "closed", "completed", "agent", "no verification", s["revision"])
        finally:
            fx.close()


class ProviderDetectorTests(unittest.TestCase):
    def test_bmad_sprint_status_prefers_native(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            d = root / "_bmad-output"
            d.mkdir()
            (d / "sprint-status.yaml").write_text("development_status: {}\n")
            result = detector.detect(root)
            self.assertEqual("DETECTED", result["status"])
            self.assertEqual("bmad", result["preferred"]["provider"])
            self.assertEqual("native", result["preferred"]["authority_mode"])

    def test_openspec_defaults_to_hybrid(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "openspec").mkdir()
            result = detector.detect(root)
            self.assertEqual("openspec", result["preferred"]["provider"])
            self.assertEqual("hybrid", result["preferred"]["authority_mode"])

    def test_no_native_provider_uses_orchestrator(self):
        with tempfile.TemporaryDirectory() as td:
            result = detector.detect(pathlib.Path(td))
            self.assertEqual("generic", result["preferred"]["provider"])
            self.assertEqual("orchestrator", result["preferred"]["authority_mode"])

    def test_multiple_candidates_requires_authority_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "openspec").mkdir()
            d = root / "_bmad-output"
            d.mkdir()
            (d / "sprint-status.yaml").write_text("development_status: {}\n")
            result = detector.detect(root)
            self.assertEqual("MULTIPLE_CANDIDATES", result["status"])
            self.assertTrue(result["requires_authority_resolution"])


if __name__ == "__main__":
    unittest.main()
