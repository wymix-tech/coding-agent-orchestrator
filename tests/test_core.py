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

    def test_lifecycle(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/login.md").write_text("Add login", encoding="utf-8")
            rc, _, _ = run(repo, "init"); self.assertEqual(rc, 0)
            rc, out, _ = run(repo, "start", "--requirement", "requirements/login.md"); self.assertEqual(rc, 0); self.assertEqual(out["action"], "IMPLEMENT")
            rc, _, _ = run(repo, "checkpoint", "--step", "implement-login-api", "--next", "run integration tests", "--summary", "JWT API implemented", "--completed", "JWT API")
            self.assertEqual(rc, 0)
            rc, _, _ = run(repo, "verify", "--script", "python3 -c \"print('ok')\""); self.assertEqual(rc, 0)
            rc, out, _ = run(repo, "done"); self.assertEqual(rc, 0); self.assertEqual(out["status"], "completed")

    def test_edit_after_verify_blocks_done(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); (repo / "requirements/a.md").write_text("A", encoding="utf-8"); (repo / "app.py").write_text("x=1", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md"); run(repo, "verify", "--script", "python3 -c \"pass\"")
            (repo / "app.py").write_text("x=2", encoding="utf-8")
            rc, out, _ = run(repo, "done"); self.assertEqual(rc, 2); self.assertEqual(out["error"], "COMPLETION_BLOCKED")

    def test_requirement_change_done_guides_reanchor(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); p = repo / "requirements/a.md"; p.write_text("A", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md"); run(repo, "verify", "--script", "python3 -c \"pass\"")
            p.write_text("A changed", encoding="utf-8")
            rc, out, _ = run(repo, "done")
            self.assertEqual(rc, 2)
            self.assertIn("cao start --requirement requirements/a.md --replace", out["next"])
            self.assertNotEqual(out["next"], "cao verify")

    def test_requirement_change_blocks_done(self):
        td, repo = self.init_repo()
        with td:
            (repo / "requirements").mkdir(); p = repo / "requirements/a.md"; p.write_text("A", encoding="utf-8")
            run(repo, "init"); run(repo, "start", "--requirement", "requirements/a.md"); run(repo, "verify", "--script", "python3 -c \"pass\"")
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
