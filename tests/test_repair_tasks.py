import argparse
import contextlib
import io
import json
import os
import re
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import action_guard
import coding_orchestrator as cli
import evidence_factory
import execution_state_manager as sm
import policy_engine
import project_bootstrap
import requirement_discovery
import requirement_identity
import start_router
import verification_planner
import fact_resolver
import semantic_intake_pipeline
import repository_snapshot
import enforcement_kernel


def git_init(repo: pathlib.Path):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)


class TransactionRepairTests(unittest.TestCase):
    def test_crash_recovery_is_complete_and_idempotent_at_each_commit_stage(self):
        for point in ("after_journal", "after_state", "after_history"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as td:
                path = pathlib.Path(td) / ".orchestrator/execution-state.yaml"
                state = sm.create_state("W", "tx", "FAST")
                sm.initialize(path, state, "test")
                base = sm._load(path)
                old = os.environ.get("ORCHESTRATOR_TXN_CRASH_AT")
                os.environ["ORCHESTRATOR_TXN_CRASH_AT"] = point
                try:
                    with self.assertRaises(RuntimeError):
                        sm.assign_role(path, "implementer", "agent-a", "test", base["revision"])
                finally:
                    if old is None:
                        os.environ.pop("ORCHESTRATOR_TXN_CRASH_AT", None)
                    else:
                        os.environ["ORCHESTRATOR_TXN_CRASH_AT"] = old
                recovered = sm._load(path)
                self.assertEqual("agent-a", recovered["assignments"]["implementer"]["assignee"])
                self.assertEqual(1, recovered["revision"])
                events = [json.loads(x) for x in sm._history_path(path).read_text().splitlines()]
                role_events = [e for e in events if e["event"] == "ROLE_ASSIGNED"]
                self.assertEqual(1, len(role_events))
                self.assertFalse(sm._journal_path(path).exists())

    def test_two_processes_from_same_revision_cannot_both_commit(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            path = root / ".orchestrator/execution-state.yaml"
            sm.initialize(path, sm.create_state("W", "race", "FAST"), "test")
            rev = sm._load(path)["revision"]
            code = r'''
import pathlib,sys
sys.path.insert(0, sys.argv[1])
import execution_state_manager as sm
p=pathlib.Path(sys.argv[2]); rev=int(sys.argv[3]); role=sys.argv[4]
try:
    sm.assign_role(p, role, role, "proc", rev)
    raise SystemExit(0)
except sm.RevisionConflict:
    raise SystemExit(3)
'''
            procs = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(path), str(rev), r])
                     for r in ("implementer", "reviewer")]
            rcs = sorted(p.wait(timeout=15) for p in procs)
            self.assertEqual([0, 3], rcs)
            state = sm._load(path)
            self.assertEqual(1, len(state["assignments"]))
            events = [json.loads(x) for x in sm._history_path(path).read_text().splitlines()]
            self.assertEqual(2, len(events))

    def test_stale_dead_owner_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as td:
            path=pathlib.Path(td)/".orchestrator/execution-state.yaml"
            sm.initialize(path, sm.create_state("W","stale-lock","FAST"), "test")
            lock=sm._lock_path(path); lock.mkdir()
            (lock/"owner.json").write_text(json.dumps({"pid":99999999,"acquired_at":"1970-01-01T00:00:00Z"}),encoding="utf-8")
            base=sm._load(path)
            updated=sm.assign_role(path,"implementer","agent","test",base["revision"] )
            self.assertEqual("agent",updated["assignments"]["implementer"]["assignee"])
            self.assertFalse(lock.exists())

    def test_live_lock_times_out_deterministically(self):
        with tempfile.TemporaryDirectory() as td:
            path=pathlib.Path(td)/".orchestrator/execution-state.yaml"
            sm.initialize(path, sm.create_state("W","lock-timeout","FAST"), "test")
            lock=sm._lock_path(path); lock.mkdir()
            (lock/"owner.json").write_text(json.dumps({"pid":os.getpid(),"acquired_at":sm.utc_now()}),encoding="utf-8")
            with self.assertRaisesRegex(sm.StateError,"state lock timeout"):
                with sm._StateFileLock(path,timeout=0.05):
                    pass
            for child in lock.iterdir(): child.unlink()
            lock.rmdir()


class WorkIdentityRepairTests(unittest.TestCase):
    def _repo(self):
        td = tempfile.TemporaryDirectory(); repo = pathlib.Path(td.name); git_init(repo)
        project_bootstrap.initialize(repo, host="none")
        return td, repo

    def test_requirement_revision_explicitly_invalidates_derived_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / ".orchestrator/execution-state.yaml"
            state = sm.create_state("W", "req", "STANDARD", requirement_id="generic:source:requirements.md", requirement_revision="rev-a")
            sm.initialize(path, state, "test")
            s = sm._load(path)
            for key in ("acceptance_criteria_present", "sdd_ready"):
                evidence_factory.establish_readiness(path, key)
            s = sm._load(path)
            s = sm.record_gate(path, "unit", True, "pending", "test", expected_revision=s["revision"])
            revised = sm.revise_work_item(path, "generic:source:requirements.md", "rev-b", "test", "requirements.md", s["revision"])
            self.assertEqual("rev-b", revised["work_item"]["requirement_revision"])
            self.assertFalse(revised["readiness"]["acceptance_criteria_present"])
            self.assertFalse(revised["readiness"]["sdd_ready"])
            self.assertEqual({}, revised["quality_gates"])
            self.assertFalse(revised["verification"]["fresh"])

    def test_active_a_rejects_intake_b_before_mutating_a(self):
        td, repo = self._repo()
        try:
            state_path = repo / ".orchestrator/execution-state.yaml"
            st = sm.create_state("WA", "A", "FAST", requirement_id="generic:source:a.md", requirement_revision="rev-a")
            sm.initialize(state_path, st, "test")
            before = sm._load(state_path)
            args = argparse.Namespace(
                repo=repo, request="B requirement", request_file=None, work_id="WB", title="B", sdd="generic", sdd_ref=None,
                base_ref=None, resolutions=None, role="implementer", stage="planning", actor="test", degraded=True,
                cbm_fixture=None, revise_current=False, requirement_id="generic:source:b.md", requirement_revision="rev-b", native_id=None,
            )
            code, result, _ = cli.cmd_intake(args)
            self.assertEqual(2, code)
            self.assertEqual("ACTIVE_WORK_ITEM_CONFLICT", result["error"])
            after = sm._load(state_path)
            self.assertEqual(before["revision"], after["revision"])
            self.assertEqual("generic:source:a.md", after["work_item"]["requirement_id"])
        finally:
            td.cleanup()

    def test_same_body_different_source_is_different_requirement_identity(self):
        body = "# Requirements\n\nThe system must expose an API.\nAcceptance criteria: response is testable.\n"
        a = requirement_identity.stable_requirement_id(provider="generic", source_path="requirements/a.md", request_text=body)
        b = requirement_identity.stable_requirement_id(provider="generic", source_path="requirements/b.md", request_text=body)
        self.assertNotEqual(a, b)
        self.assertEqual(requirement_identity.revision_id(body), requirement_identity.revision_id(body))


    def test_long_analysis_uses_optimistic_state_revision_and_cannot_overwrite_newer_commit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo); project_bootstrap.initialize(repo, host="none")
            state_path = repo / ".orchestrator/execution-state.yaml"
            state = sm.create_state("W", "race", "FAST", requirement_id="generic:source:req.md", requirement_revision="rev-1")
            sm.initialize(state_path, state, "test")
            base = sm._load(state_path)["revision"]
            fixture = ROOT / "examples/cbm-detect-changes-fixture.json"
            out1 = repo / ".orchestrator/work-items/W/revisions/rev-1/runs/a/intake"
            out2 = repo / ".orchestrator/work-items/W/revisions/rev-1/runs/b/intake"
            argv = ["--repo", str(repo), "--request", "Add a small behavior change", "--state", str(state_path),
                    "--sync-state", "--expected-state-revision", str(base), "--cbm-fixture", str(fixture),
                    "--allow-cbm-unavailable", "--output-dir", str(out1)]
            with contextlib.redirect_stdout(io.StringIO()):
                first = semantic_intake_pipeline.main(argv)
            self.assertIn(first, {0, 1, 3})
            argv[-1] = str(out2)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                second = semantic_intake_pipeline.main(argv)
            self.assertEqual(4, second)
            self.assertIn("STATE_CONFLICT", buf.getvalue())
            self.assertTrue((out2 / "state-sync.error.json").exists())
            self.assertNotEqual(base, sm._load(state_path)["revision"])


class PolicySemanticSnapshotTests(unittest.TestCase):
    def test_same_rule_id_command_required_and_body_change_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); pol = repo / ".orchestrator/policies"; pol.mkdir(parents=True)
            manifest = pol / "manifest.yaml"
            manifest.write_text("packs:\n  - {id: p, path: p.yaml, precedence: 100}\n", encoding="utf-8")
            pack = pol / "p.yaml"
            def write(statement, required, command):
                pack.write_text(f'''id: p\nrules:\n  - id: R\n    level: MUST\n    title: R\n    statement: {statement}\n    load: {{always: true}}\n    enforcement:\n      - engine: project-test\n        gate: test\n        required: {str(required).lower()}\n        command: {command}\n''', encoding="utf-8")
            impact={"changes":{"changed_files":[],"changed_symbols":[]},"impact":{"affected_files":[]},"boundaries":{},"contracts":{}}
            write("one", True, "cmd-a")
            a=policy_engine.route(repo,impact,manifest,"implementation")
            write("two", False, "cmd-b")
            b=policy_engine.route(repo,impact,manifest,"implementation")
            self.assertNotEqual(a["policy_snapshot_id"], b["policy_snapshot_id"])
            self.assertEqual("p.yaml", a["policy_sources"][0]["ref"])
            self.assertNotEqual(a["policy_sources"][0]["content_hash"], b["policy_sources"][0]["content_hash"])

    def test_policy_pack_outside_default_dir_is_tracked(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); pol=repo/".orchestrator/policies"; pol.mkdir(parents=True); custom=repo/"custom"; custom.mkdir()
            (custom/"pack.yaml").write_text("id: c\nrules: []\n",encoding="utf-8")
            m=pol/"manifest.yaml"; m.write_text("packs:\n  - {id: c, path: ../../custom/pack.yaml}\n",encoding="utf-8")
            plan=policy_engine.route(repo,{"changes":{"changed_files":[],"changed_symbols":[]},"impact":{"affected_files":[]},"boundaries":{},"contracts":{}},m)
            self.assertEqual("../../custom/pack.yaml", plan["policy_sources"][0]["ref"])


class RequirementHistoryAndBmadTests(unittest.TestCase):
    def test_completed_a_and_b_are_not_reopened_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); git_init(repo); project_bootstrap.initialize(repo,host="none")
            reqdir=repo/"requirements"; reqdir.mkdir()
            for name,feature in (("a-spec.md","alpha"),("b-spec.md","beta")):
                (reqdir/name).write_text(f"# Requirements\n\nThe system must support {feature}.\nAcceptance criteria: {feature} is testable.\n",encoding="utf-8")
            found=requirement_discovery.discover(repo,"generic")
            self.assertEqual(2, found["count"])
            for i,c in enumerate(found["candidates"]):
                # History is recorded with the same identity the router resolves: source content
                # decides the revision, the request wording only tracks it.
                ident=requirement_identity.candidate_identity(c,"generic",repo=repo)
                requirement_identity.record(repo, requirement_id=ident["requirement_id"], revision_id=ident["revision_id"],
                                            work_item_id=f"W{i}", provider="generic", source_path=c["path"], status="completed",
                                            source_revision=ident["source_revision"], request_revision=ident["request_revision"])
            routed=start_router.resolve(repo,ensure_bootstrap=False)
            self.assertEqual("REQUEST_REQUIREMENT", routed["route"])

    def test_bmad_discovers_output_story_and_filters_done_and_templates(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); git_init(repo)
            out=repo/"_bmad-output/implementation-artifacts"; out.mkdir(parents=True)
            (out/"sprint-status.yaml").write_text("development_status:\n  story-a: ready-for-dev\n  story-b: done\n",encoding="utf-8")
            (out/"story-a.md").write_text("# Story A\n\nThe user must be able to sign in.\nAcceptance criteria: valid user succeeds.\n",encoding="utf-8")
            (out/"story-b.md").write_text("# Story B\n\nCompleted story.\n",encoding="utf-8")
            tpl=repo/"_bmad/templates"; tpl.mkdir(parents=True)
            (tpl/"story-template.md").write_text("# Story Template\nThe system must do template things.\nAcceptance criteria: template.\n",encoding="utf-8")
            result=requirement_discovery.discover(repo,"bmad")
            self.assertEqual("ONE", result["status"])
            c=result["candidates"][0]
            self.assertEqual("story-a", c["native_id"])
            self.assertIn("_bmad-output/implementation-artifacts/story-a.md", c["path"])

    def test_bmad_unknown_native_format_requires_sourced_action_instead_of_none(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); git_init(repo)
            out=repo/"_bmad-output/implementation-artifacts"; out.mkdir(parents=True)
            (out/"sprint-status.yaml").write_text("unsupported_shape:\n  x: ready-for-dev\n",encoding="utf-8")
            result=requirement_discovery.discover(repo,"bmad")
            self.assertEqual("ACTION_REQUIRED", result["status"])
            self.assertEqual(0, result["count"])
            self.assertEqual("BMAD_NATIVE_STATE_FORMAT_UNSUPPORTED", result["diagnostics"][0]["code"])
            self.assertEqual("_bmad-output/implementation-artifacts/sprint-status.yaml", result["diagnostics"][0]["source_ref"])
            project_bootstrap.initialize(repo,sdd="bmad",host="none",activation=False)
            routed=start_router.resolve(repo,ensure_bootstrap=False)
            self.assertEqual("LOCATE_REQUIREMENT", routed["route"])
            self.assertEqual("BMAD_NATIVE_STATE_FORMAT_UNSUPPORTED", routed["diagnostics"][0]["code"])

    def test_bmad_pending_native_item_without_story_requires_location(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); git_init(repo)
            out=repo/"_bmad-output/implementation-artifacts"; out.mkdir(parents=True)
            (out/"sprint-status.yaml").write_text("development_status:\n  story-missing: ready-for-dev\n",encoding="utf-8")
            result=requirement_discovery.discover(repo,"bmad")
            self.assertEqual("ACTION_REQUIRED", result["status"])
            diag=result["diagnostics"][0]
            self.assertEqual("BMAD_WORK_ITEM_ARTIFACT_MISSING", diag["code"])
            self.assertEqual("story-missing", diag["native_id"])
            self.assertEqual("locate_bmad_story_artifact", result["next_action"])


class BootstrapAndNativeDiscoveryRepairTests(unittest.TestCase):
    def test_explicit_sdd_selection_merges_only_authority_fields(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            (repo / "openspec").mkdir(); (repo / "_bmad").mkdir()
            first = project_bootstrap.initialize(repo, host="none", activation=False)
            self.assertEqual("ACTION_REQUIRED", first["status"])
            cfg_path = repo / ".orchestrator/config.yaml"
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text())
            cfg["orchestrator"]["custom_project_setting"] = {"keep": True}
            cfg["orchestrator"]["engineering_policy"]["custom_threshold"] = 73
            cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
            result = project_bootstrap.initialize(repo, sdd="openspec", host="none", activation=False)
            self.assertNotEqual("ACTION_REQUIRED", result["status"])
            persisted = yaml.safe_load(cfg_path.read_text())
            self.assertEqual("openspec", persisted["orchestrator"]["sdd"]["provider"])
            self.assertEqual({"keep": True}, persisted["orchestrator"]["custom_project_setting"])
            self.assertEqual(73, persisted["orchestrator"]["engineering_policy"]["custom_threshold"])
            self.assertFalse(any((x or {}).get("code") == "SDD_AUTHORITY_REQUIRED" for x in persisted["orchestrator"]["bootstrap"]["unresolved"]))

    def test_bmad_explicit_native_state_ref_outside_default_output_is_used(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td); git_init(repo)
            custom = repo / "work/bmad-state"; custom.mkdir(parents=True)
            (custom / "status.yaml").write_text("stories:\n  - id: custom-1\n    status: ready-for-dev\n    path: stories/custom-1.md\n", encoding="utf-8")
            (custom / "stories").mkdir()
            (custom / "stories/custom-1.md").write_text("# Story custom-1\n\nThe user must be able to export data.\nAcceptance criteria: export is downloadable.\n", encoding="utf-8")
            orch = repo / ".orchestrator"; orch.mkdir()
            import yaml
            (orch / "config.yaml").write_text(yaml.safe_dump({"orchestrator": {"sdd": {"provider": "bmad", "authority_mode": "native", "native_state_ref": "work/bmad-state/status.yaml"}}}, sort_keys=False), encoding="utf-8")
            result = requirement_discovery.discover(repo, "bmad")
            self.assertEqual("ONE", result["status"])
            self.assertEqual("custom-1", result["candidates"][0]["native_id"])
            self.assertEqual("work/bmad-state/status.yaml", result["candidates"][0]["native_state_ref"])

    def test_conflicting_explicit_sdd_reports_persisted_authority_not_requested_value(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); git_init(repo)
            (repo/"openspec").mkdir(); (repo/"_bmad").mkdir()
            project_bootstrap.initialize(repo, sdd="openspec", host="none", activation=False)
            import yaml
            cfg_path=repo/".orchestrator/config.yaml"
            cfg=yaml.safe_load(cfg_path.read_text())
            cfg["orchestrator"]["sdd"]={"provider":"openspec","authority_mode":"hybrid","native_state_ref":None}
            cfg_path.write_text(yaml.safe_dump(cfg,sort_keys=False),encoding="utf-8")
            sp=repo/".orchestrator/execution-state.yaml"
            sm.initialize(sp, sm.create_state("A","active","FAST",provider="openspec",authority_mode="hybrid",requirement_id="openspec:native:a",requirement_revision="r1"), "test")
            result=project_bootstrap.initialize(repo,sdd="bmad",host="none",activation=False)
            persisted=yaml.safe_load(cfg_path.read_text())["orchestrator"]["sdd"]
            self.assertEqual("ACTION_REQUIRED", result["status"])
            self.assertEqual("openspec", persisted["provider"])
            self.assertEqual("openspec", result["sdd"]["provider"])
            self.assertEqual("bmad", result["requested_sdd"]["provider"])
            self.assertTrue(any((x or {}).get("code")=="ACTIVE_NATIVE_AUTHORITY_CONFLICT" for x in result["unresolved"]))


class VerificationObligationTests(unittest.TestCase):
    def test_plan_shrink_does_not_silently_delete_required_obligation(self):
        with tempfile.TemporaryDirectory() as td:
            path=pathlib.Path(td)/".orchestrator/execution-state.yaml"
            sm.initialize(path, sm.create_state("W","obl","FAST",requirement_id="r",requirement_revision="v1"), "test")
            s=sm._load(path)
            plan={"plan_id":"P1","items":[{"obligation_id":"obl-x","source":"semantic_impact","gate_name":"impact:integration_tests","required_by_impact":True,"reason":"boundary","evidence_ref":"impact"}],"policy_gates":[]}
            s=sm.reconcile_verification_obligations(path,plan,"test",s["revision"])
            s=sm.record_gate(path,"impact:integration_tests",True,"pending","test",expected_revision=s["revision"])
            s=sm.reconcile_verification_obligations(path,{"plan_id":"P2","items":[],"policy_gates":[]},"test",s["revision"])
            self.assertEqual("active",s["verification_obligations"]["obl-x"]["status"])
            self.assertIn("impact:integration_tests", action_guard.required_gates(s,{}))
            s=sm.dispose_verification_obligation(path,"obl-x","not_applicable","corrected impact no longer crosses boundary","project_policy","evidence:correction","test",s["revision"])
            self.assertEqual("not_applicable",s["verification_obligations"]["obl-x"]["status"])
            self.assertEqual("not_required",s["quality_gates"]["impact:integration_tests"]["status"])
            self.assertNotIn("impact:integration_tests", action_guard.required_gates(s,{}))

    def test_new_work_item_does_not_inherit_old_obligations(self):
        with tempfile.TemporaryDirectory() as td:
            path=pathlib.Path(td)/".orchestrator/execution-state.yaml"
            old=sm.create_state("A","old","FAST",requirement_id="req-a",requirement_revision="r1")
            sm.initialize(path,old,"test")
            s=sm._load(path)
            s=sm.reconcile_verification_obligations(path,{"plan_id":"old-plan","items":[{"obligation_id":"old-o","gate_name":"impact:old","required_by_impact":True}],"policy_gates":[]},"test",s["revision"])
            self.assertIn("old-o", s["verification_obligations"])
            path.unlink(); sm._history_path(path).unlink(missing_ok=True)
            new=sm.create_state("B","new","FAST",requirement_id="req-b",requirement_revision="r1")
            sm.initialize(path,new,"test")
            self.assertEqual({}, sm._load(path)["verification_obligations"])

    def test_disposition_requires_auditable_reason_authority_and_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            path=pathlib.Path(td)/".orchestrator/execution-state.yaml"
            sm.initialize(path,sm.create_state("W","obl","FAST"),"test")
            s=sm._load(path)
            plan={"plan_id":"P","items":[{"obligation_id":"o","gate_name":"impact:x","required_by_impact":True}],"policy_gates":[]}
            s=sm.reconcile_verification_obligations(path,plan,"test",s["revision"])
            with self.assertRaises(sm.StateError):
                sm.dispose_verification_obligation(path,"o","waived","","","","test",s["revision"])


class EndToEndRepairAcceptanceTests(unittest.TestCase):
    def _run_project_tests(self, repo: pathlib.Path, label: str) -> pathlib.Path:
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=repo, text=True, capture_output=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        ev = repo / ".orchestrator/evidence" / f"{label}.log"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text("$ python -m unittest discover -s tests -v\n" + proc.stdout + proc.stderr, encoding="utf-8")
        # The gate binds a result document, not a label: what a check can re-read is the proof.
        report = repo / ".orchestrator/evidence" / f"{label}.json"
        report.write_text(json.dumps({"status": "passed", "exit_code": 0,
                                      "command": "python -m unittest discover -s tests -v"}), encoding="utf-8")
        return report

    def _resolution_doc(self, facts: dict, repo: pathlib.Path) -> dict:
        resolutions=[]
        for path in (facts.get("extraction") or {}).get("resolution_queue") or []:
            if path in fact_resolver.INT_PATHS:
                value=1
            elif path in {"ambiguity.goal_explicit","ambiguity.acceptance_criteria_explicit","ambiguity.boundaries_explicit","novelty.exact_repo_precedent"}:
                value=True
            elif path in {"complexity.architecture_decision_required","complexity.concurrency_or_transaction"}:
                value=True
            else:
                value=False
            entry={"path":path,"value":value,"source_type":"acceptance_test","source":"T8","evidence":"explicit controlled acceptance fixture","strength":"authoritative"}
            # A negative fact is only worth the search that found nothing.
            if value is False:
                entry["negative_proof"]=evidence_factory.negative_proof_entry(repo, path)
            resolutions.append(entry)
        return {"source":"T8 acceptance","resolutions":resolutions}

    def test_empty_repo_to_close_and_next_requirement_with_real_verification_commands(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); git_init(repo)
            subprocess.run(["git","config","user.email","test@example.com"],cwd=repo,check=True)
            subprocess.run(["git","config","user.name","Test"],cwd=repo,check=True)
            app=repo/"app.py"; app.write_text("def lookup_user(user_id):\n    raise NotImplementedError\n",encoding="utf-8")
            subprocess.run(["git","add","."],cwd=repo,check=True); subprocess.run(["git","commit","-qm","initial"],cwd=repo,check=True)
            req=repo/"requirements.md"; req.write_text("# Requirements\n\nThe system must expose a user lookup API.\n\n## Acceptance Criteria\nUnknown users return 404.\n",encoding="utf-8")
            project_bootstrap.initialize(repo,host="none",activation=False)
            fixture=repo/"cbm-fixture.json"
            fixture.write_text(json.dumps({
                "project":repo.name,
                "changed_files":[{"path":"app.py"}],
                "changed_symbols":[{"qualified_name":"app.lookup_user","label":"Function","file_path":"app.py","module":"app","service":"app","project":repo.name}],
                "impacted_symbols":[],"edges":[],"risk_classification":"LOW"
            }),encoding="utf-8")
            start_args=cli.build_parser().parse_args(["--repo",str(repo),"start","--cbm-fixture",str(fixture)]); start_args.repo=repo
            code,result,_=cli.cmd_start(start_args)
            self.assertEqual(1,code); self.assertEqual("NEEDS_EVIDENCE",result["status"])
            sp=repo/".orchestrator/execution-state.yaml"; state=sm._load(sp)
            refusal=action_guard.authorize(repo,state,"advance",target_phase="implementation")
            self.assertFalse(refusal["allowed"]); self.assertIn("DECISION_NOT_CLASSIFIED",refusal["reason_codes"])

            facts=json.loads(pathlib.Path(state["analysis"]["work_facts_ref"]).read_text())
            # The resolution set is an input, so it is kept outside the analyzed repository content.
            res_path=pathlib.Path(tempfile.mkdtemp())/"resolutions.json"; res_path.write_text(json.dumps(self._resolution_doc(facts, repo)),encoding="utf-8")
            intake_args=cli.build_parser().parse_args(["--repo",str(repo),"intake","--request-file",str(req),"--sdd-ref",str(req),"--resolutions",str(res_path),"--cbm-fixture",str(fixture)]); intake_args.repo=repo
            code,result,_=cli.cmd_intake(intake_args)
            self.assertEqual(0,code); self.assertEqual("CLASSIFIED",result["status"])
            state=sm._load(sp)
            for key in ("behavior_change","acceptance_criteria_present","sdd_ready"):
                evidence_factory.establish_readiness(sp,key,repo=repo)
            state=sm._load(sp)
            state=sm.transition(sp,"implementation","in_progress","T8","begin implementation",state["revision"])

            app.write_text("def lookup_user(user_id):\n    if user_id == 'missing':\n        return 404, None\n    return 200, {'id': user_id}\n",encoding="utf-8")
            tests=repo/"tests"; tests.mkdir(); (tests/"test_app.py").write_text("import unittest\nfrom app import lookup_user\nclass AppTests(unittest.TestCase):\n    def test_missing_is_404(self): self.assertEqual(404, lookup_user('missing')[0])\n    def test_known_is_200(self): self.assertEqual(200, lookup_user('u1')[0])\n",encoding="utf-8")
            snap=repository_snapshot.fingerprint(repo)
            state=sm.mark_enforcement_dirty(sp,"mutation",["app.py","tests/test_app.py"],"T8",snap,"implementation",state["revision"])
            code,result,_=cli.cmd_intake(intake_args)
            self.assertEqual(0,code); self.assertEqual("CLASSIFIED",result["status"])
            state=sm._load(sp)
            # The implementation edited the code the earlier readiness claims described, so
            # those claims are re-evidenced against the content that now exists.
            for key in ("behavior_change","acceptance_criteria_present","implementation_tasks_complete"):
                evidence_factory.establish_readiness(sp,key,repo=repo)
            state=sm._load(sp)
            state=evidence_factory.establish_progress(sp,1,1,repo=repo,actor="T8")["state"]
            state=sm.transition(sp,"review","in_progress","T8","implementation complete",state["revision"])
            review_evidence=self._run_project_tests(repo,"review-tests")
            state=sm.record_review(sp,"passed","T8",0,str(review_evidence.relative_to(repo)),state["revision"])
            state=sm.transition(sp,"verification","in_progress","T8","review passed",state["revision"])

            for name,gate in list(state["quality_gates"].items()):
                if gate.get("required"):
                    evidence=self._run_project_tests(repo,"gate-"+re.sub(r"[^A-Za-z0-9._-]+","-",name))
                    state=sm.record_gate(sp,name,True,"passed","T8",evidence_ref=str(evidence.relative_to(repo)),command=gate.get("command"),expected_revision=state["revision"])
            evidence_factory.establish_readiness(sp, "acceptance_satisfied")
            state=sm._load(sp)
            final_evidence=self._run_project_tests(repo,"final-verification")
            state=sm.record_verification(sp,"passed","T8",state["execution_snapshot_id"],str(final_evidence.relative_to(repo)),state["revision"])
            self.assertEqual([],sm.transition_guard(state,"closed","completed",repo))
            state=sm.transition(sp,"closed","completed","T8","all current evidence passed",state["revision"])
            self.assertTrue(sm.completion_status(state,repo)["done"])

            nxt=repo/"requirements/next-spec.md"; nxt.parent.mkdir(exist_ok=True); nxt.write_text("# Requirements\n\nThe system must support account deletion.\nAcceptance criteria: deleted users cannot log in.\n",encoding="utf-8")
            after_new_requirement=sm.completion_status(sm._load(sp),repo)
            self.assertTrue(after_new_requirement["done"])
            self.assertTrue(after_new_requirement["historically_completed"])
            self.assertFalse(after_new_requirement["governance_close_ready"])
            start2=cli.build_parser().parse_args(["--repo",str(repo),"start","--no-auto-intake"]); start2.repo=repo
            code2,result2,_=cli.cmd_start(start2)
            self.assertEqual(0,code2)
            self.assertEqual("AUTO_INTAKE_CANDIDATE",result2["route"])
            self.assertEqual("requirements/next-spec.md",result2["candidate"]["path"])

            # Auto-start B archives A and records A's stable requirement/revision history;
            # B may still need evidence, but A must never be resurrected or reused.
            start3=cli.build_parser().parse_args(["--repo",str(repo),"start","--cbm-fixture",str(fixture)]); start3.repo=repo
            code3,result3,_=cli.cmd_start(start3)
            self.assertEqual("AUTO_INTAKE", result3["route"])
            self.assertIn(code3,{0,1})
            active=sm._load(sp)
            self.assertNotEqual("generic:source:requirements.md", active["work_item"]["requirement_id"])
            self.assertEqual("generic:source:requirements/next-spec.md", active["work_item"]["requirement_id"])
            registry=requirement_identity.load_registry(repo)
            old_rev=registry["requirements"]["generic:source:requirements.md"]["revisions"]
            self.assertTrue(any(v.get("status")=="completed" for v in old_rev.values()))

    def test_cli_pretool_transition_verify_stop_share_authorization_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); git_init(repo); project_bootstrap.initialize(repo,host="none",activation=False)
            sp=repo/".orchestrator/execution-state.yaml"
            sm.initialize(sp,sm.create_state("W","shared","FAST",requirement_id="r",requirement_revision="v1"),"test")
            state=sm._load(sp)
            direct=action_guard.authorize(repo,state,"mutate_code")
            self.assertFalse(direct["allowed"])
            check_args=argparse.Namespace(repo=repo,action="mutate_code",phase=None,status="in_progress",role="implementer",native_confirmed=False)
            ccode,cresult,_=cli.cmd_check(check_args)
            self.assertEqual(1,ccode); self.assertEqual(direct["reason_codes"],cresult["reason_codes"])
            hook=enforcement_kernel.handle(repo,"codex","pre_tool",{"tool_name":"apply_patch","tool_input":{"patch":"*** Begin Patch\n*** Update File: app.py\n@@\n-x\n+y\n*** End Patch"}})
            self.assertEqual("deny",hook["decision"])
            self.assertEqual(direct["reason_codes"],hook["metadata"]["authorization"]["reason_codes"])
            guard=sm.transition_guard(state,"implementation","in_progress",repo)
            self.assertTrue(guard)
            verify_args=argparse.Namespace(repo=repo)
            vcode,vresult,_=cli.cmd_verify(verify_args)
            self.assertEqual(1,vcode); self.assertFalse(vresult["completion"]["authorization"]["allowed"])
            stop=enforcement_kernel.handle(repo,"codex","stop",{"last_assistant_message":"Implementation completed and ready to merge","session_id":"s"})
            self.assertEqual("deny",stop["decision"])
            start_args=cli.build_parser().parse_args(["--repo",str(repo),"start","--no-auto-intake"]); start_args.repo=repo
            scode,sresult,_=cli.cmd_start(start_args)
            self.assertEqual(0,scode); self.assertEqual("READY_FOR_WORK",sresult["status"])


if __name__ == "__main__":
    unittest.main()
