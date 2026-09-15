import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import action_guard as guard
import coding_orchestrator as cli
import enforcement_kernel as ek
import execution_state_manager as sm
import fact_extractor
import repository_snapshot
import start_router
import tool_actions
from governance_fixture import refresh_context
from test_enforcement_kernel import EnforcementFixture
import evidence_factory


class ActionGuardTests(unittest.TestCase):
    def setUp(self):
        self.fx = EnforcementFixture()
        self.addCleanup(self.fx.close)

    def state(self):
        return sm._load(self.fx.state_path)

    def check(self, action, **kwargs):
        return guard.authorize(self.fx.repo, self.state(), action, **kwargs)

    def pretool(self, payload=None, role=None):
        raw = payload or {"tool_name": "Write", "tool_input": {"file_path": "src/main/java/com/acme/App.java"}}
        if role:
            raw = {**raw, "role": role}
        return ek.handle(self.fx.repo, "claude-code", "pre_tool", raw)

    def reanalyze(self, snapshot="A2"):
        d = self.fx.intake
        sm.attach_analysis(self.fx.state_path, "test", snapshot, str(d / "semantic-impact.json"),
                           str(d / "work-facts.semantic-draft.json"), str(d / "decision.json"),
                           str(d / "verification-plan.json"), context_manifest_ref=str(d / "context-manifest.json"))
        refresh_context(self.fx.repo, self.fx.state_path)

    def ready(self):
        for key in ("implementation_tasks_complete", "acceptance_satisfied"):
            evidence_factory.establish_readiness(self.fx.state_path, key, repo=self.fx.repo)
        sm.record_review(self.fx.state_path, "passed", "reviewer", evidence_ref="review")
        sm.record_verification(self.fx.state_path, "passed", "verifier", self.state()["execution_snapshot_id"], "verification")

    def test_pure_evaluator_is_deterministic_and_does_not_mutate_inputs(self):
        state = self.state()
        evidence = guard.collect_evidence(self.fx.repo, state)
        before = copy.deepcopy((state, evidence))
        first = guard.evaluate(state, "mutate_code", evidence=evidence)
        self.assertEqual(first, guard.evaluate(state, "mutate_code", evidence=evidence))
        self.assertEqual(before, (state, evidence))

    def test_ready_code_permission_matches_host_and_cli(self):
        expected = self.check("mutate_code")
        args = cli.build_parser().parse_args(["--repo", str(self.fx.repo), "check", "--action", "mutate_code"])
        code, result, _ = cli.cmd_check(args)
        self.assertEqual(0, code)
        self.assertEqual(expected, result)
        self.assertEqual(expected, self.pretool()["metadata"]["authorization"])

    def test_active_blocker_blocks_code_advance_close_and_routes_recovery(self):
        state = sm.add_blocker(self.fx.state_path, "dependency", "waiting", "test")
        blocker = state["blockers"][0]["id"]
        for action, args in [("mutate_code", {}), ("advance", {"target_phase": "review"}), ("close", {})]:
            with self.subTest(action=action):
                result = self.check(action, **args)
                self.assertFalse(result["allowed"])
                self.assertEqual("ACTIVE_BLOCKER", result["reason_codes"][0])
        self.assertEqual("deny", self.pretool()["decision"])
        routed = start_router.resolve(self.fx.repo, ensure_bootstrap=False)
        self.assertEqual("SURFACE_BLOCKER", routed["route"])
        self.assertEqual(f"resolve_blocker:{blocker}", routed["next_action"])
        with self.assertRaises(sm.TransitionDenied):
            sm.transition(self.fx.state_path, "review", "in_progress", "test", "advance")

    def test_resolved_blocker_does_not_block_start_or_code(self):
        state = sm.add_blocker(self.fx.state_path, "dependency", "waiting", "test")
        sm.resolve_blocker(self.fx.state_path, state["blockers"][0]["id"], "resolved", "test")
        self.assertEqual("RESUME_CURRENT_WORK", start_router.resolve(self.fx.repo, ensure_bootstrap=False)["route"])
        self.assertTrue(self.check("mutate_code")["allowed"])
        self.assertEqual("allow", self.pretool()["decision"])

    def test_needs_evidence_denies_both_code_and_phase_advance(self):
        (self.fx.intake / "decision.json").write_text(json.dumps({"status": "NEEDS_EVIDENCE"}))
        self.reanalyze()
        evidence_factory.establish_readiness(self.fx.state_path, "implementation_tasks_complete")
        self.assertTrue(self.state()["enforcement"]["dirty"])
        self.assertEqual("resolve_fact_evidence", sm.resume_summary(self.state(), self.fx.repo)["next_action"])
        for action, kwargs in [("mutate_code", {}), ("advance", {"target_phase": "review"}), ("close", {})]:
            self.assertIn("DECISION_NOT_CLASSIFIED", self.check(action, **kwargs)["reason_codes"])
        with self.assertRaises(sm.TransitionDenied):
            sm.transition(self.fx.state_path, "review", "in_progress", "test", "advance")

    def test_partial_impact_never_becomes_authorized(self):
        p = self.fx.intake / "semantic-impact.json"
        for completeness in ({"complete": False}, {}, None, {"complete": "unknown"}):
            with self.subTest(completeness=completeness):
                doc = json.loads(p.read_text()); doc["completeness"] = completeness; p.write_text(json.dumps(doc))
                self.reanalyze()
                self.assertIn("SEMANTIC_EVIDENCE_INCOMPLETE", self.check("mutate_code")["reason_codes"])
                self.assertTrue(self.state()["enforcement"]["dirty"])

    def test_role_restriction_matches_host(self):
        for role in ("reviewer", "verifier", "planner"):
            with self.subTest(role=role):
                expected = self.check("mutate_code", role=role)
                self.assertIn("ROLE_READ_ONLY", expected["reason_codes"])
                self.assertEqual(expected, self.pretool(role=role)["metadata"]["authorization"])

    def test_unknown_action_and_invalid_phase_fail_closed(self):
        self.assertEqual(["UNKNOWN_ACTION"], self.check("something_else")["reason_codes"])
        self.assertIn("INVALID_TRANSITION", self.check("advance", target_phase="made_up")["reason_codes"])

    def test_native_authority_is_visible_in_the_shared_decision(self):
        # Readiness is set through the entry point, so the shared decision sees bound evidence
        # instead of a state file that was edited into readiness.
        evidence_factory.establish_readiness(self.fx.state_path, "implementation_tasks_complete",
                                             repo=self.fx.repo)
        state = self.state()
        state["authority"]["mode"] = "native"
        facts = guard.collect_evidence(self.fx.repo, state)
        denied = guard.evaluate(state, "advance", target_phase="review", evidence=facts)
        self.assertIn("NATIVE_AUTHORITY_REQUIRED", denied["reason_codes"])
        confirmed = guard.evaluate(state, "advance", target_phase="review", evidence=facts, native_confirmed=True)
        self.assertTrue(confirmed["allowed"], confirmed)

    def test_release_requires_fresh_final_verification(self):
        self.ready()
        sm.record_verification(self.fx.state_path, "failed", "test", self.state()["execution_snapshot_id"], "failed verification")
        self.assertIn("VERIFICATION_NOT_PASSED", self.check("advance", target_phase="release")["reason_codes"])

    def test_gate_cli_records_a_command_without_overwriting_subcommand(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = sm.main(["--state", str(self.fx.state_path), "gate", "--name", "unit",
                            "--required", "true", "--status", "passed", "--actor", "test",
                            "--command", "pytest -q", "--evidence-ref", "unit-results"])
        self.assertEqual(0, code)
        self.assertEqual("pytest -q", self.state()["quality_gates"]["unit"]["command"])

    def test_transition_denial_exposes_the_same_authorization_as_check(self):
        expected = self.check("advance", target_phase="review")
        with self.assertRaises(sm.TransitionDenied) as exc:
            sm.transition(self.fx.state_path, "review", "in_progress", "test", "advance")
        self.assertEqual(expected, exc.exception.authorization)

    def test_reviewer_can_report_failed_outcome_without_claiming_global_completion(self):
        sm.record_review(self.fx.state_path, "failed", "reviewer", 1, "review-report")
        result = ek.handle(self.fx.repo, "claude-code", "subagent_stop",
                           {"orchestrator_role": "reviewer", "last_assistant_message": "Review completed; blocking findings recorded."})
        self.assertEqual("allow", result["decision"])
        self.assertEqual("finish_role", result["metadata"]["authorization"]["action"])
        self.assertFalse(self.check("close")["allowed"])

    def test_state_only_query_cannot_prove_completion(self):
        self.ready()
        self.assertTrue(self.check("close")["allowed"])
        result = sm.completion_status(self.state())
        self.assertFalse(result["governance_close_ready"])
        self.assertIn("REPOSITORY_CONTEXT_REQUIRED", result["authorization"]["reason_codes"])

    def test_ready_close_matches_verify_stop_and_transition(self):
        self.ready()
        expected = self.check("close")
        self.assertTrue(expected["allowed"], expected)
        args = cli.build_parser().parse_args(["--repo", str(self.fx.repo), "verify"])
        code, result, _ = cli.cmd_verify(args)
        self.assertEqual(0, code)
        self.assertEqual(expected, result["authorization"])
        self.assertEqual([], sm.transition_guard(self.state(), "closed", "completed", self.fx.repo))
        stop = ek.handle(self.fx.repo, "claude-code", "stop", {"last_assistant_message": "All done"})
        self.assertEqual(expected, stop["metadata"]["authorization"])
        sm.transition(self.fx.state_path, "closed", "completed", "test", "done")
        self.assertTrue(sm.completion_status(self.state(), self.fx.repo)["done"])

    def test_code_edit_without_hook_blocks_verify_close_and_further_writes(self):
        self.ready()
        (self.fx.repo / "src/main/java/com/acme/App.java").write_text("class App { int changed; }\n")
        self.assertIn("REPOSITORY_CHANGED", self.check("close")["reason_codes"])
        args = cli.build_parser().parse_args(["--repo", str(self.fx.repo), "verify"])
        self.assertEqual(1, cli.cmd_verify(args)[0])
        self.assertEqual("deny", self.pretool()["decision"])
        with self.assertRaises(sm.TransitionDenied):
            sm.transition(self.fx.state_path, "closed", "completed", "test", "done")

    def test_tracked_inner_loop_edit_allows_code_but_not_advance(self):
        raw = {"tool_name": "Write", "tool_input": {"file_path": "src/main/java/com/acme/App.java"}}
        self.assertEqual("allow", self.pretool(raw)["decision"])
        (self.fx.repo / raw["tool_input"]["file_path"]).write_text("class App { int edited; }\n")
        ek.handle(self.fx.repo, "claude-code", "post_tool", raw)
        self.assertTrue(self.check("mutate_code")["allowed"])
        self.assertIn("ANALYSIS_DIRTY", self.check("advance", target_phase="review")["reason_codes"])

    def test_external_edit_after_tracked_edit_is_not_exempt(self):
        raw = {"tool_name": "Write", "tool_input": {"file_path": "src/main/java/com/acme/App.java"}}
        source = self.fx.repo / raw["tool_input"]["file_path"]
        source.write_text("class App { int one; }\n")
        ek.handle(self.fx.repo, "claude-code", "post_tool", raw)
        source.write_text("class App { int one; int external; }\n")
        self.assertIn("REPOSITORY_CHANGED", self.check("mutate_code")["reason_codes"])

    def test_changed_requirement_denies_even_with_dirty_state(self):
        sm.mark_enforcement_dirty(self.fx.state_path, "mutation", [], "test", repository_snapshot.fingerprint(self.fx.repo), "edit")
        (self.fx.intake / "request-context.md").write_text("Different requirement")
        self.assertIn("AUTHORITY_EVIDENCE_STALE", self.check("mutate_code")["reason_codes"])
        self.assertEqual("deny", self.pretool()["decision"])

    def test_missing_or_changed_context_sources_prevent_close(self):
        self.ready()
        (self.fx.intake / "context-manifest.json").unlink()
        self.assertIn("CONTEXT_STALE", self.check("close")["reason_codes"])

    def test_optional_review_blocking_findings_still_prevent_close(self):
        self.ready()
        state = self.state(); state["review"]["required"] = False
        evidence = guard.collect_evidence(self.fx.repo, state)
        state["review"].update(status="failed", blocking_findings=1)
        result = guard.evaluate(state, "close", evidence=evidence)
        self.assertIn("REVIEW_BLOCKING_FINDINGS", result["reason_codes"])

    def test_required_gate_cannot_be_downgraded(self):
        sm.record_gate(self.fx.state_path, "PIT", True, "failed", "test")
        with self.assertRaises(sm.StateError):
            sm.record_gate(self.fx.state_path, "PIT", False, "skipped", "test")

    def test_impact_plan_requirement_cannot_be_omitted_from_state(self):
        p = self.fx.intake / "verification-plan.json"
        p.write_text(json.dumps({"items": [{"kind": "integration_tests", "required_by_impact": True, "status": "pending"}]}))
        self.reanalyze(); self.ready()
        denied = self.check("close")
        self.assertIn("REQUIRED_GATE_NOT_PASSED", denied["reason_codes"])
        self.assertIn("impact:integration_tests", str(denied))
        sm.record_gate(self.fx.state_path, "impact:integration_tests", True, "passed", "test", evidence_ref="current-test-report")
        self.assertTrue(self.check("close")["allowed"])

    def test_gate_and_review_must_match_new_snapshot(self):
        self.ready()
        sm.record_gate(self.fx.state_path, "unit", True, "passed", "test", evidence_ref="A1")
        self.reanalyze("A2")
        sm.record_verification(self.fx.state_path, "passed", "test", "A2", "new final")
        result = self.check("close")
        self.assertIn("GATE_EVIDENCE_STALE", result["reason_codes"])
        self.assertIn("REVIEW_STALE", result["reason_codes"])

    def test_stop_retry_budget_does_not_grant_completion(self):
        for _ in range(5):
            result = ek.handle(self.fx.repo, "pi", "stop", {"session_id": "repeat", "last_assistant_message": "All done"})
            self.assertEqual("deny", result["decision"])
        self.assertFalse(result["metadata"]["retry_recommended"])

    def test_reused_analysis_id_cannot_reuse_evidence_after_authority_change(self):
        self.ready()
        sm.record_gate(self.fx.state_path, "unit", True, "passed", "test", evidence_ref="old unit")
        snapshot = self.state()["execution_snapshot_id"]
        (self.fx.intake / "request-context.md").write_text("Revised acceptance criteria")
        self.reanalyze(snapshot)
        result = self.check("close")
        for code in ("GATE_EVIDENCE_STALE", "REVIEW_STALE", "VERIFICATION_STALE"):
            self.assertIn(code, result["reason_codes"])
        self.ready()
        sm.record_gate(self.fx.state_path, "unit", True, "passed", "test", evidence_ref="new unit")
        self.assertTrue(self.check("close")["allowed"])

    def test_state_and_context_projection_changes_do_not_dirty_material_snapshot(self):
        before = repository_snapshot.fingerprint(self.fx.repo)
        sm.assign_role(self.fx.state_path, "reviewer", "agent-r", "test")
        (self.fx.repo / ".orchestrator/session").mkdir(exist_ok=True)
        (self.fx.repo / ".orchestrator/session/projection.json").write_text("{}")
        refresh_context(self.fx.repo, self.fx.state_path)
        self.assertEqual(before, repository_snapshot.fingerprint(self.fx.repo))
        self.assertTrue(self.check("mutate_code")["allowed"])
        changed, _ = fact_extractor.collect_changed_files(self.fx.repo, None)
        self.assertNotIn(".orchestrator/session/projection.json", changed)


class ToolActionTests(unittest.TestCase):
    def test_completion_claim_without_state_does_not_bypass_close_policy(self):
        with tempfile.TemporaryDirectory() as td:
            result = ek.handle(Path(td), "pi", "stop", {"last_assistant_message": "All done"})
            self.assertEqual("deny", result["decision"])
            self.assertEqual(["STATE_MISSING"], result["metadata"]["authorization"]["reason_codes"])
            args = cli.build_parser().parse_args(["--repo", td, "verify"])
            code, verified, _ = cli.cmd_verify(args)
            self.assertEqual(1, code)
            self.assertEqual(result["metadata"]["authorization"], verified["authorization"])
            with self.assertRaises(sm.TransitionDenied) as exc:
                sm.transition(Path(td) / ".orchestrator/execution-state.yaml", "closed", "completed", "test", "done")
            self.assertEqual(verified["authorization"], exc.exception.authorization)

    def test_preparation_is_allowed_without_work_item_but_code_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # SDD authoring is legitimate preparation even with no active work item.
            for path in ("requirements.md", "docs/spec.md", "docs/stories/01-login.md"):
                result = ek.handle(repo, "claude-code", "pre_tool", {"tool_name": "Write", "tool_input": {"file_path": path}})
                self.assertEqual("allow", result["decision"], path)
            result = ek.handle(repo, "claude-code", "pre_tool", {"tool_name": "Write", "tool_input": {"file_path": "src/a.py"}})
            self.assertEqual("deny", result["decision"])

    def test_governance_inputs_are_not_agent_writable(self):
        """An agent must not widen its own authority by rewriting what grants authority."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            governed = [".orchestrator/config.yaml", ".orchestrator/enforcement.yaml",
                        ".orchestrator/execution-state.yaml", ".orchestrator/policies/manifest.yaml",
                        ".orchestrator/requirements/identity.json"]
            for path in governed:
                with self.subTest(path=path):
                    self.assertEqual("mutate_governance", tool_actions.describe(
                        {"tool_name": "Write", "tool_input": {"file_path": path}}, repo)["action"])
                    result = ek.handle(repo, "pi", "pre_tool", {"tool_name": "Write", "tool_input": {"file_path": path}})
                    self.assertEqual("deny", result["decision"])
                    self.assertIn("GOVERNANCE_CONFIG_PROTECTED", result["metadata"]["authorization"]["reason_codes"])
                    self.assertEqual("reconfigure_governance_explicitly",
                                     result["metadata"]["authorization"]["next_action"])

    def test_governance_mutation_requires_explicit_operator_override(self):
        denied = guard.evaluate(None, "mutate_governance", governance_paths=[".orchestrator/config.yaml"])
        self.assertFalse(denied["allowed"])
        allowed = guard.evaluate(None, "mutate_governance", governance_paths=[".orchestrator/config.yaml"],
                                 allow_governance_mutation=True)
        self.assertTrue(allowed["allowed"])
        self.assertIn("mutate_governance", guard.ACTIONS)

    def test_shell_escalates_governance_writes_but_not_reads(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self.assertEqual("read", tool_actions.describe(
                {"tool_name": "Bash", "tool_input": {"command": "cat .orchestrator/config.yaml"}}, repo)["action"])
            self.assertEqual("mutate_governance", tool_actions.describe(
                {"tool_name": "Bash", "tool_input": {"command": "printf x > .orchestrator/config.yaml"}}, repo)["action"])
            self.assertEqual("mutate_governance", tool_actions.describe(
                {"tool_name": "Bash", "tool_input": {"command": "python3 -c \"open('.orchestrator/policies/m.yaml','w')\""}}, repo)["action"])

    def test_write_payload_variants_have_no_readonly_exemption(self):
        payloads = [
            {"tool_name": "exec_command", "tool_input": {"cmd": "printf x > src/a.py"}},
            {"tool_name": "Bash", "tool_input": {"command": "cat README.md > src/a.py"}},
            {"tool_name": "Bash", "tool_input": {"command": "ls && python3 generate.py"}},
            {"tool_name": "Bash", "tool_input": {"command": "python3 -c \"open('src/a.py','w').write('x')\""}},
            {"tool_name": "apply_patch", "tool_input": "*** Begin Patch\n*** Add File: src/a.py\n+x\n*** End Patch"},
            {"tool_name": "apply_patch", "tool_input": {}},
        ]
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            for payload in payloads:
                with self.subTest(payload=payload):
                    self.assertEqual("mutate_code", tool_actions.describe(payload, repo)["action"])
                    self.assertEqual("deny", ek.handle(repo, "codex", "pre_tool", payload)["decision"])

    def test_patch_move_target_is_checked(self):
        payload = {"tool_name": "apply_patch", "tool_input": "*** Update File: docs/example.md\n*** Move to: src/example.py\n"}
        self.assertEqual("mutate_code", tool_actions.describe(payload, ROOT)["action"])

    def test_packaged_control_commands_remain_available_for_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            commands = [f'python3 "{ROOT / "scripts/coding_orchestrator.py"}" --repo "{repo}" start',
                        f'python3 "{ROOT / "coding-orchestrator"}" --repo "{repo}" start',
                        f'"{ROOT / "coding-orchestrator"}" --repo "{repo}" intake "Requirement"']
            for command in commands:
                payload = {"tool_name": "Bash", "tool_input": {"command": command}}
                self.assertEqual("prepare", tool_actions.describe(payload, repo)["action"])
                self.assertEqual("allow", ek.handle(repo, "pi", "pre_tool", payload)["decision"])


if __name__ == "__main__":
    unittest.main()
