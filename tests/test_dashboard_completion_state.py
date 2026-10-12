"""Authoritative dashboard closure/evidence projections; JS checks are not browser smoke."""
import json
import shutil
import subprocess
import unittest

from repair_helpers import RepairCase, ROOT
from scripts import cao
from scripts.dashboard import build_dashboard_overview, infer_execution_phase, confirm_artifact


class PhaseTests(unittest.TestCase):
    def test_workflow_completion_is_not_work_completion(self):
        for step in ("native-sdd-workflow-completed", "done", "complete", "closed", "incomplete"):
            with self.subTest(step=step):
                phase = infer_execution_phase({"status": "active", "execution": {"step": step},
                                               "sdd": {"workflow": {"status": "completed"}}}, [])
                self.assertNotEqual(phase["id"], "done")
        self.assertEqual(infer_execution_phase({"status": "completed"}, [])["id"], "done")

    def test_native_workflow_steps_have_explicit_phase_mapping(self):
        for suffix, expected in (("selected", "planning"), ("executed", "implementation"), ("completed", "review")):
            phase = infer_execution_phase({"status": "active", "execution": {"step": "native-sdd-workflow-" + suffix}}, [])
            self.assertEqual(phase["id"], expected)


class DashboardCompletionTests(RepairCase):
    def native_complete(self, exit_code=0, no_plan=True):
        wid = self.start()
        for args in (("select", "--name", "bmad-build", "--entrypoint", "bmad-build"),
                     ("execute", "--command", "bmad-build", "--exit-code", str(exit_code))):
            rc, out = self.run_cli("sdd", "workflow", *args)
            self.assertEqual(rc, 0, out)
        artifact = self.repo / "_bmad-output/story.md"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text("# story\n", encoding="utf-8")
        args = ["--no-plan"] if no_plan else []
        if exit_code:
            args += ["--allow-unverified", "--reason", "accepted environment failure"]
        rc, out = self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/story.md", *args)
        self.assertEqual(rc, 0, out)
        return wid

    def view(self):
        return build_dashboard_overview(self.repo)["work_items"][0]

    def test_completed_workflow_is_review_but_work_stays_active(self):
        self.native_complete()
        view = self.view()
        self.assertEqual(view["status"], "active")
        self.assertEqual(view["execution"]["current_phase"], "review")
        self.assertFalse(view["completion"]["ready"])
        self.assertIn("targeted_verification", view["gates"]["pending"])

    def test_fresh_verification_is_not_implicit_done(self):
        self.native_complete()
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        view = self.view()
        self.assertTrue(view["completion"]["ready"], view)
        self.assertTrue(view["verification"]["fresh"])
        self.assertEqual(view["status"], "active")
        self.assertNotEqual(view["execution"]["current_phase"], "done")
        (self.repo / "src.py").write_text("changed = True\n", encoding="utf-8")
        stale = self.view()
        self.assertFalse(stale["completion"]["ready"])
        self.assertFalse(stale["verification"]["fresh"])
        self.assertEqual(stale["verification"]["status"], "stale")
        self.assertEqual(stale["verification"]["run_status"], "passed")
        self.assertIn("targeted_verification", stale["gates"]["pending"])

    def test_legacy_completed_record_is_not_recertified_or_reopened(self):
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work.pop("acceptance")
        work["status"] = "completed"
        cao.save_work(self.repo, work)
        before = (self.repo / f".orchestrator/work/{wid}.json").read_bytes()
        view = self.view()
        self.assertEqual(view["status"], "completed")
        self.assertTrue(view["legacy_evidence"])
        self.assertEqual(view["execution"]["current_phase"], "done")
        self.assertEqual((self.repo / f".orchestrator/work/{wid}.json").read_bytes(), before)

    def test_execution_failure_exception_is_visible_and_not_success(self):
        self.native_complete(exit_code=7)
        view = self.view()
        failure = view["workflow_exception"]["execution_failure"]
        self.assertEqual(failure["exit_code"], 7)
        self.assertEqual(failure["reason"], "accepted environment failure")
        self.assertEqual(view["sdd"]["workflow"]["execution"]["exit_code"], 7)
        self.assertEqual(view["status"], "active")

    def test_unresolved_scope_and_changed_artifact_keep_completion_blocked(self):
        self.native_complete(no_plan=False)
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        unresolved = self.view()
        self.assertFalse(unresolved["completion"]["ready"])
        self.assertIn("PLAN_SCOPE_UNRESOLVED", unresolved["completion"]["reason"])
        self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/story.md", "--no-plan")
        wid = self.read_work(unresolved["id"])["id"]
        confirm_artifact(self.repo, wid, "_bmad-output/story.md")
        (self.repo / "_bmad-output/story.md").write_text("# Changed story\n", encoding="utf-8")
        changed = self.view()
        self.assertFalse(changed["completion"]["ready"])
        row = next(row for row in changed["artifact_catalog"] if row.get("path") == "_bmad-output/story.md")
        self.assertTrue(row["confirmation_stale"])

    @unittest.skipUnless(shutil.which("node"), "Node required for dashboard JS contract (not a browser test)")
    def test_ui_renders_authoritative_completion_legacy_and_exception(self):
        wid = self.native_complete(exit_code=7)
        item = self.view()
        item.update(legacy_evidence=True, completion={"ready": False, "reason": "STALE_TEST_EVIDENCE"},
                    gates={"pending": ["tests"]})
        # Evaluate the packaged page's actual functions with minimal DOM holders.
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[1],'utf8');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',className:'',
 hidden:false,classList:{toggle(){}},addEventListener(){}});return elements.get(id);};
const context=vm.createContext({window:{CAO_LANGUAGE:'zh-CN'},document:{
 getElementById:element,querySelector:element,querySelectorAll:()=>[]},
 fetch:()=>new Promise(()=>{}),setInterval(){},console,URLSearchParams});
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
const work=JSON.parse(process.argv[2]);
vm.runInContext('state='+JSON.stringify({work_items:[work]})+';selectedId='+JSON.stringify(work.id)+';renderDetail()',context);
const detail=element('work-detail').innerHTML;
for(const text of ['STALE_TEST_EVIDENCE','tests','accepted environment failure','旧版证据']) assert.ok(detail.includes(text),text);
for(const step of ['native-sdd-workflow-completed','done','complete','closed']) {
 const active={...work,status:'active',path_state:{ready:true},execution:{step}};
 assert.notEqual(vm.runInContext('currentFlowIndex('+JSON.stringify(active)+')',context),6);
}
assert.equal(vm.runInContext('currentFlowIndex({status:"completed"})',context),6);
'''
        result = subprocess.run([shutil.which("node"), "-e", script, str(ROOT / "scripts/dashboard.html"), json.dumps(item)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
