"""The 2026-10-23 session fails early, preserves Work, and recovers in place."""
import copy

from scripts import cao
from scripts.project_context import ROOT, commit_context, context_state, load_context
from scripts.project_sources import observe_prompt
from tests.repair_helpers import RepairCase


class IntakeRecovery(RepairCase):
    def test_unfilled_template_cannot_start_or_suspend_existing_work(self):
        wid = self.start()
        before = self.read_work(wid)
        rc, out = self.run_cli("requirement", "next-id", "--title", "登录系统")
        self.assertEqual(rc, 0, out)
        path = out["path"]
        rc, out = self.run_cli("start", "--requirement", path, "--replace")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "REQUIREMENT_CONTENT_REQUIRED")
        self.assertEqual(self.read_work(wid), before)
        target = self.repo / path
        target.write_text(target.read_text().replace("<!-- 在此描述需求。 -->", "生成一份项目目录清单。"))
        rc, out = self.run_cli("start", "--requirement", path, "--replace")
        self.assertEqual(rc, 0, out)
        self.assertNotEqual(out["work_item"]["id"], wid)

    def test_known_framework_cannot_become_build_tool_and_custom_names_remain_valid(self):
        before = (self.repo / ROOT / "context.json").read_bytes()
        evidence = observe_prompt(self.repo, "Python + FastAPI + Pytest", "pi", "fixture")
        rc, out = self.run_cli("project", "context", "set", "--build-tool", "FastAPI",
                               "--source", "user-specified", "--evidence", evidence)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "PROJECT_FIELD_CATEGORY_MISMATCH")
        self.assertEqual(out["diagnostic"]["expected_field"], "root.framework")
        self.assertIn("still-missing choice", out["next"]["instruction"])
        self.assertEqual((self.repo / ROOT / "context.json").read_bytes(), before)
        from scripts.project_technology import validate_categories
        validate_categories({"build_tool": "company-builder", "framework": "custom-framework"})

    def test_legacy_miscategorized_context_is_reported_and_can_be_corrected_with_evidence(self):
        old = load_context(self.repo)
        invalid = copy.deepcopy(old)
        invalid["scopes"]["root"]["technology"]["build_tool"]["value"] = "FastAPI"
        saved = commit_context(self.repo, invalid, old["revision"])
        state = context_state(self.repo)
        self.assertFalse(state["ready"])
        self.assertIn("root.build_tool", state["conflicts"])
        evidence = observe_prompt(self.repo, "构建工具使用 uv，框架 FastAPI", "pi", "fixture")
        rc, out = self.run_cli("project", "context", "set", "--build-tool", "uv", "--framework", "FastAPI",
                               "--source", "user-specified", "--evidence", evidence)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["state"]["ready"], out)
        self.assertEqual(out["context"]["previous_revision"], saved["revision"])

    def test_explicit_authentication_body_cannot_silently_select_light(self):
        wid = self.start()
        work = self.read_work(wid)
        req = self.repo / work["requirement"]["path"]
        req.write_text("# 功能需求\n开发一个登录系统，需要密码哈希和 JWT。\n")
        self.assertEqual(self.run_cli("start", "--requirement", req, "--replace")[0], 2)
        self.assertEqual(self.run_cli("reopen", "--work", wid, "--reanchor", "--reason", "explicit revised authentication requirement")[0], 0)
        rc, out = self.run_cli("path", "assess", "--profile", "light", "--reason", "Agent forgot risk flags")
        self.assertEqual(rc, 0, out)
        assessment = out["path_assessment"]
        self.assertEqual(assessment["profile"], "comprehensive")
        self.assertIn("security", assessment["risk_flags"])
        self.assertIn("security_verification", assessment["required_gates"])
        work = self.read_work(wid)
        work["path_assessment"].update(profile="light", risk_flags=[], required_gates=["targeted_verification"])
        work["resume"]["completed"] = ["preserve actual progress"]
        cao.save_json(self.repo / f".orchestrator/work/{wid}.json", work)
        rc, out = self.run_cli("next")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["action"]["reason"], "PATH_RISK_REASSESSMENT_REQUIRED")
        self.assertEqual(self.read_work(wid)["resume"]["completed"], ["preserve actual progress"])

    def test_comments_and_explicitly_excluded_authentication_do_not_escalate(self):
        from scripts.requirement_content import security_evidence
        path = self.repo / "scope.md"
        path.write_text("# 文档整理\n<!-- 开发一个登录系统 -->\n更新目录清单，不修改登录系统。\n")
        self.assertEqual(security_evidence(path), [])
        path.write_text("# 功能\n<!-- some\ncomments -->\n实现用户认证服务。\n")
        self.assertEqual(security_evidence(path)[0]["line"], 4)
