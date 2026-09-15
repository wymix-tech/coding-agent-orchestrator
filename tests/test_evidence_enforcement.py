"""Phase A / PR1: the new evidence layer must actually change authorization outcomes.

These are integration tests against the real state manager, action guard and fact resolver.
No mocking of authorization, verification, or dependency derivation.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import action_guard as guard
import evidence_provenance as ep
import execution_state_manager as sm
import fact_resolver
from test_enforcement_kernel import EnforcementFixture


CODE_DEP = {"object_kind": "code_under_test", "object_id": "src/app.py", "revision": "sha:aaa"}
NATIVE_SOURCE_DEP = {"object_kind": "native_source", "object_id": "bmad:story-1", "revision": "n1"}


def _deps(extra=None):
    deps = [{"object_kind": "requirement_revision", "object_id": "req:1", "revision": "rev-1"}]
    deps.extend(extra or [])
    return deps


class EvidenceEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.fx = EnforcementFixture()
        self.addCleanup(self.fx.close)

    def _state(self):
        return sm._load(self.fx.state_path)

    def _record(self, *, kind="agent_claim", claim_type="readiness", work_item_id=None,
                depends_on=None, **kwargs):
        return ep.build_record(
            kind=kind, claim_type=claim_type,
            work_item_id=work_item_id or self._state().get("work_item_id") or "W1",
            requirement_revision="rev-1",
            depends_on=_deps() if depends_on is None else depends_on, **kwargs)

    def _report(self, name="report.json", status="failed", exit_code=1):
        path = self.fx.repo / name
        path.write_text(json.dumps({"status": status, "exit_code": exit_code}), encoding="utf-8")
        return name

    # --- consumption-time revalidation -----------------------------------------------

    def test_invalid_bound_evidence_denies_a_guarded_action(self):
        state = self._state()
        bad = self._record(kind="mechanical_observation", claim_type="test_result", outcome="passed",
                           depends_on=_deps([CODE_DEP]), report={"path": "missing-report.json"})
        state.setdefault("evidence", {})["records"] = [bad]
        evidence = guard.collect_evidence(self.fx.repo, state)
        self.assertEqual(1, len(evidence["evidence_invalid"]))
        self.assertEqual("EVIDENCE_REPORT_UNPARSABLE", evidence["evidence_invalid"][0]["reason_code"])
        result = guard.evaluate(state, "mutate_code", evidence=evidence)
        self.assertIn("EVIDENCE_INVALID", [r["code"] for r in result["reasons"]])

    def test_round_tripping_an_evidence_record_cannot_create_trust(self):
        state = self._state()
        claim = self._record(outcome="passed", source={"type": "agent_statement", "ref": "chat"})
        state.setdefault("evidence", {})["records"] = [claim]
        evidence = guard.collect_evidence(self.fx.repo, state)
        self.assertEqual([], evidence["evidence_invalid"])
        self.assertEqual(1, len(evidence["evidence_unverified"]))
        result = guard.evaluate(state, "mutate_code", evidence=evidence)
        self.assertNotIn("EVIDENCE_INVALID", [r["code"] for r in result["reasons"]])

    def test_evidence_of_another_work_item_is_not_used_here(self):
        state = self._state()
        state["work_item_id"] = "WI-1"
        foreign = dict(self._record(work_item_id="SOMEONE-ELSE"))
        state.setdefault("evidence", {})["records"] = [foreign]
        evidence = guard.collect_evidence(self.fx.repo, state)
        self.assertEqual("EVIDENCE_SCOPE_MISMATCH", evidence["evidence_invalid"][0]["reason_code"])

    # --- gate and verification results must bind the report that ran ------------------

    def test_gate_cannot_record_passed_against_a_failed_report(self):
        report = self._report(status="failed", exit_code=1)
        with self.assertRaises(sm.StateError) as caught:
            sm.record_gate(self.fx.state_path, "tests", True, "passed", "tester", report_path=report)
        self.assertIn("EVIDENCE_REPORT_CONTRADICTION", str(caught.exception))

    def test_gate_records_the_real_failure_as_verified_failure(self):
        report = self._report(status="failed", exit_code=1)
        sm.record_gate(self.fx.state_path, "tests", True, "failed", "tester", report_path=report)
        gate = self._state()["quality_gates"]["tests"]
        self.assertEqual("failed", gate["status"])
        self.assertEqual("failed", gate["report"]["status"])
        self.assertEqual(1, gate["exit_code"])
        self.assertTrue(gate["report"]["digest"])

    def test_verification_cannot_record_passed_against_a_failed_report(self):
        report = self._report(status="failed", exit_code=2)
        state = self._state()
        with self.assertRaises(sm.StateError) as caught:
            sm.record_verification(self.fx.state_path, "passed", "verifier",
                                   state.get("execution_snapshot_id") or "snap", report,
                                   report_path=report)
        self.assertIn("EVIDENCE_REPORT_CONTRADICTION", str(caught.exception))

    def test_verification_binds_a_passed_report(self):
        report = self._report(name="passed.json", status="passed", exit_code=0)
        state = self._state()
        sm.record_verification(self.fx.state_path, "passed", "verifier",
                               state.get("execution_snapshot_id") or "snap", report,
                               report_path=report)
        verification = self._state()["verification"]
        self.assertEqual("passed", verification["status"])
        self.assertEqual("passed", verification["report"]["status"])

    def test_unreadable_report_is_rejected_not_assumed(self):
        with self.assertRaises(sm.StateError) as caught:
            sm.record_gate(self.fx.state_path, "tests", True, "passed", "tester",
                           report_path="does-not-exist.json")
        self.assertIn("EVIDENCE_REPORT_UNPARSABLE", str(caught.exception))

    # --- readiness has per-key source rules -------------------------------------------

    def test_readiness_rejects_a_kind_that_cannot_satisfy_the_key(self):
        record = self._record(kind="agent_claim", outcome="passed")
        with self.assertRaises(sm.StateError) as caught:
            sm.set_readiness(self.fx.state_path, "sdd_ready", True, "agent", ref := "evidence.json",
                             evidence_record=record)
        self.assertIn("cannot be satisfied by", str(caught.exception))

    def test_readiness_accepts_a_kind_allowed_for_the_key(self):
        record = self._record(kind="native_result", claim_type="readiness", outcome="passed",
                              depends_on=_deps([NATIVE_SOURCE_DEP]),
                              approval={"source": "bmad", "subject": "story-1", "revision": "n1"})
        sm.set_readiness(self.fx.state_path, "acceptance_criteria_present", True, "orchestrator",
                         "evidence.json", evidence_record=record)
        state = self._state()
        self.assertEqual(1, len(state["evidence"]["records"]))
        self.assertTrue(state["readiness_evidence"]["acceptance_criteria_present"]["evidence_id"])

    # --- resolutions cannot rest on placeholders --------------------------------------

    def test_resolution_rejects_placeholder_evidence(self):
        draft = {"scope": {"public_contract_change": None}}
        doc = {"resolutions": [{
            "path": "scope.public_contract_change", "value": True,
            "source_type": "agent", "source": "request", "evidence": "   ",
            "strength": "authoritative",
        }]}
        with self.assertRaises(ValueError):
            fact_resolver.apply_resolutions(draft, doc)

    def test_resolution_without_verifiable_evidence_is_marked_self_declared(self):
        draft = {"scope": {"public_contract_change": None}}
        doc = {"resolutions": [{
            "path": "scope.public_contract_change", "value": True,
            "source_type": "agent", "source": "request", "evidence": "claim",
            "strength": "authoritative",
        }]}
        out = fact_resolver.apply_resolutions(draft, doc)
        self.assertEqual("unverified", out["resolution"]["validation"][0]["validation_status"])
        self.assertEqual("self_declared", out["resolution"]["validation"][0]["authority"])

    def test_authoritative_resolution_requires_verified_evidence_record(self):
        draft = {"scope": {"public_contract_change": None}}
        record = ep.build_record(kind="agent_claim", claim_type="fact_resolution", work_item_id="W1",
                                 requirement_revision="rev-1", outcome="passed",
                                 depends_on=_deps())
        doc = {"resolutions": [{
            "path": "scope.public_contract_change", "value": True,
            "source_type": "agent", "source": "request", "evidence": "claim",
            "strength": "authoritative", "evidence_record": record,
        }]}
        with self.assertRaises(ValueError) as caught:
            fact_resolver.apply_resolutions(draft, doc, repo=self.fx.repo)
        self.assertIn("requires verified evidence", str(caught.exception))

    # --- interrupted writes stay recoverable -------------------------------------------

    def test_isolated_repo_keeps_orphan_records_and_rebuilds_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            ep.persist_verification(repo, ep.revalidate(repo, self._record()), checked_at="t0")
            (repo / ep.INDEX_REL).unlink()
            index = ep.rebuild_index(repo)
            self.assertEqual(1, len(index["records"]))


if __name__ == "__main__":
    unittest.main()
