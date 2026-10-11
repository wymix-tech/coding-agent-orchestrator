import contextlib
import copy
import io
import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from repair_helpers import RepairCase
from scripts import cao, dashboard
from scripts.state_store import repo_transaction


def can_acquire(repo):
    try:
        with repo_transaction(repo, timeout=.1):
            return True
    except RuntimeError as exc:
        if str(exc) != "STATE_LOCK_TIMEOUT":
            raise
        return False


class TransactionsTests(RepairCase):
    def test_stale_save_is_rejected(self):
        wid = self.start()
        a, b = cao.load_work(self.repo, wid), cao.load_work(self.repo, wid)
        a["resume"]["summary"] = "first"
        cao.save_work(self.repo, a)
        b["resume"]["summary"] = "second"
        with self.assertRaisesRegex(RuntimeError, "STALE_WORK_REVISION"):
            cao.save_work(self.repo, b)
        self.assertEqual(self.read_work(wid)["resume"]["summary"], "first")

    def test_write_failure_does_not_advance_callers_revision(self):
        work = cao.load_work(self.repo, self.start())
        self.assertIn("state_revision", work)
        revision = work["state_revision"]
        with mock.patch.object(cao, "save_json", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                cao.save_work(self.repo, work)
        self.assertEqual(work["state_revision"], revision)
        cao.save_work(self.repo, work)
        self.assertEqual(work["state_revision"], revision + 1)

    def test_concurrent_cli_decisions_are_all_kept(self):
        wid = self.start()
        def decide(n):
            return self.run_cli("decision", "--work", wid, "--decision", f"d-{n}")
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(decide, range(12)))
        self.assertTrue(all(rc == 0 for rc, out in results), results)
        decisions = self.read_work(wid)["decisions"]
        self.assertEqual({d["decision"] for d in decisions}, {f"d-{n}" for n in range(12)})
        self.assertEqual(len(decisions), 12)

    def test_mutate_work_serializes_updates(self):
        self.assertTrue(callable(getattr(cao, "mutate_work", None)), "mutate_work is missing")
        wid = self.start()
        def decide(n):
            def update(work):
                work["decisions"].append({"decision": f"d-{n}"})
            return cao.mutate_work(self.repo, wid, update)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(decide, range(12)))
        self.assertEqual(len(self.read_work(wid)["decisions"]), 12)
        with self.assertRaisesRegex(RuntimeError, "WORK_ITEM_NOT_FOUND"):
            cao.mutate_work(self.repo, "missing", lambda work: None)

    def test_direct_command_locks_before_reading(self):
        wid = self.start()
        original = cao.load_work
        def guarded_read(repo, work_id):
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertFalse(pool.submit(can_acquire, repo).result(timeout=5),
                                 "read-modify-write began outside the transaction")
            return original(repo, work_id)
        args = cao.build_parser().parse_args([
            "decision", "--repo", str(self.repo), "--work", wid, "--decision", "direct",
        ])
        with mock.patch.object(cao, "load_work", side_effect=guarded_read):
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cao.cmd_decision(args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.read_work(wid)["decisions"]), 1)

    def test_status_and_list_recover_missing_or_corrupt_index(self):
        wid = self.start()
        path = self.repo / cao.INDEX_REL
        for command in ("status", "list"):
            for corruption in (None, "{broken"):
                with self.subTest(command=command, corruption=corruption):
                    if corruption is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_text(corruption, encoding="utf-8")
                    rc, out = self.run_cli(command)
                    self.assertEqual(rc, 0, out)
                    self.assertTrue(path.is_file(), "missing index was not recovered")
                    index = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(index["active_work"], wid)
                    self.assertEqual([w["id"] for w in index["work_items"]], [wid])

    def test_valid_but_stale_index_is_rebuilt(self):
        wid = self.start()
        (self.repo / cao.INDEX_REL).write_text(json.dumps(cao.empty_index()), encoding="utf-8")
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0, out)
        self.assertEqual(cao.load_index(self.repo)["active_work"], wid)

    def test_multiple_active_records_report_conflict(self):
        wid = self.start()
        other = copy.deepcopy(self.read_work(wid))
        other.update(id="other", title="other")
        cao.work_path(self.repo, "other").write_text(json.dumps(other), encoding="utf-8")
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 2, out)
        self.assertIn("MULTIPLE_ACTIVE_WORK_ITEMS", out["error"])
        self.assertIn(wid, out["error"])
        self.assertIn("other", out["error"])
        index = json.loads((self.repo / cao.INDEX_REL).read_text(encoding="utf-8"))
        self.assertIsNone(index["active_work"])
        with self.assertRaisesRegex(RuntimeError, "MULTIPLE_ACTIVE_WORK_ITEMS"):
            dashboard.build_dashboard_overview(self.repo)

    def test_index_failure_does_not_lose_authoritative_update(self):
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work["decisions"].append({"decision": "persisted before index failure"})
        with mock.patch.object(cao, "save_index", side_effect=OSError("index disk")):
            with self.assertRaises(OSError):
                cao.save_work(self.repo, work)
        self.assertEqual(len(self.read_work(wid)["decisions"]), 1)
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0, out)
        self.assertEqual(cao.load_index(self.repo)["work_items"][0]["decision_count"], 1)

    def test_stale_session_registration_preserves_new_decision(self):
        wid = self.start()
        stale = cao.load_work(self.repo, wid)
        rc, out = self.run_cli("decision", "--work", wid, "--decision", "new")
        self.assertEqual(rc, 0, out)
        cao.register_session(self.repo, stale, "session", "pi")
        self.assertEqual(len(self.read_work(wid)["decisions"]), 1)
        self.assertEqual(stale, self.read_work(wid))

    def test_stale_existing_session_update_preserves_new_decision(self):
        wid = self.start()
        cao.register_session(self.repo, cao.load_work(self.repo, wid), "session", "pi")
        stale = cao.load_work(self.repo, wid)
        rc, out = self.run_cli("decision", "--work", wid, "--decision", "new")
        self.assertEqual(rc, 0, out)
        cao.update_session_event(self.repo, "session", "pi", "pre_tool", stale)
        self.assertEqual(len(self.read_work(wid)["decisions"]), 1)
        self.assertEqual(stale, self.read_work(wid))

    def test_stale_handoff_reads_current_snapshot(self):
        wid = self.start()
        stale = cao.load_work(self.repo, wid)
        rc, out = self.run_cli("checkpoint", "--work", wid, "--summary", "current progress")
        self.assertEqual(rc, 0, out)
        handoff = cao.create_handoff(self.repo, stale, None, None)
        self.assertEqual(handoff["summary"], "current progress")

    def test_stale_native_sync_preserves_new_decision(self):
        wid = self.start()
        artifact = self.repo / "_bmad-output/implementation-artifacts/spec-a.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("---\nstatus: ready-for-dev\n---\n# a\n", encoding="utf-8")
        stale = cao.load_work(self.repo, wid)
        rc, out = self.run_cli("decision", "--work", wid, "--decision", "new")
        self.assertEqual(rc, 0, out)
        self.assertTrue(cao.reconcile_native_sdd_state(self.repo, stale))
        self.assertEqual(len(self.read_work(wid)["decisions"]), 1)

    def test_stale_confirmation_preserves_new_decision(self):
        wid = self.start()
        artifact = self.repo / "_bmad-output/planning-artifacts/prd.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# PRD\n", encoding="utf-8")
        stale = cao.load_work(self.repo, wid)
        rc, out = self.run_cli("decision", "--work", wid, "--decision", "new")
        self.assertEqual(rc, 0, out)
        cao.mark_artifact_confirmed(self.repo, stale, artifact.relative_to(self.repo).as_posix())
        self.assertEqual(len(self.read_work(wid)["decisions"]), 1)

    def test_dashboard_confirmation_locks_before_catalog_validation(self):
        wid = self.start()
        artifact = self.repo / "_bmad-output/planning-artifacts/prd.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# PRD\n", encoding="utf-8")
        original = dashboard.build_sdd_artifact_catalog
        def guarded_catalog(repo, work):
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertFalse(pool.submit(can_acquire, repo).result(timeout=5),
                                 "catalog validation is outside the confirmation transaction")
            return original(repo, work)
        with mock.patch.object(dashboard, "build_sdd_artifact_catalog", side_effect=guarded_catalog):
            out = dashboard.confirm_artifact(self.repo, wid, artifact.relative_to(self.repo).as_posix())
        self.assertTrue(out["ok"])

    def test_host_dashboard_and_native_sync_keep_concurrent_decisions(self):
        wid = self.start()
        artifact = self.repo / "_bmad-output/implementation-artifacts/spec-a.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("---\nstatus: ready-for-dev\n---\n# a\n", encoding="utf-8")
        relative = artifact.relative_to(self.repo).as_posix()
        def operation(n):
            if n % 4 == 0:
                return self.run_cli("decision", "--work", wid, "--decision", f"d-{n}")
            if n % 4 == 1:
                return self.run_cli("host-event", "--host", "pi", payload={
                    "event": "pre_tool", "session_id": f"session-{n}", "tool_name": "read",
                })
            if n % 4 == 2:
                return 0, dashboard.confirm_artifact(self.repo, wid, relative, f"user-{n}")
            return self.run_cli("status")
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(operation, range(24)))
        self.assertTrue(all(rc == 0 for rc, out in results), results)
        work = self.read_work(wid)
        self.assertEqual({d["decision"] for d in work["decisions"]}, {f"d-{n}" for n in range(0, 24, 4)})
        self.assertEqual(len(work["sdd"]["artifacts"]), 1)
        self.assertEqual(work["sdd"]["artifacts"][0]["status"], "confirmed")
        self.assertEqual(work["sdd"]["native_state"]["canonical"], "ready")

    def test_start_inspect_runs_without_lock_and_returns_fresh_state(self):
        req = self.repo / ".orchestrator/requirements/a.md"
        req.parent.mkdir(exist_ok=True)
        req.write_text("# a\n", encoding="utf-8")
        wid = cao.stable_work_id(self.repo, req)
        def inspect(repo, operation, arguments):
            with ThreadPoolExecutor(max_workers=1) as pool:
                rc, out = pool.submit(self.run_cli, "decision", "--work", wid,
                                      "--decision", "during inspect").result(timeout=15)
            self.assertEqual(rc, 0, out)
            return None
        args = cao.build_parser().parse_args([
            "start", "--repo", str(self.repo), "--requirement", str(req),
        ])
        stream = io.StringIO()
        with mock.patch.object(cao, "run_sdd_command", side_effect=inspect):
            with mock.patch.object(cao, "host_session_identity", return_value=("pi", "start-session")):
                with contextlib.redirect_stdout(stream):
                    rc = cao.cmd_start(args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.read_work(wid)["decisions"]), 1)
        self.assertEqual(json.loads(stream.getvalue())["work_item"]["decision_count"], 1)
        self.assertEqual(cao.load_session(self.repo, "start-session", host="pi")["work_item_id"], wid)

    def test_verify_runs_without_lock_and_preserves_concurrent_decision(self):
        marker, release = self.repo / ".orchestrator/running", self.repo / ".orchestrator/release"
        script = self.repo / ".orchestrator/wait-tests.py"
        script.write_text(
            "import pathlib, time, unittest\n"
            f"marker = pathlib.Path({str(marker)!r})\nrelease = pathlib.Path({str(release)!r})\n"
            "marker.touch()\ndeadline = time.monotonic() + 15\n"
            "while not release.exists() and time.monotonic() < deadline: time.sleep(.01)\n"
            "assert release.exists(), 'test supervisor did not release verification'\n"
            "suite = unittest.defaultTestLoader.discover('acceptance-tests')\n"
            "result = unittest.TextTestRunner(verbosity=2).run(suite)\n"
            "raise SystemExit(0 if result.wasSuccessful() else 1)\n", encoding="utf-8",
        )
        argv = [sys.executable, str(script)]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        cfg_path = self.repo / cao.CONFIG_REL
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["verification"]["scripts"][0]["command"] = command
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        wid = self.start()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.run_cli, "verify", "--work", wid)
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline and not future.done():
                    time.sleep(.01)
                self.assertTrue(marker.exists(), "verification subprocess did not start")
                rc, out = self.run_cli("decision", "--work", wid, "--decision", "during verify")
            finally:
                release.touch()
            verify_rc, verification = future.result(timeout=30)
        self.assertEqual(rc, 0, out)
        self.assertEqual(verify_rc, 0, verification)
        self.assertIn("Ran 1 test", verification["verification"]["results"][0]["stderr_tail"])
        self.assertEqual(len(self.read_work(wid)["decisions"]), 1)

    def test_done_rechecks_after_unlocked_transition(self):
        wid = self.start()
        for args in (
            ("select", "--name", "bmad-build", "--entrypoint", "bmad-build"),
            ("execute", "--command", "bmad-build", "--exit-code", "0"),
        ):
            rc, out = self.run_cli("sdd", "workflow", *args, "--work", wid)
            self.assertEqual(rc, 0, out)
        artifact = self.repo / "_bmad-output/output.md"
        artifact.parent.mkdir()
        artifact.write_text("# Output\n", encoding="utf-8")
        rc, out = self.run_cli("sdd", "workflow", "complete", "--work", wid,
                               "--artifact", artifact.relative_to(self.repo))
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("verify", "--work", wid)
        self.assertEqual(rc, 0, out)
        self.assertTrue(cao.completion_status(self.repo, self.read_work(wid))["ready"])
        def transition(repo, state):
            with ThreadPoolExecutor(max_workers=1) as pool:
                rc, out = pool.submit(self.run_cli, "block", "--work", wid,
                                      "--reason", "during transition").result(timeout=15)
            self.assertEqual(rc, 0, out)
            return None
        args = cao.build_parser().parse_args(["done", "--repo", str(self.repo), "--work", wid])
        stream = io.StringIO()
        with mock.patch.object(cao, "sdd_transition", side_effect=transition):
            with contextlib.redirect_stdout(stream):
                rc = cao.cmd_done(args)
        self.assertEqual(rc, 2, stream.getvalue())
        self.assertEqual(self.read_work(wid)["status"], "active")
        self.assertEqual(len(self.read_work(wid)["blockers"]), 1)
        self.assertEqual(json.loads(stream.getvalue())["error"], "COMPLETION_BLOCKED")
