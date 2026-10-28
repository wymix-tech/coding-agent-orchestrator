"""Cross-module recovery regressions, exercised against real CLI fixtures."""
import json
import sys
import unittest
from tests import test_native_lifecycle_contract as lifecycle_fixture


class RecoveryClosure(unittest.TestCase):
    setUp = lifecycle_fixture.NativeLifecycle.setUp
    check = lifecycle_fixture.NativeLifecycle.check
    cli = lifecycle_fixture.NativeLifecycle.cli
    work = lifecycle_fixture.NativeLifecycle.work
    bind = lifecycle_fixture.NativeLifecycle.bind

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
        import subprocess
        from tests.test_project_context_acceptance import CLI
        resumed = subprocess.run([sys.executable, str(CLI), 'resume', '--repo', str(self.repo)], env=self.fixture.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)

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
        self.check('reopen', '--reanchor', '--reason', 'reviewed product test suite')
        raw = {'event': 'pre_tool', 'session_id': 'writer', 'toolName': 'write', 'input': {'path': 'feature.py', 'content': 'valid = True\n'}}
        self.assertEqual('allow', self.fixture.cli('host-event', '--host', 'pi', payload=raw)[1]['decision'])
        (self.repo / 'feature.py').write_text('valid = False\n')
        self.assertEqual(1, self.cli('verification', 'check', '--project')[0])
        self.assertEqual('allow', self.fixture.cli('host-event', '--host', 'pi', payload=raw)[1]['decision'])
        self.assertNotEqual(0, self.cli('done')[0])

    def test_transition_success_is_not_replayed_after_product_change(self):
        from scripts.provider_execution import transition
        path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(path.read_text())
        cfg['sdd']['command']['transition'] = sys.executable + ' transition.py'
        path.write_text(json.dumps(cfg))
        (self.repo / 'transition.py').write_text("from pathlib import Path\np=Path('transition-count')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n")
        first = transition(self.repo, 'done', self.wid)
        self.assertEqual(0, first['exit_code'])
        second = transition(self.repo, 'done', self.wid)
        self.assertEqual(first, second)
        self.assertEqual('x', (self.repo / 'transition-count').read_text())

    def test_external_command_timeout_has_receipt_and_bounded_output(self):
        from scripts.provider_execution import run
        result = run(self.repo, [sys.executable, '-c', 'import time; print("started", flush=True); time.sleep(10)'], kind='provider_inspect', timeout=0.1)
        self.assertEqual(124, result['exit_code'])
        self.assertEqual('timed_out', result['process_status'])

    def test_unknown_transition_is_not_automatically_replayed(self):
        from scripts.provider_execution import transition
        path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(path.read_text())
        cfg['sdd']['command']['transition'] = sys.executable + ' transition.py'
        path.write_text(json.dumps(cfg))
        (self.repo / 'transition.py').write_text('raise SystemExit(1)\n')
        self.assertEqual(1, transition(self.repo, 'done', self.wid)['exit_code'])
        with self.assertRaisesRegex(ValueError, 'PROVIDER_TRANSITION_UNRESOLVED'):
            transition(self.repo, 'done', self.wid)
