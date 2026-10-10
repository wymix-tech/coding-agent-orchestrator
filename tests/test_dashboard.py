from __future__ import annotations

import json
import pathlib
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.cao import cmd_artifact_refresh, load_work, stable_session_id
from scripts.dashboard import build_dashboard_overview, build_sdd_artifact_catalog, confirm_artifact, make_handler, preview_artifact, validate_bind_host

ROOT = pathlib.Path(__file__).resolve().parents[1]


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.temp.name)
        state = self.repo / ".orchestrator"
        for name in ("work", "sessions", "handoffs"):
            (state / name).mkdir(parents=True, exist_ok=True)
        (state / "config.json").write_text(json.dumps({
            "version": 1,
            "sdd": {"provider": "openspec", "command": {"inspect": "", "next": "", "transition": ""}},
            "verification": {"scripts": []},
            "rules": {"files": []},
        }), encoding="utf-8")
        (self.repo / "requirements").mkdir()
        self.requirement = self.repo / "requirements/login.md"
        self.requirement.write_text("A test requirement", encoding="utf-8")
        import hashlib
        req_sha = hashlib.sha256(self.requirement.read_bytes()).hexdigest()
        self.work_id = "login-12345678"
        self.work = {
            "version": 1,
            "id": self.work_id,
            "title": "Login feature",
            "status": "active",
            "created_at": 1791537600,
            "updated_at": 1791537601,
            "requirement": {"path": "requirements/login.md", "sha256": req_sha},
            "sdd": {"provider": "openspec", "native_ref": "changes/login", "artifacts": []},
            "execution": {"step": "implementation", "next_action": {"type": "agent", "instruction": "Run integration tests"}},
            "path_assessment": None,
            "resume": {"summary": "Endpoint implemented", "completed": ["Create endpoint"], "pending": ["Add tests"], "last_agent": "codex"},
            "decisions": [{"decision": "Use JWT", "reason": "Stateless auth"}],
            "blockers": [{"id": "b1", "reason": "Need product confirmation", "resolution": None}],
            "verification": {"status": "failed", "source": "config", "at": 1791537600, "results": [{
                "name": "unit-tests", "command": "./scripts/verify.sh", "required": True,
                "status": "failed", "exit_code": 1, "reason": "1 test failed", "stdout_tail": "SECRET SHOULD NOT LEAK", "stderr_tail": "private stderr",
            }]},
        }
        (state / "work" / f"{self.work_id}.json").write_text(json.dumps(self.work), encoding="utf-8")
        sid = stable_session_id("codex", "native-1")
        session = {
            "schema_version": 2, "id": sid, "host": "codex", "host_session_id": "native-1",
            "work_item_id": self.work_id, "kind": "primary", "parent_session_id": None,
            "status": "active", "started_at": 1791537600, "last_event": "post_tool",
            "last_event_at": 1791537600, "updated_at": 1791537600, "ended_at": None,
            "end_reason": None, "last_handoff_id": "handoff-1",
        }
        (state / "sessions" / f"{sid}.json").write_text(json.dumps(session), encoding="utf-8")
        handoff_dir = state / "handoffs" / self.work_id
        handoff_dir.mkdir()
        handoff = {
            "version": 1, "id": "handoff-1", "sequence": 1, "work_item": self.work_id,
            "host": "codex", "reason": "transfer to pi", "created_at": 1791537600,
            "step": "implementation", "summary": "Endpoint implemented", "pending": ["Add tests"],
        }
        (handoff_dir / "handoff-1.json").write_text(json.dumps(handoff), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_overview_projects_work_sessions_and_handoffs_from_state_files(self):
        data = build_dashboard_overview(self.repo)
        self.assertTrue(data["initialized"])
        self.assertEqual(data["active_work"], self.work_id)
        self.assertEqual(data["counts"]["total_work"], 1)
        self.assertEqual(data["work_items"][0]["execution"]["step"], "implementation")
        self.assertEqual(data["work_items"][0]["verification"]["results"][0]["reason"], "1 test failed")
        self.assertNotIn("SECRET SHOULD NOT LEAK", json.dumps(data))
        self.assertEqual(data["sessions"][0]["host"], "codex")
        self.assertEqual(data["handoffs"][0]["id"], "handoff-1")

    def test_dashboard_page_is_packaged_and_references_dashboard_api(self):
        from scripts.dashboard import dashboard_html_path
        html = dashboard_html_path().read_text(encoding="utf-8")
        self.assertIn("研发编排总览", html)
        self.assertIn('id="overview-page"', html)
        self.assertIn('id="detail-page"', html)
        self.assertIn("任务列表", html)
        self.assertIn("back-overview", html)
        self.assertIn("/api/overview", html)
        self.assertIn("LOCAL · CONTROLLED ACTIONS", html)
        # Auto-refresh calls renderAll() -> showPage(currentPage); scrolling to top
        # must happen only on an actual page transition, not on every refresh.
        self.assertIn("const pageChanged=page!==currentPage", html)
        self.assertIn("if(detail&&pageChanged)window.scrollTo({top:0,behavior:'smooth'})", html)


    def test_openspec_artifact_catalog_shows_generated_missing_and_confirmed(self):
        change = self.repo / "openspec/changes/login"
        (change / "specs/auth").mkdir(parents=True)
        (change / "proposal.md").write_text("# Proposal\nAdd login", encoding="utf-8")
        (change / "tasks.md").write_text("# Tasks\n- [ ] Add tests", encoding="utf-8")
        (change / "specs/auth/spec.md").write_text("# Auth spec\nLogin contract", encoding="utf-8")
        catalog = build_sdd_artifact_catalog(self.repo, self.work)
        rows = {row["path"]: row for row in catalog if row.get("path")}
        self.assertEqual(rows["openspec/changes/login/proposal.md"]["status"], "generated")
        self.assertEqual(rows["openspec/changes/login/tasks.md"]["status"], "generated")
        self.assertEqual(rows["openspec/changes/login/design.md"]["status"], "not_generated")
        spec_path = "openspec/changes/login/specs/auth/spec.md"
        confirmed = confirm_artifact(self.repo, self.work_id, spec_path, "tester")
        self.assertTrue(confirmed["ok"])
        catalog = build_sdd_artifact_catalog(self.repo, json.loads((self.repo / ".orchestrator/work" / f"{self.work_id}.json").read_text(encoding="utf-8")))
        spec_row = next(row for row in catalog if row.get("path") == spec_path)
        self.assertEqual(spec_row["status"], "confirmed")
        self.assertEqual(spec_row["confirmed_by"], "tester")

    def test_openspec_discovers_native_artifacts_without_native_ref_and_with_multiple_changes(self):
        self.work["sdd"] = {"provider": "openspec", "native_ref": "", "artifacts": []}
        self.work["execution"]["step"] = "path-selected"
        self.work["path_assessment"] = {"profile": "standard", "reason": "test"}
        (self.repo / ".orchestrator/work" / f"{self.work_id}.json").write_text(json.dumps(self.work), encoding="utf-8")
        old = self.repo / "openspec/changes/older-feature"
        active = self.repo / "openspec/changes/new-auth-flow"
        old.mkdir(parents=True)
        active.mkdir(parents=True)
        (old / "proposal.md").write_text("# Old proposal", encoding="utf-8")
        (active / "proposal.md").write_text("# Auth flow", encoding="utf-8")
        (active / "tasks.md").write_text("# Tasks\n- [ ] Implement auth", encoding="utf-8")
        catalog = build_sdd_artifact_catalog(self.repo, self.work)
        paths = {row.get("path") for row in catalog}
        self.assertIn("openspec/changes/new-auth-flow/tasks.md", paths)
        data = build_dashboard_overview(self.repo)
        item = data["work_items"][0]
        self.assertEqual(item["execution"]["current_phase"], "implementation")
        self.assertEqual(item["execution"]["phase_source"], "native-sdd-artifacts")

    def test_speckit_discovers_tasks_without_native_ref(self):
        self.work["sdd"] = {"provider": "speckit", "native_ref": "", "artifacts": []}
        feature = self.repo / "specs/new-feature"
        feature.mkdir(parents=True)
        (feature / "spec.md").write_text("# Spec", encoding="utf-8")
        (feature / "tasks.md").write_text("# Tasks\n- [ ] Implement feature", encoding="utf-8")
        catalog = build_sdd_artifact_catalog(self.repo, self.work)
        found = {row.get("path") for row in catalog}
        self.assertIn("specs/new-feature/spec.md", found)
        self.assertIn("specs/new-feature/tasks.md", found)

    def test_sdd_catalog_excludes_source_code_for_every_discovery_path(self):
        change = self.repo / "openspec/changes/login"
        change.mkdir(parents=True)
        (change / "proposal.md").write_text("# Proposal", encoding="utf-8")
        (change / "Implementation.java").write_text("class Implementation {}", encoding="utf-8")
        source = self.repo / "src/login.py"
        source.parent.mkdir(parents=True)
        source.write_text("print('implementation')", encoding="utf-8")

        # Provider directory scanning must not expose implementation source.
        catalog = build_sdd_artifact_catalog(self.repo, self.work)
        paths = {row.get("path") for row in catalog}
        self.assertIn("openspec/changes/login/proposal.md", paths)
        self.assertNotIn("openspec/changes/login/Implementation.java", paths)

        # Explicit native_ref and manually registered refs are filtered too.
        self.work["sdd"] = {
            "provider": "custom", "native_ref": "src/login.py",
            "artifacts": [{"path": "src/login.py", "role": "implementation"}],
        }
        catalog = build_sdd_artifact_catalog(self.repo, self.work)
        self.assertFalse(any(row.get("path") == "src/login.py" for row in catalog))
        with self.assertRaisesRegex(ValueError, "ARTIFACT_NOT_DISCOVERED_OR_MISSING"):
            preview_artifact(self.repo, self.work_id, "src/login.py")

    def test_speckit_catalog_excludes_code_even_inside_feature_directory(self):
        self.work["sdd"] = {"provider": "speckit", "native_ref": "", "artifacts": []}
        feature = self.repo / "specs/login"
        feature.mkdir(parents=True)
        (feature / "spec.md").write_text("# Spec", encoding="utf-8")
        (feature / "main.ts").write_text("export const login = true", encoding="utf-8")
        catalog = build_sdd_artifact_catalog(self.repo, self.work)
        paths = {row.get("path") for row in catalog}
        self.assertIn("specs/login/spec.md", paths)
        self.assertNotIn("specs/login/main.ts", paths)

    def test_artifact_confirmation_becomes_generated_when_file_changes(self):
        change = self.repo / "openspec/changes/login"
        change.mkdir(parents=True)
        artifact = change / "proposal.md"
        artifact.write_text("# First version", encoding="utf-8")
        confirm_artifact(self.repo, self.work_id, "openspec/changes/login/proposal.md")
        artifact.write_text("# Changed version", encoding="utf-8")
        catalog = build_sdd_artifact_catalog(self.repo, json.loads((self.repo / ".orchestrator/work" / f"{self.work_id}.json").read_text(encoding="utf-8")))
        row = next(row for row in catalog if row.get("path") == "openspec/changes/login/proposal.md")
        self.assertEqual(row["status"], "generated")
        self.assertTrue(row["confirmation_stale"])

    def test_artifact_preview_only_serves_discovered_repo_artifact(self):
        change = self.repo / "openspec/changes/login"
        change.mkdir(parents=True)
        (change / "proposal.md").write_text("# Login\n\n**A safe preview**", encoding="utf-8")
        preview = preview_artifact(self.repo, self.work_id, "openspec/changes/login/proposal.md")
        self.assertIn("**A safe preview**", preview["content"])
        with self.assertRaises(ValueError):
            preview_artifact(self.repo, self.work_id, "../requirements/login.md")

    def test_http_dashboard_previews_and_confirms_artifacts(self):
        change = self.repo / "openspec/changes/login"
        change.mkdir(parents=True)
        artifact = change / "proposal.md"
        artifact.write_text("# Native proposal", encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.repo, 5))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            from urllib.parse import urlencode
            query = urlencode({"work_item_id": self.work_id, "path": "openspec/changes/login/proposal.md"})
            with urlopen(base + "/api/artifact-preview?" + query, timeout=5) as response:
                preview = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertIn("Native proposal", preview["content"])
            body = json.dumps({"work_item_id": self.work_id, "path": "openspec/changes/login/proposal.md"}).encode("utf-8")
            request = Request(base + "/api/artifact/confirm", data=body, method="POST", headers={"Content-Type": "application/json", "Origin": base})
            with urlopen(request, timeout=5) as response:
                result = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertTrue(result["ok"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_dashboard_html_has_artifact_workbench_and_preview_dialog(self):
        from scripts.dashboard import dashboard_html_path
        html = dashboard_html_path().read_text(encoding="utf-8")
        self.assertIn("SDD 原生产物", html)
        self.assertIn("/api/artifact-preview", html)
        self.assertIn("确认工件", html)
        self.assertIn("artifact-dialog", html)


    def test_speckit_catalog_shows_spec_plan_tasks(self):
        (self.repo / ".specify/memory").mkdir(parents=True)
        (self.repo / ".specify/memory/constitution.md").write_text("# Principles", encoding="utf-8")
        feature = self.repo / "specs/login"
        feature.mkdir(parents=True)
        (feature / "spec.md").write_text("# Specification", encoding="utf-8")
        (feature / "plan.md").write_text("# Plan", encoding="utf-8")
        (feature / "tasks.md").write_text("# Tasks", encoding="utf-8")
        self.work["sdd"] = {"provider": "speckit", "native_ref": "specs/login", "artifacts": []}
        catalog = build_sdd_artifact_catalog(self.repo, self.work)
        found = {row["path"]: row["status"] for row in catalog if row.get("path")}
        self.assertEqual(found["specs/login/spec.md"], "generated")
        self.assertEqual(found["specs/login/plan.md"], "generated")
        self.assertEqual(found["specs/login/tasks.md"], "generated")
        self.assertEqual(found[".specify/memory/constitution.md"], "generated")

    def test_bmad_catalog_detects_planning_artifacts(self):
        planning = self.repo / "_bmad-output/planning-artifacts"
        planning.mkdir(parents=True)
        (planning / "prd.md").write_text("# PRD", encoding="utf-8")
        (planning / "architecture.md").write_text("# Architecture", encoding="utf-8")
        self.work["sdd"] = {"provider": "bmad", "native_ref": "", "artifacts": []}
        catalog = build_sdd_artifact_catalog(self.repo, self.work)
        found = {row["path"]: row["status"] for row in catalog if row.get("path")}
        self.assertEqual(found["_bmad-output/planning-artifacts/prd.md"], "generated")
        self.assertEqual(found["_bmad-output/planning-artifacts/architecture.md"], "generated")
        self.assertFalse(any(row["title"].startswith("Stories") for row in catalog if row.get("path") and row["status"] == "generated"))

    def test_confirmation_only_allowed_for_loopback_dashboard(self):
        change = self.repo / "openspec/changes/login"
        change.mkdir(parents=True)
        (change / "proposal.md").write_text("# Proposal", encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.repo, 5, allow_remote=True, bind_host="0.0.0.0"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            body = json.dumps({"work_item_id": self.work_id, "path": "openspec/changes/login/proposal.md"}).encode("utf-8")
            request = Request(base + "/api/artifact/confirm", data=body, method="POST", headers={"Content-Type": "application/json", "Origin": base})
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


    def test_artifact_refresh_clears_confirmation_after_content_change(self):
        change = self.repo / "openspec/changes/login"
        change.mkdir(parents=True)
        artifact = change / "proposal.md"
        artifact.write_text("# Before", encoding="utf-8")
        confirm_artifact(self.repo, self.work_id, "openspec/changes/login/proposal.md")
        artifact.write_text("# After", encoding="utf-8")
        import contextlib
        import io
        args = type("Args", (), {"repo": str(self.repo), "work": self.work_id, "path": "openspec/changes/login/proposal.md"})()
        with contextlib.redirect_stdout(io.StringIO()):
            code = cmd_artifact_refresh(args)
        self.assertEqual(code, 0)
        updated = load_work(self.repo, self.work_id)
        ref = next(a for a in updated["sdd"]["artifacts"] if a["path"] == "openspec/changes/login/proposal.md")
        self.assertNotIn("confirmed_at", ref)
        self.assertEqual(ref.get("status"), "generated")

    def test_http_confirmation_rejects_unexpected_host_header(self):
        change = self.repo / "openspec/changes/login"
        change.mkdir(parents=True)
        (change / "proposal.md").write_text("# Proposal", encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.repo, 5))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            body = json.dumps({"work_item_id": self.work_id, "path": "openspec/changes/login/proposal.md"}).encode("utf-8")
            request = Request(base + "/api/artifact/confirm", data=body, method="POST", headers={"Content-Type": "application/json", "Origin": "http://attacker.example", "Host": "attacker.example"})
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_remote_bind_requires_explicit_opt_in(self):
        validate_bind_host("127.0.0.1")
        validate_bind_host("localhost")
        with self.assertRaises(ValueError):
            validate_bind_host("0.0.0.0")
        validate_bind_host("0.0.0.0", allow_remote=True)

    def test_loopback_server_rejects_unexpected_host_header(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.repo, 5))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            request = Request(base + "/api/overview", headers={"Host": "attacker.example"})
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_dashboard_serves_page_and_overview_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.repo, 5))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base + "/", timeout=5) as response:
                page = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("window.CAO_REFRESH_SECONDS=5", page)
            with urlopen(base + "/api/overview", timeout=5) as response:
                data = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(data["work_items"][0]["id"], self.work_id)
            request = Request(base + "/api/overview", method="POST")
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 405)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
