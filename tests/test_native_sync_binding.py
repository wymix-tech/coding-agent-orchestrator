"""State synchronization regressions from the remote demo2 Pi session."""
import copy
import json
import shutil
import subprocess

from tests.repair_helpers import RepairCase
from scripts import cao
from scripts.dashboard import build_dashboard_overview, infer_execution_phase


SPEC = "---\ntitle: '001-login-system'\nstatus: 'ready-for-dev'\nroute: 'dispatch'\n---\n## Intent\nSession login\n## Tasks & Acceptance\n- Read users from users.yml.\n"


class NativeBindingSyncTests(RepairCase):
    def setup_work(self):
        rc, out = self.run_cli("requirement", "next-id", "--title", "登录系统开发")
        self.assertEqual(rc, 0, out)
        req = out["path"]
        (self.repo / req).write_text("# 登录系统开发\n使用 session，用户配置在 users.yml。\n")
        rc, out = self.run_cli("start", "--requirement", req)
        self.assertEqual(rc, 0, out)
        self.wid = out["work_item"]["id"]
        self.run_cli("path", "assess", "--profile", "light", "--reason", "bounded fixture")
        self.spec = self.repo / "_bmad-output/implementation-artifacts/spec-001-login-system.md"
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text(SPEC)

    def bind(self, *args):
        rc, out = self.run_cli("sdd", "spec", "bind", "--spec", self.spec.relative_to(self.repo),
                               "--criteria-section", "Tasks & Acceptance", *args)
        self.assertEqual(rc, 0, out)
        return out

    def change(self, status):
        self.spec.write_text(SPEC.replace("ready-for-dev", status))

    def view(self):
        return next(w for w in build_dashboard_overview(self.repo)["work_items"] if w["id"] == self.wid)

    def event(self, **extra):
        rc, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": "post_tool", "session_id": "demo2", "toolName": "edit",
            "input": {"path": str(self.spec), "edits": [{"oldText": "ready-for-dev", "newText": "in-progress"}]},
            **extra,
        })
        self.assertEqual(rc, 0, out)
        return out

    def test_remote_sequence_binds_chinese_work_and_syncs_on_pi_tool_result(self):
        self.setup_work()
        self.bind()
        self.assertEqual(self.view()["execution"]["current_phase"], "planning")
        before = copy.deepcopy(self.read_work(self.wid)["sdd"]["spec_binding"])
        self.change("in-progress")
        self.event()
        work = self.read_work(self.wid)
        self.assertEqual(work["execution"]["step"], "implementation")
        self.assertEqual(work["sdd"]["native_ref"], self.spec.relative_to(self.repo).as_posix())
        self.assertEqual(work["sdd"]["native_state"]["canonical"], "in_progress")
        self.assertEqual(work["sdd"]["spec_binding"], before, "Native sync must not refresh acceptance hashes")
        self.assertEqual(self.view()["execution"]["current_phase"], "implementation")
        self.assertEqual(self.run_cli("done")[1]["reason"], "LIGHT_SPEC_CHANGED")
        events_before = cao.list_lifecycle_events(self.repo, self.wid)
        self.event()
        self.view()
        self.assertEqual(cao.list_lifecycle_events(self.repo, self.wid), events_before)

    def test_legacy_bound_work_is_repaired_by_dashboard_and_cli(self):
        self.setup_work()
        self.bind()
        self.change("in-progress")
        work = cao.load_work(self.repo, self.wid)
        work["sdd"]["native_ref"] = None
        work["sdd"].pop("native_state", None)
        work["execution"]["step"] = "path-selected"
        cao.save_work(self.repo, work)
        self.assertEqual(self.view()["execution"]["current_phase"], "implementation")
        rc, out = self.run_cli("status")
        self.assertEqual(out["work"]["execution"]["step"], "implementation")

    def test_tool_arguments_cannot_claim_a_transition_without_a_file_change(self):
        self.setup_work()
        self.bind()
        self.event()  # Event claims in-progress, but the disk still says ready-for-dev.
        self.assertEqual(self.read_work(self.wid)["sdd"]["native_state"]["canonical"], "ready")
        self.assertEqual(self.view()["execution"]["current_phase"], "planning")

    def test_next_prompt_observes_out_of_band_native_changes(self):
        self.setup_work()
        self.bind()
        self.change("in-progress")
        out = self.event(event="prompt_submit")
        self.assertIn("implementation", out["additional_context"])
        self.assertEqual(self.read_work(self.wid)["execution"]["step"], "implementation")

    def test_native_directory_symlink_does_not_project_product_status(self):
        self.setup_work()
        self.bind()
        root = self.repo / "_bmad-output"
        root.rename(self.repo / "saved-native-output")
        product = self.repo / "src/implementation-artifacts"
        product.mkdir(parents=True)
        (product / self.spec.name).write_text(SPEC.replace("ready-for-dev", "in-progress"))
        root.symlink_to(self.repo / "src", target_is_directory=True)
        view = self.view()
        self.assertEqual(view["native_sync"]["state"], "source_unavailable")
        self.assertNotEqual(view["execution"]["current_phase"], "implementation")

    def test_review_after_reanchor_preserves_work_and_requires_explicit_done(self):
        self.setup_work()
        self.bind()
        self.change("in-review")
        self.run_cli("reopen", "--reanchor", "--reason", "Reviewed verification baseline")
        self.event()
        work = self.read_work(self.wid)
        self.assertEqual(work["execution"]["step"], "review")
        self.assertEqual(work["status"], "active")
        self.assertEqual(self.view()["execution"]["current_phase"], "review")
        self.change("done")
        self.event()
        self.assertEqual(self.read_work(self.wid)["status"], "active")

    def test_later_verification_checkpoint_does_not_regress(self):
        self.setup_work()
        self.bind()
        self.run_cli("checkpoint", "--step", "verification", "--summary", "Run actual checks")
        self.change("in-progress")
        self.event()
        self.assertEqual(self.read_work(self.wid)["execution"]["step"], "verification")

    def test_rebinding_identical_state_bytes_tracks_the_new_source(self):
        self.setup_work()
        self.bind()
        second = self.spec.parent / "spec-renamed.md"
        second.write_text(self.spec.read_text())
        rc, out = self.run_cli("sdd", "spec", "bind", "--spec", second.relative_to(self.repo),
                               "--criteria-section", "Tasks & Acceptance", "--replace")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(self.wid)["sdd"]["native_state"]["source"], second.relative_to(self.repo).as_posix())

    def test_ambiguous_or_malformed_native_status_cannot_advance(self):
        self.setup_work()
        self.bind()
        for text in (SPEC.replace("status: 'ready-for-dev'", "status: 'ready-for-dev'\nstatus: 'in-progress'"),
                     SPEC.replace("status: 'ready-for-dev'", "status: 'in-progress")):
            self.spec.write_text(text)
            self.assertEqual(self.view()["native_sync"]["state"], "status_unavailable")
            self.assertNotEqual(self.view()["execution"]["current_phase"], "implementation")

    def test_actual_dashboard_js_displays_sync_reason_and_implementation_node(self):
        if not shutil.which("node"):
            self.skipTest("Node required for actual packaged dashboard JS")
        from tests.repair_helpers import ROOT
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({window:{CAO_LANGUAGE:'zh-CN'},document:{
 getElementById:()=>({value:'',innerHTML:'',textContent:'',addEventListener(){}}),
 querySelectorAll:()=>[]},fetch:()=>new Promise(()=>{}),setInterval(){},console,URLSearchParams});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').match(/<script>([\s\S]*?)<\/script>/)[1],context);
assert.equal(vm.runInContext('currentFlowIndex({status:"active",execution:{current_phase:"implementation"}})',context),3);
const unbound=vm.runInContext('renderNativeSync({native_sync:{state:"unbound"}})',context);
assert.ok(unbound.includes('原生产物待关联'));
const unavailable=vm.runInContext('renderNativeSync({native_sync:{state:"source_unavailable"}})',context);
assert.ok(unavailable.includes('不会自动采用其他文件'));
const observed=vm.runInContext('renderNativeSync({native_sync:{state:"observed",raw:"in_progress",source:"<script>alert(1)</script>"}})',context);
assert.ok(observed.includes('原生状态已同步'));assert.ok(!observed.includes('<script>'));
'''
        r = subprocess.run([shutil.which("node"), "-e", script, str(ROOT / "scripts/dashboard.html")],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_binding_wins_over_other_matching_specs_and_stale_native_ref(self):
        self.setup_work()
        self.bind()
        other = self.spec.parent / "spec-other.md"
        other.write_text(SPEC.replace("ready-for-dev", "done"))
        work = cao.load_work(self.repo, self.wid)
        work["sdd"]["native_ref"] = other.relative_to(self.repo).as_posix()
        cao.save_work(self.repo, work)
        self.change("in-progress")
        self.assertEqual(self.view()["execution"]["current_phase"], "implementation")
        self.assertEqual(self.read_work(self.wid)["sdd"]["native_state"]["source"], self.spec.relative_to(self.repo).as_posix())

    def test_invalid_bound_source_cannot_fall_back_to_an_unrelated_file(self):
        self.setup_work()
        self.bind()
        original = cao.load_work(self.repo, self.wid)
        other = self.spec.parent / ("spec-" + cao._slug(original["title"]) + ".md")
        other.write_text(SPEC.replace("ready-for-dev", "in-progress"))
        for mutation in ("missing", "product", "symlink", "provider", "requirement"):
            with self.subTest(mutation=mutation):
                work = cao.load_work(self.repo, self.wid)
                work["sdd"] = copy.deepcopy(original["sdd"])
                if mutation == "missing":
                    work["sdd"]["spec_binding"]["spec"]["path"] = "_bmad-output/missing.md"
                elif mutation == "product":
                    (self.repo / "product.md").write_text(other.read_text())
                    work["sdd"]["spec_binding"]["spec"]["path"] = "product.md"
                elif mutation == "symlink":
                    (self.repo / "product.md").write_text(other.read_text())
                    alias = self.spec.parent / "alias.md"
                    alias.symlink_to(self.repo / "product.md")
                    work["sdd"]["spec_binding"]["spec"]["path"] = alias.relative_to(self.repo).as_posix()
                elif mutation == "provider":
                    work["sdd"]["spec_binding"]["provider"] = "openspec"
                else:
                    work["sdd"]["spec_binding"]["requirement_sha256"] = "different"
                work["execution"]["step"] = "path-selected"
                work["sdd"]["native_ref"] = None
                work["sdd"].pop("native_state", None)
                cao.save_work(self.repo, work)
                view = self.view()
                self.assertNotEqual(view["execution"]["current_phase"], "implementation")
                self.assertEqual(view["native_sync"]["state"], "source_unavailable")

    def test_no_binding_reports_missing_association_without_guessing_the_only_spec(self):
        self.setup_work()
        self.change("in-progress")
        view = self.view()
        self.assertEqual(view["native_sync"]["state"], "unbound")
        self.assertTrue(view["native_sync"]["next"])
        self.assertEqual(view["execution"]["current_phase"], "planning")

    def test_task_artifact_existence_does_not_claim_implementation(self):
        self.assertEqual(infer_execution_phase({"status": "active", "path_assessment": {"profile": "light"}},
            [{"role": "tasks", "exists": True, "status": "generated"}])["id"], "planning")

    def test_bound_openspec_and_speckit_use_their_own_tasks(self):
        for provider, folder in (("openspec", "openspec/changes/english-login"), ("speckit", "specs/english-login")):
            with self.subTest(provider=provider):
                self.setup_work() if not hasattr(self, "wid") else None
                self.run_cli("sdd", "select", "--provider", provider)
                work = cao.load_work(self.repo, self.wid)
                work["sdd"] = {"provider": provider, "native_ref": None, "artifacts": []}
                work["execution"]["step"] = "path-selected"
                cao.save_work(self.repo, work)
                directory = self.repo / folder
                directory.mkdir(parents=True)
                spec = directory / ("specs/auth/spec.md" if provider == "openspec" else "spec.md")
                spec.parent.mkdir(parents=True, exist_ok=True)
                spec.write_text("# Session login\n## Acceptance\n- Read users from users.yml.\n")
                tasks = directory / "tasks.md"
                tasks.write_text("- [ ] Implement login\n")
                rc, out = self.run_cli("sdd", "spec", "bind", "--spec", spec.relative_to(self.repo), "--criteria-section", "Acceptance")
                self.assertEqual(rc, 0, out)
                self.assertEqual(self.view()["execution"]["current_phase"], "planning")
                tasks.write_text("- [x] Implement login\n- [ ] Verify login\n")
                self.assertEqual(self.view()["execution"]["current_phase"], "implementation")
