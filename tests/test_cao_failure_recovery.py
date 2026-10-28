"""Recovery must not turn old native execution into current acceptance."""
from tests.test_native_lifecycle_contract import NativeLifecycle


class FailureRecovery(NativeLifecycle):
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
