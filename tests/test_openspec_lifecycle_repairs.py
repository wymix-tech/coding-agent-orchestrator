"""Regressions from the real Pi/GPT-5.6 luna/OpenSpec lifecycle."""
from copy import deepcopy
import json
import shutil
import sys
from unittest.mock import patch

from scripts import cao
from tests.repair_helpers import RepairCase


class OpenSpecLifecycleRepairs(RepairCase):
    def setup_change(self):
        (self.repo / "openspec").mkdir()
        (self.repo / "openspec/config.yaml").write_text("schema: spec-driven\n")
        rc, out = self.run_cli("sdd", "select", "--provider", "openspec")
        self.assertEqual(rc, 0, out)
        self.wid = self.start(profile="standard")
        self.change = self.repo / "openspec/changes/add-demo"
        self.change.mkdir(parents=True)
        (self.change / ".openspec.yaml").write_text("schema: spec-driven\n")
        (self.change / "spec.md").write_text("# Demo\n## Requirements\n- AC-1: calculation returns four.\n")
        (self.change / "tasks.md").write_text("# Tasks\n- [x] 1.1 Implement calculation\n- [x] 1.2 Check calculation\n")
        (self.change / "proposal.md").write_text("# Proposal\nImplement the calculation.\n")
        spec = (self.change / "spec.md").relative_to(self.repo).as_posix()
        tasks = (self.change / "tasks.md").relative_to(self.repo).as_posix()
        for args in (("sdd", "workflow", "select", "--name", "OpenSpec propose", "--entrypoint", "openspec new change"),
                     ("sdd", "workflow", "execute", "--command", "openspec new change add-demo", "--exit-code", "0"),
                     ("sdd", "workflow", "complete", "--artifact", spec, "--artifact", tasks, "--plan", tasks),
                     ("sdd", "spec", "bind", "--spec", spec, "--criteria-section", "Requirements")):
            rc, out = self.run_cli(*args)
            self.assertEqual(rc, 0, out)
        script = self.repo / "acceptance.py"
        script.write_text("""import json, os, subprocess, sys
r=json.loads(os.environ['CAO_SDD_ACCEPTANCE_REQUEST'])
p=subprocess.run([sys.executable,'-m','unittest','discover','-s','acceptance-tests','-v'], stdout=sys.stderr, stderr=sys.stderr)
print(json.dumps({**{k:r[k] for k in ('run_id','input_sha256','provider')},'items':[{'id':c['id'],'status':'passed' if p.returncode==0 else 'failed','evidence':[{'path':'acceptance-tests/test_acceptance.py','reason':'Executed the calculation check'}]} for c in r['declaration']['criteria']]}))
sys.exit(p.returncode)
""")
        declaration = {"spec": spec, "criteria": [{"id": "AC-1", "source": spec,
                       "kind": "requirement", "description": "Calculation succeeds"}],
                       "executor": {"mode": "command", "entrypoint": f'"{sys.executable}" acceptance.py'}}
        (self.repo / "declaration.json").write_text(json.dumps(declaration))
        rc, out = self.run_cli("sdd", "acceptance", "configure", "--declaration", "declaration.json")
        self.assertEqual(rc, 0, out)
        self.accept()

    def accept(self):
        for args in (("reopen", "--reanchor", "--reason", "Reviewed current native scope"),
                     ("gate", "satisfy", "--gate", "scope_review", "--evidence", "Reviewed scope"),
                     ("gate", "satisfy", "--gate", "implementation_review", "--evidence", "Reviewed calculation"),
                     ("verify",), ("sdd", "acceptance", "run")):
            rc, out = self.run_cli(*args)
            self.assertEqual(rc, 0, out)

    def archive(self, date="2026-10-15"):
        target = self.repo / f"openspec/changes/archive/{date}-add-demo"
        target.parent.mkdir(exist_ok=True)
        shutil.move(self.change, target)
        return target

    def hook(self, command):
        rc, out = self.run_cli("host-event", "--host", "pi", payload={"event": "pre_tool",
                           "session_id": "openspec-pi", "toolName": "bash", "input": {"command": command}})
        self.assertEqual(rc, 0, out)
        return out

    def test_archive_before_close_keeps_scope_and_requires_fresh_checks(self):
        self.setup_change()
        before = self.read_work(self.wid)
        target = self.archive()
        rc, out = self.run_cli("status", "--work", self.wid)
        self.assertEqual(rc, 0, out)
        work = self.read_work(self.wid)
        plan = work["sdd"]["plan_scope"]["plans"][0]
        self.assertEqual(plan["path"], (target / "tasks.md").relative_to(self.repo).as_posix())
        self.assertEqual(plan["task_ids"], before["sdd"]["plan_scope"]["plans"][0]["task_ids"])
        self.assertEqual(work["verification"], before["verification"], "Do not manufacture new verification")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("PLAN_SOURCE_MISSING", str(out))
        self.accept()
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 0, out)
        events = cao.list_lifecycle_events(self.repo, self.wid)
        self.assertEqual(sum(e["event"] == "sdd.native_paths.relocated" for e in events), 1)

    def test_archive_after_close_preserves_frozen_acceptance_history(self):
        self.setup_change()
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 0, out)
        frozen = deepcopy(self.read_work(self.wid)["sdd"]["acceptance"])
        self.archive()
        (self.repo / "later-product.py").write_text("answer = 5\n")
        rc, out = self.run_cli("sdd", "acceptance", "show", "--work", self.wid)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["state"]["state"], "passed", out)
        self.assertTrue(out["state"]["historical"], out)
        self.assertEqual(out["acceptance"], frozen)
        self.assertEqual(self.read_work(self.wid)["status"], "completed")

    def test_explicit_reopen_of_archived_history_checks_current_sources_again(self):
        self.setup_change()
        self.run_cli("done")
        target = self.archive()
        self.run_cli("sdd", "acceptance", "show", "--work", self.wid)
        rc, out = self.run_cli("reopen", "--work", self.wid, "--reanchor", "--reason", "Review archived implementation again")
        self.assertEqual(rc, 0, out)
        work = self.read_work(self.wid)
        self.assertEqual(work["sdd"]["acceptance"]["declaration"]["spec"], (target / "spec.md").relative_to(self.repo).as_posix())
        self.assertFalse(work["verification"])
        rc, out = self.run_cli("sdd", "acceptance", "show", "--work", self.wid)
        self.assertFalse(out["state"]["ready"], out)
        self.assertNotIn("historical", out["state"])

    def assert_invalid_archive(self, invalid):
        self.setup_change()
        old = deepcopy(self.read_work(self.wid)["sdd"]["spec_binding"])
        target = self.archive()
        if invalid == "ambiguous":
            shutil.copytree(target, target.with_name("2026-10-16-add-demo"))
        elif invalid == "changed":
            (target / "spec.md").write_text("# Changed\nDifferent intent\n")
        else:
            actual = self.repo / "actual-spec.md"
            shutil.move(target / "spec.md", actual)
            (target / "spec.md").symlink_to(actual)
        self.run_cli("status", "--work", self.wid)
        self.assertEqual(self.read_work(self.wid)["sdd"]["spec_binding"], old)
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)

    def test_ambiguous_archives_never_rebind(self):
        self.assert_invalid_archive("ambiguous")

    def test_changed_archive_never_rebinds(self):
        self.assert_invalid_archive("changed")

    def test_linked_archive_never_rebinds(self):
        self.assert_invalid_archive("linked")

    def test_archived_plan_task_regression_still_blocks_verification(self):
        self.setup_change()
        target = self.archive()
        self.run_cli("status", "--work", self.wid)
        tasks = target / "tasks.md"
        tasks.write_text(tasks.read_text().replace("[x] 1.2", "[ ] 1.2"))
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "SDD_PLAN_NOT_DONE", out)

    def test_split_changes_relocate_every_bound_plan_and_keep_task_identities(self):
        self.setup_change()
        second = self.change.with_name("add-second")
        second.mkdir()
        (second / "tasks.md").write_text("# Tasks\n- [x] 2.1 Second piece\n")
        rc, out = self.run_cli("sdd", "plan", "bind", "--plan", (second / "tasks.md").relative_to(self.repo))
        self.assertEqual(rc, 0, out)
        self.run_cli("sdd", "sync")
        self.archive()
        target = second.parent / "archive/2026-10-15-add-second"
        shutil.move(second, target)
        self.run_cli("status", "--work", self.wid)
        plans = self.read_work(self.wid)["sdd"]["plan_scope"]["plans"]
        self.assertEqual(len(plans), 2)
        self.assertTrue(all("/archive/" in plan["path"] for plan in plans))
        self.assertEqual({task for plan in plans for task in plan["task_ids"]}, {"1.1", "1.2", "2.1"})
        (target / "tasks.md").write_text("# Tasks\n- [ ] 2.1 Second piece\n")
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "SDD_PLAN_NOT_DONE", out)

    def test_chinese_checkpoint_keeps_phase_next_action_and_summary(self):
        self.setup_change()
        self.run_cli("checkpoint", "--step", "实现、验证与独立验收完成", "--next", "关闭当前需求", "--summary", "全部检查通过")
        self.run_cli("sdd", "sync")
        work = self.read_work(self.wid)
        self.assertEqual(work["execution"]["step"], "实现、验证与独立验收完成")
        self.assertEqual(work["execution"]["next_action"]["instruction"], "关闭当前需求")
        self.assertEqual(work["resume"]["summary"], "全部检查通过")
        self.assertEqual(work["execution"]["phase"], "verification")
        from scripts.dashboard import infer_execution_phase
        self.assertEqual(infer_execution_phase(work, [])["id"], "verification")

    def test_help_readonly_pipelines_and_native_archive_without_active_work(self):
        (self.repo / "openspec").mkdir()
        launcher = str(cao.package_root_dir() / "cao")
        for command in ("openspec archive --help", "openspec --version", "openspec list --json | sort",
                        "set -o pipefail\nopenspec archive --help\nrg old openspec 2>/dev/null || true",
                        "openspec validate --archived --json && openspec list --json; rg old openspec || true",
                        "command -v openspec || true; npm config get prefix; npm root -g; node --version",
                        f"NPM_CONFIG_PREFIX=/tmp/test-prefix {launcher} sdd install --provider openspec --host pi --yes",
                        "openspec archive add-demo --yes --json"):
            with self.subTest(command=command):
                self.assertEqual(self.hook(command)["decision"], "allow", command)
        for command in ("openspec archive --help > product.py", "openspec list | sort -o product.py",
                        "rg old openspec 2>product.py || true", "2 >/dev/null openspec archive --help",
                        "openspec list | sort --out=product.py", "openspec list | sort -roproduct.py",
                        "openspec archive add-demo --yes; touch product.py",
                        "openspec archive add-demo --store elsewhere --yes",
                        "NPM_CONFIG_PREFIX=/tmp/test-prefix echo code > product.py",
                        f"PYTHONPATH=/tmp/evil {launcher} sdd install --provider openspec --yes"):
            with self.subTest(command=command):
                self.assertEqual(self.hook(command)["decision"], "deny", command)

    def test_usable_installed_openspec_skips_global_install(self):
        with patch.object(cao, "command_exists", return_value=True), patch.object(cao, "command_output", return_value=(0, "1.14.2", "")):
            self.assertIsNone(cao.choose_external_install_command("openspec"))
        with patch.object(cao, "command_exists", return_value=True), patch.object(cao, "command_output", return_value=(1, "", "cannot start")):
            self.assertIsNotNone(cao.choose_external_install_command("openspec"))
