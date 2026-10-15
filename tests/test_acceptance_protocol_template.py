"""The documented executor runs actual checks and blocks incomplete mappings."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import acceptance_executor
from scripts.sdd_acceptance_policy import AcceptanceValidationError, normalize_report
from scripts.sdd_acceptance_protocol import protocol


class AcceptanceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.request = protocol()["request_example"]
        self.request["declaration"]["outputs"] = ["openspec/cao-acceptance/demo/report.json"]
        source = self.repo / self.request["declaration"]["spec"]
        source.parent.mkdir(parents=True)
        source.write_text("# Spec\nThe result must be four.\n")
        (self.repo / "check.py").write_text("assert 2 + 2 == 4\n")

    def run_executor(self, checks=None):
        args = [sys.executable, acceptance_executor.__file__]
        if checks is not None:
            (self.repo / "checks.json").write_text(json.dumps(checks))
            args += ["--checks", "checks.json"]
        env = {**os.environ, "CAO_SDD_ACCEPTANCE_REQUEST": json.dumps(self.request)}
        p = subprocess.run(args, cwd=self.repo, env=env, text=True, capture_output=True, timeout=15)
        self.assertTrue(p.stdout, p.stderr)
        report = json.loads(p.stdout)
        items = normalize_report(report, self.request["declaration"], self.request["run_id"], self.request["input_sha256"], self.request["provider"])
        output = self.repo / self.request["declaration"]["outputs"][0]
        self.assertEqual(json.loads(output.read_text()), report)
        return p, items

    def test_missing_checks_are_reported_as_blocked_not_passed(self):
        p, items = self.run_executor()
        self.assertEqual(p.returncode, 2)
        self.assertEqual(items[0]["status"], "blocked")

    def test_real_check_success_failure_and_stderr_do_not_corrupt_report(self):
        checks = {"AC-1": {"command": [sys.executable, "check.py"], "evidence": ["check.py"]}}
        p, items = self.run_executor(checks)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(items[0]["status"], "passed")
        (self.repo / "check.py").write_text("print('diagnostic output')\nassert 2 + 2 == 5\n")
        p, items = self.run_executor(checks)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(items[0]["status"], "failed")
        self.assertIn("diagnostic output", p.stderr)

    def test_each_criterion_requires_its_own_declared_check(self):
        criterion = {**self.request["declaration"]["criteria"][0], "id": "AC-2"}
        self.request["declaration"]["criteria"].append(criterion)
        p, items = self.run_executor({"AC-1": {"command": [sys.executable, "check.py"], "evidence": ["check.py"]}})
        self.assertEqual(p.returncode, 2)
        self.assertEqual([item["status"] for item in items], ["passed", "blocked"])

    def test_schema_and_field_diagnostic_explain_the_report_array(self):
        report = protocol()["report_example"]
        report["items"][0]["evidence"] = {"path": "check.py", "reason": "wrong shape"}
        with self.assertRaises(AcceptanceValidationError) as cm:
            normalize_report(report, self.request["declaration"], self.request["run_id"], self.request["input_sha256"], self.request["provider"])
        self.assertEqual(str(cm.exception), "SDD_ACCEPTANCE_RESULT_INVALID")
        self.assertEqual(cm.exception.diagnostic["field"], "$.items[0].evidence")
        schema = protocol()["report_schema"]
        self.assertEqual(schema["properties"]["items"]["items"]["properties"]["evidence"]["type"], "array")

    def test_protocol_is_available_without_a_work_or_initialization(self):
        from scripts import cao
        p = subprocess.run([sys.executable, cao.__file__, "sdd", "acceptance", "protocol", "--repo", str(self.repo)],
                           text=True, capture_output=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("JSON string", json.loads(p.stdout)["environment"]["encoding"])
        self.assertFalse((self.repo / ".orchestrator").exists())

    def test_missing_declared_report_identifies_the_exact_output_field(self):
        from scripts.sdd_acceptance_files import report_hashes
        with self.assertRaises(AcceptanceValidationError) as cm:
            report_hashes(self.repo, [], ["openspec/cao-acceptance/demo/missing.json"])
        self.assertEqual(cm.exception.diagnostic["field"], "$.declaration.outputs[0]")
        self.assertIn("missing.json", cm.exception.diagnostic["expected"])
