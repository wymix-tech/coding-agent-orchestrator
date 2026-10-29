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

    def test_blocked_switch_without_active_source_returns_actionable_json(self):
        from scripts.cao_work_store import load_work, save_work
        wid = self.start()
        target = self.queue()
        target_work = load_work(self.repo, target)
        requirement = target_work['requirement']['path']
        (self.repo / requirement).write_text('# changed requirement\n')
        rc, out = self.run_cli('switch', '--to', target)
        self.assertEqual(2, rc, out)
        self.assertEqual('REQUIREMENT_CHANGED_REANCHOR_REQUIRED', out['error'])
        self.assertIn(requirement, out['next'])
        self.assertNotIn('runtime effects', out['next'])
        work = load_work(self.repo, wid)
        work['status'] = 'suspended'
        save_work(self.repo, work)
        before = {key: self.read_work(key) for key in (wid, target)}
        rc, out = self.run_cli('switch', '--to', target)
        self.assertEqual(2, rc, out)
        self.assertEqual('REQUIREMENT_CHANGED_REANCHOR_REQUIRED', out['error'])
        self.assertIn(requirement, out['next'])
        self.assertNotIn('runtime effects', out['next'])
        self.assertEqual(before, {key: self.read_work(key) for key in (wid, target)})
        from scripts.work_operations import pending
        self.assertFalse(pending(self.repo))

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
        self.assertEqual(self.run_cli('evidence', 'show', '--id', out['evidence']['id'], '--output', '_bmad-output/exported.md')[0], 0)
        self.assertEqual((self.repo / '_bmad-output/exported.md').read_bytes(), b'status: failed\n')
        self.assertNotEqual(self.run_cli('evidence', 'show', '--id', out['evidence']['id'], '--output', '_bmad-output/exported.md')[0], 0)

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

    def test_pending_operation_blocks_unrelated_writes(self):
        wid = self.start()
        from scripts.work_operations import root
        from scripts.state_store import atomic_json
        atomic_json(root(self.repo) / 'pending.json', {'id': 'pending', 'status': 'prepared'})
        before = self.read_work(wid)
        self.assertNotEqual(self.run_cli('checkpoint', '--summary', 'must not commit')[0], 0)
        self.assertEqual(before, self.read_work(wid))
        rc, out = self.run_cli('host-event', '--host', 'pi', payload={'event': 'pre_tool', 'session_id': 'writer', 'toolName': 'write', 'input': {'path': '_bmad-output/task.md'}})
        self.assertEqual(rc, 0, out)
        self.assertEqual(out['decision'], 'deny')

    def test_old_session_cannot_write_new_work(self):
        self.start()
        self.run_cli('host-event', '--host', 'pi', payload={'event': 'session_start', 'session_id': 'old'})
        target = self.queue()
        self.assertEqual(self.run_cli('switch', '--to', target)[0], 0)
        rc, out = self.run_cli('host-event', '--host', 'pi', payload={'event': 'pre_tool', 'session_id': 'old', 'toolName': 'write', 'input': {'path': '_bmad-output/new.md'}})
        self.assertEqual(rc, 0, out)
        self.assertEqual(out['decision'], 'deny')
        self.assertIn('STALE_SESSION_WORK', out['reason'])
        self.assertEqual(self.run_cli('switch', '--to', target, '--session', 'old', '--host', 'pi')[0], 0)
        rc, attached = self.run_cli('host-event', '--host', 'pi', payload={'event': 'pre_tool', 'session_id': 'old', 'toolName': 'write', 'input': {'path': '_bmad-output/new.md'}})
        self.assertEqual(rc, 0, attached)
        self.assertEqual(attached['decision'], 'allow')

    def test_archive_integrity_is_checked(self):
        self.start()
        path = self.repo / '_bmad-output/result.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text('status: passed\n')
        _, out = self.run_cli('evidence', 'attach', '--kind', 'test', '--path', '_bmad-output/result.md')
        (self.repo / out['evidence']['archive']).write_bytes(b'corrupt')
        self.assertNotEqual(self.run_cli('evidence', 'show', '--id', out['evidence']['id'])[0], 0)

    def test_later_failed_attempt_cannot_be_hidden_by_old_pass(self):
        wid = self.start()
        self.assertEqual(self.run_cli('lifecycle', 'migrate', '--work', wid)[0], 0)
        path = self.repo / '_bmad-output/result.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text('status: passed\n')
        _, passed = self.run_cli('evidence', 'attach', '--kind', 'test', '--path', '_bmad-output/result.md')
        failed = self.repo / '_bmad-output/failed.md'
        failed.write_text('status: failed\n')
        self.assertEqual(self.run_cli('evidence', 'attach', '--kind', 'test', '--path', '_bmad-output/failed.md')[0], 0)
        from scripts.evidence_archive import select
        with self.assertRaisesRegex(ValueError, 'LATER_UNRESOLVED'):
            select(self.repo, self.read_work(wid), [passed['evidence']['id']])

    def test_cancelled_execution_never_launches_the_command(self):
        from scripts.verification_runner import run_script
        class Sink:
            def cancel_requested(self):
                return True
        output = self.repo / 'must-not-exist'
        result = run_script('touch ' + str(output), self.repo, log_sink=Sink())
        self.assertEqual(result['exit_code'], 130)
        self.assertEqual(result['process_status'], 'cancelled')
        self.assertFalse(output.exists())

    def test_handoff_acceptance_is_work_and_session_bound(self):
        wid = self.start()
        self.run_cli('resume', '--work', wid, '--host', 'pi', '--session', 'receiver')
        _, handoff = self.run_cli('handoff', '--work', wid)
        key = handoff['handoff']['id']
        rc, out = self.run_cli('handoff', 'accept', '--work', wid, '--id', key, '--host', 'pi', '--session', 'receiver')
        self.assertEqual(rc, 0, out)
        self.assertNotEqual(self.run_cli('handoff', 'accept', '--work', wid, '--id', key, '--host', 'pi', '--session', 'missing')[0], 0)

    def test_switch_recovers_before_and_after_each_journal_and_authority_write(self):
        import shutil, tempfile
        from scripts import state_store, cao_work_store
        from scripts.work_operations import switch, pending, recover
        wid = self.start()
        self.assertEqual(self.run_cli('lifecycle', 'migrate', '--work', wid)[0], 0)
        target = self.queue()
        for module, name, counts in [(state_store, 'atomic_json', range(1, 4)),
                                     (cao_work_store, 'save_json', range(1, 7))]:
            real = getattr(module, name)
            for number in counts:
                for after in (False, True):
                    with self.subTest(writer=name, number=number, after=after), tempfile.TemporaryDirectory() as folder:
                        repo = Path(folder) / 'copy'
                        shutil.copytree(self.repo, repo)
                        calls = [0]
                        def interrupted(*args, **kwargs):
                            calls[0] += 1
                            if calls[0] == number and not after:
                                raise OSError('before durable write')
                            value = real(*args, **kwargs)
                            if calls[0] == number and after:
                                raise OSError('after durable write')
                            return value
                        with patch.object(module, name, side_effect=interrupted):
                            try:
                                switch(repo, target, 'fault injection')
                            except OSError:
                                pass
                        journals = pending(repo)
                        if journals:
                            self.assertTrue(recover(repo, journals[0]['id'])['ok'])
                        else:
                            self.assertTrue(switch(repo, target, 'retry pre-journal failure')['ok'])
                        active = [p for p in (repo / '.orchestrator/work').glob('*.json') if __import__('json').loads(p.read_text())['status'] == 'active']
                        self.assertEqual([p.stem for p in active], [target])

    def test_unknown_run_is_not_replayed_without_explicit_disposition(self):
        wid = self.start()
        self.assertEqual(self.run_cli('lifecycle', 'migrate', '--work', wid)[0], 0)
        from scripts import cao
        from scripts.runtime_store import start_run
        work = cao.load_work(self.repo, wid)
        start_run(self.repo, wid, 'unknown', 'verification', {}, generation=work.get('execution_generation', 0))
        work['verification'] = {'run_id': 'unknown', 'status': 'running'}
        cao.save_work(self.repo, work)
        rc, out = self.run_cli('verify')
        self.assertEqual(rc, 2, out)
        self.assertEqual(out['error'], 'VERIFICATION_RUN_UNRESOLVED')
        inspection = self.repo / 'inspection.md'
        inspection.write_text('Inspected process and actual output; outcome remains unknown, no active writer observed.\n')
        rc, out = self.run_cli('runtime', 'abandon', '--run', 'unknown', '--evidence', str(inspection), '--reason', 'explicit inspected disposition')
        self.assertEqual(rc, 0, out)
        self.assertTrue(out['outcome_unknown'])
        self.assertEqual(self.read_work(wid)['verification']['status'], 'blocked')

    def test_legacy_pending_can_be_resolved_without_returning_in_new_handoff(self):
        wid = self.start()
        self.assertEqual(self.run_cli('checkpoint', '--pending', 'legacy unfinished note')[0], 0)
        note = next(row for row in self.read_work(wid)['resume']['items'] if row['summary'] == 'legacy unfinished note')
        self.assertEqual(self.run_cli('checkpoint', '--resolve', note['id'])[0], 0)
        _, out = self.run_cli('handoff')
        self.assertNotIn('legacy unfinished note', out['handoff']['pending'])
        _, preview = self.run_cli('resume', '--check', '--work', wid)
        self.assertNotIn('legacy unfinished note', preview['recovery']['continuity']['pending']['items'])

    def test_prepared_switch_can_be_aborted_without_changing_active_work(self):
        wid = self.start()
        target = self.queue()
        from scripts.work_operations import switch, pending, abort
        with patch('scripts.work_operations.recover', side_effect=OSError('before apply')):
            with self.assertRaises(OSError):
                switch(self.repo, target, 'prepare then abort')
        key = pending(self.repo)[0]['id']
        self.assertEqual(abort(self.repo, key, check=True)['status'], 'eligible')
        self.assertEqual(abort(self.repo, key)['status'], 'aborted')
        self.assertEqual(self.read_work(wid)['status'], 'active')
        self.assertEqual(self.read_work(target)['status'], 'todo')

    def test_repeated_identical_recovery_conflict_pauses(self):
        wid = self.start()
        target = self.queue()
        from scripts.work_operations import switch, pending, recover
        from scripts import cao
        with patch('scripts.work_operations.recover', side_effect=OSError('before apply')):
            with self.assertRaises(OSError):
                switch(self.repo, target, 'conflict retry')
        key = pending(self.repo)[0]['id']
        # Simulate a separately authorized change; recovery must not overwrite it.
        from scripts.work_operations import authorized
        with authorized():
            work = cao.load_work(self.repo, wid)
            work['decisions'].append({'decision': 'new actual decision'})
            cao.save_work(self.repo, work)
        with self.assertRaisesRegex(ValueError, 'SWITCH_RECOVERY_CONFLICT'):
            recover(self.repo, key)
        with self.assertRaisesRegex(ValueError, 'OPERATION_RECOVERY_PAUSED'):
            recover(self.repo, key)
        with self.assertRaisesRegex(ValueError, 'OPERATION_RECOVERY_PAUSED'):
            recover(self.repo, key)

    def test_pending_transition_still_records_late_result_for_original_work(self):
        wid = self.start()
        self.run_cli('host-event', '--host', 'pi', payload={'event': 'pre_tool', 'session_id': 'producer', 'toolName': 'write', 'toolCallId': 'late', 'input': {'path': '_bmad-output/result.md'}})
        from scripts.work_operations import root
        from scripts.state_store import atomic_json
        atomic_json(root(self.repo) / 'pending.json', {'id': 'pending', 'status': 'prepared'})
        rc, out = self.run_cli('host-event', '--host', 'pi', payload={'event': 'post_tool', 'session_id': 'producer', 'toolName': 'write', 'toolCallId': 'late', 'isError': True})
        self.assertEqual(rc, 0, out)
        from scripts.runtime_queries import events
        self.assertTrue(any(e.get('event') == 'tool.finished' and e.get('tool_call_id') == 'late' for e in events(self.repo, wid)))

    def test_evidence_pagination_reports_total_and_next_cursor(self):
        self.start()
        root = self.repo / '_bmad-output'
        root.mkdir(exist_ok=True)
        for index in range(3):
            path = root / f'plan-{index}.md'
            path.write_text(f'# Plan {index}\n')
            self.assertEqual(self.run_cli('evidence', 'attach', '--kind', 'plan', '--path', str(path))[0], 0)
        _, first = self.run_cli('evidence', 'list', '--limit', '1')
        _, second = self.run_cli('evidence', 'list', '--limit', '1', '--cursor', str(first['next_cursor']))
        self.assertEqual(first['total'], 3)
        self.assertEqual(second['next_cursor'], 2)
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])

    def test_bmad_ordered_story_inventory_has_stable_native_units(self):
        wid = self.start()
        path = self.repo / '_bmad-output/stories.yaml'
        path.parent.mkdir(exist_ok=True)
        path.write_text('- id: "story-a"\n  title: First\n  description: Implement first\n- id: "story-b"\n  title: Second\n  description: Implement second\n')
        work = self.read_work(wid)
        work['sdd']['plan_scope'] = {'plans': [{'path': '_bmad-output/stories.yaml', 'item_ids': ['story-a', 'story-b']}]}
        from scripts.native_units import units
        rows = units(self.repo, work)
        self.assertEqual([row['native_id'] for row in rows], ['story-a', 'story-b'])
        self.assertEqual({row['state'] for row in rows}, {'backlog'})

    def test_native_tool_observation_is_correlated_with_real_producer(self):
        self.start()
        path = self.repo / '_bmad-output/observed.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text('status: passed\n')
        for event in ('pre_tool', 'post_tool'):
            self.assertEqual(self.run_cli('host-event', '--host', 'pi', payload={'event': event, 'session_id': 'native-producer', 'toolName': 'write', 'toolCallId': 'actual-call', 'input': {'path': '_bmad-output/observed.md'}})[0], 0)
        rc, out = self.run_cli('evidence', 'attach', '--kind', 'test', '--path', str(path), '--host', 'pi', '--session', 'native-producer', '--tool-call', 'actual-call')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out['evidence']['source'], 'correlated_host_observation')
        self.assertEqual(out['evidence']['attempt_id'], 'tool:actual-call')
        self.assertNotEqual(self.run_cli('evidence', 'attach', '--kind', 'test', '--path', str(path), '--host', 'pi', '--session', 'native-producer', '--tool-call', 'invented')[0], 0)

    def test_suspended_late_evidence_is_history_without_completion_authority(self):
        wid = self.start()
        target = self.queue()
        self.assertEqual(self.run_cli('switch', '--to', target)[0], 0)
        path = self.repo / '_bmad-output/late.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text('status: passed\n')
        rc, out = self.run_cli('evidence', 'attach', '--work', wid, '--kind', 'test', '--path', str(path), '--historical', '--attempt', 'late-review')
        self.assertEqual(rc, 0, out)
        self.assertTrue(out['evidence']['historical_only'])
        from scripts.evidence_archive import select
        with self.assertRaisesRegex(ValueError, 'HISTORICAL_GENERATION'):
            select(self.repo, self.read_work(wid), [out['evidence']['id']])
