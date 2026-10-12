"""Regressions from the supplied real Pi session, using the real CAO CLI."""
import shlex

from repair_helpers import RepairCase, ROOT
from scripts import cao


class PostToolVerificationTests(RepairCase):
    def event(self, name):
        rc, out = self.run_cli("host-event", "--host", "pi", payload={
            "event": name, "session_id": "native-pi",
            "toolName": "write", "input": {"path": "src.py", "content": "x = 1"},
        })
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "allow")
        self.assertEqual(cao.load_session(self.repo, "native-pi", host="pi")["status"], "active")

    def test_null_verification_survives_post_tool_and_file_changed(self):
        wid = self.start()
        self.assertIsNone(self.read_work(wid)["verification"])
        for name in ("post_tool", "file_changed"):
            with self.subTest(event=name):
                self.event(name)
                self.assertIsNone(self.read_work(wid)["verification"])

    def test_missing_legacy_verification_survives_post_tool(self):
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work.pop("verification")
        cao.save_work(self.repo, work)
        self.event("post_tool")
        self.assertNotIn("verification", self.read_work(wid))

    def test_pending_verification_remains_pending(self):
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        work["verification"] = {"status": "pending"}
        cao.save_work(self.repo, work)
        self.event("post_tool")
        self.assertEqual(self.read_work(wid)["verification"]["status"], "pending")

    def test_passed_verification_still_records_stale_summary(self):
        wid = self.start()
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        (self.repo / "src.py").write_text("x = 1", encoding="utf-8")
        self.event("post_tool")
        work = self.read_work(wid)
        self.assertIn("verification is now stale", work["resume"]["summary"])
        self.assertFalse(cao.bound_verification_fresh(work, cao.verification_inputs(self.repo, work, cao.config(self.repo))))
