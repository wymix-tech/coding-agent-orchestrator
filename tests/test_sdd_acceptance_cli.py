import json
import sys
from tests.repair_helpers import RepairCase


class AcceptanceCLI(RepairCase):
    def setup_acceptance(self, profile="standard", mode="command", outputs=None):
        self.wid = self.start(profile=profile)
        folder = self.repo / "_bmad-output"; folder.mkdir(exist_ok=True)
        (folder / "spec.md").write_text("# Login\nValid credentials create sessions.\n")
        script = self.repo / "bmad-acceptance.py"
        script.write_text('''import json, os, pathlib, subprocess, sys
r=json.loads(os.environ['CAO_SDD_ACCEPTANCE_REQUEST'])
pathlib.Path('build').mkdir(exist_ok=True)
pathlib.Path('build/acceptance-marker').write_text(r['run_id'])
rc=subprocess.run([sys.executable,'-m','unittest','discover','-s','acceptance-tests','-v'], stdout=sys.stderr, stderr=sys.stderr).returncode
for output in r['declaration']['outputs']:
 p=pathlib.Path(output); p.parent.mkdir(parents=True,exist_ok=True);p.write_text('Checked '+r['run_id'])
print(json.dumps({'run_id':r['run_id'],'input_sha256':r['input_sha256'],'provider':r['provider'],
 'items':[{'id':c['id'],'status':'passed' if rc==0 else 'failed','evidence':[{'path':'acceptance-tests/test_acceptance.py','reason':'Actual unittest run'}]} for c in r['declaration']['criteria']]}))
sys.exit(rc)
''')
        import shlex
        self.d = {"spec": "_bmad-output/spec.md", "outputs": outputs or [], "criteria": [
            {"id": "AC-1", "source": "_bmad-output/spec.md", "kind": "requirement", "description": "Session created"}],
            "executor": {"mode": mode, "entrypoint": shlex.join([sys.executable, script.name]) if mode == "command" else "bmad-review"}}
        if mode == "skill":
            skill = self.repo / ".agents/skills/bmad-review"; skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("Review the implementation")
        (self.repo / "declaration.json").write_text(json.dumps(self.d))
        rc, out = self.run_cli("sdd", "acceptance", "configure", "--declaration", "declaration.json")
        self.assertEqual(rc, 0, out)
        self.run_cli("reopen", "--reanchor", "--reason", "Reviewed SDD scope")
        self.run_cli("sdd", "workflow", "select", "--name", "local", "--entrypoint", "bmad-local", "--allow-unverified", "--reason", "Fixture workflow")
        self.run_cli("sdd", "workflow", "execute", "--command", "bmad-local", "--exit-code", "0")
        self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/spec.md", "--no-plan")
        for gate in ("scope_review", "implementation_review") if profile == "standard" else ("requirements_review", "design_review", "implementation_review"):
            self.run_cli("gate", "satisfy", "--gate", gate, "--evidence", "Fixture scope inspected")

    def test_command_needs_fresh_technical_check_and_then_closes(self):
        self.setup_acceptance()
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 2, out)
        self.assertFalse((self.repo / "build/acceptance-marker").exists())
        self.run_cli("verify")
        rc, out = self.run_cli("done"); self.assertEqual(rc, 2, out)
        self.assertIn("SDD", out["reason"])
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 0, out)
        self.assertTrue((self.repo / "build/acceptance-marker").exists())
        rc, out = self.run_cli("done"); self.assertEqual(rc, 0, out)

    def test_registered_report_does_not_make_technical_snapshot_permanently_stale(self):
        self.setup_acceptance(profile="comprehensive")
        self.d["outputs"] = [f"_bmad-output/cao-acceptance/{self.wid}/report.md"]
        (self.repo / "declaration.json").write_text(json.dumps(self.d))
        self.run_cli("sdd", "acceptance", "configure", "--declaration", "declaration.json", "--replace")
        self.run_cli("reopen", "--reanchor", "--reason", "Reviewed report outputs")
        for gate in ("requirements_review", "design_review", "implementation_review"):
            self.run_cli("gate", "satisfy", "--gate", gate, "--evidence", "Reviewed")
        self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("done"); self.assertEqual(rc, 0, out)

    def test_source_changes_need_explicit_reanchor_and_new_verification(self):
        self.setup_acceptance(); self.run_cli("verify")
        (self.repo / "_bmad-output/spec.md").write_text("Changed scope\n")
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 2, out)
        self.assertFalse((self.repo / "build/acceptance-marker").exists())

    def test_skill_report_requires_current_run_and_command_cannot_use_submit(self):
        self.setup_acceptance(mode="skill"); self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "begin"); self.assertEqual(rc, 0, out)
        request = out["request"]
        report = {**{k: request[k] for k in ("run_id", "input_sha256", "provider")}, "items": [
            {"id": "AC-1", "status": "passed", "evidence": [{"path": "acceptance-tests/test_acceptance.py", "reason": "Reviewer checked"}]}]}
        (self.repo / "report.json").write_text(json.dumps(report))
        # Submit file itself is an input; creating it changes code freshness.
        rc, out = self.run_cli("sdd", "acceptance", "submit", "--run", request["run_id"], "--report", "report.json")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["run"]["status"], "stale")

    def test_bad_report_persists_failure_and_does_not_leave_success(self):
        self.setup_acceptance(); self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 0, out)
        (self.repo / "bmad-acceptance.py").write_text("print('{}')\n")
        self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 2, out)
        self.assertNotEqual(out["run"]["status"], "passed")

    def test_bad_evidence_reports_an_actionable_field_without_weakening_validation(self):
        self.setup_acceptance()
        script = self.repo / "bmad-acceptance.py"
        script.write_text(script.read_text().replace("'evidence':[{'path':'acceptance-tests/test_acceptance.py','reason':'Actual unittest run'}]",
                                                    "'evidence':{'path':'acceptance-tests/test_acceptance.py','reason':'wrong shape'}"))
        self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["run"]["status"], "blocked")
        self.assertEqual(out["run"]["diagnostic"]["field"], "$.items[0].evidence")


    def test_skill_registered_report_passes_and_cannot_be_replayed(self):
        self.setup_acceptance(mode="skill")
        output = f"_bmad-output/cao-acceptance/{self.wid}/report.json"
        self.d["outputs"] = [output]
        (self.repo / "declaration.json").write_text(json.dumps(self.d))
        self.run_cli("sdd", "acceptance", "configure", "--declaration", "declaration.json", "--replace")
        self.run_cli("reopen", "--reanchor", "--reason", "Reviewed output scope")
        for gate in ("scope_review", "implementation_review"):
            self.run_cli("gate", "satisfy", "--gate", gate, "--evidence", "Reviewed")
        rc, out = self.run_cli("verify"); self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("sdd", "acceptance", "begin"); self.assertEqual(rc, 0, out)
        request = out["request"]
        report = {**{k: request[k] for k in ("run_id", "input_sha256", "provider")}, "items": [
            {"id": "AC-1", "status": "passed", "evidence": [{"path": "acceptance-tests/test_acceptance.py", "reason": "Current reviewer evidence"}]}]}
        file = self.repo / output; file.parent.mkdir(parents=True); file.write_text(json.dumps(report))
        rc, out = self.run_cli("sdd", "acceptance", "submit", "--run", request["run_id"], "--report", output)
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("sdd", "acceptance", "submit", "--run", request["run_id"], "--report", output)
        self.assertEqual(rc, 2, out)
        rc, out = self.run_cli("done"); self.assertEqual(rc, 0, out)

    def test_command_failure_replaces_success_and_manual_gate_cannot_bypass(self):
        self.setup_acceptance(); self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 0, out)
        script = self.repo / "bmad-acceptance.py"
        script.write_text(script.read_text().replace("sys.exit(rc)", "sys.exit(1)"))
        self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run")
        self.assertEqual(rc, 1, out); self.assertEqual(out["run"]["status"], "failed")
        rc, out = self.run_cli("gate", "satisfy", "--gate", "sdd_acceptance", "--evidence", "Claimed passed")
        self.assertEqual(rc, 2, out)
        rc, out = self.run_cli("done"); self.assertEqual(rc, 2, out)

    def test_concurrent_decision_is_preserved_but_product_change_is_stale(self):
        from tests.repair_helpers import ROOT
        self.setup_acceptance()
        script = self.repo / "bmad-acceptance.py"
        original = script.read_text()
        decision = f"subprocess.run([sys.executable, {str(ROOT / 'scripts/cao.py')!r}, 'decision', '--decision', 'Concurrent decision', '--reason', 'Keep it'], check=True, stdout=sys.stderr)\n"
        # Another CLI acquires the lock during the executor: a long-held run lock would deadlock.
        script.write_text(original.replace("rc=subprocess.run", decision + "rc=subprocess.run"))
        self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 0, out)
        self.assertIn("Concurrent decision", str(self.read_work(self.wid)["decisions"]))
        script.write_text(script.read_text().replace("rc=subprocess.run", "pathlib.Path('product.py').write_text('changed = True')\nrc=subprocess.run"))
        self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run")
        self.assertEqual(rc, 2, out); self.assertEqual(out["run"]["status"], "stale")
        self.assertIn("Concurrent decision", str(self.read_work(self.wid)["decisions"]))

    def test_timeout_and_oversized_report_are_errors(self):
        self.setup_acceptance()
        self.d["executor"]["timeout_seconds"] = 0.2
        (self.repo / "declaration.json").write_text(json.dumps(self.d))
        self.run_cli("sdd", "acceptance", "configure", "--declaration", "declaration.json", "--replace")
        self.run_cli("reopen", "--reanchor", "--reason", "Reviewed timeout")
        script = self.repo / "bmad-acceptance.py"
        for body in ("import time; time.sleep(5)", "print('x' * (1024 * 1024 + 10))"):
            script.write_text(body)
            self.run_cli("verify")
            rc, out = self.run_cli("sdd", "acceptance", "run")
            self.assertIn(rc, (2, 3), out)
            self.assertNotEqual(out["run"]["status"], "passed")

    def test_changed_report_and_missing_skill_cannot_certify_completion(self):
        self.setup_acceptance(profile="comprehensive")
        self.d["outputs"] = [f"_bmad-output/cao-acceptance/{self.wid}/report.md"]
        (self.repo / "declaration.json").write_text(json.dumps(self.d))
        self.run_cli("sdd", "acceptance", "configure", "--declaration", "declaration.json", "--replace")
        self.run_cli("reopen", "--reanchor", "--reason", "Reviewed reports")
        self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 0, out)
        (self.repo / self.d["outputs"][0]).write_text("Edited report")
        rc, out = self.run_cli("done"); self.assertEqual(rc, 2, out)
        self.assertEqual(self.read_work(self.wid)["status"], "active")

    def test_schema_and_dashboard_show_independent_acceptance(self):
        from scripts.dashboard import build_dashboard_overview
        self.setup_acceptance(); self.run_cli("verify")
        rc, out = self.run_cli("sdd", "acceptance", "run"); self.assertEqual(rc, 0, out)
        view = build_dashboard_overview(self.repo)["work_items"][0]
        self.assertEqual(view["sdd_acceptance"]["state"], "passed")
        self.assertNotIn("sdd_acceptance", view["gates"]["pending"])
        self.assertEqual(view["status"], "active")
        rc, shown = self.run_cli("gate", "show")
        self.assertEqual(rc, 0, shown)
        self.assertIn("sdd_acceptance", shown["gates"]["satisfied"])
        from tests.repair_helpers import ROOT
        schema = json.loads((ROOT / "protocol/work-item.schema.json").read_text())
        record = self.read_work(self.wid)["sdd"]["acceptance"]
        contract = schema["properties"]["sdd"]["properties"]["acceptance"]
        self.assertTrue(set(contract["required"]).issubset(record))
        self.assertIn(record["run"]["status"], contract["properties"]["run"]["properties"]["status"]["enum"])

    def test_downgrade_to_light_preserves_history_without_full_acceptance_gate(self):
        self.setup_acceptance()
        rc, out = self.run_cli("path", "assess", "--profile", "light", "--reason", "Explicit bounded fixture re-assessment")
        self.assertEqual(rc, 0, out)
        (self.repo / "_bmad-output/spec.md").unlink()
        rc, out = self.run_cli("reopen", "--reanchor", "--reason", "Reviewed light technical standard")
        self.assertEqual(rc, 0, out)
        file = self.repo / "_bmad-output/light.md"
        file.write_text("# Local fixture\nBounded fixture\n## Acceptance\n- Fixture check passes.\n")
        rc, out = self.run_cli("sdd", "spec", "bind", "--spec", "_bmad-output/light.md", "--criteria-section", "Acceptance")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("verify"); self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("done"); self.assertEqual(rc, 0, out)
        self.assertIn("acceptance", self.read_work(self.wid)["sdd"])
