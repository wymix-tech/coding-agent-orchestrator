"""Technology clarification reaches the real intake, setup and native route surfaces."""
import pathlib
import shlex
import sys
import tempfile

from tests.repair_helpers import RepairCase
from scripts.provider_restart import require_restart


class TechnologyGuidance(RepairCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name)
        code, out = self.run_cli("init", "--host", "pi", "--sdd", "bmad")
        self.assertEqual(code, 0, out)
        (self.repo / "_bmad").mkdir()
        from tests.helpers import provision_bmad
        provision_bmad(self.repo)
        skill = self.repo / ".agents/skills/bmad-build/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: bmad-build\ndescription: Native fixture\n---\nFollow native planning.\n")

    def hook(self, event, **data):
        code, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": event, "session_id": "same-conversation", **data})
        self.assertEqual(code, 0, out)
        return out

    def start_work(self, text):
        code, out = self.run_cli("requirement", "next-id", "--title", "登录系统")
        self.assertEqual(code, 0, out)
        path = self.repo / out["path"]
        self.assertIn("待确认", path.read_text())
        path.write_text(text)
        code, out = self.run_cli("start", "--requirement", out["path"])
        self.assertEqual(code, 0, out)
        return out

    def assert_policy(self, instruction):
        for fragment in ("TECH_STACK_CLARIFICATION_REQUIRED", "WAIT", "user-specified",
                         "repository-established", "user-delegated", "not user authorization",
                         "pending", "delegation"):
            self.assertIn(fragment, instruction)

    def test_restart_to_intake_requires_clarification_before_writing_choices(self):
        require_restart(self.repo, "bmad", ["pi"])
        code, out = self.run_cli("bootstrap", "--host", "pi")
        self.assertEqual(out["next"]["reason"], "HOST_RESTART_REQUIRED")
        self.hook("session_start", reason="startup")
        code, out = self.run_cli("bootstrap", "--host", "pi")
        self.assertEqual(out["mode"], "select")
        self.assert_policy(out["next"]["instruction"])
        self.assertLess(out["next"]["instruction"].index("Technology clarification"),
                        out["next"]["instruction"].index("For an inline requirement"))
        self.assert_policy(self.hook("prompt_submit", prompt="开发一个登录系统")["additional_context"])
        self.assertEqual(self.run_cli("list")[1]["work_items"], [])

    def test_agent_written_confirmed_stack_is_not_promoted_to_authorization(self):
        out = self.start_work("# 登录\n开发登录系统。\n已确认技术栈：Java + Spring Boot + Maven + JUnit 5\n")
        self.assertEqual(out["action"], "VERIFICATION_SETUP")
        self.assert_policy(out["next_action"]["instruction"])
        wid = out["work_item"]["id"]
        self.assertEqual(self.read_work(wid)["decisions"], [])
        for command, key in (("bootstrap", "next"), ("next", "action")):
            code, current = self.run_cli(command)
            self.assertEqual(code, 0, current)
            self.assert_policy(current[key]["instruction"])
        self.assert_policy(self.hook("post_tool", toolName="read", input={"path": "requirement.md"})["additional_context"])

    def test_existing_baseline_cannot_hide_technology_policy_during_native_preparation(self):
        out = self.start_work("# 状态页\n开发状态页，技术选择待确认。\n")
        folder = self.repo / "acceptance-tests"
        folder.mkdir(exist_ok=True)
        (folder / "test_fixture.py").write_text("import unittest\nclass Fixture(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
        code, out = self.run_cli("verification", "configure", "--test-script",
                                 shlex.join([sys.executable, "-m", "unittest", "discover", "-s", "acceptance-tests"]))
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("path", "assess", "--profile", "standard", "--reason", "Planning fixture")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("next")
        self.assertEqual(out["action"]["reason"], "NATIVE_WORKFLOW_SELECTION_REQUIRED")
        self.assert_policy(out["action"]["instruction"])

    def test_choice_origins_survive_restart_without_replacing_work_or_decisions(self):
        (self.repo / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>\n")
        self.hook("prompt_submit", prompt="使用 Java，沿用已有 Maven pom.xml，测试框架你来选。")
        out = self.start_work("# 登录\n按原项目完成登录功能。\n")
        wid = out["work_item"]["id"]
        for choice, source in (("Java", "user-specified; 用户原话：使用 Java"),
                               ("Maven", "repository-established; pre-existing pom.xml"),
                               ("JUnit 5", "user-delegated; 用户原话：测试框架你来选; scope=test runner")):
            code, out = self.run_cli("decision", "--scope", "technology", "--decision", choice, "--reason", source)
            self.assertEqual(code, 0, out)
        before = self.read_work(wid)
        self.hook("turn_end")
        require_restart(self.repo, "bmad", ["pi"])
        self.hook("session_start", reason="reload")
        code, out = self.run_cli("bootstrap", "--host", "pi")
        self.assertEqual(out["work_item"]["id"], wid)
        self.assertEqual(out["context"]["decisions"], before["decisions"])
        self.assertEqual(self.read_work(wid)["requirement"], before["requirement"])
        self.assertIn("do not ask again", out["next"]["instruction"])
