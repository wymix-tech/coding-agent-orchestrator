"""Behavioral continuity tests, including real CLI and interrupted multi-file writes."""
from pathlib import Path
from unittest.mock import patch
from tests.repair_helpers import RepairCase


class OperationsTests(RepairCase):
    def queue(self):
        path = self.repo / '.orchestrator/requirements/queued.md'
        path.write_text('# queued\nNew independent request\n')
        rc, out = self.run_cli('start', '--defer', '--requirement', str(path))
        self.assertEqual(rc, 0, out)
        return out['work_item']['id']

    def bytes(self):
        return {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}

    def test_previews_are_readonly_and_queue_preserves_active(self):
        wid = self.start()
        before = self.read_work(wid)
        target = self.queue()
        self.assertEqual(before, self.read_work(wid))
        files = self.bytes()
        self.assertEqual(self.run_cli('resume', '--work', wid, '--check')[0], 0)
        self.assertEqual(self.run_cli('switch', '--to', target, '--check')[0], 0)
        self.assertEqual(files, self.bytes())

    def test_switch_roundtrip_and_same_target_are_idempotent(self):
        wid = self.start()
        target = self.queue()
        rc, out = self.run_cli('switch', '--to', target)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(wid)['status'], 'suspended')
        self.assertEqual(self.read_work(target)['status'], 'active')
        self.assertEqual(self.run_cli('switch', '--to', target)[1]['status'], 'unchanged')
        self.assertEqual(self.run_cli('switch', '--to', wid)[0], 0)
        self.assertEqual(self.read_work(wid)['status'], 'active')

    def test_switch_write_failure_recovers_same_journal(self):
        wid = self.start()
        target = self.queue()
        from scripts import cao_work_store
        from scripts.work_operations import switch, pending, recover
        real = cao_work_store.save_work
        def fail_target(repo, work):
            if work['id'] == target and work['status'] == 'active':
                raise OSError('simulated write failure')
            return real(repo, work)
        with patch('scripts.cao_work_store.save_work', side_effect=fail_target):
            with self.assertRaises(OSError):
                switch(self.repo, target, 'test interruption')
        journal = pending(self.repo)[0]
        self.assertEqual(self.read_work(wid)['status'], 'suspended')
        self.assertEqual(recover(self.repo, journal['id'])['status'], 'switched')
        self.assertEqual(recover(self.repo, journal['id'])['status'], 'unchanged')
        self.assertEqual(self.read_work(target)['status'], 'active')

    def test_coordination_item_resolution_survives_resume(self):
        wid = self.start()
        self.assertEqual(self.run_cli('checkpoint', '--item', 'collect report')[0], 0)
        key = self.read_work(wid)['resume']['items'][0]['id']
        self.assertEqual(self.run_cli('checkpoint', '--resolve', key)[0], 0)
        report = self.run_cli('resume', '--check', '--work', wid)[1]['recovery']
        self.assertEqual(report['continuity']['coordination_items']['total'], 0)

    def test_amend_checks_revision_and_preserves_history(self):
        wid = self.start()
        path = self.repo / '.orchestrator/requirements/candidate.md'
        path.write_text('# revised\nExplicitly revised scope\n')
        rc, preview = self.run_cli('amend', '--work', wid, '--requirement', str(path), '--request', 'user revision', '--check')
        self.assertEqual(rc, 0, preview)
        self.assertNotEqual(self.run_cli('amend', '--work', wid, '--requirement', str(path), '--request', 'user revision', '--apply', '--expected-revision', '0')[0], 0)
        rc, out = self.run_cli('amend', '--work', wid, '--requirement', str(path), '--request', 'user revision', '--apply', '--expected-revision', str(preview['expected_revision']))
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.read_work(wid)['requirement_history'])

    def test_original_evidence_bytes_survive_overwrite(self):
        self.start()
        path = self.repo / '_bmad-output/result.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text('status: failed\n')
        rc, out = self.run_cli('evidence', 'attach', '--kind', 'test', '--path', '_bmad-output/result.md')
        self.assertEqual(rc, 0, out)
        path.write_text('status: passed\n')
        rc, shown = self.run_cli('evidence', 'show', '--id', out['evidence']['id'])
        self.assertEqual(rc, 0, shown)
        import base64
        self.assertEqual(base64.b64decode(shown['original_base64']), b'status: failed\n')

    def test_complete_receipt_finalizes_without_reexecuting(self):
        wid = self.start()
        from scripts import cao, cao_verification
        import contextlib, io
        args = cao.build_parser().parse_args(['verify', '--repo', str(self.repo)])
        with patch('scripts.cao_verification.commit_verification', side_effect=OSError('interrupt commit')):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
                cao_verification.cmd_verify(args)
        work = self.read_work(wid)
        rid = work['verification']['run_id']
        self.assertEqual(work['verification']['status'], 'running')
        rc, out = self.run_cli('runtime', 'finalize', '--run', rid)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out['verification']['status'], 'passed')
        self.assertEqual(self.run_cli('runtime', 'finalize', '--run', rid)[1]['status'], 'unchanged')

    def test_runtime_projection_does_not_finalize(self):
        wid = self.start()
        from scripts.runtime_store import start_run, project_result
        start_run(self.repo, wid, 'fake', 'verification', {})
        project_result(self.repo, wid, 'fake', {'status': 'passed'})
        self.assertNotEqual(self.run_cli('runtime', 'finalize', '--run', 'fake')[0], 0)

    def test_unknown_host_effect_blocks_switch(self):
        self.start()
        target = self.queue()
        rc, _ = self.run_cli('host-event', '--host', 'pi', payload={'event': 'pre_tool', 'session_id': 'writer', 'toolName': 'write', 'toolCallId': 'pending', 'input': {'path': '_bmad-output/note.md'}})
        self.assertEqual(rc, 0)
        rc, out = self.run_cli('switch', '--to', target)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out['error'], 'SOURCE_HOST_EFFECT_UNRESOLVED')
