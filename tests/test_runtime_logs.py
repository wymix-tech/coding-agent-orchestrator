"""Bounded streams, credential splitting, and graceful diagnostic I/O failure."""
import hashlib
import os
import subprocess
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.runtime_logs import LogSink
from scripts.runtime_execution import execute
from scripts.runtime_store import start_run
from scripts.verification_runner import run_script


class LogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.cfg = {'mode': 'bounded', 'segment_bytes': 31, 'max_bytes_per_stream': 512}

    def command(self, code):
        args = [sys.executable, "-c", code]
        return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)

    def sink(self, budget=1024, secrets=()):
        return LogSink(self.repo, self.cfg, budget, secrets)

    def contents(self):
        return b''.join(p.read_bytes() for p in sorted(self.repo.glob('*.log')))

    def test_split_tokens_unicode_and_url_credentials_never_reach_disk_or_tail(self):
        sink = self.sink(secrets=['CUSTOM-PRIVATE'])
        data = ('中文🙂 token=VERY_PRIVATE\nAuthorization: Bearer AUTH_PRIVATE\n'
                'https://user:URL_PRIVATE@example.test\n--password ARG_PRIVATE\nCUSTOM-PRIVATE\n'
                'password="FIRST SECOND"\n--secret \'THIRD FOURTH\'\n').encode()
        for byte in data:
            sink.write(0, bytes([byte]))
        metadata = sink.close()['stdout']
        saved = self.contents().decode()
        self.assertIn('中文🙂', saved)
        for value in ('VERY_PRIVATE', 'AUTH_PRIVATE', 'URL_PRIVATE', 'ARG_PRIVATE', 'CUSTOM-PRIVATE', 'FIRST', 'SECOND', 'THIRD', 'FOURTH'):
            self.assertNotIn(value, saved)
            self.assertNotIn(value, sink.tails()['stdout_tail'])
        self.assertTrue(metadata['redacted'])
        for segment in metadata['segments']:
            self.assertLessEqual(segment['bytes'], 31)
            self.assertEqual(hashlib.sha256((self.repo / segment['path']).read_bytes()).hexdigest(), segment['sha256'])

    def test_large_unterminated_lines_are_discarded_and_tail_survives_quota(self):
        sink = self.sink(budget=128)
        for _ in range(512):
            sink.write(0, b'x' * 4096)
        sink.write(0, b'\nlast failure\n')
        metadata = sink.close()['stdout']
        self.assertLessEqual(metadata['captured_bytes'], 64)
        self.assertEqual(metadata['observed_bytes'], 512 * 4096 + len(b'\nlast failure\n'))
        self.assertIn('last failure', sink.tails()['stdout_tail'])
        self.assertNotIn(b'xxxx', self.contents())
        self.assertTrue(metadata['truncated'])

    def test_disabled_logs_keep_safe_tail_and_no_files(self):
        self.cfg['mode'] = 'off'
        sink = self.sink(); sink.write(1, b'token=private\nfailure\n'); sink.close()
        self.assertEqual(self.contents(), b'')
        self.assertNotIn('private', sink.tails()['stderr_tail'])
        self.assertIn('failure', sink.tails()['stderr_tail'])

    def test_write_failure_does_not_stop_pipe_drain_or_change_exit(self):
        sink = self.sink()
        command = self.command("import sys; print('ordinary\\n'*10000); sys.exit(2)")
        with patch('scripts.runtime_logs.Path.open', side_effect=OSError('full')):
            result = run_script(command, self.repo, timeout=10, log_sink=sink)
            metadata = sink.close()['stdout']
        self.assertEqual(result['exit_code'], 2)
        self.assertGreater(result['stdout_bytes'], 4000)
        self.assertIsNotNone(metadata['capture_error'])
        self.assertIn('ordinary', sink.tails()['stdout_tail'])

    def test_raw_protocol_is_unmodified_and_safe_tail_has_redacted_copy(self):
        start_run(self.repo, 'w', 'r', 'sdd_acceptance', {})
        command = self.command('print(\'{"reason":"token=PRIVATE"}\')')
        result = execute(self.repo, 'w', 'r', 'command-0001', command, 10)
        self.assertIn('token=PRIVATE', result['stdout_tail'])
        self.assertNotIn('PRIVATE', result['diagnostic_tails']['stdout_tail'])
        saved = ''.join(p.read_text() for p in (self.repo / '.orchestrator/runtime/w').rglob('*') if p.is_file())
        self.assertNotIn('PRIVATE', saved)

    def test_launch_record_failure_prevents_execution(self):
        start_run(self.repo, 'w', 'r', 'verification', {})
        with patch('scripts.runtime_execution.write_record', side_effect=OSError('full')), \
                patch('scripts.verification_runner.run_script') as runner:
            with self.assertRaises(OSError):
                execute(self.repo, 'w', 'r', 'command-0001', 'would have side effects', 1)
        runner.assert_not_called()
