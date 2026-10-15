"""Run identity and evidence freshness do not depend on unrelated Work revisions."""
from copy import deepcopy
import unittest

from scripts.sdd_acceptance_runtime import (configure_record, reanchor_record, start_run,
                                           finish_run, acceptance_status)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.work = {"id": "w", "sdd": {"provider": "bmad"}, "decisions": []}
        self.declaration = {"spec": "_bmad-output/spec.md", "criteria": [
            {"id": "AC-1", "source": "_bmad-output/spec.md", "kind": "requirement", "description": "Login"}],
            "executor": {"mode": "command", "entrypoint": "python acceptance.py"}}
        configure_record(self.work, self.declaration)
        self.sources = {"_bmad-output/spec.md": "source-sha"}
        reanchor_record(self.work, self.sources)
        self.inputs = {"code_snapshot": "code-sha", "source_hashes": self.sources,
                       "declaration_sha256": self.work["sdd"]["acceptance"]["declaration_sha256"]}

    def report(self, run):
        return {"run_id": run["id"], "input_sha256": run["input_sha256"], "provider": "bmad",
                "items": [{"id": "AC-1", "status": "passed", "evidence": [{"path": "app.py", "reason": "Checked"}]}]}

    def test_pass_is_bound_to_all_current_inputs_and_file_bytes(self):
        run = start_run(self.work, self.inputs)
        finish_run(self.work, run["id"], self.inputs, self.report(run), {"exit_code": 0}, {"app.py": "sha"})
        self.assertTrue(acceptance_status(self.work, self.inputs, {"app.py": "sha"})["ready"])
        self.assertFalse(acceptance_status(self.work, {**self.inputs, "code_snapshot": "changed"}, {"app.py": "sha"})["ready"])
        self.assertFalse(acceptance_status(self.work, self.inputs, {"app.py": "changed"})["ready"])

    def test_replaced_run_rejects_old_submit_and_failed_run_clears_success(self):
        first = start_run(self.work, self.inputs)
        second = start_run(self.work, self.inputs)
        with self.assertRaisesRegex(ValueError, "SDD_ACCEPTANCE_RUN_CHANGED"):
            finish_run(self.work, first["id"], self.inputs, self.report(first), {"exit_code": 0}, {})
        finish_run(self.work, second["id"], self.inputs, self.report(second), {"exit_code": 1}, {})
        self.assertEqual(acceptance_status(self.work, self.inputs, {})["state"], "failed")

    def test_input_changes_and_invalid_reports_are_persisted_failures(self):
        run = start_run(self.work, self.inputs)
        finish_run(self.work, run["id"], {**self.inputs, "code_snapshot": "new"}, self.report(run), {"exit_code": 0}, {})
        self.assertEqual(self.work["sdd"]["acceptance"]["run"]["status"], "stale")
        run = start_run(self.work, self.inputs)
        finish_run(self.work, run["id"], self.inputs, {}, {"exit_code": 0}, {})
        self.assertEqual(self.work["sdd"]["acceptance"]["run"]["status"], "blocked")

    def test_configuration_does_not_anchor_or_lose_unrelated_work(self):
        before = deepcopy(self.work)
        self.assertFalse(configure_record(self.work, self.declaration))
        self.assertEqual(before, self.work)
        self.work["decisions"].append("Keep session")
        changed = {**self.declaration, "executor": {"mode": "command", "entrypoint": "python different.py"}}
        self.assertTrue(configure_record(self.work, changed))
        self.assertIsNone(self.work["sdd"]["acceptance"]["anchor_sha256"])
        self.assertEqual(self.work["decisions"], ["Keep session"])

