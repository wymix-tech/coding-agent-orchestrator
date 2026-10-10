"""Session lifecycle, identity, and current-schema contract tests."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAO = ROOT / "scripts" / "cao.py"


def run(repo: pathlib.Path, *args: str, payload: dict | None = None):
    result = subprocess.run(
        [sys.executable, str(CAO), *args],
        cwd=repo,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        timeout=20,
    )
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        output = {"raw": result.stdout}
    return result.returncode, output, result.stderr


def init_repo(repo: pathlib.Path, with_work: bool = False):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=20)
    if with_work:
        (repo / "openspec").mkdir()
        (repo / "openspec" / "config.yaml").write_text("context: test\n", encoding="utf-8")
        (repo / "requirements").mkdir()
        (repo / "requirements" / "login.md").write_text("Implement login", encoding="utf-8")
        run(repo, "init")
        run(repo, "start", "--requirement", "requirements/login.md")


def sessions_in(repo: pathlib.Path):
    folder = repo / ".orchestrator" / "sessions"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


class SessionModelTests(unittest.TestCase):
    def test_session_schema_tracks_host_events_without_duplicate_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo, with_work=True)
            rc, out, _ = run(repo, "host-event", "--host", "codex", payload={"event": "session_start", "session_id": "native-1"})
            self.assertEqual(rc, 0)
            # `cao start` attaches the current runtime Session as well, so scope
            # the assertion to the host-session identity under test.
            sessions = [s for s in sessions_in(repo) if s["host_session_id"] == "native-1"]
            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session["schema_version"], 2)
            self.assertEqual(session["host_session_id"], "native-1")
            self.assertRegex(session["id"], r"^session-[a-f0-9]{16}$")
            self.assertEqual(session["work_item_id"], run(repo, "status")[1]["work"]["work_item"]["id"])
            self.assertEqual(session["last_event"], "session_start")
            self.assertNotIn("last_action", session)
            self.assertNotIn("last_seen", session)
            index = json.loads((repo / ".orchestrator" / "index.json").read_text(encoding="utf-8"))
            work_file = next((repo / ".orchestrator" / "work").glob("*.json"))
            work = json.loads(work_file.read_text(encoding="utf-8"))
            self.assertNotIn("sessions", index)
            self.assertNotIn("sessions", work)

    def test_native_ids_do_not_collide_across_hosts(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            for host in ("codex", "pi"):
                rc, _, _ = run(repo, "host-event", "--host", host, payload={"event": "session_start", "session_id": "shared-native-id"})
                self.assertEqual(rc, 0)
            sessions = sessions_in(repo)
            self.assertEqual(len(sessions), 2)
            self.assertEqual({s["host"] for s in sessions}, {"codex", "pi"})
            self.assertEqual(len({s["id"] for s in sessions}), 2)
            rc, out, _ = run(repo, "sessions", "show", "--id", "shared-native-id")
            self.assertEqual(rc, 2)
            self.assertIn("ambiguous", out["error"])
            rc, out, _ = run(repo, "sessions", "show", "--id", "shared-native-id", "--host", "pi")
            self.assertEqual(rc, 0)
            self.assertEqual(out["session"]["host"], "pi")

    def test_unknown_host_session_is_rekeyed_when_host_becomes_known(self):
        import hashlib

        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "host-event", "--host", "unknown", payload={"event": "session_start", "session_id": "late-bound-session"})
            unknown_session = sessions_in(repo)[0]
            self.assertEqual(unknown_session["host"], "unknown")

            run(repo, "host-event", "--host", "codex", payload={"event": "prompt_submit", "session_id": "late-bound-session"})
            sessions = sessions_in(repo)
            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            expected_id = "session-" + hashlib.sha256(b"codex\0late-bound-session").hexdigest()[:16]
            self.assertEqual(session["id"], expected_id)
            self.assertEqual(session["host"], "codex")
            self.assertFalse((repo / ".orchestrator" / "sessions" / f"{unknown_session['id']}.json").exists())

    def test_activity_is_derived_and_stale_does_not_end_session(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            run(repo, "host-event", "--host", "codex", payload={"event": "session_start", "session_id": "quiet-session"})
            session = sessions_in(repo)[0]
            session["last_event_at"] = 1
            (repo / ".orchestrator" / "sessions" / f"{session['id']}.json").write_text(json.dumps(session), encoding="utf-8")
            rc, out, _ = run(repo, "sessions", "list", "--stale-after-minutes", "1")
            self.assertEqual(rc, 0)
            self.assertEqual(out["sessions"][0]["activity"], "stale")
            self.assertEqual(out["sessions"][0]["status"], "active")

    def test_primary_stop_creates_handoff_then_ends_session(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo, with_work=True)
            run(repo, "host-event", "--host", "claude-code", payload={"event": "session_start", "session_id": "main-session"})
            rc, _, _ = run(repo, "host-event", "--host", "claude-code", payload={"event": "stop", "session_id": "main-session", "last_assistant_message": "Continuing later"})
            self.assertEqual(rc, 0)
            session = next(s for s in sessions_in(repo) if s["host_session_id"] == "main-session")
            self.assertEqual(session["status"], "ended")
            self.assertEqual(session["last_event"], "stop")
            self.assertEqual(session["end_reason"], "stop")
            self.assertIsNotNone(session["last_handoff_id"])
            handoff_file = next((repo / ".orchestrator" / "handoffs").rglob("*.json"))
            handoff = json.loads(handoff_file.read_text(encoding="utf-8"))
            self.assertEqual(handoff["session_id"], session["id"])
            self.assertEqual(handoff["id"], session["last_handoff_id"])

    def test_subagent_stop_ends_only_subagent_without_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo, with_work=True)
            run(repo, "host-event", "--host", "codex", payload={"event": "session_start", "session_id": "parent-native"})
            run(repo, "host-event", "--host", "codex", payload={"event": "subagent_start", "session_id": "parent-native", "agent_id": "child-native", "parent_session_id": "parent-native"})
            rc, _, _ = run(repo, "host-event", "--host", "codex", payload={"event": "subagent_stop", "session_id": "parent-native", "agent_id": "child-native"})
            self.assertEqual(rc, 0)
            child = next(s for s in sessions_in(repo) if s["host_session_id"] == "child-native")
            self.assertEqual(child["kind"], "subagent")
            self.assertEqual(child["parent_session_id"], next(s["id"] for s in sessions_in(repo) if s["host_session_id"] == "parent-native"))
            self.assertEqual(child["status"], "ended")
            self.assertEqual(list((repo / ".orchestrator" / "handoffs").rglob("*.json")) if (repo / ".orchestrator" / "handoffs").exists() else [], [])
            parent = next(s for s in sessions_in(repo) if s["host_session_id"] == "parent-native")
            self.assertEqual(parent["status"], "active")

    def test_manual_handoff_does_not_end_session_unless_requested(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo, with_work=True)
            run(repo, "host-event", "--host", "codex", payload={"event": "session_start", "session_id": "manual-session"})
            rc, _, _ = run(repo, "handoff", "--session", "manual-session", "--host", "codex", "--reason", "transfer")
            self.assertEqual(rc, 0)
            session = next(s for s in sessions_in(repo) if s["host_session_id"] == "manual-session")
            self.assertEqual(session["status"], "active")
            rc, _, _ = run(repo, "handoff", "--session", "manual-session", "--host", "codex", "--reason", "finish", "--end-session")
            self.assertEqual(rc, 0)
            session = next(s for s in sessions_in(repo) if s["host_session_id"] == "manual-session")
            self.assertEqual(session["status"], "ended")

    def test_blocked_primary_stop_does_not_end_session_or_create_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo, with_work=True)
            run(repo, "host-event", "--host", "codex", payload={"event": "session_start", "session_id": "guarded-session"})
            rc, out, _ = run(repo, "host-event", "--host", "codex", payload={"event": "stop", "session_id": "guarded-session", "last_assistant_message": "Done, login is implemented"})
            self.assertEqual(rc, 0)
            self.assertEqual(out.get("decision"), "block")
            session = next(s for s in sessions_in(repo) if s["host_session_id"] == "guarded-session")
            self.assertEqual(session["status"], "active")
            self.assertEqual(session["last_event"], "stop_blocked")
            handoffs = list((repo / ".orchestrator" / "handoffs").rglob("*.json")) if (repo / ".orchestrator" / "handoffs").exists() else []
            self.assertEqual(handoffs, [])

    def test_resuming_ended_native_session_reactivates_same_internal_session(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo, with_work=True)
            run(repo, "host-event", "--host", "codex", payload={"event": "session_start", "session_id": "reused-session"})
            run(repo, "host-event", "--host", "codex", payload={"event": "stop", "session_id": "reused-session", "last_assistant_message": "Pausing work"})
            before = next(s for s in sessions_in(repo) if s["host_session_id"] == "reused-session")
            rc, _, _ = run(repo, "resume", "--host", "codex", "--session", "reused-session")
            self.assertEqual(rc, 0)
            after = next(s for s in sessions_in(repo) if s["host_session_id"] == "reused-session")
            self.assertEqual(after["id"], before["id"])
            self.assertEqual(after["status"], "active")
            self.assertIsNone(after["ended_at"])
            self.assertEqual(after["last_event"], "resume")

    def test_legacy_session_records_are_not_loaded_or_migrated(self):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            init_repo(repo)
            orch = repo / ".orchestrator"
            (orch / "sessions").mkdir(parents=True)
            (orch / "work").mkdir()
            work_file = orch / "work" / "login.json"
            index_file = orch / "index.json"
            session_file = orch / "sessions" / "legacy-1.json"
            legacy_work = {"id": "login", "sessions": [{"id": "legacy-1"}]}
            legacy_index = {"version": 1, "active_work": None, "work_items": [], "sessions": [{"id": "legacy-1"}]}
            legacy_session = {
                "id": "legacy-1", "host": "codex", "work_item": "login",
                "status": "active", "created_at": 10, "last_seen": 20,
                "last_action": {"instruction": "do something"},
            }
            work_file.write_text(json.dumps(legacy_work), encoding="utf-8")
            index_file.write_text(json.dumps(legacy_index), encoding="utf-8")
            session_file.write_text(json.dumps(legacy_session), encoding="utf-8")

            rc, out, _ = run(repo, "sessions", "list")
            self.assertEqual(rc, 0)
            self.assertEqual(out["sessions"], [])
            self.assertTrue(session_file.exists())
            self.assertEqual(json.loads(index_file.read_text(encoding="utf-8")), legacy_index)
            self.assertEqual(json.loads(work_file.read_text(encoding="utf-8")), legacy_work)


if __name__ == "__main__":
    unittest.main()
