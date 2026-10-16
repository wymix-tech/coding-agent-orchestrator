"""Replay the Oct 16 Pi route deadlock through actual CLI and host events."""
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
from unittest.mock import patch

from scripts import cao
from scripts.provider_restart import require_restart
from tests.repair_helpers import RepairCase


class PiRouteGuidance(RepairCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.run_cli("init", "--host", "pi", "--sdd", "bmad")
        (self.repo / "_bmad").mkdir()
        self.skill = ".agents/skills/bmad-build/SKILL.md"
        path = self.repo / self.skill
        path.parent.mkdir(parents=True)
        self.rule = "Use oneshot for this bounded fixture; follow the native renderer."
        path.write_text("---\nname: bmad-build\ndescription: Native fixture\n---\n" + self.rule + "\n")
        _, out = self.run_cli("requirement", "next-id", "--title", "登录系统")
        self.req = out["path"]
        (self.repo / self.req).write_text("# 登录系统\nPrepare a fixture, do not fabricate execution.\n")
        _, self.started = self.run_cli("start", "--requirement", self.req)
        self.wid = self.started["work_item"]["id"]

    def configure(self):
        folder = self.repo / "acceptance-tests"
        folder.mkdir()
        (folder / "test_fixture.py").write_text("import unittest\nclass Fixture(unittest.TestCase):\n def test_fixture(self): self.assertEqual(2+2,4)\n")
        self.run_cli("verification", "configure", "--test-script",
                     shlex.join([sys.executable, "-m", "unittest", "discover", "-s", "acceptance-tests"]))

    def assess(self):
        return self.run_cli("path", "assess", "--profile", "standard", "--reason", "Replay native preparation only")[1]

    def hook(self, event, **payload):
        rc, out = self.run_cli("host-event", "--host", "pi",
                               payload={"event": event, "session_id": "pi-route-replay", **payload})
        self.assertEqual(rc, 0, out)
        return out

    def assert_current(self, reason):
        for command, field in (("bootstrap", "next"), ("next", "action")):
            rc, out = self.run_cli(command)
            self.assertEqual(rc, 0, out)
            self.assertEqual(out[field]["reason"], reason, out)
            self.assertEqual(out["work_item"]["next_action"], out[field])
        for event in ("post_tool", "turn_end", "before_settle"):
            out = self.hook(event, tool_name="bash", tool_input={"command": "cao status"})
            self.assertIn(reason, out["additional_context"])
            self.assertNotIn("retry `cao done`", out["additional_context"])

    def test_baseline_precedes_assessment_and_route_on_every_surface(self):
        self.assertEqual(self.started["next_action"]["reason"], "VERIFICATION_BASELINE_REQUIRED")
        self.assertEqual(self.started["action"], "VERIFICATION_SETUP")
        self.assertEqual(self.started["guidance"]["stage"], "verification_setup")
        self.assert_current("VERIFICATION_BASELINE_REQUIRED")
        assessed = self.assess()  # Early assessment is stored, but cannot displace setup.
        self.assertEqual(assessed["next_action"]["reason"], "VERIFICATION_BASELINE_REQUIRED")
        self.assert_current("VERIFICATION_BASELINE_REQUIRED")
        self.configure()
        self.assert_current("NATIVE_WORKFLOW_SELECTION_REQUIRED")
        self.assertEqual(self.read_work(self.wid)["path_assessment"]["revision"], 1)

    def test_host_restart_precedes_native_preparation_and_restores_its_current_action(self):
        self.configure()
        self.assess()
        before = self.read_work(self.wid)
        require_restart(self.repo, "bmad", ["pi"])
        with patch.object(cao, "detect_current_host", return_value="pi"):
            current = cao.current_work_action(self.repo, before)
        self.assertEqual((current["reason"], current["type"], current["stage"]),
                         ("HOST_RESTART_REQUIRED", "human", "startup"))
        self.assert_current("HOST_RESTART_REQUIRED")
        self.hook("session_start", reason="new")
        self.assert_current("HOST_RESTART_REQUIRED")
        self.hook("session_start", reason="reload")
        self.assert_current("NATIVE_WORKFLOW_SELECTION_REQUIRED")
        after = self.read_work(self.wid)
        self.assertEqual(after["requirement"], before["requirement"])
        self.assertEqual(after["path_assessment"], before["path_assessment"])

    def test_route_preparation_reaches_writable_work_scoped_declaration(self):
        self.configure()
        self.assert_current("execution path has not been assessed")
        assessed = self.assess()
        self.assertEqual(assessed["next_action"]["reason"], "NATIVE_WORKFLOW_SELECTION_REQUIRED")
        self.assertIn(str(self.repo / self.skill), assessed["guidance"]["read_before_action"])
        self.assertTrue(any(path.endswith("references/native-route-contract.md") for path in assessed["guidance"]["read_before_action"]))
        self.assert_current("NATIVE_WORKFLOW_SELECTION_REQUIRED")
        self.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build")
        self.assert_current("NATIVE_SPEC_PREPARATION_REQUIRED")
        spec = "_bmad-output/implementation-artifacts/spec-fixture.md"
        path = self.repo / spec
        path.parent.mkdir(parents=True)
        path.write_text("---\nroute: oneshot\nstatus: draft\n---\n## Intent\nNative route fixture.\n## Implementation Notes\nNo implementation claimed.\n")
        self.run_cli("sdd", "artifact", "bind", "--artifact", spec)
        _, out = self.run_cli("next")
        self.assertEqual(out["action"]["reason"], "NATIVE_ROUTE_REQUIRED")
        declaration = out["action"]["declaration_path"]
        self.assertEqual(declaration, f"_bmad-output/cao/{self.wid}/route.json")
        self.assert_current("NATIVE_ROUTE_REQUIRED")
        for value, expected in ((declaration, "allow"), ("route-declaration.json", "deny"),
                                ("src/auth.py", "deny"), (".orchestrator/work/forged.json", "deny")):
            result = self.hook("pre_tool", tool_name="write", tool_input={"path": value, "content": "{}"})
            self.assertEqual(result["decision"], expected, result)
        work = self.read_work(self.wid)
        raw = {"provider": "bmad", "provider_version": "fixture", "profile": "standard",
               "requirement_sha256": work["requirement"]["sha256"], "workflow": "bmad-build",
               "route": "direct-build", "build_route": "oneshot", "reason": "The installed fixture rule permits this route.",
               "primary_artifact": spec, "required_artifacts": [{"path": spec, "role": "spec"}],
               "rules": [{"path": self.skill, "sha256": hashlib.sha256((self.repo / self.skill).read_bytes()).hexdigest(), "quote": self.rule}],
               "omitted": [{"name": "standalone planning", "reason": "The fixture SPEC carries the bounded native intent.", "replaced_by": spec}]}
        target = self.repo / declaration
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(raw))
        rc, accepted = self.run_cli("sdd", "route", "propose", "--declaration", declaration)
        self.assertEqual(rc, 0, accepted)
        self.assertEqual(accepted["route"]["declaration"]["path"], declaration)
        self.assertEqual(self.read_work(self.wid)["id"], self.wid)
        self.assertNotEqual(self.read_work(self.wid)["status"], "completed")

    def test_invalid_inputs_explain_file_contract_without_creating_evidence(self):
        self.configure()
        self.assess()
        before = (self.repo / f".orchestrator/work/{self.wid}.json").read_bytes()
        for value, error in ((json.dumps({"provider": "bmad", "route": "backend-api"}), "NATIVE_ROUTE_DECLARATION_PATH_REQUIRED"),
                             ("route-declaration.json", "NATIVE_ROUTE_DECLARATION_FILE_MISSING")):
            rc, out = self.run_cli("sdd", "route", "propose", "--declaration", value)
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], error)
            self.assertIn("not inline JSON", out["reason"])
            self.assertIn(self.wid, out["declaration_path"])
            self.assertNotIn("No such file", str(out))
        path = self.repo / "_bmad-output/broken-route.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text("{")
        rc, out = self.run_cli("sdd", "route", "propose", "--declaration", str(path))
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "NATIVE_ROUTE_DECLARATION_JSON_INVALID")
        self.assertEqual((self.repo / f".orchestrator/work/{self.wid}.json").read_bytes(), before)

    def test_completion_recovery_preserves_specific_action(self):
        self.assertEqual(cao.completion_next_instruction(self.repo, self.read_work(self.wid),
                         {"reason": "NATIVE_ROUTE_REQUIRED", "next": "Prepare actual native evidence first."}),
                         "Prepare actual native evidence first.")

    def test_rejected_route_names_report_supported_native_values(self):
        self.configure()
        self.assess()
        self.run_cli("sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build")
        work = self.read_work(self.wid)
        target = self.repo / "_bmad-output/invalid-route.json"
        target.parent.mkdir()
        for route in ("backend-api", "full-stack-web-app"):
            target.write_text(json.dumps({"provider": "bmad", "requirement_sha256": work["requirement"]["sha256"],
                                         "workflow": "bmad-build", "route": route, "build_route": "oneshot"}))
            rc, out = self.run_cli("sdd", "route", "propose", "--declaration", str(target))
            self.assertEqual(rc, 2, out)
            self.assertEqual(out["error"], "NATIVE_ROUTE_NAME_INVALID")
            self.assertEqual(out["allowed_routes"], ["direct-build", "full-planning"])
            self.assertEqual(out["allowed_build_routes"], ["dispatch", "oneshot"])
        self.assertNotIn("route_decision", self.read_work(self.wid)["sdd"])

    def test_selected_planning_workflow_is_preserved_and_prd_is_not_a_build_spec(self):
        self.configure()
        self.assess()
        skill = self.repo / ".agents/skills/bmad-prd/SKILL.md"
        skill.parent.mkdir()
        skill.write_text("---\nname: bmad-prd\ndescription: PRD fixture\n---\nFinish native PRD planning.\n")
        self.run_cli("sdd", "workflow", "select", "--name", "bmad-prd", "--entrypoint", "bmad-prd")
        prd = self.repo / "_bmad-output/planning-artifacts/prd.md"
        prd.parent.mkdir(parents=True)
        prd.write_text("# PRD\n## Intent\nPlanning is not a Build SPEC.\n")
        self.run_cli("sdd", "artifact", "bind", "--artifact", str(prd))
        _, out = self.run_cli("next")
        self.assertEqual(out["action"]["reason"], "NATIVE_SPEC_PREPARATION_REQUIRED")
        self.assertIn("invoke `bmad-prd`", out["action"]["instruction"])
        self.assertIn("do not switch workflows", out["action"]["instruction"])
        self.assertIn(str(skill), out["guidance"]["read_before_action"])
        self.assertNotIn(str(self.repo / self.skill), out["guidance"]["read_before_action"])
        self.assertEqual(self.read_work(self.wid)["sdd"]["workflow"]["name"], "bmad-prd")
