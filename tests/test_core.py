import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


def run(repo, *args, input_text=None):
    p = subprocess.run([sys.executable, str(CAO), *args], cwd=repo, text=True, capture_output=True, input=input_text, timeout=20)
    try:
        out = json.loads(p.stdout)
    except Exception:
        out = {"raw": p.stdout}
    return p.returncode, out, p.stderr


class CoreTests(unittest.TestCase):
    def init_repo(self):
        td = tempfile.TemporaryDirectory()
        repo = pathlib.Path(td.name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
        (repo / "openspec").mkdir()
        (repo / "openspec/config.yaml").write_text("context: test\n", encoding="utf-8")
        return td, repo

    def test_status_reconciles_bmad_execution_artifact_and_native_ref(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir()
            (repo / "requirements/spring-boot-auth-login.md").write_text("Implement login", encoding="utf-8")
            (repo / "_bmad").mkdir()
            run(repo, "init"); run(repo, "sdd", "select", "--provider", "bmad")
            run(repo, "start", "--requirement", "requirements/spring-boot-auth-login.md")
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature with planning")
            run(repo, "checkpoint", "--step", "path-selected", "--next", "Run BMAD planning")
            artifact = repo / "_bmad-output/implementation-artifacts/spec-spring-boot-auth-login.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("---\nstatus: ready_for_dev\n---\n# Login implementation spec\n", encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work"]["execution"]["step"], "ready-for-dev")
            self.assertEqual(out["work"]["work_item"]["sdd"]["native_ref"], "_bmad-output/implementation-artifacts/spec-spring-boot-auth-login.md")
            artifact.write_text("---\nstatus: in_process\n---\n# Login implementation spec\n", encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            work = out["work"]
            self.assertEqual(work["execution"]["step"], "implementation")
            self.assertEqual(work["work_item"]["sdd"]["native_state"]["canonical"], "in_progress")
            run(repo, "status")
            rc, events, _ = run(repo, "lifecycle", "events")
            self.assertEqual(rc, 0)
            names = [event["event"] for event in events["events"]]
            self.assertEqual(names.count("task.execution.started"), 1)
            self.assertEqual(names.count("sdd.native_state.changed"), 2)
            self.assertIn("task.ready", names)
            # Simulate a stale older session writing a planning cursor after native state sync.
            work_file = next((repo / ".orchestrator/work").glob("*.json"))
            persisted = json.loads(work_file.read_text(encoding="utf-8"))
            persisted["execution"]["step"] = "path-selected"
            work_file.write_text(json.dumps(persisted), encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work"]["execution"]["step"], "implementation")
            rc, after, _ = run(repo, "lifecycle", "events")
            self.assertEqual(rc, 0)
            self.assertEqual(len(after["events"]), len(events["events"]))

    def test_bootstrap_reconciles_native_state_before_new_session_context(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir()
            (repo / "requirements/spring-boot-auth-login.md").write_text("Implement login", encoding="utf-8")
            (repo / "_bmad").mkdir()
            run(repo, "init"); run(repo, "sdd", "select", "--provider", "bmad")
            run(repo, "start", "--requirement", "requirements/spring-boot-auth-login.md")
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature with planning")
            run(repo, "checkpoint", "--step", "path-selected", "--next", "Run BMAD planning")
            artifact = repo / "_bmad-output/implementation-artifacts/spec-spring-boot-auth-login.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("---\nstatus: in_process\n---\n# Login implementation spec\n", encoding="utf-8")
            rc, out, _ = run(repo, "bootstrap")
            self.assertEqual(rc, 0)
            self.assertEqual(out["mode"], "resume")
            self.assertEqual(out["context"]["work_item"]["sdd"]["native_state"]["canonical"], "in_progress")
            rc, state, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(state["work"]["execution"]["step"], "implementation")

    def test_ambiguous_bmad_artifacts_do_not_bind_or_advance_state(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir()
            (repo / "requirements/spring-boot-auth-login.md").write_text("Implement login", encoding="utf-8")
            (repo / "_bmad").mkdir()
            run(repo, "init"); run(repo, "sdd", "select", "--provider", "bmad")
            run(repo, "start", "--requirement", "requirements/spring-boot-auth-login.md")
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature with planning")
            run(repo, "checkpoint", "--step", "path-selected", "--next", "Run BMAD planning")
            root = repo / "_bmad-output/implementation-artifacts"
            root.mkdir(parents=True)
            (root / "spec-auth-login.md").write_text("---\nstatus: in_process\n---\n# Candidate A\n", encoding="utf-8")
            (root / "spec-boot-auth-login.md").write_text("---\nstatus: in_process\n---\n# Candidate B\n", encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work"]["execution"]["step"], "path-selected")
            self.assertIsNone(out["work"]["work_item"]["sdd"].get("native_ref"))

    def test_bmad_artifact_without_status_binds_but_does_not_start_execution(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir()
            (repo / "requirements/spring-boot-auth-login.md").write_text("Implement login", encoding="utf-8")
            (repo / "_bmad").mkdir()
            run(repo, "init"); run(repo, "sdd", "select", "--provider", "bmad")
            run(repo, "start", "--requirement", "requirements/spring-boot-auth-login.md")
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature with planning")
            run(repo, "checkpoint", "--step", "path-selected", "--next", "Run BMAD planning")
            artifact = repo / "_bmad-output/implementation-artifacts/spec-spring-boot-auth-login.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("# Login implementation spec\n", encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work"]["execution"]["step"], "path-selected")
            self.assertEqual(out["work"]["work_item"]["sdd"]["native_ref"], "_bmad-output/implementation-artifacts/spec-spring-boot-auth-login.md")
            rc, events, _ = run(repo, "lifecycle", "events")
            self.assertEqual(rc, 0)
            self.assertEqual([event["event"] for event in events["events"]], ["task.created"])

    def test_status_reconciles_openspec_tasks_without_native_ref(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir()
            (repo / "requirements/add-login.md").write_text("Add login", encoding="utf-8")
            run(repo, "init"); run(repo, "sdd", "select", "--provider", "openspec")
            run(repo, "start", "--requirement", "requirements/add-login.md")
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature with planning")
            run(repo, "checkpoint", "--step", "path-selected", "--next", "Run OpenSpec")
            change = repo / "openspec/changes/add-login"
            change.mkdir(parents=True)
            (change / "proposal.md").write_text("# Proposal", encoding="utf-8")
            (change / "tasks.md").write_text("# Tasks\n- [ ] Implement", encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work"]["execution"]["step"], "ready-for-dev")
            self.assertEqual(out["work"]["work_item"]["sdd"]["native_ref"], "openspec/changes/add-login/proposal.md")
            (change / "tasks.md").write_text("# Tasks\n- [x] Implement\n- [ ] Verify\n", encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work"]["execution"]["step"], "implementation")
            (change / "tasks.md").write_text("# Tasks\n- [x] Implement\n- [x] Verify\n", encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work"]["execution"]["step"], "review")
            self.assertEqual(out["work_item"]["status"] if "work_item" in out else out["work"]["work_item"]["status"], "active")

    def test_status_reconciles_speckit_tasks_without_native_ref(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir()
            (repo / "requirements/add-login.md").write_text("Add login", encoding="utf-8")
            (repo / ".specify/memory").mkdir(parents=True)
            (repo / ".specify/memory/constitution.md").write_text("Constitution", encoding="utf-8")
            run(repo, "init"); run(repo, "sdd", "select", "--provider", "speckit")
            run(repo, "start", "--requirement", "requirements/add-login.md")
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature with planning")
            run(repo, "checkpoint", "--step", "path-selected", "--next", "Run Spec Kit")
            feature = repo / "specs/add-login"
            feature.mkdir(parents=True)
            (feature / "spec.md").write_text("# Spec", encoding="utf-8")
            (feature / "tasks.md").write_text("# Tasks\n- [ ] Implement", encoding="utf-8")
            rc, out, _ = run(repo, "status")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work"]["execution"]["step"], "ready-for-dev")
            self.assertEqual(out["work"]["work_item"]["sdd"]["native_ref"], "specs/add-login/spec.md")

    def test_lifecycle(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            rc, _, _ = run(repo, "init"); self.assertEqual(rc, 0)
            rc, out, _ = run(repo, "start", "--requirement", "requirements/login.md"); self.assertEqual(rc, 0); self.assertEqual(out["action"], "ASSESS_PATH")
            run(repo, "path", "assess", "--profile", "light", "--reason", "Small, clear, local change")
            rc, _, _ = run(repo, "checkpoint", "--step", "implement-login-api", "--next", "run integration tests", "--summary", "JWT API implemented", "--completed", "JWT API")
            self.assertEqual(rc, 0)
            rc, _, _ = run(repo, "verify", "--script", "python3 -c \"print('ok')\""); self.assertEqual(rc, 0)
            rc, out, _ = run(repo, "done"); self.assertEqual(rc, 0); self.assertEqual(out["status"], "completed")
            rc, events, _ = run(repo, "lifecycle", "events")
            self.assertEqual(rc, 0)
            names = [event["event"] for event in events["events"]]
            self.assertIn("task.created", names)
            self.assertIn("task.closed", names)

    def test_standard_sdd_workflow_is_required_before_done(self):
        td, repo = self.init_repo()
        with td:
            (repo / "_bmad").mkdir()
            (repo / "requirements").mkdir(); (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            run(repo, "init"); run(repo, "sdd", "select", "--provider", "bmad")
            run(repo, "start", "--requirement", "requirements/login.md")
            rc, _, _ = run(repo, "path", "assess", "--profile", "standard", "--reason", "Feature requiring analysis and review")
            self.assertEqual(rc, 0)
            rc, out, _ = run(repo, "done")
            self.assertEqual(rc, 2)
            self.assertEqual(out["reason"], "native SDD workflow is not recorded as completed")
            rc, out, _ = run(repo, "sdd", "workflow", "select", "--name", "BMAD Specify Requirements", "--entrypoint", "project-local BMAD workflow/skill")
            self.assertEqual(rc, 0)
            (repo / "_bmad-output").mkdir(); (repo / "_bmad-output/requirements.md").write_text("Requirements", encoding="utf-8")
            rc, out, _ = run(repo, "sdd", "workflow", "complete", "--artifact", "_bmad-output/requirements.md")
            self.assertEqual(rc, 0)
            run(repo, "verify", "--script", "python3 -c \"pass\"")
            rc, out, _ = run(repo, "done")
            self.assertEqual(rc, 0)

    def test_standard_sdd_workflow_requires_existing_artifacts(self):
        td, repo = self.init_repo()
        with td:
            (repo / "openspec").mkdir(exist_ok=True)
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            run(repo, "init"); run(repo, "sdd", "select", "--provider", "openspec")
            run(repo, "start", "--requirement", "requirements/a.md")
            run(repo, "path", "assess", "--profile", "standard", "--reason", "Ordinary feature")
            run(repo, "sdd", "workflow", "select", "--name", "OpenSpec change", "--entrypoint", "openspec new change")
            rc, out, _ = run(repo, "sdd", "workflow", "complete", "--artifact", "openspec/missing.md")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "ARTIFACT_INVALID")

    def test_edit_after_verify_blocks_done(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8"); (repo / "app.py").write_text("x=1", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md"); run(repo, "path", "assess", "--profile", "light", "--reason", "Small local change"); run(repo, "verify", "--script", "python3 -c \"pass\"")
            (repo / "app.py").write_text("x=2", encoding="utf-8")
            rc, out, _ = run(repo, "done"); self.assertEqual(rc, 2); self.assertEqual(out["error"], "COMPLETION_BLOCKED")

    def test_requirement_change_done_guides_reanchor(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); p = repo / "requirements/a.md"; p.write_text("A", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md"); run(repo, "path", "assess", "--profile", "light", "--reason", "Small local change"); run(repo, "verify", "--script", "python3 -c \"pass\"")
            p.write_text("A changed", encoding="utf-8")
            rc, out, _ = run(repo, "done")
            self.assertEqual(rc, 2)
            self.assertIn("cao start --requirement requirements/a.md --replace", out["next"])
            self.assertNotEqual(out["next"], "cao verify")

    def test_requirement_change_blocks_done(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); p = repo / "requirements/a.md"; p.write_text("A", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md"); run(repo, "path", "assess", "--profile", "light", "--reason", "Small local change"); run(repo, "verify", "--script", "python3 -c \"pass\"")
            p.write_text("A changed", encoding="utf-8"); rc, out, _ = run(repo, "done"); self.assertEqual(rc, 2); self.assertIn("requirement changed", out["reason"])

    def test_multiple_work_items_have_independent_state(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8"); (repo / "requirements/b.md").write_text("B", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md")
            wid_a = run(repo, "status")[1]["work"]["work_item"]["id"]
            run(repo, "checkpoint", "--step", "a-step", "--next", "a-next", "--summary", "A progress")
            run(repo, "block", "--reason", "Need product confirmation", "--suspend")
            run(repo, "start", "--requirement", "requirements/b.md")
            wid_b = run(repo, "status")[1]["work"]["work_item"]["id"]
            self.assertNotEqual(wid_a, wid_b)
            items = run(repo, "list")[1]["work_items"]
            states = {x["id"]: x["status"] for x in items}; self.assertEqual(states[wid_a], "suspended"); self.assertEqual(states[wid_b], "active")

    def test_resume_creates_handoff_and_new_session_can_continue(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/login.md")
            run(repo, "decision", "--decision", "Use Redis for refresh token", "--reason", "Supports revocation")
            run(repo, "checkpoint", "--step", "implement-login-api", "--next", "run integration tests", "--summary", "Login endpoint implemented", "--completed", "JWT endpoint")
            run(repo, "handoff", "--session", "claude-1", "--host", "claude-code", "--reason", "session exited")
            self.assertTrue((repo / ".orchestrator/handoffs").exists())
            rc, out, _ = run(repo, "resume", "--host", "codex", "--session", "codex-1")
            self.assertEqual(rc, 0)
            self.assertIn("Login endpoint implemented", out.get("raw", ""))
            work = run(repo, "status")[1]["work"]
            self.assertEqual(work["work_item"]["last_agent"], "codex")
            session_files = list((repo / ".orchestrator/sessions").glob("*.json"))
            session = next(json.loads(path.read_text(encoding="utf-8")) for path in session_files if json.loads(path.read_text(encoding="utf-8")).get("host_session_id") == "codex-1")
            self.assertEqual(session["work_item_id"], work["work_item"]["id"])

    def test_decision_is_persistent_and_append_only(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md")
            run(repo, "decision", "--decision", "Use DB", "--reason", "Durability")
            out = run(repo, "status")[1]["work"]
            self.assertEqual(out["decisions"][0]["decision"], "Use DB")

    def test_sdd_auto_discovery_keeps_native_ref_as_reference(self):
        td, repo = self.init_repo()
        with td:
            (repo / "openspec" / "changes" / "add-login" / "specs").mkdir(parents=True)
            p = repo / "openspec" / "changes" / "add-login" / "specs" / "auth.md"; p.write_text("change", encoding="utf-8")
            run(repo, "init"); rc, out, _ = run(repo, "start", "--requirement", str(p), "--sdd-ref", "changes/add-login")
            self.assertEqual(rc, 0); self.assertEqual(out["work_item"]["sdd"]["provider"], "openspec"); self.assertEqual(out["work_item"]["sdd"]["native_ref"], "changes/add-login")

    def test_host_blocks_mutation_without_work(self):
        td, repo = self.init_repo()
        with td:
            run(repo, "init")
            payload = {"event": "pre_tool", "tool_name": "write", "tool_input": {"path": "app.py", "content": "x"}}
            rc, out, _ = run(repo, "host-event", "--host", "claude-code", input_text=json.dumps(payload)); self.assertEqual(rc, 0); self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_index_is_synced_after_checkpoint(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md")
            wid = run(repo, "status")[1]["work"]["work_item"]["id"]
            run(repo, "checkpoint", "--step", "plan-ready", "--next", "mvn -q test", "--summary", "Planning complete")
            index = json.loads((repo / ".orchestrator/index.json").read_text(encoding="utf-8"))
            item = next(x for x in index["work_items"] if x["id"] == wid)
            self.assertEqual(item["step"], "plan-ready")
            self.assertEqual(index["active_work"], wid)

    def test_title_can_override_requirement_filename(self):
        td, repo = self.init_repo()
        with td:
            (repo / "SPEC.md").write_text("Build login", encoding="utf-8")
            run(repo, "init"); rc, out, _ = run(repo, "start", "--requirement", "SPEC.md", "--title", "用户登录功能")
            self.assertEqual(rc, 0)
            self.assertEqual(out["work_item"]["title"], "用户登录功能")
            self.assertNotEqual(out["work_item"]["title"], "SPEC")

    def test_verify_runs_all_configured_scripts_and_records_results(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            run(repo, "init")
            cfg_path = repo / ".orchestrator/config.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["verification"]["scripts"] = [
                {"name": "unit", "command": "python3 -c \"print('unit')\"", "required": True},
                {"name": "lint", "command": "python3 -c \"print('lint')\"", "required": True},
            ]
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            run(repo, "start", "--requirement", "requirements/a.md")
            rc, out, _ = run(repo, "verify")
            self.assertEqual(rc, 0)
            self.assertEqual(out["verification"]["status"], "passed")
            self.assertEqual([x["name"] for x in out["verification"]["results"]], ["unit", "lint"])
            self.assertTrue(all(x["exit_code"] == 0 for x in out["verification"]["results"]))

    def test_init_persists_multiple_verification_scripts(self):
        td, repo = self.init_repo()
        with td:
            rc, _, _ = run(repo, "init", "--verify-script", "./verify-unit.sh", "--verify-script", "./verify-coverage.sh")
            self.assertEqual(rc, 0)
            cfg = json.loads((repo / ".orchestrator/config.json").read_text(encoding="utf-8"))
            self.assertEqual(
                cfg["verification"]["scripts"],
                [
                    {"name": "script-1", "command": "./verify-unit.sh", "required": True},
                    {"name": "script-2", "command": "./verify-coverage.sh", "required": True},
                ],
            )

    def test_default_verification_scripts_are_empty(self):
        td, repo = self.init_repo()
        with td:
            run(repo, "init")
            cfg = json.loads((repo / ".orchestrator/config.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["verification"]["scripts"], [])
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            run(repo, "start", "--requirement", "requirements/a.md")
            rc, out, _ = run(repo, "verify")
            self.assertEqual(rc, 2)
            self.assertEqual(out["error"], "VERIFICATION_SCRIPT_REQUIRED")

    def test_artifact_reference_has_writer_and_refreshes_hash(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            (repo / "spec.md").write_text("one", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md")
            rc, out, _ = run(repo, "artifact", "add", "--path", "spec.md", "--role", "requirement", "--native-type", "spec", "--native-ref", "spec.md")
            self.assertEqual(rc, 0)
            first_sha = out["artifact"]["sha256"]
            work = json.loads((repo / ".orchestrator/work").joinpath(next(p.name for p in (repo / ".orchestrator/work").glob("*.json"))).read_text(encoding="utf-8"))
            self.assertEqual(work["sdd"]["artifacts"][0]["path"], "spec.md")
            (repo / "spec.md").write_text("two", encoding="utf-8")
            rc, out, _ = run(repo, "artifact", "refresh", "--path", "spec.md")
            self.assertEqual(rc, 0)
            self.assertNotEqual(out["refreshed"][0]["sha256"], first_sha)
            rc, out, _ = run(repo, "artifact", "remove", "--path", "spec.md")
            self.assertEqual(rc, 0)
            self.assertEqual(out["removed"], 1)

    def test_verify_script_exit_code_reason_and_blocked_status(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            run(repo, "init")
            cfg_path = repo / ".orchestrator/config.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["verification"]["scripts"] = [
                {"name": "unit", "command": "python3 -c \"print('tests failed'); raise SystemExit(1)\"", "required": True},
                {"name": "env", "command": "python3 -c \"print('Redis is unavailable'); raise SystemExit(2)\"", "required": True},
                {"name": "optional", "command": "python3 -c \"print('informational check failed'); raise SystemExit(1)\"", "required": False},
            ]
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            run(repo, "start", "--requirement", "requirements/a.md")
            rc, out, _ = run(repo, "verify")
            self.assertEqual(rc, 2)
            self.assertEqual(out["verification"]["status"], "blocked")
            self.assertEqual(out["verification"]["results"][0]["status"], "failed")
            self.assertEqual(out["verification"]["results"][0]["reason"], "tests failed")
            self.assertEqual(out["verification"]["results"][1]["status"], "blocked")
            self.assertEqual(out["verification"]["results"][1]["reason"], "Redis is unavailable")
            self.assertEqual(out["verification"]["results"][2]["status"], "failed")

    def test_verify_scripts_all_zero_passes(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8")
            run(repo, "init")
            cfg_path = repo / ".orchestrator/config.json"
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["verification"]["scripts"] = [
                {"name": "unit", "command": "python3 -c \"print('all tests passed: 100%')\"", "required": True},
                {"name": "coverage", "command": "python3 -c \"print('coverage 82% >= 80%')\"", "required": True},
            ]
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            run(repo, "start", "--requirement", "requirements/a.md")
            rc, out, _ = run(repo, "verify")
            self.assertEqual(rc, 0)
            self.assertEqual(out["verification"]["status"], "passed")
            self.assertTrue(all(x["status"] == "passed" for x in out["verification"]["results"]))


if __name__ == "__main__":
    unittest.main()
