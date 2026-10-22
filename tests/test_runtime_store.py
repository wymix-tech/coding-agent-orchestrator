"""Durability, safe paths, quotas, and event-history contracts."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import cao
from scripts.runtime_store import (ensure_manifest, observation, read_record, safe_path,
                                   start_run, work_events, write_record)
from tests.repair_helpers import RepairCase, ROOT


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def test_path_escape_and_symlink_are_rejected(self):
        for value in ('../x', '.', '..', '/tmp/x', r'C:\\temp', 'x/y', r'x\y', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_path(self.repo, value)
        target = self.repo / 'outside'; target.mkdir()
        parent = self.repo / '.orchestrator'; parent.mkdir()
        try:
            (parent / 'runtime').symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaises(ValueError):
            ensure_manifest(self.repo, 'work')
        self.assertEqual(list(target.iterdir()), [])

    def test_idempotency_conflict_and_unknown_schema(self):
        path = safe_path(self.repo, 'w', 'fact.json')
        write_record(path, {'schema_version': 1, 'value': 1})
        write_record(path, {'schema_version': 1, 'value': 1})
        with self.assertRaisesRegex(ValueError, 'CONFLICT'):
            write_record(path, {'schema_version': 1, 'value': 2})
        self.assertEqual(read_record(path)['value'], 1)
        path.write_text('{"schema_version": 99}')
        with self.assertRaisesRegex(ValueError, 'VERSION_UNSUPPORTED'):
            read_record(path)

    def test_atomic_failure_preserves_previous_file(self):
        path = safe_path(self.repo, 'w', 'fact.json')
        write_record(path, {'value': 1})
        with patch('scripts.state_store.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                write_record(path, {'value': 2}, immutable=False)
        self.assertEqual(read_record(path)['value'], 1)

    def test_parallel_process_observations_do_not_overwrite(self):
        script = ('from pathlib import Path; from scripts.runtime_store import observation; '
                  'import sys; observation(Path(sys.argv[1]), "w", sys.argv[2], '
                  '{"event":"test", "source":"host_observation"})')
        def record(i):
            return subprocess.run([sys.executable, '-c', script, str(self.repo), f'obs-{i}'],
                                  cwd=ROOT, capture_output=True, timeout=30)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(record, range(16)):
                self.assertEqual(result.returncode, 0, result.stderr)
        folder = safe_path(self.repo, 'w', 'observations')
        self.assertEqual(len(list(folder.glob('*.json'))), 16)
        with self.assertRaisesRegex(ValueError, 'CONFLICT'):
            observation(self.repo, 'w', 'obs-0', {'different': True})

    def test_quota_reservation_precedes_fact_write(self):
        from scripts.runtime_store import DEFAULTS
        cfg = json.loads(json.dumps(DEFAULTS)); cfg['max_observations'] = 1
        with patch('scripts.runtime_store.settings', return_value=cfg):
            observation(self.repo, 'w', 'one', {'event': 'test'})
            observation(self.repo, 'w', 'one', {'event': 'test'})
            with self.assertRaisesRegex(RuntimeError, 'QUOTA'):
                observation(self.repo, 'w', 'two', {'event': 'test'})


class HistoryTests(RepairCase):
    def test_checkpoints_decisions_blockers_have_history_without_reintake(self):
        wid = self.start()
        before = self.read_work(wid)
        for summary in ('first fragment', 'second fragment'):
            self.assertEqual(self.run_cli('checkpoint', '--summary', summary, '--completed', summary)[0], 0)
        self.run_cli('decision', '--decision', 'use bounded logs', '--reason', 'keep disk bounded')
        self.run_cli('block', '--reason', 'await fixture')
        self.run_cli('unblock', '--work', wid)
        history = list(work_events(self.repo, wid))
        checkpoints = [e for e in history if e['event'] == 'checkpoint.recorded']
        self.assertEqual([e['payload']['summary'] for e in checkpoints], ['first fragment', 'second fragment'])
        self.assertLess(checkpoints[0]['state_revision'], checkpoints[1]['state_revision'])
        self.assertEqual(len([e for e in history if e['event'] == 'decision.recorded']), 1)
        self.assertEqual(len([e for e in history if e['event'].startswith('blocker.')]), 2)
        after = self.read_work(wid)
        self.assertEqual(before['requirement'], after['requirement'])
        rc, out = self.run_cli('runtime', 'events', '--limit', '2')
        self.assertEqual(rc, 0, out)
        seen = [e['event_id'] for e in out['items']]
        rc, more = self.run_cli('runtime', 'events', '--limit', '2', '--cursor', out['next_cursor'])
        self.assertEqual(rc, 0, more)
        self.assertFalse(set(seen) & {e['event_id'] for e in more['items']})

    def test_runtime_activity_and_log_config_do_not_change_code_snapshot(self):
        wid = self.start()
        before = cao.code_snapshot(self.repo)
        start_run(self.repo, wid, 'test', 'verification', {})
        observation(self.repo, wid, 'test-obs', {'event': 'test'})
        cfg = cao.config(self.repo); cfg['runtime']['logs']['mode'] = 'off'
        cao.save_json(self.repo / '.orchestrator/config.json', cfg)
        self.assertEqual(cao.code_snapshot(self.repo), before)
        (self.repo / 'product.py').write_text('changed=True\n')
        self.assertNotEqual(cao.code_snapshot(self.repo), before)
