"""Regression for the fresh-project BMAD session of 2026-10-14.

These fixtures exercise CLI/host contracts, not semantic BMAD reviewer quality.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import cao
from scripts.dashboard import build_dashboard_overview

ROOT = Path(__file__).resolve().parents[1]


class RouteSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.env = {**os.environ, "CAO_HOST": "", "PI_SESSION_ID": "", "CODEX_THREAD_ID": ""}
        self.run_cli("init", "--sdd", "bmad")
        self.write("_bmad/bmm/config.yaml", "communication_language: Simplified Chinese\n")
        self.write(".agents/skills/bmad-build/SKILL.md", "---\nname: bmad-build\ndescription: Build\n---\nUse the project renderer and follow routing rules.\n")
        from tests.helpers import provision_bmad
        provision_bmad(self.repo)
        self.rule = "_bmad/render/bmad-build/step-02-plan.md"
        self.quote = "Otherwise write the full spec. Set route: 'dispatch' and continue."
        self.write(self.rule, self.quote + "\nApprove and continue or approve and stop before implementation.\n")
        self.spec = "_bmad-output/implementation-artifacts/spec-login-system.md"
        self.write(self.spec, "---\nstatus: 'draft'\nroute: 'dispatch'\n---\n"
                   "<frozen-after-approval>\n## Intent\n用户配置文件登录。\n"
                   "## Acceptance\n有效密码返回令牌；错误密码被拒绝。\n</frozen-after-approval>\n"
                   "## Tasks & Acceptance\n- [ ] Implement login\n")
        self.write(".orchestrator/requirements/001-登录系统.md", "# 登录系统\n用户配置在配置文件中。\n")
        self.run_cli("start", "--requirement", ".orchestrator/requirements/001-登录系统.md")
        self.wid = json.loads((self.repo / ".orchestrator/index.json").read_text())["active_work"]
        self.run_cli("verification", "configure", "--test-script", f"{sys.executable} -m unittest discover -s tests")
        self.run_cli("path", "assess", "--profile", "standard", "--reason", "安全敏感认证")
        self.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build")
        self.hook("session_start")

    def write(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")

    def run_cli(self, *args, expected=0, raw=None):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/cao.py"), *args],
                              cwd=self.repo, env=self.env, input=json.dumps(raw) if raw else None,
                              text=True, capture_output=True, timeout=30)
        if args and args[0] == "init" and proc.returncode == 0:
            from tests.helpers import provision_fixture_background
            provision_fixture_background(self.repo)
        self.assertEqual(expected, proc.returncode, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def work(self):
        return cao.load_work(self.repo, self.wid)

    def declaration(self):
        return {"provider": "bmad", "provider_version": "fixture-6.12.2",
                "requirement_sha256": self.work()["requirement"]["sha256"], "profile": "standard",
                "workflow": "bmad-build", "route": "direct-build", "build_route": "dispatch",
                "reason": "单一交付目标；原生完整 Build spec 承载需求、设计与验收。",
                "primary_artifact": self.spec,
                "required_artifacts": [{"path": self.spec, "role": "spec"}],
                "rules": [{"path": self.rule, "sha256": hashlib.sha256((self.repo / self.rule).read_bytes()).hexdigest(), "quote": self.quote}],
                "omitted": [{"name": "standalone planning documents", "reason": "职责由当前 spec 承载", "replaced_by": self.spec}]}

    def propose(self, raw=None, expected=0):
        self.write("route.json", json.dumps(raw or self.declaration(), ensure_ascii=False))
        return self.run_cli("sdd", "route", "propose", "--declaration", "route.json", expected=expected)

    def hook(self, event, **extra):
        return self.run_cli("host-event", "--host", "pi", raw={"event": event, "session_id": "primary-fixture", **extra})

    def product_write(self):
        return self.hook("pre_tool", tool_name="write", tool_input={"path": "src/auth.py", "content": "pass"})

    def approve(self, text="批准并继续"):
        self.run_cli("sdd", "approval", "request")
        return self.hook("prompt_submit", prompt=text)

    def test_initial_baseline_does_not_reopen_or_reset_phase(self):
        work = self.work()
        self.assertNotEqual("reopened", work["execution"]["step"])
        self.assertTrue(cao.verification_baseline_state(self.repo, work)["ready"])
        self.assertTrue(any(e["event"] == "verification.baseline.established" for e in cao.list_lifecycle_events(self.repo)))
        out = self.run_cli("verification", "configure", "--test-script", "different-tests", "--replace")
        self.assertTrue(out["acceptance_review_required"])
        self.assertFalse(out["initial_baseline_established"])

    def test_changed_baseline_cannot_be_self_approved_by_reanchor_reason(self):
        out = self.run_cli("verification", "configure", "--test-script", "changed-real-suite", "--replace")
        self.assertTrue(out["acceptance_review_required"])
        self.assertEqual("VERIFICATION_REVIEW_REQUIRED", self.run_cli(
            "reopen", "--reanchor", "--reason", "Agent says human reviewed", expected=2)["error"])
        self.hook("prompt_submit", prompt="继续")
        self.assertEqual("VERIFICATION_REVIEW_REQUIRED", self.run_cli(
            "reopen", "--reanchor", "--reason", "Reviewed", expected=2)["error"])
        self.hook("prompt_submit", prompt="批准验证基线")
        self.run_cli("reopen", "--reanchor", "--reason", "User approved changed scripts")
        self.assertTrue(cao.verification_baseline_state(self.repo, self.work())["ready"])

    def test_missing_route_blocks_product_but_allows_native_and_scaffold(self):
        self.assertEqual("deny", self.product_write()["decision"])
        for path in [self.spec, "pyproject.toml", "tests/test_auth.py"]:
            self.assertEqual("allow", self.hook("pre_tool", tool_name="write", tool_input={"path": path})["decision"])

    def test_direct_build_keeps_standard_and_requires_explicit_approval(self):
        before = self.work()["path_assessment"]
        out = self.propose()
        self.assertEqual("NATIVE_SPEC_APPROVAL_REQUIRED", out["implementation"]["reason"])
        self.assertEqual(before, self.work()["path_assessment"])
        self.assertEqual("deny", self.product_write()["decision"])
        self.approve()
        self.assertEqual("allow", self.product_write()["decision"])
        evidence = self.work()["sdd"]["spec_approval"]["evidence"]
        self.assertEqual("prompt_submit", evidence["event"])
        self.assertEqual("批准并继续", evidence["response"])

    def test_technical_answers_and_continue_are_not_spec_approval(self):
        self.propose(); self.run_cli("sdd", "approval", "request")
        self.hook("prompt_submit", prompt="批准并继续\n仅适用于本夹具")
        self.assertEqual("pending", self.work()["sdd"]["spec_approval"]["status"])
        hint = self.run_cli("sdd", "approval", "show")["next"]
        self.assertIn("standalone", hint)
        self.hook("post_tool", tool_name="ask_user_question", details={"answers": [{"answer": "HS256 (推荐)"}]})
        self.hook("prompt_submit", prompt="继续")
        self.assertEqual("pending", self.work()["sdd"]["spec_approval"]["status"])
        self.assertEqual("deny", self.product_write()["decision"])
        self.hook("pre_tool", tool_name="ask_user_question", toolCallId="spec-approval-question")
        self.hook("post_tool", tool_name="ask_user_question", toolCallId="spec-approval-question",
                  details={"answers": [{"answer": "批准并继续"}]})
        self.assertEqual("allow", self.product_write()["decision"])

    def test_stop_approval_does_not_authorize_implementation(self):
        self.propose(); self.approve("批准并停止")
        self.assertIn("NATIVE_IMPLEMENTATION_PAUSED", self.product_write()["reason"])
        self.hook("prompt_submit", prompt="继续实现")
        self.assertEqual("allow", self.product_write()["decision"])

    def test_native_implementation_observed_but_unapproved_is_blocked(self):
        self.propose()
        content = (self.repo / self.spec).read_text().replace("'draft'", "'in-progress'")
        self.write(self.spec, content)
        self.hook("post_tool", tool_name="edit", input={"path": self.spec})
        work = self.work()
        self.assertEqual("implementation", work["execution"]["step"])
        self.assertEqual("in_progress", work["sdd"]["native_state"]["canonical"])
        self.assertFalse(work["sdd"]["implementation_state"]["ready"])
        self.assertEqual("deny", self.product_write()["decision"])
        out = self.run_cli("verify", expected=2)
        self.assertEqual("NATIVE_SPEC_APPROVAL_REQUIRED", out["error"])

    def test_english_spec_is_explicitly_bound_to_chinese_requirement(self):
        self.propose(); self.approve()
        self.write(self.spec, (self.repo / self.spec).read_text().replace("'draft'", "'in-progress'"))
        out = self.run_cli("sdd", "sync")
        self.assertEqual("implementation", out["work_item"]["step"])
        self.assertEqual(self.spec, out["native_sync"]["source"])
        overview = build_dashboard_overview(self.repo)
        view = next(w for w in overview["work_items"] if w["id"] == self.wid)
        self.assertEqual("implementation", view["execution"]["current_phase"])
        self.assertEqual("direct-build", view["sdd"]["route_decision"]["route"])
        self.assertTrue(view["implementation_state"]["ready"])
        self.assertNotEqual("completed", self.work()["sdd"]["workflow"]["status"])

    def test_rule_changes_invalidate_route(self):
        self.propose(); self.approve()
        self.write(self.rule, self.quote + "\nChanged native rule.\n")
        self.assertIn("NATIVE_ROUTE_RULES_CHANGED", self.product_write()["reason"])

    def test_intent_change_invalidates_approval_and_route_but_status_does_not(self):
        self.propose(); self.approve()
        content = (self.repo / self.spec).read_text().replace("'draft'", "'in-progress'").replace("[ ]", "[x]")
        self.write(self.spec, content)
        self.assertEqual("allow", self.product_write()["decision"])
        self.write(self.spec, content.replace("错误密码被拒绝", "错误密码也被接受"))
        self.assertIn("NATIVE_ROUTE_INTENT_CHANGED", self.product_write()["reason"])

    def test_route_changes_preserve_history_and_invalidate_old_approval(self):
        first = self.propose()["route"]; self.approve()
        raw = self.declaration(); raw["reason"] += " 已重新核对边界。"
        second = self.propose(raw)["route"]
        self.assertEqual(first["id"], second["supersedes"])
        history = self.run_cli("sdd", "route", "show")["history"]
        self.assertEqual([first["id"], second["id"]], [d["id"] for d in history])
        self.assertNotIn("spec_approval", self.work()["sdd"])
        self.assertEqual("deny", self.product_write()["decision"])

    def test_identical_proposal_preserves_approval_and_history(self):
        first = self.propose()["route"]; self.approve()
        before = self.work()
        out = self.propose()
        self.assertEqual("unchanged", out["status"])
        self.assertEqual(first["id"], out["route"]["id"])
        self.assertEqual(before, self.work())

    def test_approval_request_retry_is_idempotent(self):
        self.propose()
        first = self.run_cli("sdd", "approval", "request")["approval"]
        self.assertEqual(first, self.run_cli("sdd", "approval", "request")["approval"])
        self.hook("prompt_submit", prompt="批准并继续")
        before = self.work()["sdd"]["spec_approval"]
        self.assertEqual(before, self.run_cli("sdd", "approval", "request")["approval"])

    def test_invalid_evidence_and_profile_downgrade_do_not_mutate_work(self):
        for key, value, error in [("profile", "light", "NATIVE_ROUTE_PROFILE_CHANGE_REQUIRES_ASSESSMENT"),
                                  ("omitted", [], "NATIVE_ROUTE_OMISSIONS_REQUIRED")]:
            raw = self.declaration(); raw[key] = value
            self.assertEqual(error, self.propose(raw, expected=2)["error"])
            self.assertNotIn("route_decision", self.work()["sdd"])
        raw = self.declaration(); raw["rules"][0]["quote"] = "invented rule"
        self.assertEqual("NATIVE_ROUTE_RULE_EVIDENCE_MISMATCH", self.propose(raw, expected=2)["error"])

    def test_request_cannot_approve_changed_intent(self):
        self.propose(); self.run_cli("sdd", "approval", "request")
        self.write(self.spec, (self.repo / self.spec).read_text().replace("用户配置文件登录", "匿名访问"))
        self.hook("prompt_submit", prompt="批准并继续")
        self.assertEqual("pending", self.work()["sdd"]["spec_approval"]["status"])
        self.assertIn("NATIVE_ROUTE_INTENT_CHANGED", self.product_write()["reason"])

    def test_open_questions_take_precedence_over_approval_and_checkpoint(self):
        self.write(self.spec, (self.repo / self.spec).read_text() + "\n## Open Questions\n- API 还是网页？\n")
        out = self.propose()
        self.assertEqual("NATIVE_SPEC_CLARIFICATION_REQUIRED", out["implementation"]["reason"])
        self.run_cli("checkpoint", "--step", "native-planning-open-questions", "--next", "等待用户选择交付形态")
        self.run_cli("list")
        self.assertEqual("human", self.work()["execution"]["next_action"]["type"])
        self.assertEqual("NATIVE_SPEC_CLARIFICATION_REQUIRED", self.work()["execution"]["next_action"]["reason"])
        self.assertEqual("NATIVE_SPEC_CLARIFICATION_REQUIRED", self.run_cli("sdd", "approval", "request", expected=2)["error"])
        self.assertNotIn("spec_approval", self.work()["sdd"])
        self.assertEqual("deny", self.product_write()["decision"])
        # An empty template comment is not a real unresolved question.
        content = (self.repo / self.spec).read_text().replace("- API 还是网页？", "<!-- Resolved -->")
        self.write(self.spec, content)
        self.assertEqual("NATIVE_SPEC_APPROVAL_REQUIRED", self.run_cli("sdd", "sync")["implementation"]["reason"])

    def test_removing_skill_does_not_remove_required_route(self):
        (self.repo / ".agents/skills/bmad-build/SKILL.md").unlink()
        result = self.product_write()
        self.assertEqual("deny", result["decision"])
        self.assertIn("SDD_NOT_INSTALLED", result["reason"])

    def test_required_artifact_cannot_escape_through_symlink(self):
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / "other.md"; target.write_text("unrelated")
            link = self.repo / "_bmad-output/linked.md"; link.symlink_to(target)
            raw = self.declaration(); raw["required_artifacts"][0]["path"] = "_bmad-output/linked.md"
            self.assertEqual("PATH_OUTSIDE_REPOSITORY", self.propose(raw, expected=2)["error"])

    def test_full_planning_requires_all_declared_planning_roles(self):
        raw = self.declaration(); raw["route"] = "full-planning"; raw["omitted"] = []
        self.assertEqual("NATIVE_ROUTE_FULL_PLANNING_ARTIFACTS_REQUIRED", self.propose(raw, expected=2)["error"])
        for role in ("prd", "architecture", "epics", "sprint"):
            path = "_bmad-output/sprint-status.yaml" if role == "sprint" else f"_bmad-output/{role}.md"
            self.write(path, "development_status:\n  1-1-login: ready-for-dev\n" if role == "sprint" else f"# {role}\nActual fixture document")
            raw["required_artifacts"].append({"path": path, "role": role})
        out = self.propose(raw)
        self.assertEqual("NATIVE_ROUTE_PLAN_SCOPE_REQUIRED", out["implementation"]["reason"])
        self.run_cli("sdd", "plan", "bind", "--plan-item", "_bmad-output/sprint-status.yaml#1-1-login")
        self.approve()
        self.assertEqual("allow", self.product_write()["decision"])

    def test_light_oneshot_needs_no_workflow_registration(self):
        work = self.work(); work["sdd"].pop("workflow"); cao.save_work(self.repo, work)
        self.run_cli("path", "assess", "--profile", "light", "--reason", "Bounded reversible fixture")
        self.write(self.spec, (self.repo / self.spec).read_text().replace("'dispatch'", "'oneshot'"))
        raw = self.declaration(); raw["profile"] = "light"; raw["build_route"] = "oneshot"
        out = self.propose(raw)
        self.assertTrue(out["implementation"]["ready"])
        self.assertNotIn("workflow", self.work()["sdd"])

    def test_reanchor_preserves_implementation_and_does_not_restore_approval(self):
        self.propose(); self.approve()
        self.write(self.spec, (self.repo / self.spec).read_text().replace("'draft'", "'in-progress'"))
        self.run_cli("sdd", "sync")
        started = self.work()["started_at"]
        self.run_cli("reopen", "--reanchor", "--reason", "Reviewed changed baseline")
        self.assertEqual("implementation", self.work()["execution"]["step"])
        self.assertEqual(started, self.work()["started_at"])
        self.assertTrue(cao.native_route_state(self.repo, self.work())["ready"])

    def test_explicit_bind_never_certifies_execution_or_picks_unrelated_spec(self):
        self.write("_bmad-output/implementation-artifacts/spec-other.md", "---\nstatus: done\n---\nUnrelated")
        out = self.run_cli("sdd", "artifact", "bind", "--artifact", self.spec)
        self.assertEqual(self.spec, out["native_sync"]["source"])
        self.assertIsNone(self.work()["sdd"]["workflow"]["execution"])
        self.assertNotEqual("completed", self.work()["status"])


if __name__ == "__main__":
    unittest.main()
