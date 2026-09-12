import importlib
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cbm_provider
import impact_mapper
import verification_planner
import decision_engine
import execution_state_manager as sm


def fixture():
    return json.loads((ROOT / "examples" / "cbm-detect-changes-fixture.json").read_text(encoding="utf-8"))


def base_facts():
    # Reuse complete deterministic values, but make structural fields unresolved as they
    # would be before semantic resolution in the V6 pipeline.
    facts = json.loads((ROOT / "examples" / "work-facts-trivial.json").read_text(encoding="utf-8"))
    facts["complexity"]["predicted_components"] = None
    facts["complexity"]["distributed_coordination"] = None
    facts["scope"]["modules_touched"] = None
    facts["scope"]["deployable_units"] = None
    facts["scope"]["external_consumers"] = None
    facts["scope"]["public_contract_change"] = None
    facts["verification"]["deterministic_local"] = None
    facts["verification"]["requires_integration_boundary"] = None
    facts["verification"]["async_retry_timing"] = None
    facts["verification"]["compatibility_or_migration"] = None
    facts.setdefault("provenance", {})
    facts["extraction"] = {"snapshot_id": "unit", "resolution_queue": []}
    return facts


class SemanticImpactTests(unittest.TestCase):
    def test_cbm_is_structural_only(self):
        p = cbm_provider.CBMProvider()
        caps = p.capabilities()
        self.assertEqual("codebase-memory-mcp", caps["provider"])
        self.assertEqual("ignored_for_flow", caps["provider_risk"])

    def test_normalize_extracts_blast_radius_and_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            out = cbm_provider.normalize_detect_changes(fixture(), repo=pathlib.Path(td), project="ams-platform")
        self.assertEqual(1, len(out["changes"]["changed_symbols"]))
        self.assertEqual(2, out["impact"]["impacted_symbol_count"])
        self.assertEqual(["auth-service", "gateway"], out["impact"]["affected_modules"])
        self.assertTrue(out["boundaries"]["cross_service"])
        self.assertIn("HTTP_CALLS", out["boundaries"]["integration_relations"])

    def test_provider_risk_is_quarantined(self):
        with tempfile.TemporaryDirectory() as td:
            out = cbm_provider.normalize_detect_changes(fixture(), repo=pathlib.Path(td), project="ams-platform")
        self.assertIn("HIGH", out["provider_opinion"]["risk_labels"])
        self.assertFalse(out["provider_opinion"]["authoritative_for_flow"])
        self.assertNotIn("risk", out)

    def test_impact_mapper_sets_scope_and_integration_not_business_risk(self):
        with tempfile.TemporaryDirectory() as td:
            impact = cbm_provider.normalize_detect_changes(fixture(), repo=pathlib.Path(td), project="ams-platform")
        facts = impact_mapper.enrich_work_facts(base_facts(), impact)
        self.assertEqual(2, facts["scope"]["modules_touched"])
        self.assertEqual(2, facts["scope"]["deployable_units"])
        self.assertTrue(facts["verification"]["requires_integration_boundary"])
        self.assertFalse(facts["verification"]["deterministic_local"])
        self.assertFalse(facts["risk"]["authn_authz"])

    def test_false_local_fact_has_explicit_proof(self):
        with tempfile.TemporaryDirectory() as td:
            impact = cbm_provider.normalize_detect_changes(fixture(), repo=pathlib.Path(td), project="ams-platform")
        facts = impact_mapper.enrich_work_facts(base_facts(), impact)
        entries = facts["provenance"]["verification.deterministic_local"]
        self.assertTrue(entries[-1]["negative_proof"])

    def test_semantic_scope_can_supersede_narrow_file_scope(self):
        facts = base_facts()
        facts["scope"]["modules_touched"] = 1
        facts["provenance"]["scope.modules_touched"] = [{
            "value": 1, "strength": "derived", "source": "changed files", "source_type": "repo", "evidence": ["auth-service"]
        }]
        with tempfile.TemporaryDirectory() as td:
            impact = cbm_provider.normalize_detect_changes(fixture(), repo=pathlib.Path(td), project="ams-platform")
        out = impact_mapper.enrich_work_facts(facts, impact)
        self.assertEqual(2, out["scope"]["modules_touched"])
        self.assertEqual("superseded", out["provenance"]["scope.modules_touched"][0]["strength"])
        self.assertEqual("derived", out["provenance"]["scope.modules_touched"][-1]["strength"])

    def test_async_relation_raises_async_verification(self):
        raw = fixture()
        raw["edges"].append({"relationship": "ASYNC_CALLS", "source": "gateway", "target": "audit"})
        with tempfile.TemporaryDirectory() as td:
            impact = cbm_provider.normalize_detect_changes(raw, repo=pathlib.Path(td), project="ams-platform")
        facts = impact_mapper.enrich_work_facts(base_facts(), impact)
        self.assertTrue(impact["boundaries"]["async_boundary"])
        self.assertTrue(facts["verification"]["async_retry_timing"])
        self.assertTrue(facts["complexity"]["distributed_coordination"])

    def test_changed_route_is_contract_signal(self):
        raw = fixture()
        raw["changed_symbols"].append({
            "qualified_name": "POST /login", "label": "Route", "file_path": "auth-service/src/LoginRoute.java",
            "module": "auth-service", "service": "auth-service", "project": "ams-platform"
        })
        with tempfile.TemporaryDirectory() as td:
            impact = cbm_provider.normalize_detect_changes(raw, repo=pathlib.Path(td), project="ams-platform")
        facts = impact_mapper.enrich_work_facts(base_facts(), impact)
        self.assertTrue(impact["contracts"]["public_contract_signal"])
        self.assertTrue(facts["scope"]["public_contract_change"])
        self.assertTrue(facts["verification"]["compatibility_or_migration"])

    def test_partial_cbm_page_does_not_finalize_numeric_scope(self):
        raw = fixture()
        raw["next_impacted_symbols_cursor"] = "cursor-2"
        with tempfile.TemporaryDirectory() as td:
            impact = cbm_provider.normalize_detect_changes(raw, repo=pathlib.Path(td), project="ams-platform")
        self.assertFalse(impact["completeness"]["complete"])
        facts = impact_mapper.enrich_work_facts(base_facts(), impact)
        self.assertIsNone(facts["scope"]["modules_touched"])
        self.assertIsNone(facts["scope"]["deployable_units"])
        self.assertTrue(facts["verification"]["requires_integration_boundary"])

    def test_verification_plan_uses_impact_not_provider_risk(self):
        with tempfile.TemporaryDirectory() as td:
            impact = cbm_provider.normalize_detect_changes(fixture(), repo=pathlib.Path(td), project="ams-platform")
        facts = impact_mapper.enrich_work_facts(base_facts(), impact)
        decision = {"flow_profile": "STANDARD"}
        plan = verification_planner.build_plan(facts, impact, decision)
        kinds = {x["kind"] for x in plan["items"]}
        self.assertIn("targeted_unit_or_component_tests", kinds)
        self.assertIn("integration_tests", kinds)
        self.assertIn("regression_suite", kinds)
        self.assertNotIn("security_regression_tests", kinds)

    def test_state_manager_attaches_analysis_with_revision_history(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / ".orchestrator" / "execution-state.yaml"
            state = sm.create_state("W-6", "Semantic impact", "STANDARD")
            sm.initialize(path, state, "test")
            s = sm._load(path)
            sm.attach_analysis(
                path, "orchestrator", "analysis-1", "impact.json", "facts.json", "decision.json", "plan.json",
                "codebase-memory-mcp", s["revision"]
            )
            current = sm._load(path)
            self.assertEqual("analysis-1", current["analysis"]["analysis_snapshot_id"])
            self.assertEqual("impact.json", sm.resume_summary(current)["analysis"]["semantic_impact_ref"])
            history = path.with_name("execution-history.jsonl").read_text(encoding="utf-8")
            self.assertIn("ANALYSIS_ATTACHED", history)

    def test_flow_reconciliation_raises_review_and_deescalation_does_not_erase_it(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / ".orchestrator" / "execution-state.yaml"
            state = sm.create_state("W-8", "Flow reconcile", "FAST")
            sm.initialize(path, state, "test")
            s = sm._load(path)
            s = sm.set_flow_profile(path, "STANDARD", "orchestrator", "decision:1", s["revision"])
            self.assertEqual("STANDARD", s["flow_profile"])
            self.assertTrue(s["review"]["required"])
            s = sm.set_flow_profile(path, "FAST", "orchestrator", "decision:2", s["revision"])
            self.assertEqual("FAST", s["flow_profile"])
            self.assertTrue(s["review"]["required"])

    def test_new_analysis_snapshot_invalidates_prior_verification(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / ".orchestrator" / "execution-state.yaml"
            state = sm.create_state("W-7", "Snapshot bridge", "FAST")
            sm.initialize(path, state, "test")
            s = sm._load(path)
            sm.set_execution_snapshot(path, "A1", "agent", "initial analysis", s["revision"])
            s = sm._load(path)
            sm.record_verification(path, "passed", "verifier", "A1", "verify:A1", s["revision"])
            self.assertTrue(sm._load(path)["verification"]["fresh"])
            s = sm._load(path)
            sm.attach_analysis(path, "orchestrator", "A2", "impact2.json", "facts2.json", "decision2.json", "plan2.json", "codebase-memory-mcp", s["revision"])
            current = sm._load(path)
            self.assertEqual("A2", current["execution_snapshot_id"])
            self.assertFalse(current["verification"]["fresh"])

    def test_semantic_snapshot_changes_when_payload_changes(self):
        raw1 = fixture()
        raw2 = fixture()
        raw2["impacted_symbols"].append({
            "qualified_name": "ams.audit.AuditListener.onToken", "label": "Method", "file_path": "audit/src/Audit.java",
            "module": "audit", "service": "audit-service", "project": "ams-platform", "distance": 1
        })
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            a = cbm_provider.normalize_detect_changes(raw1, repo=root, project="ams-platform")
            b = cbm_provider.normalize_detect_changes(raw2, repo=root, project="ams-platform")
        self.assertNotEqual(a["snapshot"]["id"], b["snapshot"]["id"])


if __name__ == "__main__":
    unittest.main()
