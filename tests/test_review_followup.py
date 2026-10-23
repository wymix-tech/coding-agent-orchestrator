"""Regressions for the 2026-10-14 branch review, through real CLI/host events."""
import json
import os
import shlex
import subprocess
import sys
import unittest

from scripts import cao
from scripts.native_obligations import commit_state, review_state
from tests import test_native_route_sync as fixtures
from tests import repair_helpers
from tests.helpers import bind_light_spec, fixture_native_review
from tests.test_provider_light_adaptation import ONESHOT


class ReviewFollowup(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.RouteSync()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def switch_work(self):
        t = self.case
        t.write(".orchestrator/requirements/002-other.md", "# Independent work\n")
        t.run_cli("start", "--requirement", ".orchestrator/requirements/002-other.md", "--replace")
        t.wid = json.loads((t.repo / ".orchestrator/index.json").read_text())["active_work"]
        t.run_cli("path", "assess", "--profile", "standard", "--reason", "Second work")
        t.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build")
        t.spec = "_bmad-output/implementation-artifacts/spec-other.md"
        t.write(t.spec, "---\nstatus: draft\nroute: dispatch\n---\n"
                "<frozen-after-approval>\n## Intent\nIndependent work.\n</frozen-after-approval>\n")
        t.propose()
        t.run_cli("host-event", "--host", "pi", raw={"event": "session_start", "session_id": "second-session"})
        t.run_cli("sdd", "approval", "request")

    def question(self, call_id):
        self.case.hook("pre_tool", tool_name="ask_user_question", toolCallId=call_id,
                       input={"questions": [{"question": "Approve SPEC?"}]})

    def answer(self, call_id):
        self.case.hook("post_tool", tool_name="ask_user_question", toolCallId=call_id,
                       details={"answers": [{"answer": "批准并继续"}]})

    def test_delayed_question_result_cannot_approve_another_work(self):
        t = self.case
        t.propose()
        t.run_cli("sdd", "approval", "request")
        self.question("question-a")
        original = t.wid
        self.switch_work()
        self.answer("question-a")
        self.assertEqual("pending", t.work()["sdd"]["spec_approval"]["status"])
        self.assertEqual("pending", cao.load_work(t.repo, original)["sdd"]["spec_approval"]["status"])
        self.assertEqual(original, cao.load_session(t.repo, "primary-fixture", host="pi")["work_item_id"])

    def test_delayed_result_before_request_does_not_authorize_old_session(self):
        t = self.case
        t.propose()
        t.run_cli("sdd", "approval", "request")
        self.switch_work()
        t.hook("post_tool", tool_name="read", toolCallId="old-read", content=[])
        raw = t.declaration()
        raw["reason"] += " Refreshed pending approval."
        t.propose(raw)
        t.run_cli("sdd", "approval", "request")
        t.hook("prompt_submit", prompt="批准并继续")
        self.assertEqual("pending", t.work()["sdd"]["spec_approval"]["status"])

    def test_old_session_prompt_cannot_approve_new_work(self):
        t = self.case
        t.propose()
        t.run_cli("sdd", "approval", "request")
        self.switch_work()
        t.hook("prompt_submit", prompt="批准并继续")
        self.assertEqual("pending", t.work()["sdd"]["spec_approval"]["status"])

    def test_question_result_cannot_approve_a_replaced_request_in_same_session(self):
        t = self.case
        t.propose()
        t.run_cli("sdd", "approval", "request")
        self.question("old-question")
        raw = t.declaration()
        raw["reason"] += " Reassessed."
        t.propose(raw)
        t.run_cli("sdd", "approval", "request")
        self.answer("old-question")
        self.assertEqual("pending", t.work()["sdd"]["spec_approval"]["status"])
        self.question("new-question")
        self.answer("new-question")
        self.assertEqual("approved", t.work()["sdd"]["spec_approval"]["status"])

    def test_oneshot_obligations_apply_to_all_quality_profiles(self):
        t = self.case
        for profile in ("standard", "comprehensive"):
            with self.subTest(profile=profile):
                t.run_cli("path", "assess", "--profile", profile, "--reason", "Review route obligations")
                t.write(t.spec, ONESHOT)
                raw = t.declaration()
                raw.update(profile=profile, build_route="oneshot")
                t.propose(raw)
                self.assertEqual("NATIVE_REVIEW_REQUIRED", review_state(t.repo, t.work(), cao.code_snapshot)["reason"])
                t.run_cli("verify", expected=2)
                subprocess.run(["git", "init", "-q"], cwd=t.repo, check=True)
                self.assertEqual("NATIVE_SPEC_UNTRACKED", commit_state(t.repo, t.work(), cao.SNAPSHOT_IGNORE_DIRS)["reason"])

    def test_unchecked_direct_spec_blocks_verify_and_done_even_with_no_plan(self):
        t = self.case
        t.propose()
        t.approve()
        t.run_cli("sdd", "workflow", "execute", "--command", "bmad-build")
        t.run_cli("sdd", "workflow", "complete", "--artifact", t.spec, "--no-plan")
        out = t.run_cli("verify", expected=2)
        self.assertEqual("BMAD_SPEC_TASKS_NOT_DONE", out["error"])
        self.assertIn("Implement login", str(out["verification_plan"]["pending_plan_items"]))
        self.assertEqual("BMAD_SPEC_TASKS_NOT_DONE", t.run_cli("done", expected=2)["reason"])

    def test_checked_direct_spec_ignores_unassociated_project_sprint(self):
        t = self.case
        t.propose()
        t.approve()
        t.write(t.spec, (t.repo / t.spec).read_text().replace("[ ]", "[x]"))
        t.write("_bmad-output/implementation-artifacts/sprint-status.yaml",
                "development_status:\n  epic-9: in-progress\n  9-1-unrelated: backlog\n")
        state = cao.provider_verification_plan_state(t.repo, t.work())
        self.assertTrue(state["ready"], state)
        self.assertNotIn("sprint-status", str(state.get("observations")))

    def test_explicit_sprint_association_is_still_enforced_for_direct_build(self):
        t = self.case
        t.write("_bmad-output/sprint-status.yaml", "development_status:\n  1-1-login: in-progress\n")
        t.propose()
        t.approve()
        t.write(t.spec, (t.repo / t.spec).read_text().replace("[ ]", "[x]"))
        t.run_cli("sdd", "plan", "bind", "--plan-item", "_bmad-output/sprint-status.yaml#1-1-login")
        self.assertFalse(cao.provider_verification_plan_state(t.repo, t.work())["ready"])

    def test_removing_or_replacing_observed_spec_tasks_cannot_shrink_scope(self):
        t = self.case
        t.propose()
        t.approve()
        original = (t.repo / t.spec).read_text()
        for replacement in ("", "- [x] Unrelated replacement\n"):
            with self.subTest(replacement=replacement):
                t.write(t.spec, original.replace("- [ ] Implement login\n", replacement))
                out = t.run_cli("verify", expected=2)
                self.assertEqual("BMAD_SPEC_TASK_REMOVED", out["error"])

    def test_new_tasks_are_retained_after_sync(self):
        t = self.case
        t.propose()
        t.approve()
        original = (t.repo / t.spec).read_text().replace("[ ]", "[x]")
        t.write(t.spec, original + "- [ ] Added native task\n")
        t.run_cli("sdd", "sync")
        t.write(t.spec, original)
        state = cao.provider_verification_plan_state(t.repo, t.work())
        self.assertEqual("blocked", state["state"])
        self.assertTrue(state["reason"].startswith("BMAD_SPEC_TASK_REMOVED"))

    def test_late_baseline_approval_cannot_cross_work(self):
        t = self.case
        t.propose()
        t.run_cli("verification", "configure", "--test-script", "different-tests", "--replace")
        self.switch_work()
        t.run_cli("verification", "configure", "--test-script", "another-tests", "--replace")
        t.hook("prompt_submit", prompt="批准验证基线")
        self.assertEqual("pending", t.work()["verification_review"]["status"])

    def test_legacy_baseline_request_can_be_recreated_without_changing_scripts(self):
        t = self.case
        t.run_cli("verification", "configure", "--test-script", "changed-tests", "--replace")
        work = t.work()
        old = work["verification_review"]["id"]
        work["verification_review"].pop("work_item_id")
        work["verification_review"].pop("contexts")
        cao.save_work(t.repo, work)
        t.run_cli("verification", "configure", "--test-script", "changed-tests")
        self.assertNotEqual(old, t.work()["verification_review"]["id"])
        t.hook("prompt_submit", prompt="批准验证基线")
        t.run_cli("reopen", "--reanchor", "--reason", "User approved current baseline")
        self.assertTrue(cao.verification_baseline_state(t.repo, t.work())["ready"])

    def test_legacy_approved_baseline_is_requested_again_before_reanchor(self):
        t = self.case
        t.run_cli("verification", "configure", "--test-script", "changed-tests", "--replace")
        work = t.work()
        work["verification_review"].update(status="approved")
        work["verification_review"].pop("work_item_id")
        work["verification_review"].pop("contexts")
        cao.save_work(t.repo, work)
        out = t.run_cli("reopen", "--reanchor", "--reason", "Upgrade legacy review", expected=2)
        self.assertEqual("VERIFICATION_REVIEW_REQUIRED", out["error"])
        self.assertEqual("pending", t.work()["verification_review"]["status"])
        t.hook("prompt_submit", prompt="批准验证基线")
        t.run_cli("reopen", "--reanchor", "--reason", "Observed current user approval")

    def test_new_session_must_receive_request_context_before_approval(self):
        t = self.case
        t.propose()
        t.run_cli("sdd", "approval", "request")
        t.run_cli("host-event", "--host", "pi", raw={"event": "prompt_submit", "session_id": "unseen", "prompt": "批准并继续"})
        self.assertEqual("pending", t.work()["sdd"]["spec_approval"]["status"])
        t.run_cli("host-event", "--host", "pi", raw={"event": "prompt_submit", "session_id": "unseen", "prompt": "批准并继续"})
        self.assertEqual("approved", t.work()["sdd"]["spec_approval"]["status"])

    def test_standard_oneshot_requires_review_then_commit_and_can_finish(self):
        t = self.case
        t.write(t.spec, ONESHOT)
        raw = t.declaration()
        raw["build_route"] = "oneshot"
        t.propose(raw)
        t.write("tests/test_real.py", "import unittest\nclass T(unittest.TestCase):\n def test_one(self): self.assertEqual(1+1,2)\n")
        t.write("fixture-acceptance.py", "import json,os,subprocess,sys\n"
                "r=json.loads(os.environ['CAO_SDD_ACCEPTANCE_REQUEST'])\n"
                "p=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests'],stdout=sys.stderr,stderr=sys.stderr)\n"
                "print(json.dumps({'run_id':r['run_id'],'input_sha256':r['input_sha256'],'provider':r['provider'],"
                "'items':[{'id':'AC-1','status':'passed' if p.returncode==0 else 'failed',"
                "'evidence':[{'path':'tests/test_real.py','reason':'Executed local fixture unittest'}]}]}))\n"
                "sys.exit(p.returncode)\n")
        argv = [sys.executable, "fixture-acceptance.py"]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        declaration = {"spec": t.spec, "criteria": [{"id": "AC-1", "source": t.spec,
                       "kind": "requirement", "description": "Fixture unittest passes"}],
                       "executor": {"mode": "command", "entrypoint": command}}
        from scripts.acceptance_trace import criteria
        declaration["criteria"][0]["covers"] = [item["id"] for item in criteria(t.repo, t.work())]
        t.write("acceptance.json", json.dumps(declaration))
        t.run_cli("sdd", "acceptance", "configure", "--declaration", "acceptance.json")
        t.run_cli("reopen", "--reanchor", "--reason", "Reviewed native fixture acceptance")
        t.run_cli("sdd", "workflow", "execute", "--command", "bmad-build")
        t.run_cli("sdd", "workflow", "complete", "--artifact", t.spec, "--no-plan")
        for gate in ("scope_review", "implementation_review"):
            t.run_cli("gate", "satisfy", "--gate", gate, "--evidence", "Explicit fixture review")
        subprocess.run(["git", "init", "-q"], cwd=t.repo, check=True, capture_output=True)
        self.assertEqual("NATIVE_REVIEW_REQUIRED", t.run_cli("verify", expected=2)["error"])
        fixture_native_review(t.repo, lambda repo, *args: (0, t.run_cli(*args)), t.wid)
        t.run_cli("verify")
        t.run_cli("sdd", "acceptance", "run")
        self.assertEqual("NATIVE_SPEC_UNTRACKED", t.run_cli("done", expected=2)["reason"])
        (t.repo / ".git/info/exclude").write_text("__pycache__/\n*.py[cod]\n")
        subprocess.run(["git", "add", "-A"], cwd=t.repo, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                        "commit", "-qm", "Reviewed oneshot fixture"], cwd=t.repo, check=True, capture_output=True)
        self.assertEqual("completed", t.run_cli("done")["status"])

    def test_recovered_native_status_clears_only_automatic_blocker(self):
        t = self.case
        t.propose()
        t.approve()
        t.run_cli("block", "--work", t.wid, "--reason", "Manual dependency")
        content = (t.repo / t.spec).read_text()
        t.write(t.spec, content.replace("'draft'", "'blocked'"))
        t.run_cli("sdd", "sync")
        self.assertIn("native-sdd-state", {b["id"] for b in t.work()["blockers"]})
        t.write(t.spec, content.replace("'draft'", "'in-progress'"))
        t.run_cli("sdd", "sync")
        blockers = t.work()["blockers"]
        self.assertEqual(["Manual dependency"], [b["reason"] for b in blockers])
        events = cao.list_lifecycle_events(t.repo, t.wid)
        self.assertEqual(1, sum(e["event"] == "sdd.native_blocker.cleared" for e in events))
        t.run_cli("sdd", "sync")
        self.assertEqual(1, sum(e["event"] == "sdd.native_blocker.cleared" for e in cao.list_lifecycle_events(t.repo, t.wid)))


class FailedSwitch(unittest.TestCase):
    def test_invalid_target_baseline_does_not_suspend_current_work(self):
        t = repair_helpers.RepairCase()
        t.setUp()
        self.addCleanup(t.doCleanups)
        current = t.start("current")
        before = t.read_work(current)
        requirement = t.repo / ".orchestrator/requirements/next.md"
        requirement.write_text("# Next requirement\n", encoding="utf-8")
        cfg = cao.config(t.repo)
        cfg["verification"]["scripts"] = [{"command": "suite", "required": "invalid"}]
        cao.save_json(t.repo / cao.CONFIG_REL, cfg)
        rc, out = t.run_cli("start", "--requirement", requirement.relative_to(t.repo), "--replace")
        self.assertEqual(2, rc, out)
        self.assertEqual(before, t.read_work(current))
        self.assertEqual(current, cao.load_index(t.repo)["active_work"])
        self.assertEqual([], cao.list_handoff_records(t.repo, current))

    def test_rejected_completed_target_preserves_current_work_and_handoffs(self):
        t = repair_helpers.RepairCase()
        t.setUp()
        self.addCleanup(t.doCleanups)
        t.start("first")
        bind_light_spec(t.repo, lambda repo, *args: t.run_cli(*args), "_bmad-output/spec-first.md")
        self.assertEqual(0, t.run_cli("verify")[0])
        from tests.helpers import complete_acceptance
        complete_acceptance(t.repo, lambda repo, *args: t.run_cli(*args))
        self.assertEqual(0, t.run_cli("done")[0])
        second = t.start("second")
        before = t.read_work(second)
        handoffs = cao.list_handoff_records(t.repo, second)
        rc, out = t.run_cli("start", "--requirement", ".orchestrator/requirements/first.md", "--replace")
        self.assertEqual(2, rc)
        self.assertEqual("WORK_ITEM_ALREADY_COMPLETED", out["error"])
        self.assertEqual(before, t.read_work(second))
        self.assertEqual(second, cao.load_index(t.repo)["active_work"])
        self.assertEqual(handoffs, cao.list_handoff_records(t.repo, second))
