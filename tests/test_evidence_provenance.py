"""Phase A / PR1: evidence must be verifiable, not self-declared.

These tests encode contract-evidence-trust.md. Core verification is exercised for real:
no mocking of the verifier, the dependency derivation, or the record store.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("evidence_provenance", ROOT / "scripts" / "evidence_provenance.py")
ep = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader
_SPEC.loader.exec_module(ep)


def _deps(*extra: dict) -> list:
    base = [
        {"object_kind": "code_under_test", "object_id": "src/app.py", "revision": "sha:aaa"},
        {"object_kind": "requirement_revision", "object_id": "req:1", "revision": "rev-1"},
    ]
    return base + list(extra)


def _report(path: Path, *, status: str, exit_code: int = 0) -> None:
    path.write_text(json.dumps({"status": status, "exit_code": exit_code}), encoding="utf-8")


class EvidenceProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    # --- three independent dimensions -------------------------------------------------

    def test_kind_status_and_outcome_are_independent_dimensions(self):
        self.assertTrue(ep.EVIDENCE_KINDS)
        self.assertEqual({"unverified", "verified", "invalid"}, ep.VALIDATION_STATUS)
        # validation_status must never encode a source type
        self.assertFalse(ep.VALIDATION_STATUS & ep.EVIDENCE_KINDS)

    def test_real_failure_from_trusted_source_is_verified_failed_not_invalid(self):
        _report(self.repo / "report.json", status="failed", exit_code=1)
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="failed", producer="pytest",
            depends_on=_deps(), report={"path": "report.json"},
        )
        inputs = ep.collect_verification_inputs(self.repo, record)
        result = ep.verify(record, inputs=inputs)
        self.assertEqual("verified", result["validation_status"])
        self.assertEqual("failed", result["outcome"])
        self.assertIsNone(result["reason_code"])

    def test_claimed_passed_against_failed_report_is_not_accepted(self):
        _report(self.repo / "report.json", status="failed", exit_code=1)
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest",
            depends_on=_deps(), report={"path": "report.json"},
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("verified", result["validation_status"], "a real failure is valid evidence")
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("EVIDENCE_REPORT_CONTRADICTION", result["reason_code"])

    def test_missing_report_cannot_back_a_passed_claim(self):
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest",
            depends_on=_deps(), report={"path": "missing.json"},
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_REPORT_UNPARSABLE", result["reason_code"])

    # --- recorded fields never establish trust ----------------------------------------

    def test_swapping_kind_verifier_or_producer_cannot_upgrade_an_unverified_claim(self):
        variants = [
            {"kind": "agent_claim"},
            {"kind": "agent_claim", "verifier": "human"},
            {"kind": "human_approval", "producer": "tech-lead", "verifier": "tech-lead"},
            {"kind": "native_result", "producer": "bmad"},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                record = ep.build_record(
                    kind=variant["kind"], claim_type="verification", work_item_id="W1",
                    requirement_revision="rev-1", outcome="passed",
                    producer=variant.get("producer", "agent"),
                    verifier=variant.get("verifier"),
                    source={"type": "agent_statement", "ref": "chat"},
                    depends_on=_deps(),
                )
                result = ep.revalidate(self.repo, record)
                self.assertNotEqual("verified", result["validation_status"])

    def test_recorded_fields_alone_do_not_grant_authority(self):
        record = ep.build_record(
            kind="agent_claim", claim_type="verification", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="agent",
            source={"type": "authoritative", "ref": "self-declared"},
            depends_on=_deps(), extra={"strength": "authoritative", "actor": "agent"},
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_UNVERIFIED", result["reason_code"])

    # --- verifier-owned dependencies ---------------------------------------------------

    def test_required_dependencies_are_derived_by_the_verifier(self):
        self.assertIn("code_under_test",
                      ep.required_dependencies(kind="mechanical_observation", claim_type="test_result"))
        self.assertIn("requirement_revision",
                      ep.required_dependencies(kind="agent_claim", claim_type="fact_resolution"))

    def test_dropping_the_code_dependency_does_not_extend_validity(self):
        _report(self.repo / "report.json", status="passed", exit_code=0)
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest",
            depends_on=[{"object_kind": "requirement_revision", "object_id": "req:1", "revision": "rev-1"}],
            report={"path": "report.json"},
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_DEPENDENCY_MISSING", result["reason_code"])

    def test_empty_dependency_set_does_not_extend_validity(self):
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest", depends_on=[],
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_DEPENDENCY_MISSING", result["reason_code"])

    def test_substituting_the_dependency_object_drifts_the_evidence(self):
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest", depends_on=_deps(),
        )
        inputs = {"current_revisions": {
            "code_under_test": {"object_id": "src/other.py", "revision": "sha:aaa"}}, "report": None}
        result = ep.verify(record, inputs=inputs)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_DEPENDENCY_DRIFT", result["reason_code"])

    def test_dependency_must_bind_a_concrete_revision(self):
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest",
            depends_on=[{"object_kind": "code_under_test", "object_id": "src/app.py"},
                        {"object_kind": "requirement_revision", "object_id": "req:1", "revision": "rev-1"}],
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])

    def test_evidence_cannot_depend_on_itself(self):
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest", depends_on=_deps(),
        )
        record["depends_on"].append({"object_kind": "evidence", "object_id": ep.evidence_id(record),
                                     "revision": "x"})
        result = ep.revalidate(self.repo, record)
        self.assertEqual("EVIDENCE_SELF_REFERENTIAL", result["reason_code"])

    # --- scope and fixture identity ----------------------------------------------------

    def test_evidence_for_another_work_item_is_rejected(self):
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="OTHER",
            requirement_revision="rev-1", outcome="passed", producer="pytest", depends_on=_deps(),
        )
        result = ep.revalidate(self.repo, record, work_item_id="W1")
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_SCOPE_MISMATCH", result["reason_code"])

    def test_fixture_identity_is_rejected(self):
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest",
            source={"type": "fixture", "ref": "tests/fixtures/report.json"}, depends_on=_deps(),
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("EVIDENCE_FIXTURE_IDENTITY_REJECTED", result["reason_code"])

    # --- readiness has per-key source rules --------------------------------------------

    def test_readiness_keys_have_independent_source_rules(self):
        self.assertIn("sdd_ready", ep.READINESS_SOURCE_RULES)
        self.assertIn("acceptance_satisfied", ep.READINESS_SOURCE_RULES)

    def test_agent_claim_does_not_satisfy_sdd_ready(self):
        self.assertFalse(ep.readiness_kind_allowed("sdd_ready", "agent_claim"))
        self.assertTrue(ep.readiness_kind_allowed("sdd_ready", "native_result"))

    def test_acceptance_present_is_not_acceptance_satisfied(self):
        present = ep.READINESS_SOURCE_RULES["acceptance_criteria_present"]
        satisfied = ep.READINESS_SOURCE_RULES["acceptance_satisfied"]
        self.assertNotEqual(present.get("claim_type"), satisfied.get("claim_type"))

    # --- purity ------------------------------------------------------------------------

    def test_verify_is_deterministic_and_time_does_not_change_the_decision(self):
        _report(self.repo / "report.json", status="passed", exit_code=0)
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest",
            depends_on=_deps(), report={"path": "report.json"},
        )
        inputs = ep.collect_verification_inputs(self.repo, record)
        first = ep.verify(record, inputs=inputs, now="2026-01-01T00:00:00Z")
        second = ep.verify(record, inputs=inputs, now="2026-09-15T00:00:00Z")
        self.assertEqual(first["validation_status"], second["validation_status"])
        self.assertEqual(first["outcome"], second["outcome"])
        self.assertEqual(first["reason_code"], second["reason_code"])

    # --- immutable records, rebuildable index ------------------------------------------

    def test_records_are_content_addressed_and_immutable(self):
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest", depends_on=_deps(),
        )
        first = ep.evidence_id(record)
        mutated = dict(record, producer="someone-else")
        self.assertNotEqual(first, ep.evidence_id(mutated))

    def test_orphan_record_is_legal_and_index_is_rebuildable(self):
        _report(self.repo / "report.json", status="passed", exit_code=0)
        record = ep.build_record(
            kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
            requirement_revision="rev-1", outcome="passed", producer="pytest",
            depends_on=_deps(), report={"path": "report.json"},
        )
        result = ep.revalidate(self.repo, record)
        ep.persist_verification(self.repo, result, checked_at="2026-09-15T00:00:00Z")
        self.assertEqual(1, len(ep.load_index(self.repo)["records"]))
        # index lost (interrupted write): orphan record remains and the index is rebuilt
        (self.repo / ep.INDEX_REL).unlink()
        self.assertEqual({}, ep.load_index(self.repo)["records"])
        rebuilt = ep.rebuild_index(self.repo)
        self.assertEqual(1, len(rebuilt["records"]))


if __name__ == "__main__":
    unittest.main()
