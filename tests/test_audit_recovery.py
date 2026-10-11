import copy
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from repair_helpers import RepairCase
from scripts import cao, state_store


class AuditTests(RepairCase):
    def require_queue(self):
        self.assertTrue(callable(getattr(cao, "queue_event", None)), "queue_event is missing")
        self.assertTrue(callable(getattr(cao, "drain_events", None)), "drain_events is missing")

    def events(self, wid, name="task.reopened"):
        return [event for event in cao.list_lifecycle_events(self.repo, wid, 1000)
                if event["event"] == name]

    def legacy_work(self, status="active"):
        wid = self.start()
        work = self.read_work(wid)
        for key in ("state_revision", "integrity_version", "pending_events"):
            work.pop(key, None)
        work["status"] = status
        original = (json.dumps(work, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        cao.work_path(self.repo, wid).write_bytes(original)
        return wid, original

    def backup_path(self, wid):
        return self.repo / f".orchestrator/backups/integrity-v2/work/{wid}.json"

    def test_two_reanchors_produce_two_events(self):
        wid = self.start()
        for reason in ("first", "second"):
            rc, out = self.run_cli("reopen", "--work", wid, "--reanchor", "--reason", reason)
            self.assertEqual(rc, 0, out)
        events = self.events(wid)
        self.assertEqual(len(events), 2)
        self.assertEqual(len({event["idempotency_key"] for event in events}), 2)
        self.assertEqual({event["reason"] for event in events}, {"first", "second"})

    def test_keyless_events_are_distinct_and_explicit_retry_is_idempotent(self):
        event = {"event": "test.event", "work_item_id": "test", "source": "test"}
        self.assertTrue(cao._append_lifecycle_event(self.repo, event))
        self.assertTrue(cao._append_lifecycle_event(self.repo, event))
        keyed = {**event, "idempotency_key": "explicit-operation"}
        self.assertTrue(cao._append_lifecycle_event(self.repo, keyed))
        self.assertFalse(cao._append_lifecycle_event(self.repo, keyed))
        events = self.events("test", "test.event")
        self.assertEqual(len(events), 3)
        self.assertEqual(len({event["idempotency_key"] for event in events}), 3)

    def test_two_work_items_do_not_dedupe_reopen_events(self):
        a = self.start("a")
        rc, out = self.run_cli("reopen", "--work", a, "--reanchor", "--reason", "same reason")
        self.assertEqual(rc, 0, out)
        self.run_cli("block", "--work", a, "--reason", "switch", "--suspend")
        b = self.start("b")
        rc, out = self.run_cli("reopen", "--work", b, "--reanchor", "--reason", "same reason")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.events(a)), 1)
        self.assertEqual(len(self.events(b)), 1)
        self.assertNotEqual(self.events(a)[0]["idempotency_key"], self.events(b)[0]["idempotency_key"])

    def test_log_failure_keeps_pending_state_and_new_process_recovers(self):
        self.require_queue()
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work["resume"]["summary"] = "committed before audit"
        cao.queue_event(work, {"event": "task.reopened", "work_item_id": wid, "reason": "recovery"})
        with mock.patch.object(cao, "_append_lifecycle_event", side_effect=OSError("log disk")):
            with self.assertRaises(OSError):
                cao.save_work(self.repo, work)
        pending = self.read_work(wid)["pending_events"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.read_work(wid)["resume"]["summary"], "committed before audit")
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(wid)["pending_events"], [])
        self.assertEqual([e["idempotency_key"] for e in self.events(wid)], [pending[0]["idempotency_key"]])

    def test_queue_clear_failure_retries_without_duplicate_event(self):
        self.require_queue()
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work["resume"]["summary"] = "queue clear injection"
        cao.queue_event(work, {"event": "task.reopened", "work_item_id": wid})
        original = cao.save_json
        def fail_clear(path, value):
            if (path == cao.work_path(self.repo, wid) and not value.get("pending_events")
                    and value.get("resume", {}).get("summary") == "queue clear injection"):
                raise OSError("after log fsync, before queue clear")
            return original(path, value)
        with mock.patch.object(cao, "save_json", side_effect=fail_clear):
            with self.assertRaises(OSError):
                cao.save_work(self.repo, work)
        self.assertEqual(len(self.read_work(wid)["pending_events"]), 1)
        self.assertEqual(len(self.events(wid)), 1)
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.events(wid)), 1)
        self.assertEqual(self.read_work(wid)["pending_events"], [])

    def test_log_fsync_failure_is_not_acknowledged_as_drained(self):
        self.require_queue()
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        cao.queue_event(work, {"event": "task.reopened", "work_item_id": wid})
        original = cao._append_lifecycle_event
        def fail_sync(repo, event):
            with mock.patch.object(cao.os, "fsync", side_effect=OSError("log fsync")):
                return original(repo, event)
        with mock.patch.object(cao, "_append_lifecycle_event", side_effect=fail_sync):
            with self.assertRaises(OSError):
                cao.save_work(self.repo, work)
            # A complete line can be visible even though its fsync failed.
            with self.assertRaises(OSError):
                cao.load_work(self.repo, wid)
        self.assertEqual(len(self.read_work(wid)["pending_events"]), 1)
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.events(wid)), 1)
        self.assertEqual(self.read_work(wid)["pending_events"], [])

    def test_direct_work_read_drains_a_persisted_queue(self):
        self.require_queue()
        wid = self.start()
        work = self.read_work(wid)
        cao.queue_event(work, {"event": "task.reopened", "work_item_id": wid})
        cao.work_path(self.repo, wid).write_text(json.dumps(work), encoding="utf-8")
        fresh = cao.load_work(self.repo, wid)
        self.assertEqual(fresh["pending_events"], [])
        self.assertEqual(len(self.events(wid)), 1)
        self.assertEqual(fresh, self.read_work(wid))

    def test_native_event_is_queued_only_after_authority_commits(self):
        wid = self.start()
        artifact = self.repo / "_bmad-output/implementation-artifacts/spec-a.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("---\nstatus: ready-for-dev\n---\n# a\n", encoding="utf-8")
        work = cao.load_work(self.repo, wid)
        with mock.patch.object(cao, "save_json", side_effect=OSError("authority disk")):
            with self.assertRaises(OSError):
                cao.reconcile_native_sdd_state(self.repo, work)
        self.assertEqual(self.events(wid, "sdd.native_state.changed"), [])
        self.assertNotIn("native_state", self.read_work(wid)["sdd"])
        fresh = cao.load_work(self.repo, wid)
        self.assertTrue(cao.reconcile_native_sdd_state(self.repo, fresh))
        self.assertEqual(len(self.events(wid, "sdd.native_state.changed")), 1)

    def test_partial_log_tail_does_not_swallow_recovered_event(self):
        self.require_queue()
        wid = self.start()
        path = self.repo / ".orchestrator/lifecycle/events.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"event":"interrupted"')
        work = cao.load_work(self.repo, wid)
        cao.queue_event(work, {"event": "task.reopened", "work_item_id": wid})
        cao.save_work(self.repo, work)
        self.assertEqual(len(self.events(wid)), 1)
        self.assertEqual(self.read_work(wid)["pending_events"], [])
        self.assertIn('{"event":"interrupted"', path.read_text(encoding="utf-8"))

    def test_pending_audit_does_not_lock_out_explicit_conflict_repair(self):
        self.require_queue()
        a = self.start()
        other = copy.deepcopy(self.read_work(a))
        other.update(id="other", title="other")
        cao.queue_event(other, {"event": "task.reopened", "work_item_id": "other"})
        cao.work_path(self.repo, "other").write_text(json.dumps(other), encoding="utf-8")
        rc, out = self.run_cli("block", "--work", "other", "--suspend", "--reason", "repair")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work("other")["status"], "suspended")
        self.assertEqual(self.read_work("other")["pending_events"], [])
        self.assertEqual(cao.load_index(self.repo)["active_work"], a)
        self.assertEqual(len(self.events("other")), 1)

    def test_first_upgrade_backs_up_exact_bytes_once(self):
        wid, original = self.legacy_work()
        rc, out = self.run_cli("checkpoint", "--work", wid, "--summary", "first upgrade")
        self.assertEqual(rc, 0, out)
        path = self.backup_path(wid)
        self.assertTrue(path.is_file(), "pre-upgrade backup is missing")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.read_work(wid)["integrity_version"], 2)
        rc, out = self.run_cli("checkpoint", "--work", wid, "--summary", "later update")
        self.assertEqual(rc, 0, out)
        self.assertEqual(path.read_bytes(), original)

    def test_backup_copy_failure_prevents_upgrade_and_cleans_temp(self):
        wid, original = self.legacy_work()
        work = cao.load_work(self.repo, wid)
        work["resume"]["summary"] = "must not commit"
        def partial_copy(source, output):
            output.write(b"partial backup")
            raise OSError("copy disk failure")
        with mock.patch.object(shutil, "copyfileobj", side_effect=partial_copy):
            with self.assertRaises(OSError):
                cao.save_work(self.repo, work)
        self.assertEqual(cao.work_path(self.repo, wid).read_bytes(), original)
        self.assertFalse(self.backup_path(wid).exists())
        self.assertEqual(list(self.backup_path(wid).parent.glob("*.tmp")), [])
        self.assertNotIn("state_revision", work)

    def test_interrupted_backup_temp_is_not_a_completed_backup(self):
        wid, original = self.legacy_work()
        folder = self.backup_path(wid).parent
        folder.mkdir(parents=True)
        (folder / "interrupted.tmp").write_bytes(b"partial")
        rc, out = self.run_cli("checkpoint", "--work", wid, "--summary", "retry upgrade")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.backup_path(wid).is_file(), "backup was not retried")
        self.assertEqual(self.backup_path(wid).read_bytes(), original)

    def test_backup_once_does_not_overwrite_existing_backup(self):
        self.assertTrue(callable(getattr(state_store, "backup_once", None)), "backup_once is missing")
        wid, original = self.legacy_work()
        state_store.backup_once(self.repo, cao.work_path(self.repo, wid))
        self.assertEqual(self.backup_path(wid).read_bytes(), original)
        with mock.patch.object(shutil, "copyfileobj", side_effect=OSError("must not recopy")):
            state_store.backup_once(self.repo, cao.work_path(self.repo, wid))
        self.assertEqual(self.backup_path(wid).read_bytes(), original)

    def test_old_completed_record_is_read_without_reopening_or_recertifying(self):
        wid, original = self.legacy_work(status="completed")
        rc, out = self.run_cli("status", "--work", wid)
        self.assertEqual(rc, 0, out)
        self.assertEqual(cao.work_path(self.repo, wid).read_bytes(), original)
        self.assertEqual(self.read_work(wid)["status"], "completed")
        self.assertIsNone(cao.load_index(self.repo)["active_work"])

    def test_concurrent_handoff_sequences_are_unique_and_identical_snapshots_dedupe(self):
        wid = self.start()
        def handoff(n):
            return self.run_cli("handoff", "--work", wid, "--reason", f"switch-{n}")
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(handoff, range(12)))
        self.assertTrue(all(rc == 0 for rc, out in results), results)
        records = cao.list_handoff_records(self.repo, wid)
        self.assertEqual({record["sequence"] for record in records}, set(range(1, 13)))
        args = ("handoff", "--work", wid, "--reason", "same snapshot")
        first_rc, first = self.run_cli(*args)
        second_rc, second = self.run_cli(*args)
        self.assertEqual((first_rc, second_rc), (0, 0), (first, second))
        self.assertEqual(first["handoff"]["id"], second["handoff"]["id"])
