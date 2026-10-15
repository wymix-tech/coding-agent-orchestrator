import contextlib
import copy
import io
import json
from unittest import mock

from repair_helpers import RepairCase
from scripts import cao


class ActiveTests(RepairCase):
    def suspend(self, wid):
        rc, out = self.run_cli("block", "--work", wid, "--reason", "waiting", "--suspend")
        self.assertEqual(rc, 0, out)

    def test_unblock_does_not_steal_active_work(self):
        a = self.start("a")
        self.suspend(a)
        b = self.start("b")
        rc, out = self.run_cli("unblock", "--work", a)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(a)["status"], "suspended")
        self.assertEqual(self.read_work(b)["status"], "active")
        self.assertEqual(self.read_work(a)["blockers"], [])
        self.assertEqual(cao.load_index(self.repo)["active_work"], b)
        self.assertIn("--replace", out["next"])

    def test_unblock_activates_when_no_other_work_is_active(self):
        a = self.start()
        self.suspend(a)
        rc, out = self.run_cli("unblock", "--work", a)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(a)["status"], "active")
        self.assertEqual(cao.load_index(self.repo)["active_work"], a)

    def test_resume_reactivate_conflict_leaves_authority_unchanged(self):
        a = self.start("a")
        self.suspend(a)
        b = self.start("b")
        before = self.read_work(a)
        rc, out = self.run_cli("resume", "--work", a, "--reactivate", "--host", "pi")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "WORK_ITEM_ALREADY_ACTIVE")
        self.assertIn(b, out["active_work_items"])
        self.assertEqual(self.read_work(a), before)
        self.assertEqual(cao.load_index(self.repo)["active_work"], b)

    def test_reopen_conflict_leaves_completed_evidence_unchanged(self):
        a = self.start("a")
        work = cao.load_work(self.repo, a)
        work["status"] = "completed"
        work["gate_status"] = {"scope_review": {"satisfied": True, "evidence": "existing"}}
        cao.save_work(self.repo, work)
        b = self.start("b")
        before = self.read_work(a)
        rc, out = self.run_cli("reopen", "--work", a, "--reason", "incorrect completion")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["error"], "WORK_ITEM_ALREADY_ACTIVE")
        self.assertIn(b, out["active_work_items"])
        self.assertEqual(self.read_work(a), before)

    def test_checkpoint_on_suspended_work_keeps_active_pointer(self):
        a = self.start("a")
        self.suspend(a)
        b = self.start("b")
        rc, out = self.run_cli("checkpoint", "--work", a, "--summary", "progress on a")
        self.assertEqual(rc, 0, out)
        self.assertEqual(cao.load_index(self.repo)["active_work"], b)
        self.assertEqual(self.read_work(a)["status"], "suspended")

    def test_storage_rejects_second_active_before_commit(self):
        a = self.start("a")
        self.suspend(a)
        self.start("b")
        work = cao.load_work(self.repo, a)
        before = copy.deepcopy(work)
        work["status"] = "active"
        with self.assertRaisesRegex(RuntimeError, "WORK_ITEM_ALREADY_ACTIVE"):
            cao.save_work(self.repo, work)
        self.assertEqual(self.read_work(a), before)

    def test_replace_interruption_leaves_zero_not_two_active_items(self):
        a = self.start("a")
        requirement = self.repo / ".orchestrator/requirements/b.md"
        requirement.write_text("# b\n", encoding="utf-8")
        b = cao.stable_work_id(self.repo, requirement)
        original = cao.save_work
        def fail_new_work(repo, work):
            if work["id"] == b:
                raise OSError("new work disk failure")
            return original(repo, work)
        args = cao.build_parser().parse_args([
            "start", "--repo", str(self.repo), "--requirement", str(requirement), "--replace",
        ])
        with mock.patch.object(cao, "save_work", side_effect=fail_new_work):
            with self.assertRaises(OSError):
                with contextlib.redirect_stdout(io.StringIO()):
                    cao.cmd_start(args)
        self.assertEqual(self.read_work(a)["status"], "suspended")
        self.assertIsNone(cao.load_work(self.repo, b))
        self.assertIsNone(cao.load_index(self.repo)["active_work"])
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(cao.load_index(self.repo)["active_work"])

    def test_explicit_suspend_repairs_three_legacy_active_items(self):
        a = self.start()
        for wid in ("other-1", "other-2"):
            work = copy.deepcopy(self.read_work(a))
            work.update(id=wid, title=wid)
            cao.work_path(self.repo, wid).write_text(json.dumps(work), encoding="utf-8")
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 2, out)
        for wid in ("other-1", "other-2"):
            rc, out = self.run_cli("block", "--work", wid, "--reason", "repair conflict", "--suspend")
            self.assertEqual(rc, 0, out)
            self.assertEqual(self.read_work(wid)["status"], "suspended")
        rc, out = self.run_cli("status")
        self.assertEqual(rc, 0, out)
        self.assertEqual(cao.load_index(self.repo)["active_work"], a)

    def test_non_repair_command_cannot_mutate_legacy_conflict(self):
        a = self.start()
        other = copy.deepcopy(self.read_work(a))
        other.update(id="other", title="other")
        cao.work_path(self.repo, "other").write_text(json.dumps(other), encoding="utf-8")
        before = self.read_work(a)
        rc, out = self.run_cli("block", "--work", a, "--reason", "not a repair")
        self.assertEqual(rc, 2, out)
        self.assertIn("MULTIPLE_ACTIVE_WORK_ITEMS", out["error"])
        self.assertEqual(self.read_work(a), before)
