"""The documented executor runs actual checks and blocks incomplete mappings."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from scripts import acceptance_executor
from scripts.sdd_acceptance_policy import AcceptanceValidationError, normalize_declaration, normalize_report
from scripts.sdd_acceptance_protocol import protocol


class AcceptanceProtocolTests(unittest.TestCase):
    def test_documented_examples_match_protocol_and_runtime_validation(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "references/sdd-acceptance-protocol.md").read_text()
        request, report = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.S)[:2]]
        machine = protocol()
        self.assertEqual(request, machine["request_example"])
        self.assertEqual(report, machine["report_example"])
        identity = machine["request_schema"]["properties"]["input_sha256"]["pattern"]
        self.assertRegex(request["input_sha256"], identity)
        declaration = normalize_declaration(request["declaration"])
        results = normalize_report(report, declaration, request["run_id"], request["input_sha256"], request["provider"])
        self.assertEqual(results[0]["status"], "blocked")
        # Validate real evidence files as well as JSON fields. Sample output is
        # deliberately blocked; it must never be usable as a claimed passed run.
        from scripts.sdd_acceptance_files import report_hashes
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            source = repo / request["declaration"]["spec"]
            source.parent.mkdir(parents=True)
            source.write_text("Illustrative native source; no acceptance was executed.")
            self.assertTrue(report_hashes(repo, results, []))
        # The same shared report contract also accepts the Skill declaration.
        declaration["executor"] = {"mode": "skill", "entrypoint": "native-acceptance"}
        self.assertEqual(normalize_report(report, declaration, request["run_id"], request["input_sha256"], request["provider"]), results)

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

    def test_strict_policy_rejects_broadcast_pass_without_criterion_receipt(self):
        self.request['declaration']['evidence_policy'] = 'criterion-checks-v1'
        report = {key: self.request[key] for key in ('run_id', 'input_sha256', 'provider')}
        report['items'] = [{'id': 'AC-1', 'status': 'passed',
                            'evidence': [{'path': 'check.py', 'reason': 'security.sh exit 0'}]}]
        with self.assertRaisesRegex(ValueError, 'SDD_ACCEPTANCE_CRITERION_CHECK_REQUIRED'):
            normalize_report(report, self.request['declaration'], self.request['run_id'],
                             self.request['input_sha256'], self.request['provider'])
        p, items = self.run_executor({'AC-1': {'command': [sys.executable, 'check.py'],
                    'evidence': ['check.py'], 'selector': 'check.py: arithmetic assertion',
                    'assertion': 'result equals four'}})
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual('AC-1', items[0]['check']['criterion_id'])

    def test_strict_policy_blocks_missing_assertion_mapping(self):
        self.request['declaration']['evidence_policy'] = 'criterion-checks-v1'
        p, items = self.run_executor({'AC-1': {'command': [sys.executable, 'check.py'], 'evidence': ['check.py']}})
        self.assertEqual(2, p.returncode)
        self.assertEqual('blocked', items[0]['status'])

    def test_one_actual_execution_retains_distinct_assertion_outcomes(self):
        self.request['declaration']['evidence_policy'] = 'criterion-checks-v1'
        self.request['declaration']['criteria'].append({**self.request['declaration']['criteria'][0], 'id': 'AC-2'})
        (self.repo / 'shared.py').write_text("import json\nfrom pathlib import Path\np=Path('executions.txt');p.write_text(p.read_text()+'x' if p.exists() else 'x')\nprint(json.dumps({'checks':{'login':{'status':'passed','observed':'valid password accepted'},'reject':{'status':'failed','observed':'invalid password was accepted'}}}))\n")
        common = {'command': [sys.executable, 'shared.py'], 'evidence': ['shared.py'], 'shared_run': 'auth-suite'}
        p, items = self.run_executor({
            'AC-1': {**common, 'selector': 'login', 'assertion': 'valid login succeeds', 'result_key': 'login'},
            'AC-2': {**common, 'selector': 'reject', 'assertion': 'invalid login fails', 'result_key': 'reject'}})
        self.assertEqual(1, p.returncode, p.stderr)
        self.assertEqual(['passed', 'failed'], [item['status'] for item in items])
        self.assertEqual('x', (self.repo / 'executions.txt').read_text())
        self.assertEqual(items[0]['check']['execution_id'], items[1]['check']['execution_id'])

    def test_all_skipped_test_output_cannot_certify_a_criterion(self):
        (self.repo / 'check.py').write_text("print('Ran 2 tests in 0.001s')\nprint('OK (skipped=2)')\n")
        p, items = self.run_executor({'AC-1': {'command': [sys.executable, 'check.py'], 'evidence': ['check.py']}})
        self.assertEqual(2, p.returncode)
        self.assertEqual('blocked', items[0]['status'])

    def test_malformed_shared_result_container_blocks_instead_of_crashing(self):
        (self.repo / 'check.py').write_text("print('{\"checks\": []}')\n")
        p, items = self.run_executor({'AC-1': {'command': [sys.executable, 'check.py'],
                    'evidence': ['check.py'], 'result_key': 'missing'}})
        self.assertEqual(2, p.returncode, p.stderr)
        self.assertEqual('blocked', items[0]['status'])
