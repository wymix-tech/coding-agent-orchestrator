"""Phase A / PR1: evidence must be verifiable, not self-declared.

These tests encode contract-evidence-trust.md. Core verification is exercised for real: the
verifier, the dependency resolver, the record store and the source adapters are never mocked.
Dependencies name objects that really exist in the temp repository, so every "verified" result
here was reached by resolving something, and every refusal names the object that failed.
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


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _deps(repo: Path, *extra: dict) -> list:
    """Dependencies that resolve against real objects in `repo`, resolved the way the verifier does.

    Existing files are left alone: a test may have written the requirement it cares about, and
    rebuilding the baseline would silently change what the revision means.
    """
    if not (repo / "src/app.py").exists():
        _write(repo / "src/app.py", "def handle():\n    return 200\n")
    if not (repo / "docs/story.md").exists():
        _write(repo / "docs/story.md", "# Story\n\nReturn 200 for a valid token.\n")
    base = [ep.resolve_dependency(repo, bare) | bare
            for bare in ({"object_kind": "code_under_test", "object_id": "src/app.py"},
                         {"object_kind": "requirement_revision", "object_id": "docs/story.md"})]
    for dep in base:
        dep.pop("resolved", None)
        dep.pop("error", None)
        dep.pop("message", None)
        dep.pop("object_ref", None)
    return base + list(extra)


def _report(repo: Path, *, status: str, exit_code: int | None = 0,
            command: str | None = "python3 -m pytest tests", name: str = "report.json") -> dict:
    payload: dict = {"status": status}
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if command is not None:
        payload["command"] = command
    path = repo / name
    _write(path, json.dumps(payload))
    return {"path": name}


def _passed_record(repo: Path, *, report_body: str | None = None, **overrides) -> dict:
    """A mechanical claim about a report. `report_body` lets a test rewrite what was produced."""
    record = ep.build_record(
        kind="mechanical_observation", claim_type="test_result", work_item_id="W1",
        outcome="passed", producer="pytest", depends_on=_deps(repo),
        report=_report(repo, status="passed"),
    )
    if report_body is not None:
        _write(repo / "report.json", report_body)
    record.update(overrides)
    return record


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
        record = _passed_record(
            self.repo, outcome="failed",
            report_body=json.dumps({"status": "failed", "exit_code": 1,
                                    "command": "python3 -m pytest tests"}))
        result = ep.revalidate(self.repo, record)
        self.assertEqual("verified", result["validation_status"])
        self.assertEqual("failed", result["outcome"])
        self.assertIsNone(result["reason_code"])

    def test_claimed_passed_against_failed_report_is_not_accepted(self):
        record = _passed_record(
            self.repo,
            report_body=json.dumps({"status": "failed", "exit_code": 1,
                                    "command": "python3 -m pytest tests"}))
        result = ep.revalidate(self.repo, record)
        self.assertEqual("verified", result["validation_status"], "a real failure is valid evidence")
        self.assertEqual("failed", result["outcome"])
        self.assertEqual("EVIDENCE_REPORT_CONTRADICTION", result["reason_code"])

    def test_missing_report_cannot_back_a_passed_claim(self):
        record = _passed_record(self.repo, report={"path": "missing.json"})
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
                    outcome="passed", producer=variant.get("producer", "agent"),
                    verifier=variant.get("verifier"), source={"type": "agent_statement", "ref": "chat"},
                    depends_on=_deps(self.repo),
                )
                result = ep.revalidate(self.repo, record)
                self.assertNotEqual("verified", result["validation_status"])

    def test_recorded_fields_alone_do_not_grant_authority(self):
        record = ep.build_record(
            kind="agent_claim", claim_type="verification", work_item_id="W1", outcome="passed",
            producer="agent", source={"type": "authoritative", "ref": "self-declared"},
            depends_on=_deps(self.repo), extra={"strength": "authoritative", "actor": "agent"},
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
        record = _passed_record(self.repo, depends_on=[d for d in _deps(self.repo)
                                                       if d["object_kind"] != "code_under_test"])
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_DEPENDENCY_MISSING", result["reason_code"])

    def test_empty_dependency_set_does_not_extend_validity(self):
        record = _passed_record(self.repo, depends_on=[])
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_DEPENDENCY_MISSING", result["reason_code"])

    def test_dependency_must_bind_a_concrete_revision(self):
        record = _passed_record(self.repo)
        record["depends_on"][0].pop("revision")
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])

    def test_evidence_cannot_depend_on_itself(self):
        record = _passed_record(self.repo)
        record["depends_on"].append({"object_kind": "evidence", "object_id": ep.evidence_id(record),
                                     "revision": "x"})
        result = ep.revalidate(self.repo, record)
        self.assertEqual("EVIDENCE_SELF_REFERENTIAL", result["reason_code"])

    # --- freshness is resolved against the project, never echoed from the claim ---------

    def test_dependency_revisions_are_observed_not_copied(self):
        """F3: current revisions come from the objects themselves."""
        record = _passed_record(self.repo)
        inputs = ep.collect_verification_inputs(self.repo, record)
        for dep in record["depends_on"]:
            observation = inputs["current_revisions"][f"{dep['object_kind']}::{dep['object_id']}"]
            self.assertTrue(observation["resolved"], dep)
            self.assertEqual(dep["revision"], observation["revision"])

    def test_a_missing_dependency_object_is_not_verified(self):
        record = _passed_record(self.repo)
        (self.repo / "src/app.py").unlink()
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_DEPENDENCY_UNRESOLVED", result["reason_code"])

    def test_changing_the_code_under_test_drifts_the_evidence(self):
        record = _passed_record(self.repo)
        self.assertEqual("verified", ep.revalidate(self.repo, record)["validation_status"])
        _write(self.repo / "src/app.py", "def handle():\n    return 418\n")
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_DEPENDENCY_DRIFT", result["reason_code"])
        self.assertIn("code_under_test", result["drifted_dependency"])

    def test_runtime_only_edits_to_the_requirement_do_not_drift_the_evidence(self):
        """Tick-boxes inside Tasks are not requirement changes, so the evidence still holds."""
        story = "# Story\n\nReturn 200 for a valid token.\n\n## Acceptance Criteria\n\nReturn 200.\n\n## Tasks\n\n- [ ] handle endpoint\n"
        _write(self.repo / "docs/story.md", story)
        record = _passed_record(self.repo)
        self.assertEqual("verified", ep.revalidate(self.repo, record)["validation_status"])
        _write(self.repo / "docs/story.md", story.replace("- [ ] handle endpoint", "- [x] handle endpoint"))
        self.assertEqual("verified", ep.revalidate(self.repo, record)["validation_status"])
        _write(self.repo / "docs/story.md",
               story.replace("Return 200.", "Return 201.").replace("- [ ]", "- [x]"))
        self.assertEqual("EVIDENCE_DEPENDENCY_DRIFT", ep.revalidate(self.repo, record)["reason_code"])

    def test_two_objects_of_the_same_kind_are_checked_individually(self):
        _write(self.repo / "src/other.py", "def other():\n    return 1\n")
        extra = ep.resolve_dependency(self.repo, {"object_kind": "code_under_test",
                                                  "object_id": "src/other.py"})
        record = _passed_record(self.repo)
        record["depends_on"].append({"object_kind": "code_under_test", "object_id": "src/other.py",
                                     "revision": extra["revision"]})
        self.assertEqual("verified", ep.revalidate(self.repo, record)["validation_status"])
        _write(self.repo / "src/other.py", "def other():\n    return 2\n")
        result = ep.revalidate(self.repo, record)
        self.assertEqual("EVIDENCE_DEPENDENCY_DRIFT", result["reason_code"])
        self.assertIn("src/other.py", result["drifted_dependency"])

    def test_replacing_the_report_changes_its_digest(self):
        record = _passed_record(self.repo)
        record["report"]["digest"] = __import__("hashlib").sha256(
            (self.repo / "report.json").read_bytes()).hexdigest()
        self.assertEqual("verified", ep.revalidate(self.repo, record)["validation_status"])
        _write(self.repo / "report.json", json.dumps({"status": "passed", "exit_code": 0,
                                                      "command": "python3 -m pytest tests",
                                                      "note": "edited"}))
        self.assertEqual("EVIDENCE_REPORT_DRIFT", ep.revalidate(self.repo, record)["reason_code"])

    # --- trust adapters: reports and approvals are read, not believed -------------------

    def test_an_empty_report_is_not_a_passed_verdict(self):
        result = ep.revalidate(self.repo, _passed_record(self.repo, report_body="{}"))
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_EXECUTION_UNBOUND", result["reason_code"])

    def test_a_report_without_exit_code_or_command_is_not_a_passed_verdict(self):
        result = ep.revalidate(self.repo, _passed_record(self.repo,
                                                          report_body=json.dumps({"status": "passed"})))
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_EXECUTION_UNBOUND", result["reason_code"])

    def test_a_report_with_an_unknown_status_is_not_a_passed_verdict(self):
        result = ep.revalidate(self.repo, _passed_record(self.repo, report_body=json.dumps(
            {"status": "green", "exit_code": 0, "command": "make test"})))
        self.assertEqual("unverified", result["validation_status"])

    def test_a_self_filled_approval_source_is_not_an_approval(self):
        record = ep.build_record(
            kind="human_approval", claim_type="approval", work_item_id="W1", outcome="approved",
            producer="agent", depends_on=_deps(self.repo),
            approval={"source": "does/not/exist.json", "subject": "W1", "revision": "appr-1"},
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_APPROVAL_UNRESOLVED", result["reason_code"])

    def test_an_approval_is_read_from_its_source_file(self):
        # Who may approve is published by the project. Without that configuration a name is
        # just a name, and a name that differs from the producer proves no authority.
        _write(self.repo / ".orchestrator/config.yaml",
               "orchestrator:\n  evidence:\n    approval_authorities:\n      - reviewer-1\n")
        _write(self.repo / ".orchestrator/approvals.json", json.dumps({"approvals": [
            {"id": "appr-1", "subject": "W1", "decision": "approved", "approver": "reviewer-1"}]}))
        record = ep.build_record(
            kind="human_approval", claim_type="approval", work_item_id="W1", outcome="approved",
            producer="agent", depends_on=_deps(self.repo),
            approval={"source": ".orchestrator/approvals.json", "subject": "W1", "revision": "appr-1"},
        )
        self.assertEqual("verified", ep.revalidate(self.repo, record)["validation_status"])

    def test_an_approval_for_another_subject_does_not_apply(self):
        _write(self.repo / ".orchestrator/approvals.json", json.dumps({"approvals": [
            {"id": "appr-1", "subject": "OTHER", "decision": "approved", "approver": "reviewer-1"}]}))
        record = ep.build_record(
            kind="human_approval", claim_type="approval", work_item_id="W1", outcome="approved",
            producer="agent", depends_on=_deps(self.repo),
            approval={"source": ".orchestrator/approvals.json", "subject": "W1", "revision": "appr-1"},
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_APPROVAL_SUBJECT_MISMATCH", result["reason_code"])

    def test_self_approval_is_not_an_authority(self):
        _write(self.repo / ".orchestrator/approvals.json", json.dumps({"approvals": [
            {"id": "appr-1", "subject": "W1", "decision": "approved", "approver": "agent"}]}))
        record = ep.build_record(
            kind="human_approval", claim_type="approval", work_item_id="W1", outcome="approved",
            producer="agent", depends_on=_deps(self.repo),
            approval={"source": ".orchestrator/approvals.json", "subject": "W1", "revision": "appr-1"},
        )
        result = ep.revalidate(self.repo, record)
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_APPROVER_NOT_AUTHORIZED", result["reason_code"])

    # --- scope ------------------------------------------------------------------------

    def test_evidence_for_another_work_item_is_rejected(self):
        record = _passed_record(self.repo, work_item_id="OTHER")
        result = ep.revalidate(self.repo, record, work_item_id="W1")
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_SCOPE_MISMATCH", result["reason_code"])

    def test_fixture_identity_is_rejected(self):
        record = _passed_record(self.repo, source={"type": "fixture", "ref": "tests/fixtures/report.json"})
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

    def test_verified_without_a_success_outcome_does_not_satisfy_a_key(self):
        result = {"kind": "mechanical_observation", "claim_type": "verification",
                  "validation_status": "verified", "outcome": "failed"}
        decision = ep.readiness_decision("acceptance_satisfied", result)
        self.assertFalse(decision["allowed"])
        self.assertEqual("EVIDENCE_OUTCOME_NOT_SUCCESS", decision["error"])

    def test_a_wrong_claim_type_does_not_satisfy_a_key(self):
        result = {"kind": "mechanical_observation", "claim_type": "readiness",
                  "validation_status": "verified", "outcome": "passed"}
        decision = ep.readiness_decision("acceptance_satisfied", result)
        self.assertFalse(decision["allowed"])
        self.assertEqual("EVIDENCE_CLAIM_TYPE_MISMATCH", decision["error"])

    # --- purity ------------------------------------------------------------------------

    def test_verify_is_deterministic_and_time_does_not_change_the_decision(self):
        record = _passed_record(self.repo)
        inputs = ep.collect_verification_inputs(self.repo, record)
        first = ep.verify(record, inputs=inputs, now="2026-01-01T00:00:00Z")
        second = ep.verify(record, inputs=inputs, now="2026-09-15T00:00:00Z")
        self.assertEqual(first["validation_status"], second["validation_status"])
        self.assertEqual(first["outcome"], second["outcome"])
        self.assertEqual(first["reason_code"], second["reason_code"])

    # --- immutable originals, mutable checks, rebuildable index --------------------------

    def test_records_are_content_addressed_and_immutable(self):
        record = _passed_record(self.repo)
        first = ep.evidence_id(record)
        self.assertNotEqual(first, ep.evidence_id(dict(record, producer="someone-else")))

    def test_checking_again_does_not_overwrite_the_original_record(self):
        """F10: the raw claim is written once; checks are separate records."""
        record = _passed_record(self.repo)
        stored = ep.store_evidence(self.repo, record, checked_at="2026-09-15T00:00:00Z")
        path = ep.records_dir(self.repo) / f"{stored['record']['evidence_id']}.json"
        first_bytes = path.read_bytes()
        ep.store_evidence(self.repo, record, checked_at="2026-09-16T00:00:00Z")
        self.assertEqual(first_bytes, path.read_bytes())
        self.assertEqual(2, len(list(ep.checks_dir(self.repo).glob(f"{stored['record']['evidence_id']}.*.json"))))

    def test_a_different_verdict_is_a_different_identity_and_never_overwrites(self):
        passed = _passed_record(self.repo)
        failed = _passed_record(self.repo, outcome="failed", report_body=json.dumps(
            {"status": "failed", "exit_code": 1, "command": "python3 -m pytest tests"}))
        self.assertNotEqual(ep.evidence_id(passed), ep.evidence_id(failed))
        ep.store_evidence(self.repo, passed, checked_at="2026-09-15T00:00:00Z")
        passed_bytes = (ep.records_dir(self.repo) / f"{ep.evidence_id(passed)}.json").read_bytes()
        ep.store_evidence(self.repo, failed, checked_at="2026-09-15T00:00:00Z")
        self.assertEqual(passed_bytes,
                         (ep.records_dir(self.repo) / f"{ep.evidence_id(passed)}.json").read_bytes())
        self.assertEqual(2, len(ep.load_index(self.repo)["records"]))

    def test_the_stored_record_carries_everything_needed_to_recheck_it(self):
        record = _passed_record(self.repo)
        stored = ep.store_evidence(self.repo, record, checked_at="2026-09-15T00:00:00Z")
        payload = ep.load_record(self.repo, stored["record"]["evidence_id"])
        self.assertEqual("W1", payload["work_item_id"])
        self.assertTrue(payload["report"]["path"])
        self.assertTrue(payload["depends_on"])

    def test_orphan_record_is_legal_and_index_is_rebuildable(self):
        record = _passed_record(self.repo)
        ep.store_evidence(self.repo, record, checked_at="2026-09-15T00:00:00Z")
        self.assertEqual(1, len(ep.load_index(self.repo)["records"]))
        # index lost (interrupted write): orphan record remains and the index is rebuilt
        (self.repo / ep.INDEX_REL).unlink()
        self.assertEqual({}, ep.load_index(self.repo)["records"])
        rebuilt = ep.rebuild_index(self.repo)
        self.assertEqual(1, len(rebuilt["records"]))


class ApprovalAuthorityTests(unittest.TestCase):
    """R3: an approval is trust only against the project's published authorities."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _approvals(self, *entries: dict) -> None:
        _write(self.repo / ".orchestrator/approvals.json",
               json.dumps({"approvals": list(entries) or [
                   {"id": "appr-1", "subject": "W1", "decision": "approved", "approver": "reviewer-1"}]}))

    def _record(self, **overrides) -> dict:
        record = ep.build_record(
            kind="human_approval", claim_type="approval", work_item_id="W1", outcome="approved",
            producer="agent", depends_on=_deps(self.repo),
            approval={"source": ".orchestrator/approvals.json", "subject": "W1", "revision": "appr-1"},
        )
        record.update(overrides)
        return record

    def test_a_name_that_is_not_a_published_authority_is_not_trust(self):
        self._approvals()
        result = ep.revalidate(self.repo, self._record())
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_APPROVAL_AUTHORITY_UNCONFIGURED", result["reason_code"])

    def test_the_project_names_who_may_approve(self):
        _write(self.repo / ".orchestrator/config.yaml",
               "orchestrator:\n  evidence:\n    approval_authorities:\n      - reviewer-1\n")
        self._approvals()
        self.assertEqual("verified", ep.revalidate(self.repo, self._record())["validation_status"])

    def test_an_approval_must_bind_the_revision_it_approves(self):
        _write(self.repo / ".orchestrator/config.yaml",
               "orchestrator:\n  evidence:\n    approval_authorities:\n      - reviewer-1\n")
        self._approvals()
        result = ep.revalidate(self.repo, self._record(requirement_revision="rev-1"),
                               work_item_id="W1", requirement_revision="rev-1")
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_APPROVAL_REVISION_UNBOUND", result["reason_code"])

    def test_an_approval_for_another_revision_does_not_cover_this_one(self):
        _write(self.repo / ".orchestrator/config.yaml",
               "orchestrator:\n  evidence:\n    approval_authorities:\n      - reviewer-1\n")
        self._approvals({"id": "appr-1", "subject": "W1", "decision": "approved",
                         "approver": "reviewer-1", "requirement_revision": "rev-old"})
        result = ep.revalidate(self.repo, self._record(requirement_revision="rev-1"),
                               work_item_id="W1", requirement_revision="rev-1")
        self.assertEqual("unverified", result["validation_status"])
        self.assertEqual("EVIDENCE_APPROVAL_REVISION_MISMATCH", result["reason_code"])


class ObjectUnderTestTests(unittest.TestCase):
    """R4: the object a mechanical claim ran against is code, and its sources must be readable."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _write(self.repo / "src/app.py", "def handle():\n    return 200\n")

    def test_a_document_is_not_the_code_under_test(self):
        """Prose is a legal requirement source; it is never the thing a gate ran against."""
        _write(self.repo / "docs/story.md", "# Story\n\nReturn 200 for a valid token.\n")
        _write(self.repo / "docs/spec.md", "# Spec\n\nReturn 200 for a valid token.\n")
        deps = [{"object_kind": "code_under_test", "object_id": "docs/story.md",
                 "revision": ep.path_revision(self.repo, "docs/story.md")},
                dict(ep.resolve_dependency(self.repo, {"object_kind": "requirement_revision",
                                                       "object_id": "docs/spec.md"})
                     | {"object_kind": "requirement_revision", "object_id": "docs/spec.md"})]
        record = ep.build_record(kind="mechanical_observation", claim_type="test_result",
                                 work_item_id="W1", outcome="passed", producer="pytest",
                                 depends_on=deps, report=_report(self.repo, status="passed"))
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_OBJECT_NOT_CODE", result["reason_code"])
        self.assertEqual("docs/story.md", result["non_code_object"])

    def test_a_requirement_source_that_cannot_be_read_is_unresolved(self):
        """A missing requirement is never substituted by whatever the state declares."""
        record = _passed_record(self.repo)
        # The requirement the claim names is gone now: nothing may stand in for it.
        (self.repo / "docs/story.md").unlink()
        result = ep.revalidate(self.repo, record)
        self.assertEqual("invalid", result["validation_status"])
        self.assertEqual("EVIDENCE_DEPENDENCY_UNRESOLVED", result["reason_code"])


class ResultBindingTests(unittest.TestCase):
    """R2: a passed result is bound to what produced it, and re-read when it is used."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.ref = "results/unit.json"

    def _write_result(self, **overrides) -> None:
        payload = {"status": "passed", "exit_code": 0, "command": "python3 -m pytest tests"}
        payload.update(overrides)
        _write(self.repo / self.ref, json.dumps(payload))

    def _binding(self, **scope) -> dict:
        return ep.result_document_binding(self.repo, self.ref, target="gate:unit", **scope)

    def _recheck(self, binding, **scope) -> dict:
        return ep.recheck_result_binding(self.repo, binding, target="gate:unit", **scope)

    def test_a_passed_status_without_a_command_binds_nothing(self):
        self._write_result(command=None)
        result = self._binding()
        self.assertFalse(result["available"])
        self.assertEqual("EVIDENCE_RESULT_SOURCE_UNBOUND", result["error"])

    def test_a_result_that_does_not_record_exit_code_binds_nothing(self):
        self._write_result(exit_code=None)
        result = self._binding()
        self.assertFalse(result["available"])
        self.assertEqual("EVIDENCE_RESULT_SOURCE_UNBOUND", result["error"])

    def test_a_result_produced_for_another_gate_does_not_bind_this_one(self):
        self._write_result(target="gate:integration")
        result = self._binding()
        self.assertFalse(result["available"])
        self.assertEqual("EVIDENCE_RESULT_TARGET_MISMATCH", result["error"])

    def test_a_binding_from_a_command_that_ran_is_accepted(self):
        self._write_result()
        result = self._binding(work_item_id="W1", requirement_revision="rev-1", code_revision="snap-1")
        self.assertTrue(result["available"])
        self.assertEqual("command", result["binding"]["producer"]["kind"])
        self.assertTrue(self._recheck(result["binding"], work_item_id="W1",
                                      requirement_revision="rev-1")["ok"])

    def test_editing_the_result_document_invalidates_the_binding(self):
        self._write_result()
        binding = self._binding()["binding"]
        self._write_result(command="python3 -m pytest tests -k unit")
        result = self._recheck(binding)
        self.assertFalse(result["ok"])
        self.assertEqual("EVIDENCE_RESULT_DRIFT", result["error"])

    def test_a_result_document_that_stops_recording_passed_is_stale(self):
        """A binding stored without a digest still re-reads the verdict it recorded."""
        self._write_result()
        binding = dict(self._binding()["binding"])
        binding.pop("digest", None)
        self._write_result(status="failed", exit_code=1)
        result = self._recheck(binding)
        self.assertFalse(result["ok"])
        self.assertEqual("EVIDENCE_RESULT_STALE", result["error"])

    def test_deleting_the_result_document_unbinds_the_result(self):
        self._write_result()
        binding = self._binding()["binding"]
        (self.repo / self.ref).unlink()
        result = self._recheck(binding)
        self.assertFalse(result["ok"])
        self.assertEqual("EVIDENCE_RESULT_SOURCE_UNBOUND", result["error"])

    def test_a_result_of_another_work_item_does_not_carry_this_one(self):
        self._write_result()
        binding = self._binding(work_item_id="W1")["binding"]
        result = self._recheck(binding, work_item_id="W2")
        self.assertFalse(result["ok"])
        self.assertEqual("EVIDENCE_RESULT_SCOPE_MISMATCH", result["error"])

    def test_a_result_against_another_requirement_revision_is_out_of_scope(self):
        self._write_result()
        binding = self._binding(requirement_revision="rev-1")["binding"]
        result = self._recheck(binding, requirement_revision="rev-2")
        self.assertFalse(result["ok"])
        self.assertEqual("EVIDENCE_RESULT_SCOPE_MISMATCH", result["error"])


if __name__ == "__main__":
    unittest.main()
