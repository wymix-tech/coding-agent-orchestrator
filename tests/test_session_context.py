import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import context_plane
import enforcement_kernel as ek
import execution_state_manager as sm
import session_context as sc


class SessionFixture:
    def __init__(self):
        self.td = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.td.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.repo, check=True)
        src = self.repo / "src/main/java/com/acme"
        src.mkdir(parents=True)
        (src / "App.java").write_text("class App {}\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.repo, check=True)

        self.intake = self.repo / ".orchestrator" / "intake"
        self.intake.mkdir(parents=True)
        self.state = self.repo / ".orchestrator" / "execution-state.yaml"
        st = sm.create_state("W-42", "Session context feature", "STANDARD")
        sm.initialize(self.state, st, "test")
        for k, v in [("behavior_change", True), ("acceptance_criteria_present", True), ("sdd_ready", True)]:
            s = sm._load(self.state)
            sm.set_readiness(self.state, k, v, "test", "evidence", s["revision"])
        s = sm._load(self.state)
        sm.transition(self.state, "implementation", "in_progress", "test", "ready", s["revision"])
        s = sm._load(self.state)
        sm.set_cursor(self.state, "test", "T4.2", "Implement async reporting", "implement_task:T4.2", s["revision"])

        (self.intake / "request-context.md").write_text("Implement async login reporting with acceptance criteria.\n", encoding="utf-8")
        (self.intake / "decision.json").write_text(json.dumps({"status": "CLASSIFIED", "flow_profile": "STANDARD"}), encoding="utf-8")
        (self.intake / "work-facts.semantic-draft.json").write_text(json.dumps({
            "extraction": {"analysis_snapshot_id": "A42", "resolution_queue": []},
            "risk": {}, "scope": {}, "complexity": {}, "verification": {}, "ambiguity": {}, "novelty": {}
        }), encoding="utf-8")
        (self.intake / "semantic-impact.json").write_text(json.dumps({
            "snapshot": {"id": "S42"}, "completeness": {"complete": True},
            "changes": {"changed_symbols": [{"qualified_name": "LoginReportService.report"}]},
            "impact": {"impacted_symbol_count": 4, "affected_modules": ["sdk", "service"], "affected_services": ["ams"]},
            "boundaries": {"cross_service": True, "async_boundary": True}
        }), encoding="utf-8")
        (self.intake / "policy-plan.json").write_text(json.dumps({
            "status": "OK", "policy_snapshot_id": "P42",
            "applicable_rules": [{"id": "ARCH-SPRING-LAYER-001", "level": "MUST"}]
        }), encoding="utf-8")
        (self.intake / "policy-context.md").write_text("# Policy\n- ARCH-SPRING-LAYER-001\n", encoding="utf-8")
        (self.intake / "verification-plan.json").write_text(json.dumps({"items": [{"kind": "integration_tests"}]}), encoding="utf-8")
        s = sm._load(self.state)
        sm.attach_analysis(
            self.state, "test", "A42", str(self.intake / "semantic-impact.json"),
            str(self.intake / "work-facts.semantic-draft.json"), str(self.intake / "decision.json"),
            str(self.intake / "verification-plan.json"), "codebase-memory-mcp", s["revision"],
            policy_plan_ref=str(self.intake / "policy-plan.json"),
            policy_context_ref=str(self.intake / "policy-context.md"), policy_snapshot_id="P42",
            context_manifest_ref=str(self.intake / "context-manifest.json"),
            context_pack_ref=str(self.intake / "context-pack.implementer.implementation.json"),
        )
        manifest = context_plane.build_manifest(
            self.repo, self.intake, state_ref=self.state, request_ref=self.intake / "request-context.md", sdd_provider="generic"
        )
        context_plane._dump_json(self.intake / "context-manifest.json", manifest)
        pack = context_plane.build_pack(manifest, "implementer", "implementation")
        context_plane._dump_json(self.intake / "context-pack.implementer.implementation.json", pack)
        (self.intake / "context-pack.implementer.implementation.md").write_text(context_plane.render_pack(pack), encoding="utf-8")

    def close(self):
        self.td.cleanup()


class SessionContextTests(unittest.TestCase):
    def test_fresh_project_bootstrap_is_safe_and_uninitialized(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            doc = sc.build_bootstrap(repo)
            self.assertEqual("fresh_project", doc["session_type"])
            self.assertEqual("UNINITIALIZED", doc["status"])
            self.assertIn("STATE_NOT_INITIALIZED", {x["code"] for x in doc["blockers"]})

    def test_resume_bootstrap_projects_current_state(self):
        fx = SessionFixture()
        try:
            doc = sc.build_bootstrap(fx.repo, role="implementer", host="claude-code", session_id="s1")
            self.assertEqual("session_resume", doc["session_type"])
            self.assertEqual("T4.2", doc["current"]["current_task"]["id"])
            self.assertEqual("implement_task:T4.2", doc["next_action"])
            self.assertIn("ARCH-SPRING-LAYER-001", doc["required_policy_ids"])
            self.assertEqual("FRESH", doc["context"]["manifest_status"])
        finally:
            fx.close()

    def test_handoff_becomes_next_bootstrap_context(self):
        fx = SessionFixture()
        try:
            handoff = sc.build_handoff(
                fx.repo, from_role="implementer", to_role="reviewer",
                summary=["Implemented async reporting", "Added retry handling"],
                completed_task_id="T4.2", completed_task_title="Implement async reporting",
                next_action="review_task:T4.2", known_risks=["retry timing"],
            )
            sc.persist_handoff(fx.repo, handoff)
            doc = sc.build_bootstrap(fx.repo, role="reviewer")
            self.assertEqual("agent_handoff", doc["session_type"])
            self.assertEqual("FRESH", doc["latest_handoff"]["freshness"]["status"])
            text = sc.render_bootstrap(doc)
            self.assertIn("Previous handoff", text)
            self.assertIn("Implemented async reporting", text)
        finally:
            fx.close()

    def test_handoff_stales_when_execution_snapshot_changes(self):
        fx = SessionFixture()
        try:
            handoff = sc.build_handoff(fx.repo, from_role="implementer", to_role="reviewer", summary=["done"])
            self.assertEqual("FRESH", sc.validate_handoff(fx.repo, handoff)["status"])
            s = sm._load(fx.state)
            sm.set_execution_snapshot(fx.state, "A43", "test", "new code snapshot", s["revision"])
            result = sc.validate_handoff(fx.repo, handoff)
            self.assertEqual("STALE", result["status"])
            self.assertIn("execution snapshot changed", result["reasons"])
        finally:
            fx.close()

    def test_state_revision_only_does_not_stale_handoff(self):
        fx = SessionFixture()
        try:
            handoff = sc.build_handoff(fx.repo, from_role="implementer", to_role="reviewer", summary=["done"])
            s = sm._load(fx.state)
            sm.assign_role(fx.state, "observer", "agent-x", "test", s["revision"])
            self.assertEqual("FRESH", sc.validate_handoff(fx.repo, handoff)["status"])
        finally:
            fx.close()

    def test_persist_uses_json_canonical_and_markdown_projection(self):
        fx = SessionFixture()
        try:
            doc = sc.build_bootstrap(fx.repo)
            jp, mp = sc.persist_bootstrap(fx.repo, doc)
            self.assertEqual(doc["bootstrap_snapshot_id"], json.loads(jp.read_text(encoding="utf-8"))["bootstrap_snapshot_id"])
            self.assertIn("Orchestrator Session Bootstrap", mp.read_text(encoding="utf-8"))
        finally:
            fx.close()

    def test_session_artifacts_do_not_dirty_worktree_fingerprint(self):
        fx = SessionFixture()
        try:
            before = ek.worktree_snapshot(fx.repo)
            sc.persist_bootstrap(fx.repo, sc.build_bootstrap(fx.repo))
            after = ek.worktree_snapshot(fx.repo)
            self.assertEqual(before, after)
        finally:
            fx.close()

    def test_session_start_injects_bootstrap_but_prompt_submit_is_delta_only(self):
        fx = SessionFixture()
        try:
            r = ek.handle(fx.repo, "claude-code", "session_start", {"session_id": "s1"})
            self.assertIn("Orchestrator Session Bootstrap", r["additional_context"])
            self.assertTrue((fx.repo / ".orchestrator/session/session-bootstrap.json").exists())
            r2 = ek.handle(fx.repo, "claude-code", "prompt_submit", {"session_id": "s1"})
            self.assertNotIn("Orchestrator Session Bootstrap", r2["additional_context"])
            self.assertEqual("delta_only", r2["metadata"]["context_mode"])
        finally:
            fx.close()

    def test_bootstrap_does_not_inline_full_graph_or_history(self):
        fx = SessionFixture()
        try:
            text = sc.render_bootstrap(sc.build_bootstrap(fx.repo))
            self.assertNotIn("full CBM graph", text)
            self.assertLess(len(text), 7001)
        finally:
            fx.close()


    def test_handoff_stales_when_decision_or_policy_authority_changes(self):
        fx = SessionFixture()
        try:
            handoff = sc.build_handoff(fx.repo, from_role="implementer", to_role="reviewer", summary=["done"])
            self.assertEqual("FRESH", sc.validate_handoff(fx.repo, handoff)["status"])
            (fx.intake / "decision.json").write_text(json.dumps({"status": "CLASSIFIED", "flow_profile": "DEEP"}), encoding="utf-8")
            result = sc.validate_handoff(fx.repo, handoff)
            self.assertEqual("STALE", result["status"])
            self.assertIn("requirement/decision/policy/evidence authority changed", result["reasons"])
        finally:
            fx.close()

    def test_handoff_for_other_role_is_not_injected_as_agent_handoff(self):
        fx = SessionFixture()
        try:
            handoff = sc.build_handoff(fx.repo, from_role="implementer", to_role="reviewer", summary=["ready for review"])
            sc.persist_handoff(fx.repo, handoff)
            doc = sc.build_bootstrap(fx.repo, role="implementer")
            self.assertEqual("session_resume", doc["session_type"])
            self.assertIsNone(doc["latest_handoff"])
        finally:
            fx.close()

    def test_handoff_requires_summary(self):
        fx = SessionFixture()
        try:
            with self.assertRaises(ValueError):
                sc.build_handoff(fx.repo, from_role="implementer", to_role="reviewer", summary=[])
        finally:
            fx.close()


if __name__ == "__main__":
    unittest.main()
