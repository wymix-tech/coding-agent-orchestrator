import json
import pathlib
import sys
import tempfile
import unittest
import subprocess
import contextlib
import io

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import context_plane
import execution_state_manager as sm
import semantic_intake_pipeline as sip


class ContextPlaneFixture:
    def __init__(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.td.name)
        self.intake = self.repo / ".orchestrator" / "intake"
        self.intake.mkdir(parents=True)
        self.request = self.intake / "request-context.md"
        self.request.write_text("Add user REST endpoint with acceptance criteria.\n", encoding="utf-8")
        (self.intake / "work-facts.semantic-draft.json").write_text(json.dumps({
            "extraction": {"analysis_snapshot_id": "A1", "resolution_queue": []},
            "complexity": {}, "scope": {}, "risk": {}, "verification": {}, "ambiguity": {}, "novelty": {}
        }), encoding="utf-8")
        (self.intake / "decision.json").write_text(json.dumps({"status": "CLASSIFIED", "flow_profile": "STANDARD"}), encoding="utf-8")
        (self.intake / "semantic-impact.json").write_text(json.dumps({
            "snapshot": {"id": "S1"}, "completeness": {"complete": True},
            "changes": {"changed_symbols": [{"qualified_name": "UserController.get"}]},
            "impact": {"impacted_symbol_count": 3, "affected_modules": ["web", "service"], "affected_services": ["app"]},
            "boundaries": {"cross_service": False, "async_boundary": False}
        }), encoding="utf-8")
        (self.intake / "policy-plan.json").write_text(json.dumps({
            "status": "OK", "policy_snapshot_id": "P1",
            "applicable_rules": [{"id": "ARCH-SPRING-LAYER-001", "level": "MUST"}]
        }), encoding="utf-8")
        (self.intake / "policy-context.md").write_text("# Policy\n- ARCH-SPRING-LAYER-001 MUST web -> service only\n", encoding="utf-8")
        (self.intake / "policy-evaluation.json").write_text(json.dumps({"status": "PASSED", "blocking_violations": []}), encoding="utf-8")
        (self.intake / "verification-plan.json").write_text(json.dumps({"items": [
            {"kind": "targeted_unit_or_component_tests"}, {"kind": "integration_tests"}
        ]}), encoding="utf-8")
        policies = self.repo / ".orchestrator" / "policies"
        policies.mkdir(parents=True)
        self.policy_manifest = policies / "manifest.yaml"
        self.policy_manifest.write_text("version: 1\n", encoding="utf-8")
        self.state = self.repo / ".orchestrator" / "execution-state.yaml"
        state = sm.create_state("W-1", "User endpoint", "STANDARD")
        sm.initialize(self.state, state, "test")
        s = sm._load(self.state)
        sm.attach_analysis(
            self.state, "orchestrator", "A1", str(self.intake / "semantic-impact.json"),
            str(self.intake / "work-facts.semantic-draft.json"), str(self.intake / "decision.json"),
            str(self.intake / "verification-plan.json"), "codebase-memory-mcp", s["revision"],
            policy_plan_ref=str(self.intake / "policy-plan.json"),
            policy_evaluation_ref=str(self.intake / "policy-evaluation.json"),
            policy_context_ref=str(self.intake / "policy-context.md"), policy_snapshot_id="P1",
            context_manifest_ref=str(self.intake / "context-manifest.json"),
            context_pack_ref=str(self.intake / "context-pack.implementer.implementation.json"),
        )

    def close(self):
        self.td.cleanup()

    def manifest(self):
        return context_plane.build_manifest(
            self.repo, self.intake, state_ref=self.state, request_ref=self.request,
            sdd_provider="generic", policy_manifest_ref=self.policy_manifest,
        )


class ContextPlaneTests(unittest.TestCase):
    def test_manifest_unifies_current_authorities_and_sources(self):
        fx = ContextPlaneFixture()
        try:
            m = fx.manifest()
            ids = {x["id"] for x in m["sources"]}
            self.assertIn("requirement", ids)
            self.assertIn("decision", ids)
            self.assertIn("semantic_impact", ids)
            self.assertIn("policy_context", ids)
            self.assertIn("verification_plan", ids)
            self.assertIn("execution_state", ids)
            self.assertEqual("canonical_execution_state", m["authorities"]["execution"]["authority"])
            self.assertEqual("A1", m["snapshots"]["analysis_snapshot_id"])
            self.assertEqual("P1", m["snapshots"]["policy_snapshot_id"])
        finally:
            fx.close()

    def test_implementer_pack_prefers_state_semantic_policy_and_requirement(self):
        fx = ContextPlaneFixture()
        try:
            p = context_plane.build_pack(fx.manifest(), "implementer", "implementation")
            selected = {x["id"] for x in p["selected"]}
            self.assertTrue({"requirement", "execution_state", "semantic_impact", "decision"}.issubset(selected))
            self.assertIn("policy_context", selected)
            self.assertNotIn("execution_history", selected)
        finally:
            fx.close()

    def test_reviewer_pack_makes_policy_evaluation_mandatory_when_policy_exists(self):
        fx = ContextPlaneFixture()
        try:
            p = context_plane.build_pack(fx.manifest(), "reviewer", "review")
            eval_item = next(x for x in p["selected"] if x["id"] == "policy_evaluation")
            self.assertTrue(eval_item["mandatory"])
        finally:
            fx.close()

    def test_tight_budget_does_not_drop_mandatory_context(self):
        fx = ContextPlaneFixture()
        try:
            p = context_plane.build_pack(fx.manifest(), "implementer", "implementation", max_items=1, max_chars=120)
            selected = {x["id"] for x in p["selected"]}
            self.assertTrue({"requirement", "execution_state", "semantic_impact", "decision"}.issubset(selected))
        finally:
            fx.close()

    def test_manifest_validation_detects_stale_source_hash(self):
        fx = ContextPlaneFixture()
        try:
            m = fx.manifest()
            self.assertEqual("FRESH", context_plane.validate_manifest(fx.repo, m)["status"])
            (fx.intake / "decision.json").write_text(json.dumps({"status": "CLASSIFIED", "flow_profile": "DEEP"}), encoding="utf-8")
            result = context_plane.validate_manifest(fx.repo, m)
            self.assertEqual("STALE", result["status"])
            self.assertIn("decision", {x["id"] for x in result["stale_sources"]})
        finally:
            fx.close()

    def test_context_snapshot_changes_when_execution_state_changes(self):
        fx = ContextPlaneFixture()
        try:
            m1 = fx.manifest()
            s = sm._load(fx.state)
            sm.set_cursor(fx.state, "agent", "T2", "Implement service", "implement_task:T2", s["revision"])
            m2 = fx.manifest()
            self.assertNotEqual(m1["context_snapshot_id"], m2["context_snapshot_id"])
        finally:
            fx.close()

    def test_policy_failure_is_exposed_as_context_blocker(self):
        fx = ContextPlaneFixture()
        try:
            (fx.intake / "policy-evaluation.json").write_text(json.dumps({
                "status": "FAILED", "blocking_violations": [{"rule_id": "ARCH-SPRING-LAYER-001"}]
            }), encoding="utf-8")
            m = fx.manifest()
            self.assertIn("POLICY_VIOLATION", {x["code"] for x in m["blockers"]})
        finally:
            fx.close()

    def test_pack_is_snapshot_bound_and_render_is_compact(self):
        fx = ContextPlaneFixture()
        try:
            m = fx.manifest()
            p = context_plane.build_pack(m, "verifier", "verification")
            self.assertEqual(m["context_snapshot_id"], p["valid_for"]["context_snapshot_id"])
            text = context_plane.render_pack(p)
            self.assertIn("Context Pack", text)
            self.assertNotIn("full CBM graph", json.dumps(p["selected"]))
        finally:
            fx.close()

    def test_sdd_directory_can_be_requirement_authority_and_hash_stales_on_change(self):
        fx = ContextPlaneFixture()
        try:
            change = fx.repo / "openspec" / "changes" / "user-endpoint"
            change.mkdir(parents=True)
            (change / "spec.md").write_text("requirement v1\n", encoding="utf-8")
            m = context_plane.build_manifest(
                fx.repo, fx.intake, state_ref=fx.state, request_ref=fx.request,
                sdd_ref=change, sdd_provider="openspec", policy_manifest_ref=fx.policy_manifest,
            )
            req = next(x for x in m["sources"] if x["id"] == "requirement")
            self.assertEqual("repository_sdd", req["authority"])
            self.assertEqual(1, req["file_count"])
            (change / "spec.md").write_text("requirement v2\n", encoding="utf-8")
            self.assertEqual("STALE", context_plane.validate_manifest(fx.repo, m)["status"])
        finally:
            fx.close()

    def test_semantic_pipeline_generates_context_even_when_more_evidence_is_needed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            (repo / "App.java").write_text("class App {}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
            (repo / "App.java").write_text("class App { int x; }\n", encoding="utf-8")
            state_path = repo / ".orchestrator" / "execution-state.yaml"
            state = sm.create_state("W-P", "Pipeline context", "STANDARD")
            sm.initialize(state_path, state, "test")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = sip.main([
                    "--repo", str(repo),
                    "--request", "Change App behavior.",
                    "--cbm-fixture", str(ROOT / "examples" / "cbm-detect-changes-fixture.json"),
                    "--skip-policy",
                    "--state", str(state_path),
                    "--context-role", "implementer",
                    "--context-stage", "implementation",
                ])
            self.assertEqual(1, rc)  # expected: unresolved Work Facts remain
            intake = repo / ".orchestrator" / "intake"
            self.assertTrue((intake / "context-manifest.json").exists())
            self.assertTrue((intake / "context-pack.implementer.implementation.json").exists())
            manifest = json.loads((intake / "context-manifest.json").read_text(encoding="utf-8"))
            self.assertIn("DECISION_NOT_CLASSIFIED", {x["code"] for x in manifest["blockers"]})

    def test_state_analysis_can_store_context_refs(self):
        fx = ContextPlaneFixture()
        try:
            analysis = sm._load(fx.state)["analysis"]
            self.assertTrue(analysis["context_manifest_ref"].endswith("context-manifest.json"))
            self.assertTrue(analysis["context_pack_ref"].endswith("context-pack.implementer.implementation.json"))
        finally:
            fx.close()


if __name__ == "__main__":
    unittest.main()
