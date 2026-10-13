import copy
import json

from repair_helpers import RepairCase
from tests.helpers import complete_acceptance
from scripts import cao, completion_policy as policy


class WorkflowExecutionTests(RepairCase):
    def select_execute(self, exit_code=0, entry_exception=False):
        wid = self.start(profile="standard")
        args = ["sdd", "workflow", "select", "--name", "bmad-build", "--entrypoint", "bmad-build"]
        if entry_exception:
            args += ["--allow-unverified", "--reason", "entrypoint uncertainty"]
        rc, out = self.run_cli(*args)
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("sdd", "workflow", "execute", "--command", "bmad-build",
                               "--exit-code", exit_code)
        self.assertEqual(rc, 0, out)
        self.artifact = self.repo / "_bmad-output/story.md"
        self.artifact.parent.mkdir(exist_ok=True)
        self.artifact.write_text("# story\n", encoding="utf-8")
        return wid

    def complete(self, *args):
        return self.run_cli("sdd", "workflow", "complete", "--artifact", "_bmad-output/story.md", "--no-plan", *args)

    def ready(self, work):
        self.assertTrue(callable(getattr(policy, "workflow_ready", None)), "workflow_ready is missing")
        return policy.workflow_ready(work, self.repo)

    def test_nonzero_execution_cannot_complete(self):
        wid = self.select_execute(1)
        before = self.read_work(wid)
        rc, out = self.complete()
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "NATIVE_WORKFLOW_EXECUTION_FAILED")
        self.assertEqual(self.read_work(wid), before)

    def test_successful_workflow_binds_scope_and_artifact_hash(self):
        wid = self.select_execute()
        rc, out = self.complete()
        self.assertEqual(rc, 0, out)
        work = self.read_work(wid)
        wf = work["sdd"]["workflow"]
        self.assertEqual(wf.get("requirement_sha256"), work["requirement"]["sha256"])
        self.assertEqual(wf.get("provider"), "bmad")
        self.assertEqual(wf.get("artifact_hashes", {}).get("_bmad-output/story.md"), cao.sha256_file(self.artifact))
        self.assertEqual(self.ready(work), (True, None))
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        for gate in ("scope_review", "implementation_review"):
            self.run_cli("gate", "satisfy", "--gate", gate, "--evidence", "Fixture reviewed")
        complete_acceptance(self.repo, lambda repo, *args: self.run_cli(*args), wid)
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 0, out)

    def test_failure_exception_is_explicit_and_audited(self):
        wid = self.select_execute(7)
        rc, out = self.complete("--allow-unverified", "--reason", "accepted environment failure")
        self.assertEqual(rc, 0, out)
        wf = self.read_work(wid)["sdd"]["workflow"]
        exception = wf.get("failure_exception") or {}
        self.assertEqual(exception.get("exit_code"), 7)
        self.assertEqual(exception.get("reason"), "accepted environment failure")
        self.assertFalse(wf.get("unverified"), "execution failure must not mint an entrypoint exception")
        events = [event for event in cao.list_lifecycle_events(self.repo, wid, 100)
                  if event["event"] == "workflow.execution_failure_exception"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["exit_code"], 7)
        self.assertEqual(self.ready(self.read_work(wid)), (True, None))
        self.run_cli("verify")
        for gate in ("scope_review", "implementation_review"):
            self.run_cli("gate", "satisfy", "--gate", gate, "--evidence", "Fixture reviewed")
        complete_acceptance(self.repo, lambda repo, *args: self.run_cli(*args), wid)
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 0, out)

    def test_entry_exception_does_not_excuse_execution_failure(self):
        self.select_execute(1, entry_exception=True)
        rc, out = self.complete()
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "NATIVE_WORKFLOW_EXECUTION_FAILED")

    def test_failure_exception_requires_nonempty_reason(self):
        wid = self.select_execute(1)
        before = self.read_work(wid)
        rc, out = self.complete("--allow-unverified", "--reason", " ")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "UNVERIFIED_REASON_REQUIRED")
        self.assertEqual(self.read_work(wid), before)

    def test_new_execution_cannot_reuse_previous_failure_exception_or_completion(self):
        wid = self.select_execute(1)
        rc, out = self.complete("--allow-unverified", "--reason", "first accepted failure")
        self.assertEqual(rc, 0, out)
        self.run_cli("sdd", "workflow", "execute", "--command", "bmad-build", "--exit-code", "2")
        wf = self.read_work(wid)["sdd"]["workflow"]
        self.assertNotEqual(wf["status"], "completed")
        self.assertFalse(wf.get("failure_exception"))
        rc, out = self.complete()
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "NATIVE_WORKFLOW_EXECUTION_FAILED")

    def test_done_rejects_historically_completed_nonzero_execution(self):
        wid = self.select_execute()
        self.complete()
        # Model the old persisted bug, not a supported state-editing command.
        work = self.read_work(wid)
        work["sdd"]["workflow"]["execution"]["exit_code"] = 1
        cao.work_path(self.repo, wid).write_text(json.dumps(work), encoding="utf-8")
        self.run_cli("verify")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertIn("execution failed", out["reason"])

    def test_missing_or_changed_artifact_invalidates_completed_record(self):
        wid = self.select_execute()
        self.complete()
        self.artifact.write_text("# changed\n", encoding="utf-8")
        ok, reason = self.ready(self.read_work(wid))
        self.assertFalse(ok)
        self.assertIn("_bmad-output/story.md", reason)
        self.run_cli("verify")
        rc, out = self.run_cli("done")
        self.assertEqual(rc, 2, out)
        self.assertIn("_bmad-output/story.md", out["reason"])
        self.artifact.unlink()
        ok, reason = self.ready(self.read_work(wid))
        self.assertFalse(ok)
        self.assertIn("_bmad-output/story.md", reason)

    def test_legacy_completed_workflow_does_not_gain_scope_by_complete(self):
        wid = self.select_execute()
        self.complete()
        work = self.read_work(wid)
        for key in ("requirement_sha256", "provider", "artifact_hashes"):
            work["sdd"]["workflow"].pop(key, None)
        cao.work_path(self.repo, wid).write_text(json.dumps(work), encoding="utf-8")
        rc, out = self.complete()
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "NATIVE_WORKFLOW_EVIDENCE_STALE")
        self.assertIn("execute", out["next"])

    def test_scope_changes_and_legacy_fields_do_not_match(self):
        wid = self.select_execute()
        self.complete()
        work = self.read_work(wid)
        for change in ("requirement", "provider", "legacy"):
            with self.subTest(change=change):
                stale = copy.deepcopy(work)
                if change == "requirement":
                    stale["requirement"]["sha256"] = "0" * 64
                elif change == "provider":
                    stale["sdd"]["provider"] = "openspec"
                else:
                    stale["sdd"]["workflow"].pop("requirement_sha256", None)
                ok, reason = self.ready(stale)
                self.assertFalse(ok)
                self.assertIn("stale", reason)
