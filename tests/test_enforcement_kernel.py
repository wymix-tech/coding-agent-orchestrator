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


class EnforcementFixture:
    def __init__(self, classified=True, phase="implementation"):
        self.td = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.td.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.repo, check=True)
        (self.repo / "src/main/java/com/acme").mkdir(parents=True)
        (self.repo / "src/main/java/com/acme/App.java").write_text("class App {}\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.repo, check=True)
        self.intake = self.repo / ".orchestrator" / "intake"
        self.intake.mkdir(parents=True)
        self.state_path = self.repo / ".orchestrator" / "execution-state.yaml"
        st = sm.create_state("W-1", "Example", "STANDARD")
        sm.initialize(self.state_path, st, "test")
        s = sm._load(self.state_path)
        sm.set_enforcement_enabled(self.state_path, True, "test", s["revision"])
        if phase == "implementation":
            for k, v in [("behavior_change", True), ("acceptance_criteria_present", True), ("sdd_ready", True)]:
                s = sm._load(self.state_path); sm.set_readiness(self.state_path, k, v, "test", "e", s["revision"])
            s = sm._load(self.state_path); sm.transition(self.state_path, "implementation", "in_progress", "test", "ready", s["revision"])
        decision = {"status": "CLASSIFIED" if classified else "NEEDS_EVIDENCE", "flow_profile": "STANDARD" if classified else None}
        (self.intake / "decision.json").write_text(json.dumps(decision), encoding="utf-8")
        (self.intake / "semantic-impact.json").write_text(json.dumps({"snapshot":{"id":"A1"},"changes":{"changed_symbols":[]},"impact":{"impacted_symbol_count":0,"affected_modules":[],"affected_services":[]},"boundaries":{},"completeness":{"complete":True}}), encoding="utf-8")
        (self.intake / "work-facts.semantic-draft.json").write_text(json.dumps({"extraction":{"analysis_snapshot_id":"A1","resolution_queue":[]},"risk":{},"scope":{},"complexity":{},"verification":{},"ambiguity":{},"novelty":{}}), encoding="utf-8")
        (self.intake / "verification-plan.json").write_text(json.dumps({"items":[]}), encoding="utf-8")
        (self.intake / "request-context.md").write_text("Implement change", encoding="utf-8")
        s = sm._load(self.state_path)
        sm.attach_analysis(self.state_path, "test", "A1", str(self.intake/"semantic-impact.json"), str(self.intake/"work-facts.semantic-draft.json"), str(self.intake/"decision.json"), str(self.intake/"verification-plan.json"), "cbm", s["revision"], context_manifest_ref=str(self.intake/"context-manifest.json"), context_pack_ref=str(self.intake/"context-pack.implementer.implementation.json"))
        manifest = context_plane.build_manifest(self.repo, self.intake, state_ref=self.state_path, request_ref=self.intake/"request-context.md", sdd_provider="generic")
        context_plane._dump_json(self.intake/"context-manifest.json", manifest)
        pack = context_plane.build_pack(manifest, "implementer", "implementation")
        context_plane._dump_json(self.intake/"context-pack.implementer.implementation.json", pack)
        (self.intake/"context-pack.implementer.implementation.md").write_text(context_plane.render_pack(pack), encoding="utf-8")
        # establish runtime baseline
        ek.handle(self.repo, "claude-code", "session_start", {"session_id":"s1"})

    def close(self): self.td.cleanup()


class EnforcementKernelTests(unittest.TestCase):
    def test_missing_state_blocks_code_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); (repo/"src").mkdir();
            r=ek.handle(repo,"codex","pre_tool",{"tool_name":"apply_patch","tool_input":{"command":"*** Update File: src/A.java"}})
            self.assertEqual("deny", r["decision"])

    def test_unclassified_decision_blocks_code_mutation(self):
        fx=EnforcementFixture(classified=False)
        try:
            r=ek.handle(fx.repo,"claude-code","pre_tool",{"tool_name":"Write","tool_input":{"file_path":"src/main/java/com/acme/App.java"}})
            self.assertEqual("deny",r["decision"]); self.assertIn("CLASSIFIED", r["reason"])
        finally: fx.close()

    def test_wrong_phase_blocks_code_mutation(self):
        fx=EnforcementFixture(classified=True, phase="discovery")
        try:
            r=ek.handle(fx.repo,"claude-code","pre_tool",{"tool_name":"Write","tool_input":{"file_path":"src/main/java/com/acme/App.java"}})
            self.assertEqual("deny",r["decision"]); self.assertIn("phase=discovery", r["reason"])
        finally: fx.close()

    def test_fresh_implementation_allows_mutation(self):
        fx=EnforcementFixture()
        try:
            r=ek.handle(fx.repo,"claude-code","pre_tool",{"tool_name":"Write","tool_input":{"file_path":"src/main/java/com/acme/App.java"}})
            self.assertEqual("allow",r["decision"])
        finally: fx.close()

    def test_post_mutation_marks_state_dirty_and_verification_stale(self):
        fx=EnforcementFixture()
        try:
            p=fx.repo/"src/main/java/com/acme/App.java"; p.write_text("class App { int x; }\n",encoding="utf-8")
            r=ek.handle(fx.repo,"claude-code","post_tool",{"tool_name":"Write","tool_input":{"file_path":str(p)}})
            s=sm._load(fx.state_path)
            self.assertTrue(s["enforcement"]["dirty"])
            self.assertFalse(s["enforcement"]["semantic_fresh"])
            self.assertFalse(s["verification"]["fresh"])
            self.assertIn("stale", r["additional_context"].lower())
        finally: fx.close()

    def test_dirty_state_blocks_review_transition(self):
        fx=EnforcementFixture()
        try:
            p=fx.repo/"src/main/java/com/acme/App.java"; p.write_text("class App { int x; }\n",encoding="utf-8")
            ek.handle(fx.repo,"claude-code","post_tool",{"tool_name":"Write","tool_input":{"file_path":str(p)}})
            s=sm._load(fx.state_path)
            sm.set_readiness(fx.state_path,"implementation_tasks_complete",True,"test","tasks",s["revision"])
            s=sm._load(fx.state_path)
            with self.assertRaises(sm.TransitionDenied):
                sm.transition(fx.state_path,"review","in_progress","test","review",s["revision"])
        finally: fx.close()

    def test_reanalysis_clears_semantic_dirty_guard(self):
        fx=EnforcementFixture()
        try:
            p=fx.repo/"src/main/java/com/acme/App.java"; p.write_text("class App { int x; }\n",encoding="utf-8")
            ek.handle(fx.repo,"claude-code","post_tool",{"tool_name":"Write","tool_input":{"file_path":str(p)}})
            s=sm._load(fx.state_path)
            sm.attach_analysis(fx.state_path,"test","A2",str(fx.intake/"semantic-impact.json"),str(fx.intake/"work-facts.semantic-draft.json"),str(fx.intake/"decision.json"),str(fx.intake/"verification-plan.json"),"cbm",s["revision"])
            self.assertFalse(sm._load(fx.state_path)["enforcement"]["dirty"])
            self.assertTrue(sm._load(fx.state_path)["enforcement"]["semantic_fresh"])
        finally: fx.close()

    def test_completion_claim_is_blocked_when_governance_not_ready(self):
        fx=EnforcementFixture()
        try:
            r=ek.handle(fx.repo,"codex","stop",{"session_id":"s1","last_assistant_message":"Done. Implementation completed."})
            self.assertEqual("deny",r["decision"])
            self.assertIn("Cannot claim completion",r["reason"])
        finally: fx.close()

    def test_non_completion_mid_turn_can_stop(self):
        fx=EnforcementFixture()
        try:
            r=ek.handle(fx.repo,"claude-code","stop",{"session_id":"s1","last_assistant_message":"I found the failing branch and need to continue next turn."})
            self.assertEqual("allow",r["decision"])
        finally: fx.close()

    def test_runtime_rebaselines_after_reanalysis_then_detects_external_change(self):
        fx=EnforcementFixture()
        try:
            p=fx.repo/"src/main/java/com/acme/App.java"
            p.write_text("class App { int x; }\n",encoding="utf-8")
            ek.handle(fx.repo,"claude-code","post_tool",{"tool_name":"Write","tool_input":{"file_path":str(p)}})
            s=sm._load(fx.state_path)
            sm.attach_analysis(fx.state_path,"test","A2",str(fx.intake/"semantic-impact.json"),str(fx.intake/"work-facts.semantic-draft.json"),str(fx.intake/"decision.json"),str(fx.intake/"verification-plan.json"),"cbm",s["revision"])
            ek.handle(fx.repo,"claude-code","session_start",{"session_id":"s1"})
            self.assertFalse(ek.load_runtime(fx.repo)["dirty"])
            p.write_text("class App { int x; int y; }\n",encoding="utf-8")
            ek.handle(fx.repo,"claude-code","prompt_submit",{"session_id":"s1"})
            self.assertTrue(sm._load(fx.state_path)["enforcement"]["dirty"])
        finally: fx.close()

    def test_host_format_pretool_deny(self):
        out=ek.format_host_output("codex","pre_tool",{"decision":"deny","reason":"x","additional_context":None})
        self.assertEqual("deny",out["hookSpecificOutput"]["permissionDecision"])


if __name__ == "__main__": unittest.main()
