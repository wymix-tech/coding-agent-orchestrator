"""Reuse a real Pi command result without rerunning it in CAO."""
import subprocess
import sys
import unittest
from tests import test_native_lifecycle_contract as fixtures


class NativeHostExecution(unittest.TestCase):
    def setUp(self):
        self.n = fixtures.NativeLifecycle()
        self.n.setUp()
        self.addCleanup(self.n.doCleanups)
        self.n.bind()
        self.repo = self.n.repo
        self.command = sys.executable + ' preparation_suite.py'
        self.hook('session_start')

    def hook(self, event, **extra):
        return self.n.fixture.check('host-event', '--host', 'pi', payload={'event': event, 'session_id': 'native-primary', **extra})

    def start(self, call='native-check'):
        out = self.hook('pre_tool', toolName='bash', toolCallId=call, input={'command': self.command})
        self.assertEqual('allow', out['decision'])

    def finish(self, call='native-check', **changes):
        result = subprocess.run([sys.executable, 'preparation_suite.py'], cwd=self.repo, capture_output=True, text=True, check=True)
        payload = {'output': result.stdout + result.stderr, 'truncated': False, 'exit_code': 0, **changes}
        return self.hook('post_tool', toolName='bash', toolCallId=call, input={'command': self.command}, structuredContent=payload)

    def test_real_output_is_reusable_and_duplicate_events_do_not_create_runs(self):
        (self.repo / 'product.py').write_text('updated = True\n')
        self.start()
        self.finish()
        rows = self.n.work()['sdd']['native_host_executions']
        self.assertEqual('passed', rows[-1]['status'])
        self.finish()
        self.assertEqual(rows, self.n.work()['sdd']['native_host_executions'])
        self.n.check('lifecycle', 'bind', '--report', rows[-1]['reports'][0]['path'])
        self.n.check('verify')
        self.n.check('done')
        self.assertEqual('completed', self.n.work()['status'])
        self.assertFalse(self.n.work()['sdd'].get('completion_executions'))

    def test_truncated_and_wrong_snapshot_results_do_not_certify_execution(self):
        self.start()
        self.finish(truncated=True)
        self.assertEqual('unavailable', self.n.work()['sdd']['native_host_executions'][-1]['status'])
        self.start(call='changed-check')
        (self.repo / 'new-product.py').write_text('changed = True\n')
        self.finish(call='changed-check')
        self.assertEqual('stale', self.n.work()['sdd']['native_host_executions'][-1]['status'])
        self.assertEqual(2, self.n.cli('done')[0])

    def test_post_without_correlated_start_does_not_create_a_receipt(self):
        self.finish(call='unknown-call')
        self.assertFalse(self.n.work()['sdd'].get('native_host_executions'))
