"""Phase A / PR2: every supported write path must consult the same native projection.

Front-end `native-sync`, the bottom-level `sync-native` path (`sm.sync_native`) and the
natively authoritative `transition` / `set_progress` are treated as one surface: any of them
that trusted a restated value instead of parsed content must fail here.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evidence_provenance as ep
import execution_state_manager as sm
import native_state_parser as nsp
from test_enforcement_kernel import EnforcementFixture


SPRINT = "development_status:\n  story-1: {status}\n"


class NativeEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.fx = EnforcementFixture()
        self.addCleanup(self.fx.close)

    def _state(self):
        return sm._load(self.fx.state_path)

    def _make_native(self, status: str, *, mode: str = "native", ref: str = "sprint-status.yaml") -> str:
        (self.fx.repo / ref).write_text(SPRINT.format(status=status), encoding="utf-8")
        state = self._state()
        state["authority"]["mode"] = mode
        state["work_item"]["id"] = "story-1"
        state["authority"]["native_state_ref"] = ref
        self._persist(state)
        return ref

    def _persist(self, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
        self.fx.state_path.write_text(payload + "\n", encoding="utf-8")

    def _projection(self):
        state = self._state()
        projection, diag = nsp.resolve_projection(self.fx.repo, state)
        self.assertTrue(diag == {}, diag)
        return projection

    # --- caller may only declare expectations, never project them -----------------------

    def test_implementation_target_is_refused_when_native_says_ready_for_dev(self):
        self._make_native("ready-for-dev")
        with self.assertRaises(sm.StateError) as caught:
            sm.sync_native(self.fx.state_path, "implementation", "in_progress", "agent", "sprint-status.yaml")
        self.assertIn("NATIVE_PROJECTION_MISMATCH", str(caught.exception))

    def test_review_target_is_refused_when_native_has_not_moved(self):
        self._make_native("ready-for-dev")
        with self.assertRaises(sm.StateError) as caught:
            sm.sync_native(self.fx.state_path, "review", "in_progress", "agent", "sprint-status.yaml")
        self.assertIn("NATIVE_PROJECTION_MISMATCH", str(caught.exception))

    def test_declared_revision_must_match_the_parsed_digest(self):
        self._make_native("in-progress")
        with self.assertRaises(sm.StateError) as caught:
            sm.sync_native(self.fx.state_path, "implementation", "in_progress", "agent",
                           "sprint-status.yaml", native_revision="sha:claimed-by-the-caller")
        self.assertIn("NATIVE_STATE_REVISION_MISMATCH", str(caught.exception))

    def test_missing_native_source_is_reported_not_defaulted(self):
        state = self._state()
        state["authority"]["mode"] = "native"
        state["work_item"]["id"] = "story-1"
        self._persist(state)
        with self.assertRaises(sm.StateError) as caught:
            sm.sync_native(self.fx.state_path, "implementation", "in_progress", "agent", "sprint-status.yaml")
        self.assertIn("NATIVE_SOURCE_UNCONFIGURED", str(caught.exception))

    # --- observation is always recorded -------------------------------------------------

    def test_a_matching_projection_records_the_observation(self):
        self._make_native("in-progress")
        result = sm.sync_native(self.fx.state_path, "implementation", "in_progress", "agent",
                                "sprint-status.yaml")
        self.assertEqual("story-1", result["native_observation"]["work_item_id"])
        self.assertEqual(self._projection().content_digest,
                         result["native_observation"]["content_digest"])
        observation = self._state()["authority"]["native_observation"]
        self.assertEqual("implementation", observation["phase"])
        self.assertEqual(observation["content_digest"],
                         self._state()["authority"]["last_native_sync"]["native_revision"])

    # --- native authority owns transition and progress ------------------------------------

    def test_progress_must_match_the_parsed_native_tasks(self):
        body = ("development_status:\n"
                "  story-1:\n"
                "    status: in-progress\n"
                "    tasks:\n"
                "      a: true\n"
                "      b: false\n")
        (self.fx.repo / "sprint-status.yaml").write_text(body, encoding="utf-8")
        state = self._state()
        state["authority"]["mode"] = "native"
        state["work_item"]["id"] = "story-1"
        state["authority"]["native_state_ref"] = "sprint-status.yaml"
        self._persist(state)
        with self.assertRaises(sm.NativeAuthorityRequired) as caught:
            sm.set_progress(self.fx.state_path, 2, 2, "agent", "evidence.json", native_confirmed=True)
        self.assertIn("work_item.progress", str(caught.exception))

    def test_transition_under_native_authority_must_be_projected(self):
        self._make_native("ready-for-dev")
        with self.assertRaises(sm.NativeAuthorityRequired) as caught:
            sm.transition(self.fx.state_path, "implementation", "in_progress", "agent",
                          "native projects something else", native_confirmed=True)
        self.assertIn("native authority owns phase/status", str(caught.exception))

    def test_native_confirmed_alone_does_not_grant_a_projection(self):
        self._make_native("in-progress")
        with self.assertRaises(sm.NativeAuthorityRequired) as caught:
            sm.transition(self.fx.state_path, "closed", "completed", "agent",
                          "caller claims native completion", native_confirmed=True)
        self.assertIn("not closed/completed", str(caught.exception))

    # --- governance Divergence -----------------------------------------------------------

    def _invalid_evidence(self) -> dict:
        state = self._state()
        return ep.build_record(
            kind="mechanical_observation", claim_type="test_result",
            work_item_id=state.get("work_item", {}).get("id") or "story-1",
            requirement_revision="rev-1", outcome="passed",
            depends_on=[{"object_kind": "code_under_test", "object_id": "src/app.py", "revision": "sha:aaa"},
                        {"object_kind": "requirement_revision", "object_id": "req:1", "revision": "rev-1"}],
            report={"path": "missing-report.json"})

    def test_native_done_does_not_close_governance_when_authorization_fails(self):
        self._make_native("done")
        state = self._state()
        state["evidence"] = {"records": [self._invalid_evidence()]}
        self._persist(state)
        result = sm.sync_native(self.fx.state_path, "closed", "completed", "agent", "sprint-status.yaml")
        self.assertFalse(result["governance_applied"])
        after = self._state()
        self.assertNotEqual("closed", after["phase"])
        self.assertIsNone(after.get("completion_record"))
        self.assertEqual("closed/completed", after["authority"]["native_divergence"]["native"])
        # the native fact itself is never dropped
        self.assertEqual("closed", after["authority"]["native_observation"]["phase"])


if __name__ == "__main__":
    unittest.main()
