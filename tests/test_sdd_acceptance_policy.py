"""Pure SDD final acceptance contracts, not proof of native Skill execution."""
from copy import deepcopy
import unittest

try:
    from scripts import sdd_acceptance_policy as policy
except ImportError:
    policy = None


class AcceptancePolicyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(policy, "Independent SDD acceptance policy is missing")
        self.declaration = {
            "spec": "_bmad-output/spec.md", "sources": [], "outputs": [],
            "criteria": [
                {"id": "AC-1", "source": "_bmad-output/spec.md", "kind": "requirement", "description": "Login succeeds"},
                {"id": "DC-1", "source": "_bmad-output/design.md", "kind": "design", "description": "Passwords remain protected"},
            ],
            "executor": {"mode": "command", "entrypoint": "python check_acceptance.py"},
        }
        self.report = {
            "run_id": "sdda-current", "input_sha256": "frozen-sha", "provider": "bmad",
            "items": [
                {"id": "AC-1", "status": "passed", "evidence": [{"path": "tests/test_login.py", "reason": "Actual login assertion passed"}]},
                {"id": "DC-1", "status": "passed", "evidence": [{"path": "src/passwords.py", "reason": "Reviewed password protection"}]},
            ],
        }

    def normalize_report(self, raw):
        return policy.normalize_report(raw, policy.normalize_declaration(self.declaration),
                                       "sdda-current", "frozen-sha", "bmad")

    def test_declaration_normalizes_sources_timeout_without_mutating_input(self):
        before = deepcopy(self.declaration)
        d = policy.normalize_declaration(self.declaration)
        self.assertEqual(d["sources"], ["_bmad-output/design.md", "_bmad-output/spec.md"])
        self.assertEqual(d["executor"]["timeout_seconds"], 300.0)
        self.assertEqual(self.declaration, before)
        d["criteria"][0]["description"] = "Other"
        self.assertEqual(self.declaration, before)

    def test_light_can_bind_one_spec_with_no_extra_documents_or_plan(self):
        self.declaration["criteria"] = self.declaration["criteria"][:1]
        d = policy.normalize_declaration(self.declaration)
        self.assertEqual(d["sources"], [d["spec"]])
        self.assertEqual(d["outputs"], [])
        self.assertNotIn("plan", d)

    def test_skill_mode_uses_explicit_native_entrypoint(self):
        self.declaration["executor"] = {"mode": "skill", "entrypoint": "bmad-review"}
        self.assertEqual(policy.normalize_declaration(self.declaration)["executor"]["mode"], "skill")

    def test_invalid_declaration_shapes_and_text_are_not_coerced(self):
        values = [None, [], True]
        for value in values:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_DECLARATION_INVALID"):
                policy.normalize_declaration(value)
        for field in ("spec", "sources", "criteria", "executor", "outputs"):
            for value in (None, True, 123):
                d = deepcopy(self.declaration)
                d[field] = value
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_DECLARATION_INVALID"):
                    policy.normalize_declaration(d)

    def test_non_portable_and_traversal_source_paths_rejected(self):
        for path in ("/tmp/spec.md", "../spec.md", "a/../spec.md", "a/./spec.md", "a//spec.md", "C:/spec.md", "C:spec.md", "a\\spec.md", ".", "a/", "x\x00.md"):
            d = deepcopy(self.declaration)
            d["spec"] = path
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_DECLARATION_INVALID"):
                policy.normalize_declaration(d)

    def test_empty_duplicate_and_invalid_criteria_rejected(self):
        cases = [[], [self.declaration["criteria"][0]] * 2, ["AC-1"]]
        for field, value in (("id", " "), ("id", True), ("kind", "review"), ("description", ""), ("source", "../../spec.md")):
            item = deepcopy(self.declaration["criteria"][0])
            item[field] = value
            cases.append([item])
        cases.append([self.declaration["criteria"][1]])  # Design alone cannot replace the requirement.
        for criteria in cases:
            d = deepcopy(self.declaration)
            d["criteria"] = criteria
            with self.subTest(criteria=criteria), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_DECLARATION_INVALID"):
                policy.normalize_declaration(d)

    def test_executor_mode_text_and_limits_rejected(self):
        for field, value in (("mode", "manual"), ("mode", True), ("entrypoint", " "), ("entrypoint", 1),
                             ("timeout_seconds", 0), ("timeout_seconds", True), ("timeout_seconds", float("inf")), ("timeout_seconds", "300")):
            d = deepcopy(self.declaration)
            d["executor"][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_DECLARATION_INVALID"):
                policy.normalize_declaration(d)

    def test_output_source_overlap_and_duplicates_rejected(self):
        for outputs in (["_bmad-output/spec.md"], ["_bmad-output/design.md"], ["report.json", "report.json"], ["../report.json"]):
            d = deepcopy(self.declaration)
            d["outputs"] = outputs
            with self.subTest(outputs=outputs), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_DECLARATION_INVALID"):
                policy.normalize_declaration(d)

    def test_canonical_hash_tracks_all_declared_standards(self):
        d = policy.normalize_declaration(self.declaration)
        initial = policy.declaration_hash(d)
        self.assertEqual(initial, policy.declaration_hash(dict(reversed(list(d.items())))))
        for change in ("description", "entrypoint", "outputs", "timeout"):
            other = deepcopy(d)
            if change == "description": other["criteria"][0]["description"] = "Changed criterion"
            elif change == "entrypoint": other["executor"]["entrypoint"] = "python other.py"
            elif change == "outputs": other["outputs"] = ["report.json"]
            else: other["executor"]["timeout_seconds"] = 12
            with self.subTest(change=change): self.assertNotEqual(initial, policy.declaration_hash(other))

    def test_report_coverage_follows_expected_order_without_mutating_report(self):
        self.report["items"].reverse()
        before = deepcopy(self.report)
        results = self.normalize_report(self.report)
        self.assertEqual([item["id"] for item in results], ["AC-1", "DC-1"])
        self.assertEqual(self.report, before)
        results[0]["evidence"][0]["reason"] = "Changed"
        self.assertEqual(self.report, before)

    def test_report_wrong_run_inputs_or_provider_rejected(self):
        for field in ("run_id", "input_sha256", "provider"):
            r = deepcopy(self.report)
            r[field] = "wrong"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_RESULT_IDENTITY"):
                self.normalize_report(r)

    def test_overall_claim_does_not_fill_missing_duplicate_or_unknown_ids(self):
        cases = [[], self.report["items"][:1], [self.report["items"][0]] * 2]
        unknown = deepcopy(self.report["items"])
        unknown[0]["id"] = "UNKNOWN"
        cases.append(unknown)
        for items in cases:
            r = {**self.report, "items": items, "overall": "passed"}
            with self.subTest(items=items), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_RESULT_COVERAGE"):
                self.normalize_report(r)

    def test_report_malformed_items_status_and_evidence_rejected(self):
        for raw in (None, [], {"items": []}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.normalize_report(raw)
        for field, value in (("id", True), ("status", "skipped"), ("status", True), ("evidence", []),
                             ("evidence", ["trust me"]), ("evidence", [{"path": "src.py", "reason": " "}]),
                             ("evidence", [{"path": "../outside", "reason": "checked"}])):
            r = deepcopy(self.report)
            r["items"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_RESULT_INVALID"):
                self.normalize_report(r)

    def test_aggregate_respects_failures_and_actual_exit(self):
        for items, code, expected in (([{"status": "passed"}], 0, "passed"),
                                      ([{"status": "failed"}], 0, "failed"),
                                      ([{"status": "blocked"}], 0, "blocked"),
                                      ([{"status": "passed"}], 1, "failed"),
                                      ([{"status": "passed"}], 2, "blocked"),
                                      ([{"status": "passed"}], 124, "error"),
                                      ([{"status": "passed"}], -9, "error"), ([], 0, "blocked")):
            with self.subTest(code=code, items=items):
                self.assertEqual(policy.result_state(items, code), expected)
        for code in (True, "0", None):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_RESULT_INVALID"):
                policy.result_state([{"status": "passed"}], code)

    def test_invalid_result_status_cannot_aggregate_to_pass(self):
        for results in (None, True, "passed", ["passed"], [{}], [{"status": "skipped"}], [{"status": True}]):
            with self.subTest(results=results):
                try:
                    state = policy.result_state(results, 0)
                except ValueError as exc:
                    self.assertEqual(str(exc), "SDD_ACCEPTANCE_RESULT_INVALID")
                except (TypeError, KeyError) as exc:
                    self.fail(f"Invalid results need a structured rejection, not {type(exc).__name__}")
                else:
                    self.fail(f"Invalid results were accepted as {state}")

    def test_freshness_binds_inputs_and_all_evidence_and_output_bytes(self):
        inputs = {"requirement_sha256": "req", "code_snapshot": "code", "source_hashes": {"spec.md": "spec"}}
        hashes = {"src.py": "source", "report.json": "report"}
        run = {"status": "passed", "inputs": deepcopy(inputs), "input_sha256": policy.digest(inputs),
               "evidence_hashes": {"src.py": "source"}, "output_hashes": {"report.json": "report"}}
        self.assertTrue(policy.is_fresh(run, inputs, hashes))
        self.assertFalse(policy.is_fresh(None, inputs, hashes))
        for field in ("code_snapshot", "requirement_sha256", "source_hashes"):
            other = deepcopy(inputs)
            other[field] = "changed"
            with self.subTest(field=field): self.assertFalse(policy.is_fresh(run, other, hashes))
        for changed in ({"src.py": "changed", "report.json": "report"}, {"src.py": "source"}, {**hashes, "unknown": "x"}):
            self.assertFalse(policy.is_fresh(run, inputs, changed))
        for state in ("running", "failed", "blocked", "stale", "pending"):
            self.assertFalse(policy.is_fresh({**run, "status": state}, inputs, hashes))


if __name__ == "__main__":
    unittest.main()
