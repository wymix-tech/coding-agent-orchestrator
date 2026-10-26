"""Real discovery counters survive truncation and module-level zero suites."""
import shlex
import sys
import tempfile
from pathlib import Path
import unittest

from scripts.test_discovery import DiscoveryReceipt
from scripts.verification_runner import run_script


class TestDiscovery(unittest.TestCase):
    def receipt(self, *chunks):
        value = DiscoveryReceipt()
        for chunk in chunks:
            value.feed(chunk)
        return value.finish()

    def test_common_zero_receipts(self):
        for value in (b'Ran 0 tests in 0.000s\nOK', b'[INFO] Tests run: 0, Failures: 0\n',
                      b'# tests 0\n', b'collected 0 items\n', b'No tests found\n'):
            with self.subTest(value=value):
                self.assertEqual(self.receipt(value), {'observed': True, 'maximum_count': 0})

    def test_split_chunks_and_multiple_modules(self):
        value = self.receipt(b'Tests ru', b'n: 0, Failures: 0\nTests run: 12, Failures: 0\n')
        self.assertEqual(value, {'observed': True, 'maximum_count': 12})

    def test_non_test_diagnostics_remain_unknown(self):
        self.assertEqual(self.receipt(b'0 tests failed\n'), {'observed': False, 'maximum_count': 0})

    def test_actual_runner_counts_are_independent_of_bounded_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / 'receipt.py').write_text("print('Ran 0 tests in 0.001s')\nprint('x' * 20000)\n")
            result = run_script(shlex.join([sys.executable, 'receipt.py']), repo, max_bytes=32)
            self.assertEqual(result['exit_code'], 0)
            self.assertNotIn('Ran 0 tests', result['stdout_tail'])
            self.assertEqual(result['test_discovery'], {'observed': True, 'maximum_count': 0})

    def test_all_skipped_does_not_count_as_executed(self):
        for value in (b'Ran 3 tests in 0.01s\nOK (skipped=3)\n',
                      b'Tests run: 4, Failures: 0, Errors: 0, Skipped: 4\n',
                      b'collected 3 items\n3 skipped in 0.01s\n',
                      b'running 2 tests\ntest result: ok. 0 passed; 0 failed; 2 ignored\n',
                      b'# tests 2\n# skipped 2\n'):
            self.assertEqual({'observed': True, 'maximum_count': 0}, self.receipt(value))

    def test_go_rust_ctest_and_node_receipts(self):
        for value in (b'--- PASS: TestReady (0.00s)\n', b'running 1 test\n',
                      b'100% tests passed, 0 tests failed out of 1\n', b'\xe2\x84\xb9 tests 1\n'):
            self.assertEqual({'observed': True, 'maximum_count': 1}, self.receipt(value))
