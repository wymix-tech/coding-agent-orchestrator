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
