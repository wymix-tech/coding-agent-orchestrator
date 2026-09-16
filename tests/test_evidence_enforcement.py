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
import evidence_factory
import evidence_provenance as ep
import execution_state_manager as sm
import fact_resolver
from test_enforcement_kernel import EnforcementFixture


def _deps(repo, extra=None):
    """Dependencies that really resolve in `repo`: the objects exist, the revisions are observed."""
    deps = [evidence_factory.requirement_dependency(repo)]
    deps.extend(extra or [])
    return deps


class EvidenceEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.fx = EnforcementFixture()
        self.addCleanup(self.fx.close)

    def _state(self):
        return sm._load(self.fx.state_path)

    def _record(self, *, kind="agent_claim", claim_type="readiness", work_item_id=None,
                requirement_revision=None, depends_on=None, **kwargs):
        # Scope is read from the state, not invented: a record that names a different work item
        # or a revision that is not the active one must be out of scope.
        state = self._state()
        work_item = state.get("work_item") or {}
        return ep.build_record(
            kind=kind, claim_type=claim_type,
            work_item_id=work_item_id or work_item.get("id") or state.get("work_item_id") or "W1",
            requirement_revision=(requirement_revision if requirement_revision is not None
                                  else work_item.get("requirement_revision")),
            depends_on=_deps(self.fx.repo) if depends_on is None else depends_on, **kwargs)

    def _report(self, name="report.json", status="failed", exit_code=1,
                command="python3 -m pytest -q"):
        # A result is what a command produced: the report names the command, so the binding
        # stays re-readable instead of being a status someone wrote down.
        path = self.fx.repo / name
        path.write_text(json.dumps({"status": status, "exit_code": exit_code,
                                    "command": command}), encoding="utf-8")
        return name

    # --- consumption-time revalidation -----------------------------------------------

    def test_invalid_bound_evidence_denies_a_guarded_action(self):
        state = self._state()
        bad = self._record(kind="mechanical_observation", claim_type="test_result", outcome="passed",
                           depends_on=_deps(self.fx.repo, [evidence_factory.code_dependency(
                               self.fx.repo)]), report={"path": "missing-report.json"})
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

    def test_a_gate_passed_against_a_report_that_later_moved_binds_nothing(self):
        """R2: the passing status is stored history; the source is re-read when the gate is used."""
        report = self._report(status="passed", exit_code=0)
        sm.record_gate(self.fx.state_path, "tests", True, "passed", "tester", report_path=report)
        (self.fx.repo / report).write_text(
            json.dumps({"status": "failed", "exit_code": 1, "command": "pytest -q"}), encoding="utf-8")
        evidence = guard.collect_evidence(self.fx.repo, self._state())
        broken = {item["name"]: item["error"] for item in evidence["result_bindings_unverified"]["gates"]}
        self.assertEqual({"tests": "EVIDENCE_RESULT_DRIFT"}, broken)

    def test_a_gate_without_a_passing_source_closes_nothing(self):
        """R2: a gate whose result cannot be reproduced is not a gate that passed."""
        report = self._report(status="passed", exit_code=0)
        sm.record_gate(self.fx.state_path, "tests", True, "passed", "tester", report_path=report)
        state = self._state()
        state["phase"] = "release"
        state.setdefault("readiness", {})["implementation_tasks_complete"] = True
        state.setdefault("readiness", {})["acceptance_satisfied"] = True
        (self.fx.repo / report).write_text(
            json.dumps({"status": "failed", "exit_code": 1, "command": "pytest -q"}), encoding="utf-8")
        evidence = guard.collect_evidence(self.fx.repo, state)
        result = guard.evaluate(state, "close", evidence=evidence)
        self.assertIn("GATE_RESULT_UNVERIFIABLE", result["reason_codes"])

    def test_a_review_whose_result_source_moved_supports_nothing(self):
        """R2: a review is a result too, so it is re-read like any other."""
        report = self._report(name="review.json", status="passed", exit_code=0)
        sm.record_review(self.fx.state_path, "passed", "reviewer", report_path=report, exit_code=0)
        state = self._state()
        state["phase"] = "review"
        state.setdefault("readiness", {})["implementation_tasks_complete"] = True
        state["review"]["required"] = True
        (self.fx.repo / report).write_text(
            json.dumps({"status": "failed", "exit_code": 1, "command": "pytest -q"}), encoding="utf-8")
        evidence = guard.collect_evidence(self.fx.repo, state)
        self.assertEqual("EVIDENCE_RESULT_DRIFT", evidence["result_bindings_unverified"]["review"]["error"])
        result = guard.evaluate(state, "advance", target_phase="verification", evidence=evidence)
        self.assertIn("REVIEW_RESULT_UNVERIFIABLE", result["reason_codes"])

    # --- readiness has per-key source rules -------------------------------------------

    def test_readiness_is_only_worth_its_last_verification(self):
        """R1: a verdict stored when the evidence was written is history, not a current result."""
        evidence_factory.establish_readiness(self.fx.state_path, "behavior_change", repo=self.fx.repo)
        state = self._state()
        self.assertTrue(state["readiness"]["behavior_change"])
        evidence_id = state["readiness_evidence"]["behavior_change"]["evidence_id"]
        # This run revalidated nothing, so no stored verdict can support any key now.
        unsupported = guard.unsupported_readiness(state, {})
        true_keys = sorted(key for key, value in (state.get("readiness") or {}).items() if value is True)
        self.assertEqual(true_keys, sorted(item["key"] for item in unsupported))
        self.assertEqual({"EVIDENCE_UNVERIFIED"}, {item["error"] for item in unsupported})
        # Re-read in this run it is what it says: the same evidence, verified now.
        evidence = guard.collect_evidence(self.fx.repo, state)
        self.assertIn(evidence_id, evidence["evidence_results"])
        self.assertEqual([], [item["key"] for item in evidence["readiness_unsupported"]])

    def test_readiness_rejects_a_kind_that_cannot_satisfy_the_key(self):
        record = self._record(kind="agent_claim", outcome="passed")
        with self.assertRaises(sm.StateError) as caught:
            sm.set_readiness(self.fx.state_path, "sdd_ready", True, "agent", ref := "evidence.json",
                             evidence_record=record)
        self.assertIn("cannot be satisfied by", str(caught.exception))

    def test_readiness_accepts_a_kind_allowed_for_the_key(self):
        # The native decision is read from the file that holds it, not cited by name, and it
        # must name the native revision that is really there.
        work_item_id = (self._state().get("work_item") or {}).get("id") or "W1"
        native = evidence_factory.native_dependency(self.fx.repo)
        approvals = evidence_factory.write_approval(self.fx.repo, approval_id=native["revision"],
                                                    subject=work_item_id, approver="bmad")
        record = self._record(kind="native_result", claim_type="readiness", outcome="passed",
                              depends_on=_deps(self.fx.repo, [native]),
                              approval={"source": approvals, "subject": work_item_id,
                                        "revision": native["revision"]})
        sm.set_readiness(self.fx.state_path, "acceptance_criteria_present", True, "orchestrator",
                         "evidence.json", evidence_record=record)
        state = self._state()
        evidence_id = state["readiness_evidence"]["acceptance_criteria_present"]["evidence_id"]
        self.assertTrue(evidence_id)
        self.assertIn(evidence_id, [r.get("evidence_id") for r in state["evidence"]["records"]])

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
                                 depends_on=_deps(self.fx.repo))
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
