"""Recovery must not turn old native execution into current acceptance."""
from tests.test_native_lifecycle_contract import NativeLifecycle


import unittest


class FailureRecovery(unittest.TestCase):
    setUp = NativeLifecycle.setUp
    check = NativeLifecycle.check
    cli = NativeLifecycle.cli
    work = NativeLifecycle.work
    bind = NativeLifecycle.bind
    def test_old_report_cannot_be_rebound_after_product_edit(self):
        self.bind()
        (self.repo / 'feature.py').write_text('changed = True\n')
        rc, out = self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')
        self.assertEqual(2, rc, out)
        self.assertIn('NATIVE_COMPLETION_REEXECUTION_REQUIRED', str(out))

    def test_stale_native_evidence_returns_phase_to_implementation(self):
        from scripts.cao_work_store import load_work, save_work
        self.bind()
        work = load_work(self.repo, self.wid)
        work['execution'].update(phase='verification', step='verification')
        save_work(self.repo, work)
        (self.repo / 'feature.py').write_text('changed = True\n')
        self.check('next')
        self.assertEqual('implementation', self.work()['execution']['phase'])

    def test_copying_old_report_does_not_refresh_execution(self):
        self.bind()
        (self.repo / 'feature.py').write_text('changed = True\n')
        (self.repo / 'openspec/copied.md').write_bytes(self.report.read_bytes())
        self.assertEqual(2, self.cli('lifecycle', 'bind', '--report', 'openspec/copied.md')[0])

    def test_new_execution_with_identical_output_can_close(self):
        import sys
        self.bind()
        (self.repo / 'feature.py').write_text('changed = True\n')
        result = self.check('lifecycle', 'execute', '--command', sys.executable + ' preparation_suite.py',
                            '--report', 'openspec/completion.md')
        self.assertEqual('passed', result['execution']['status'])
        original = self.report.read_bytes()
        self.bind()
        self.check('verify')
        self.check('done')
        self.assertEqual('completed', self.work()['status'])
        self.assertEqual(original, self.report.read_bytes())

    def test_failed_execution_blocks_binding_old_success(self):
        import sys
        self.bind()
        self.assertEqual(2, self.cli('lifecycle', 'execute', '--command', sys.executable + ' -c "raise SystemExit(1)"',
                                    '--report', 'openspec/failure.log')[0])
        self.assertEqual(2, self.cli('conformance', 'check')[0])
        self.assertEqual(2, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')[0])
        self.assertEqual(2, self.cli('done')[0])

    def test_failed_baseline_next_requires_diagnosis(self):
        from scripts.cao_work_store import load_work, save_work
        self.bind()
        work = load_work(self.repo, self.wid)
        work['verification'] = {'status': 'failed', 'results': [{'reason': 'AssertionError'}]}
        work['execution'].update(phase='verification', step='verification')
        save_work(self.repo, work)
        action = self.check('next')['action']
        self.assertEqual('recovery', action['stage'])
        self.assertEqual('PROJECT_BASELINE_FAILED', action['reason'])
        self.assertIn('Diagnose', action['instruction'])
        self.assertEqual('verification', self.work()['execution']['phase'])
