import copy
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("decision_engine", ROOT / "scripts" / "decision_engine.py")
engine = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(engine)


def load(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class DecisionEngineTests(unittest.TestCase):
    def test_trivial_is_trivial(self):
        result = engine.classify(load("work-facts-trivial.json"))
        self.assertEqual("CLASSIFIED", result["status"])
        self.assertEqual("TRIVIAL", result["flow_profile"])

    def test_two_line_auth_change_is_not_trivial(self):
        result = engine.classify(load("work-facts-auth.json"))
        self.assertEqual("STANDARD", result["flow_profile"])
        self.assertEqual(2, result["work_profile"]["risk"]["score"])
        self.assertTrue(any(x.startswith("O1 ") for x in result["decision_trace"]))

    def test_ambiguous_small_request_blocks_implementation(self):
        result = engine.classify(load("work-facts-ambiguous.json"))
        self.assertEqual("STANDARD", result["flow_profile"])
        self.assertIn("resolve_ambiguity_before_implementation", result["implementation_blockers"])

    def test_system_scope_maps_standard(self):
        facts = load("work-facts-trivial.json")
        facts["scope"]["modules_touched"] = 2
        facts["scope"]["files_estimate"] = 4
        facts["complexity"]["predicted_components"] = 2
        result = engine.classify(facts)
        self.assertEqual("STANDARD", result["flow_profile"])
        self.assertEqual(2, result["work_profile"]["scope"]["score"])

    def test_novel_distributed_work_maps_deep(self):
        facts = load("work-facts-trivial.json")
        facts["complexity"]["distributed_coordination"] = True
        facts["novelty"]["exact_repo_precedent"] = False
        facts["novelty"]["first_repo_use"] = True
        facts["novelty"]["new_external_dependency_or_protocol"] = True
        facts["verification"]["deterministic_local"] = False
        facts["verification"]["concurrency_or_distributed_faults"] = True
        result = engine.classify(facts)
        self.assertEqual("DEEP", result["flow_profile"])
        self.assertEqual(3, result["work_profile"]["novelty"]["score"])

    def test_four_high_dimensions_combination_rule(self):
        facts = load("work-facts-trivial.json")
        facts["complexity"]["predicted_components"] = 4  # complexity 2
        facts["scope"]["modules_touched"] = 2           # scope 2
        facts["risk"]["persistent_data_change"] = True # risk 2
        facts["verification"]["async_retry_timing"] = True  # verification 2
        result = engine.classify(facts)
        self.assertEqual("DEEP", result["flow_profile"])
        self.assertTrue(any(x.startswith("C1 ") for x in result["decision_trace"]))

    def test_missing_fact_is_needs_evidence_not_false(self):
        facts = load("work-facts-trivial.json")
        facts["risk"]["authn_authz"] = None
        result = engine.classify(facts)
        self.assertEqual("NEEDS_EVIDENCE", result["status"])
        self.assertIn("risk.authn_authz", result["missing_facts"])

    def test_policy_minimum_can_raise_flow(self):
        facts = load("work-facts-trivial.json")
        facts["policy"]["minimum_flow"] = "STANDARD"
        result = engine.classify(facts)
        self.assertEqual("STANDARD", result["flow_profile"])

    def test_policy_cannot_force_below_computed_minimum(self):
        facts = load("work-facts-auth.json")
        facts["policy"]["fixed_flow"] = "FAST"
        result = engine.classify(facts)
        self.assertEqual("POLICY_CONFLICT", result["status"])
        self.assertEqual("STANDARD", result["computed_minimum_flow"])

    def test_same_input_is_stable(self):
        facts = load("work-facts-auth.json")
        a = engine.classify(copy.deepcopy(facts))
        b = engine.classify(copy.deepcopy(facts))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
