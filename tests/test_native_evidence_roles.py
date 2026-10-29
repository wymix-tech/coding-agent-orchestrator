"""Gate evidence and actual command capture regressions from the Pi session."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.native_reports import read, validate_coverage
from scripts.native_execution import validate_command
from scripts.native_validation import literal


class EvidenceRoles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.work = {'id': 'work-1', 'requirement': {'sha256': 'req'}, 'sdd': {'provider': 'bmad'}}
        self.path = self.repo / 'result.json'

    def coverage(self, gates, observed=False):
        with patch('scripts.lifecycle_contract.inputs', return_value={'code_snapshot': 'snapshot'}):
            parsed = read(self.repo, self.work, self.path)
        with patch('scripts.native_execution.receipt_matches', return_value=observed), patch('scripts.lifecycle_contract.inputs', return_value={'code_snapshot': 'snapshot'}):
            return validate_coverage(self.repo, self.work, [{'path': 'result.json', 'sha256': 'sha'}], [parsed], gates)

    def test_done_spec_is_not_test_or_acceptance_evidence(self):
        self.path = self.repo / 'spec.md'
        self.path.write_text("---\nstatus: 'done'\n---\n## Verification\n- mvn test expected success\n")
        with self.assertRaisesRegex(ValueError, 'CHECK_EVIDENCE_REQUIRED'):
            self.coverage(['verification', 'sdd_acceptance', 'security_verification'])

    def test_tests_do_not_automatically_satisfy_security(self):
        self.path = self.repo / 'maven.log'
        self.path.write_text('Tests run: 21, Failures: 0, Errors: 0\nBUILD SUCCESS\n')
        with self.assertRaisesRegex(ValueError, 'EXECUTION_SOURCE_REQUIRED'):
            self.coverage(['verification'])
        self.assertEqual(['verification'], self.coverage(['verification'], observed=True))
        with self.assertRaisesRegex(ValueError, 'GATE_EVIDENCE_REQUIRED'):
            self.coverage(['security_verification'], observed=True)

    def test_manual_native_checks_need_scope_and_per_gate_evidence(self):
        payload = {'evidence_type': 'acceptance_result', 'work_id': 'work-1', 'requirement_sha256': 'req', 'code_snapshot': 'snapshot',
                   'status': 'passed', 'checks': [{'id': 'auth-1', 'description': 'Unauthorized request is rejected',
                     'status': 'passed', 'evidence': [{'path': 'http.log', 'sha256': __import__('hashlib').sha256(b'HTTP 401').hexdigest()}], 'gates': ['sdd_acceptance', 'security_verification']}]}
        (self.repo / 'http.log').write_text('HTTP 401')
        self.path.write_text(json.dumps(payload))
        self.assertIn('security_verification', self.coverage(['sdd_acceptance', 'security_verification'], observed=True))
        payload['checks'][0].pop('evidence')
        self.path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'REFERENCE_REQUIRED'):
            self.coverage(['security_verification'])

    def test_actual_silent_execution_can_prove_verification(self):
        self.path = self.repo / 'compile.log'
        self.path.write_text('')
        row = read(self.repo, self.work, self.path, execution={'command': 'mvn compile', 'exit_code': 0})
        self.assertEqual(['execution_result'], row['evidence_types'])
        with patch('scripts.native_execution.receipt_matches', return_value=True), patch('scripts.lifecycle_contract.inputs', return_value={'code_snapshot': 'snapshot'}):
            self.assertEqual(['verification'], validate_coverage(self.repo, self.work, [{}], [row], ['verification']))

    def test_project_check_can_be_executed_without_workflow_command_match(self):
        with patch('scripts.cao_runtime.config', return_value={'verification': {'scripts': [{'command': 'mvn -B test'}]}}), patch('scripts.native_validation.commands', return_value=[]):
            validate_command(self.work, 'mvn -B test', self.repo)
            with self.assertRaisesRegex(ValueError, 'UNBOUND'):
                validate_command(self.work, 'cat old.log', self.repo)

    def test_native_declaration_is_literal_and_rejects_shell_reconstruction(self):
        self.assertTrue(literal('Run `mvn -B test`.', 'mvn -B test'))
        self.assertFalse(literal('mvn test && cat old.log', 'mvn test && cat old.log'))
        self.assertFalse(literal('mvn -B test', 'mvn test'))

    def test_declared_report_replay_or_inline_output_is_not_native_execution(self):
        from scripts.native_validation import execution_command
        for command in ('cat old.log', 'env cat old.log', 'echo passed', 'python -c "print(123)"', 'bash -lc "cat old.log"'):
            self.assertFalse(execution_command(command), command)
        self.assertTrue(execution_command('mvn -B test'))
        self.assertTrue(execution_command('bash .orchestrator/scripts/verification/native.sh'))


class ReferenceValidation(unittest.TestCase):
    def test_missing_tampered_and_unobserved_references(self):
        from scripts.native_reports import validate_references
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            ref = {'path': 'test.log', 'sha256': hashlib.sha256(b'passed').hexdigest()}
            observation = {'check_records': [{'gates': ['integration_verification'], 'evidence': [ref]}]}
            with self.assertRaisesRegex(ValueError, 'REFERENCE_MISSING'):
                validate_references(repo, {}, observation)
            (repo / 'test.log').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'REFERENCE_CHANGED'):
                validate_references(repo, {}, observation)
            (repo / 'test.log').write_text('passed')
            with patch('scripts.native_execution.receipt_matches', return_value=False):
                with self.assertRaisesRegex(ValueError, 'REFERENCE_EXECUTION_REQUIRED'):
                    validate_references(repo, {}, observation)
            with patch('scripts.native_execution.receipt_matches', return_value=True):
                validate_references(repo, {}, observation)
