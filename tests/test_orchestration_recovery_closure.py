"""Cross-module recovery regressions, exercised against real CLI fixtures."""
import json
import sys
import unittest
from tests.test_native_lifecycle_contract import NativeLifecycle


class RecoveryClosure(unittest.TestCase):
    setUp = NativeLifecycle.setUp
    check = NativeLifecycle.check
    cli = NativeLifecycle.cli
    work = NativeLifecycle.work
    bind = NativeLifecycle.bind

    def execute(self):
        return self.check('lifecycle', 'execute', '--command', sys.executable + ' preparation_suite.py', '--report', 'openspec/completion.md')

    def test_native_success_is_resolved_and_resumable(self):
        from scripts.runtime_queries import inspect_run
        from scripts.recovery_plan import plan
        self.bind()
        rid = self.execute()['execution']['run_id']
        state = inspect_run(self.repo, self.work(), rid)
        self.assertEqual('passed', state['admission_status'])
        self.assertTrue(state['current_run'])
        self.assertFalse(plan(self.repo, self.work())['unresolved_runs'])
        self.assertEqual(0, self.cli('resume')[0])

    def test_corrected_gate_request_can_bind(self):
        for _ in range(2):
            self.assertEqual(2, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md', '--gate', 'made_up')[0])
        self.bind()

    def test_wrapper_execution_keeps_dependencies_without_command_in_log(self):
        from scripts.cao_runtime import config
        command = config(self.repo)['verification']['scripts'][0]['command']
        self.report.write_text('Command: ' + command + '\nstatus: passed\n')
        self.bind()
        self.check('lifecycle', 'execute', '--command', command, '--report', 'openspec/completion.md')
        self.bind()
        self.check('conformance', 'check')

    def test_identical_binding_finishes_product_repair_once(self):
        from scripts.cao_work_store import load_work, save_work
        (self.repo / 'preparation_suite.py').write_text("import unittest,sys\nclass T(unittest.TestCase):\n def test_real(self): self.assertEqual(2+2,4)\nr=unittest.TestResult()\nunittest.defaultTestLoader.loadTestsFromTestCase(T).run(r)\nprint('Ran 1 test in native execution\\nOK' if r.wasSuccessful() else 'FAILED')\nsys.exit(not r.wasSuccessful())\n")
        self.check('verification', 'check', '--project')
        self.check('reopen', '--reanchor', '--reason', 'deterministic runner')
        self.bind()
        self.execute()
        self.bind()
        work = load_work(self.repo, self.wid)
        work['verification'] = {'status': 'failed', 'run_id': 'failed-baseline'}
        save_work(self.repo, work)
        self.check('lifecycle', 'repair', '--kind', 'product', '--reason', 'product diagnosis')
        self.execute()
        self.bind()
        self.assertNotIn('completion_repair', self.work()['sdd'])
        before = self.work()['sdd'].get('completion_repair_history')
        self.bind()
        self.assertEqual(before, self.work()['sdd'].get('completion_repair_history'))

    def test_failed_project_product_check_allows_repair(self):
        (self.repo / 'feature.py').write_text('valid = True\n')
        (self.repo / 'preparation_suite.py').write_text('import unittest,feature\nclass T(unittest.TestCase):\n def test_behavior(self):self.assertTrue(feature.valid)\nunittest.main()\n')
        self.check('verification', 'check', '--project')
        raw = {'event': 'pre_tool', 'session_id': 'writer', 'toolName': 'write', 'input': {'path': 'feature.py', 'content': 'valid = True\n'}}
        self.assertEqual('allow', self.fixture.cli('host-event', '--host', 'pi', payload=raw)[1]['decision'])
        (self.repo / 'feature.py').write_text('valid = False\n')
        self.assertEqual(1, self.cli('verification', 'check', '--project')[0])
        self.assertEqual('allow', self.fixture.cli('host-event', '--host', 'pi', payload=raw)[1]['decision'])
        self.assertNotEqual(0, self.cli('done')[0])
