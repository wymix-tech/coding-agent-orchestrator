"""Crash windows never become passes; rebuilding/retention cannot modify gates."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from scripts import cao
from scripts.runtime_execution import owner, owner_status
from scripts.runtime_queries import inspect_run
from scripts.runtime_store import project_result, record_receipt, run_path, start_run
from tests.repair_helpers import RepairCase, ROOT


class RecoveryTests(RepairCase):
    def test_orphan_receipt_and_forged_projection_do_not_grant_admission(self):
        wid = self.start()
        work = self.read_work(wid)
        start_run(self.repo, wid, 'orphan', 'verification', {})
        self.assertEqual(inspect_run(self.repo, work, 'orphan')['recovery_status'], 'orphaned_start')
        record_receipt(self.repo, wid, 'orphan', {'commands': [{'exit_code': 0}]})
        project_result(self.repo, wid, 'orphan', {'status': 'passed'})
        result = inspect_run(self.repo, work, 'orphan')
        self.assertEqual(result['recovery_status'], 'result_uncommitted')
        self.assertEqual(result['admission_status'], 'pending')
        before = self.read_work(wid)
        rc, out = self.run_cli('runtime', 'recover', '--check')
        self.assertEqual(rc, 0, out)
        self.assertEqual(before, self.read_work(wid))
        self.assertIsNone(self.read_work(wid)['verification'])

    def test_dead_executor_owner_reports_unknown_without_replaying_command(self):
        wid = self.start()
        start_run(self.repo, wid, 'interrupted', 'verification', {})
        script = ('from scripts.runtime_execution import owner; from pathlib import Path; import os,sys; '
                  'c=owner(Path(sys.argv[1]),sys.argv[2],"interrupted"); c.__enter__(); os._exit(9)')
        result = subprocess.run([sys.executable, '-c', script, str(self.repo), wid], cwd=ROOT, timeout=10)
        self.assertEqual(result.returncode, 9)
        self.assertEqual(owner_status(self.repo, wid, 'interrupted'), 'released')
        work = cao.load_work(self.repo, wid)
        work['verification'] = {'run_id': 'interrupted', 'status': 'running'}
        cao.save_work(self.repo, work)
        result = inspect_run(self.repo, self.read_work(wid), 'interrupted')
        self.assertEqual(result['recovery_status'], 'outcome_unknown')
        self.assertEqual(result['admission_status'], 'pending')

    def test_live_owner_and_missing_projection_rebuild(self):
        wid = self.start()
        self.assertEqual(self.run_cli('verify')[0], 0)
        work = self.read_work(wid); rid = work['verification']['run_id']
        path = run_path(self.repo, wid, rid, 'result.json')
        path.write_text('{corrupt disposable projection')
        rc, out = self.run_cli('runtime', 'show', '--run', rid)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out['recovery_status'], 'projection_missing')
        self.assertEqual(out['admission_status'], 'passed')
        before = self.read_work(wid)
        self.assertEqual(self.run_cli('runtime', 'rebuild')[0], 0)
        self.assertEqual(before, self.read_work(wid))
        with owner(self.repo, wid, rid):
            self.assertEqual(owner_status(self.repo, wid, rid), 'running')
        self.assertEqual(owner_status(self.repo, wid, rid), 'released')
        rc, out = self.run_cli('runtime', 'logs', '--run', rid, '--command', 'command-0001', '--stream', 'stderr')
        self.assertEqual(rc, 0, out)
        self.assertIn('Ran 1 test', out['text'])

    def test_projection_failure_leaves_committed_authority_intact(self):
        wid = self.start()
        from scripts import cao_verification
        args = cao.build_parser().parse_args(['verify', '--repo', str(self.repo)])
        import contextlib, io
        with patch('scripts.runtime_store.write_record', wraps=__import__('scripts.runtime_store', fromlist=['write_record']).write_record) as writer:
            def fail_projection(path, record, **kwargs):
                if path.name == 'result.json':
                    raise OSError('optional projection unavailable')
                return writer._mock_wraps(path, record, **kwargs)
            writer.side_effect = fail_projection
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cao_verification.cmd_verify(args), 0)
        work = self.read_work(wid)
        self.assertEqual(work['verification']['status'], 'passed')
        self.assertEqual(inspect_run(self.repo, work, work['verification']['run_id'])['recovery_status'], 'projection_missing')

    def test_result_commit_failure_keeps_receipt_uncommitted(self):
        wid = self.start()
        from scripts import cao_verification
        args = cao.build_parser().parse_args(['verify', '--repo', str(self.repo)])
        with patch('scripts.cao_verification.commit_verification', side_effect=OSError('state volume full')):
            with self.assertRaises(OSError):
                cao_verification.cmd_verify(args)
        work = self.read_work(wid)
        self.assertEqual(work['verification']['status'], 'running')
        self.assertEqual(inspect_run(self.repo, work, work['verification']['run_id'])['recovery_status'], 'result_uncommitted')

    def test_prune_protects_current_run_and_only_deletes_expired_bodies(self):
        wid = self.start()
        from tests.test_provider_light_adaptation import ONESHOT
        from tests.helpers import fixture_native_review
        spec = self.repo / '_bmad-output/spec-fix.md'; spec.parent.mkdir(exist_ok=True)
        spec.write_text(ONESHOT)
        self.assertEqual(self.run_cli('sdd', 'spec', 'bind', '--spec', '_bmad-output/spec-fix.md')[0], 0)
        fixture_native_review(self.repo, lambda repo, *args: self.run_cli(*args))
        self.run_cli('verify'); first = self.read_work(wid)['verification']['run_id']
        self.run_cli('verify'); second = self.read_work(wid)['verification']['run_id']
        self.assertEqual(self.run_cli('done')[0], 0)
        work = cao.load_work(self.repo, wid)
        work['completed_at'] = 1; cao.save_work(self.repo, work)
        start = run_path(self.repo, wid, first, 'start.json')
        value = json.loads(start.read_text()); value['started_at'] = 1; start.write_text(json.dumps(value))
        rc, preview = self.run_cli('runtime', 'prune', '--work', wid)
        self.assertEqual(rc, 0, preview)
        self.assertEqual([item['run_id'] for item in preview['candidates']], [first])
        self.assertIn(second, preview['protected_runs'])
        before = self.read_work(wid)
        rc, out = self.run_cli('runtime', 'prune', '--work', wid, '--apply')
        self.assertEqual(rc, 0, out)
        self.assertEqual(before, self.read_work(wid))
        self.assertTrue(run_path(self.repo, wid, first, 'receipt.json').exists())
        self.assertFalse(list(run_path(self.repo, wid, first).rglob('*.log')))
        self.assertTrue(list(run_path(self.repo, wid, second).rglob('*.log')))

    def test_new_generation_supersedes_old_intent_and_check_is_audited(self):
        wid = self.start()
        work = cao.load_work(self.repo, wid)
        generation = work.get('execution_generation', 0)
        start_run(self.repo, wid, 'old', 'verification', {}, generation=generation)
        work['verification'] = {'run_id': 'old', 'status': 'running'}
        cao.save_work(self.repo, work)
        work = cao.load_work(self.repo, wid)
        work['execution_generation'] = generation + 1
        cao.save_work(self.repo, work)
        result = inspect_run(self.repo, self.read_work(wid), 'old')
        self.assertEqual(result['admission_status'], 'superseded')
        self.assertEqual(result['recovery_status'], 'superseded')
        before = self.read_work(wid)
        self.assertEqual(self.run_cli('runtime', 'recover', '--check')[0], 0)
        self.assertEqual(before, self.read_work(wid))
        rc, out = self.run_cli('runtime', 'events')
        self.assertEqual(rc, 0, out)
        self.assertIn('run.recovery_checked', [item['event'] for item in out['items']])
