"""Cross-module recovery regressions, exercised against real CLI fixtures."""
import json
import sys
import unittest
from tests import test_native_lifecycle_contract as lifecycle_fixture


class RecoveryClosure(unittest.TestCase):
    setUp = lifecycle_fixture.NativeLifecycle.setUp
    check = lifecycle_fixture.NativeLifecycle.check
    cli = lifecycle_fixture.NativeLifecycle.cli
    work = lifecycle_fixture.NativeLifecycle.work
    bind = lifecycle_fixture.NativeLifecycle.bind

    def execute(self):
        return self.check('lifecycle', 'execute', '--command', sys.executable + ' preparation_suite.py', '--report', 'openspec/completion.md')

    def test_native_success_is_resolved_and_resumable(self):
        from scripts.runtime_queries import inspect_run
        from scripts.recovery_plan import plan
        queued = self.repo / '.orchestrator/requirements/queued.md'
        queued.parent.mkdir(parents=True, exist_ok=True)
        queued.write_text('# Queued independent requirement\nProvide an independent follow-up change.\n')
        target = self.check('start', '--defer', '--requirement', str(queued))['work_item']['id']
        self.bind()
        rid = self.execute()['execution']['run_id']
        self.bind()
        state = inspect_run(self.repo, self.work(), rid)
        self.assertEqual('passed', state['admission_status'])
        self.assertTrue(state['current_run'])
        self.assertFalse(plan(self.repo, self.work())['unresolved_runs'])
        import subprocess
        from tests.test_project_context_acceptance import CLI
        resumed = subprocess.run([sys.executable, str(CLI), 'resume', '--repo', str(self.repo)], env=self.fixture.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.check('switch', '--to', target)
        self.check('switch', '--to', self.wid)
        self.check('verify')
        self.check('done')
        self.assertEqual('completed', self.work()['status'])

    def test_corrected_gate_request_can_bind(self):
        for _ in range(2):
            self.assertEqual(2, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md', '--gate', 'made_up')[0])
        self.bind()

    def test_wrapper_execution_keeps_dependencies_without_command_in_log(self):
        from scripts.cao_runtime import config
        command = config(self.repo)['verification']['scripts'][0]['command']
        self.report.write_text('Command: ' + command + '\nstatus: passed\n')
        self.bind()
        self.check('lifecycle', 'execute', '--command', command, '--report', 'openspec/completion.md')
        self.bind()
        self.check('conformance', 'check')

    def test_identical_binding_finishes_product_repair_once(self):
        from scripts.cao_work_store import load_work, save_work
        (self.repo / 'preparation_suite.py').write_text("import unittest,sys\nclass T(unittest.TestCase):\n def test_real(self): self.assertEqual(2+2,4)\nr=unittest.TestResult()\nunittest.defaultTestLoader.loadTestsFromTestCase(T).run(r)\nprint('Ran 1 test in native execution\\nOK' if r.wasSuccessful() else 'FAILED')\nsys.exit(not r.wasSuccessful())\n")
        self.check('verification', 'check', '--project')
        self.check('reopen', '--reanchor', '--reason', 'deterministic runner')
        self.bind()
        self.execute()
        self.bind()
        work = load_work(self.repo, self.wid)
        work['verification'] = {'status': 'failed', 'run_id': 'failed-baseline'}
        save_work(self.repo, work)
        self.check('lifecycle', 'repair', '--kind', 'product', '--reason', 'product diagnosis')
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')[0])
        self.assertIn('completion_repair', self.work()['sdd'])
        self.execute()
        self.bind()
        self.assertNotIn('completion_repair', self.work()['sdd'])
        before = self.work()['sdd'].get('completion_repair_history')
        self.bind()
        self.assertEqual(before, self.work()['sdd'].get('completion_repair_history'))

    def test_failed_project_product_check_allows_repair(self):
        (self.repo / 'feature.py').write_text('valid = True\n')
        (self.repo / 'preparation_suite.py').write_text('import unittest,feature\nclass T(unittest.TestCase):\n def test_behavior(self):self.assertTrue(feature.valid)\nunittest.main()\n')
        self.check('verification', 'check', '--project')
        self.check('reopen', '--reanchor', '--reason', 'reviewed product test suite')
        self.bind()
        raw = {'event': 'pre_tool', 'session_id': 'writer', 'toolName': 'write', 'input': {'path': 'feature.py', 'content': 'valid = True\n'}}
        self.assertEqual('allow', self.fixture.cli('host-event', '--host', 'pi', payload=raw)[1]['decision'])
        (self.repo / 'feature.py').write_text('valid = False\n')
        self.assertEqual(1, self.cli('verification', 'check', '--project')[0])
        self.assertEqual('allow', self.fixture.cli('host-event', '--host', 'pi', payload=raw)[1]['decision'])
        self.assertNotEqual(0, self.cli('done')[0])
        (self.repo / 'feature.py').write_text('valid = True\n')
        self.execute()
        self.bind()
        # Refresh the failed project check against the repaired product before
        # switching to a Work that cannot inherit this Work's repair permission.
        self.check('verification', 'check', '--project')
        queued = self.repo / '.orchestrator/requirements/after-repair.md'
        queued.parent.mkdir(parents=True, exist_ok=True)
        queued.write_text('# Follow-up\nImplement an independent follow-up operation.\n')
        target = self.check('start', '--defer', '--requirement', str(queued))['work_item']['id']
        import subprocess
        from tests.test_project_context_acceptance import CLI
        resumed = subprocess.run([sys.executable, str(CLI), 'resume', '--repo', str(self.repo)], env=self.fixture.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.check('switch', '--to', target)
        self.check('switch', '--to', self.wid)
        self.check('verify')
        self.check('done')
        self.assertEqual('completed', self.work()['status'])

    def test_transition_success_is_not_replayed_after_product_change(self):
        from scripts.provider_execution import transition
        path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(path.read_text())
        cfg['sdd']['command']['transition'] = sys.executable + ' transition.py'
        path.write_text(json.dumps(cfg))
        (self.repo / 'transition.py').write_text("from pathlib import Path\np=Path('transition-count')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n")
        first = transition(self.repo, 'done', self.wid)
        self.assertEqual(0, first['exit_code'])
        second = transition(self.repo, 'done', self.wid)
        self.assertEqual(first, second)
        self.assertEqual('x', (self.repo / 'transition-count').read_text())

    def test_external_command_timeout_has_receipt_and_bounded_output(self):
        from scripts.provider_execution import run
        result = run(self.repo, [sys.executable, '-c', 'import time; print("started", flush=True); time.sleep(10)'], kind='provider_inspect', timeout=0.1)
        self.assertEqual(124, result['exit_code'])
        self.assertEqual('timed_out', result['process_status'])

    def test_unknown_transition_is_not_automatically_replayed(self):
        from scripts.provider_execution import transition
        path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(path.read_text())
        cfg['sdd']['command']['transition'] = sys.executable + ' transition.py'
        path.write_text(json.dumps(cfg))
        (self.repo / 'transition.py').write_text('raise SystemExit(1)\n')
        self.assertEqual(1, transition(self.repo, 'done', self.wid)['exit_code'])
        cfg['sdd']['command']['transition'] = ''
        path.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError, 'PROVIDER_TRANSITION_UNRESOLVED'):
            transition(self.repo, 'done', self.wid)

    def test_missing_projection_does_not_make_native_success_unresolved(self):
        from scripts.runtime_store import run_path
        from scripts.runtime_queries import inspect_run
        self.bind()
        rid = self.execute()['execution']['run_id']
        run_path(self.repo, self.wid, rid, 'result.json').unlink(missing_ok=True)
        state = inspect_run(self.repo, self.work(), rid)
        self.assertEqual('projection_missing', state['recovery_status'])
        self.assertEqual('passed', state['admission_status'])

    def test_duplicate_report_argument_cannot_bypass_pause(self):
        self.report.write_text('status: failed\n')
        for _ in range(2):
            self.assertEqual(2, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')[0])
        code, out = self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md', '--report', 'openspec/completion.md')
        self.assertEqual(2, code)
        self.assertEqual('NATIVE_COMPLETION_REPAIR_PAUSED', out['error'])
        self.assertEqual(2, self.work()['sdd']['completion_binding_failure']['attempts'])

    def test_evidence_selection_pause_does_not_append_repeated_failures(self):
        from scripts.runtime_store import work_events
        self.report.write_text('status: failed\n')
        identity = self.check('evidence', 'attach', '--kind', 'test', '--path', 'openspec/completion.md')['evidence']['id']
        for _ in range(2):
            self.assertEqual(2, self.cli('lifecycle', 'bind', '--evidence', identity)[0])
        before = list(work_events(self.repo, self.wid))
        code, result = self.cli('lifecycle', 'bind', '--evidence', identity, '--evidence', identity)
        self.assertEqual(2, code)
        self.assertEqual('NATIVE_COMPLETION_REPAIR_PAUSED', result['error'])
        self.assertEqual(before, list(work_events(self.repo, self.wid)))
        self.assertIn('cao lifecycle bind', result['next'])
        self.assertIn(identity, result['retry_command'])

    def test_unknown_project_failure_does_not_inherit_write_permission(self):
        from scripts.verification_probe import project_state
        (self.repo / 'preparation_suite.py').write_text('raise RuntimeError("environment unavailable")\n')
        self.assertNotEqual(0, self.cli('verification', 'check', '--project')[0])
        self.assertFalse(project_state(self.repo, work=self.work())['ready'])

    def test_native_abandon_requires_inspection_and_retains_history(self):
        from scripts.cao_work_store import save_work
        from scripts.runtime_store import start_run
        work = self.work()
        rid = 'native-interrupted'
        work['sdd']['completion_executions'] = [{'run_id': rid, 'status': 'running', 'generation': work.get('execution_generation', 0)}]
        start_run(self.repo, self.wid, rid, 'native_completion', {}, generation=work.get('execution_generation', 0))
        save_work(self.repo, work)
        self.assertNotEqual(0, self.cli('runtime', 'abandon', '--run', rid, '--evidence', 'missing.md', '--reason', 'inspected')[0])
        (self.repo / 'inspection.md').write_text('No process remains; inspected product effects.\n')
        self.check('runtime', 'abandon', '--run', rid, '--evidence', 'inspection.md', '--reason', 'inspected effects')
        row = self.work()['sdd']['completion_executions'][-1]
        self.assertEqual('abandoned', row['status'])
        self.assertTrue(row['outcome_unknown'])
        self.assertIn('resolution', row)

    def test_unknown_transition_blocks_recovery_until_inspected(self):
        from scripts.provider_execution import transition
        from scripts.recovery_plan import plan
        path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(path.read_text())
        cfg['sdd']['command']['transition'] = sys.executable + ' transition.py'
        path.write_text(json.dumps(cfg))
        (self.repo / 'transition.py').write_text('raise SystemExit(1)\n')
        transition(self.repo, 'done', self.wid)
        self.assertEqual('provider_transition', plan(self.repo, self.work())['unresolved_runs'][-1]['kind'])
        operation = self.work()['sdd']['transition_runs'][-1]['id']
        (self.repo / 'inspection.md').write_text('Provider did not apply the transition.\n')
        self.check('sdd', 'transition', 'resolve', '--work', self.wid, '--operation', operation, '--outcome', 'not-applied', '--evidence', 'inspection.md', '--reason', 'inspected provider state')
        self.assertFalse(plan(self.repo, self.work())['unresolved_runs'])

    def test_project_owned_provider_command_can_be_cancelled(self):
        import time
        from concurrent.futures import ThreadPoolExecutor
        from scripts.provider_execution import run
        from scripts.runtime_store import run_records
        from scripts.runtime_execution import owner_status
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run, self.repo, [sys.executable, '-c', 'import time;time.sleep(10)'], kind='provider_inspect', timeout=3)
            deadline = time.monotonic() + 2
            rid = None
            while time.monotonic() < deadline:
                rid = next((p.name for p in run_records(self.repo, '_unassigned') if owner_status(self.repo, '_unassigned', p.name) == 'running'), None)
                if rid:
                    break
                time.sleep(.01)
            self.assertIsNotNone(rid)
            self.check('runtime', 'cancel', '--work', '_unassigned', '--run', rid, '--reason', 'operator cancellation')
            result = future.result(timeout=5)
        self.assertEqual(130, result['exit_code'])
        self.assertEqual('cancelled', result['process_status'])
        self.assertEqual('complete', self.check('runtime', 'show', '--work', '_unassigned', '--run', rid)['recovery_status'])

    def test_reused_evaluation_matches_independent_decisions(self):
        from scripts.lifecycle_contract import completion, conformance, next_action
        self.bind()
        for changed in (False, True):
            if changed:
                (self.repo / 'feature.py').write_text('valid = False\n')
            combined = completion(self.repo, self.work())
            self.assertEqual(conformance(self.repo, self.work()), combined['conformance'])
            self.assertEqual(next_action(self.repo, self.work()), combined['next_action'])

    def test_context_evaluation_is_not_cached_across_calls(self):
        from scripts.cao_guidance import render_context, current_work_action
        self.bind()
        before = render_context(self.repo, self.work())
        self.assertEqual('PROJECT_BASELINE_REQUIRED', current_work_action(self.repo, self.work())['reason'])
        (self.repo / 'feature.py').write_text('valid = False\n')
        after = render_context(self.repo, self.work())
        self.assertNotEqual(before, after)
        self.assertIn('NATIVE_COMPLETION_EVIDENCE_STALE', after)
        self.assertEqual('NATIVE_COMPLETION_EVIDENCE_STALE', current_work_action(self.repo, self.work())['reason'])

    def test_transition_commit_interruption_requires_inspection_without_replay(self):
        from unittest.mock import patch
        from scripts.provider_execution import transition
        from scripts.cao_work_store import save_work
        path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(path.read_text())
        cfg['sdd']['command']['transition'] = sys.executable + ' transition.py'
        path.write_text(json.dumps(cfg))
        (self.repo / 'transition.py').write_text("from pathlib import Path\np=Path('transition-count')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n")
        calls = []
        def interrupt(repo, work):
            calls.append(True)
            if len(calls) == 2:
                raise OSError('injected commit interruption')
            return save_work(repo, work)
        with patch('scripts.cao_work_store.save_work', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'injected commit'):
                transition(self.repo, 'done', self.wid)
        with self.assertRaisesRegex(ValueError, 'PROVIDER_TRANSITION_UNRESOLVED'):
            transition(self.repo, 'done', self.wid)
        operation = self.work()['sdd']['transition_runs'][-1]['id']
        (self.repo / 'inspection.md').write_text('Provider applied the transition; observed transition-count is x.\n')
        self.check('sdd', 'transition', 'resolve', '--work', self.wid, '--operation', operation, '--outcome', 'applied', '--evidence', 'inspection.md', '--reason', 'observed external result')
        self.assertEqual(0, transition(self.repo, 'done', self.wid)['exit_code'])
        self.assertEqual('x', (self.repo / 'transition-count').read_text())

    def test_opaque_project_failure_requires_explicit_product_diagnosis(self):
        from scripts.verification_probe import project_state
        (self.repo / 'feature.py').write_text('valid = True\n')
        (self.repo / 'preparation_suite.py').write_text('import feature\nprint("Ran 1 test")\nraise SystemExit(0 if feature.valid else 1)\n')
        self.check('verification', 'check', '--project')
        self.check('reopen', '--reanchor', '--reason', 'accepted opaque existing runner')
        (self.repo / 'feature.py').write_text('valid = False\n')
        self.assertEqual(1, self.cli('verification', 'check', '--project')[0])
        self.assertFalse(project_state(self.repo, work=self.work())['ready'])
        self.check('lifecycle', 'repair', '--kind', 'unknown', '--reason', 'investigating')
        self.assertFalse(project_state(self.repo, work=self.work())['ready'])
        self.check('lifecycle', 'repair', '--kind', 'product', '--reason', 'inspected assertion: feature.valid must be true')
        self.assertTrue(project_state(self.repo, work=self.work())['ready'])
        self.assertNotEqual(0, self.cli('done')[0])

    def prepare_product_repair(self):
        from scripts.cao_work_store import save_work
        self.bind()
        self.execute()
        self.bind()
        work = self.work()
        work['verification'] = {'status': 'failed', 'run_id': 'review-failure'}
        save_work(self.repo, work)
        self.check('lifecycle', 'repair', '--kind', 'product', '--reason', 'reviewed defect')

    def test_legacy_repair_requires_new_execution_without_timestamp_guessing(self):
        from scripts.cao_work_store import save_work
        self.prepare_product_repair()
        work = self.work()
        work['sdd']['completion_repair'].pop('execution_boundary')
        save_work(self.repo, work)
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')[0])
        self.execute()
        self.bind()
        self.assertNotIn('completion_repair', self.work()['sdd'])

    def test_new_execution_in_old_generation_cannot_resolve_repair(self):
        from scripts.cao_work_store import save_work
        self.prepare_product_repair()
        self.execute()
        work = self.work()
        work['execution_generation'] = work.get('execution_generation', 0) + 1
        save_work(self.repo, work)
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')[0])
        self.assertIn('completion_repair', self.work()['sdd'])

    def test_multiple_reports_require_post_repair_receipt_for_each(self):
        self.prepare_product_repair()
        self.check('lifecycle', 'execute', '--command', sys.executable + ' preparation_suite.py', '--report', 'openspec/second.log')
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md', '--report', 'openspec/second.log')[0])
        self.execute()
        self.check('lifecycle', 'bind', '--report', 'openspec/completion.md', '--report', 'openspec/second.log')
        self.assertNotIn('completion_repair', self.work()['sdd'])

    def test_binding_commit_failure_keeps_repair_recoverable(self):
        import contextlib
        import io
        from unittest.mock import patch
        from scripts.cao_cli import build_parser
        from scripts.lifecycle_contract import command
        from scripts.cao_work_store import save_work
        self.prepare_product_repair()
        self.execute()
        args = build_parser().parse_args(['lifecycle', 'bind', '--repo', str(self.repo), '--report', 'openspec/completion.md'])
        calls = []
        def fail_once(repo, work):
            calls.append(True)
            if len(calls) == 1:
                raise OSError('injected bind commit interruption')
            return save_work(repo, work)
        with patch('scripts.cao_work_store.save_work', side_effect=fail_once), contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(0, command(args))
        self.assertIn('completion_repair', self.work()['sdd'])
        self.bind()
        self.assertNotIn('completion_repair', self.work()['sdd'])

    def test_native_commit_rejects_inputs_changed_during_execution(self):
        import contextlib
        import io
        from unittest.mock import patch
        from scripts.cao_cli import build_parser
        from scripts.native_execution import command
        from scripts.runtime_execution import execute
        self.bind()
        def changed(*args, **kwargs):
            result = execute(*args, **kwargs)
            (self.repo / 'feature.py').write_text('valid = False\n')
            return result
        args = build_parser().parse_args(['lifecycle', 'execute', '--repo', str(self.repo), '--command', sys.executable + ' preparation_suite.py', '--report', 'openspec/completion.md'])
        with patch('scripts.runtime_execution.execute', side_effect=changed), contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(0, command(args))
        self.assertEqual('stale', self.work()['sdd']['completion_executions'][-1]['status'])
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')[0])

    def test_static_shell_helper_change_invalidates_native_receipt(self):
        from scripts.cao_runtime import config
        folder = self.repo / '.orchestrator/scripts/verification'
        folder.mkdir(parents=True, exist_ok=True)
        helper = folder / 'native-helper.sh'
        helper.write_text('# declared static helper\n')
        command = 'bash .orchestrator/scripts/verification/native-wrapper.sh'
        (folder / 'native-wrapper.sh').write_text('source .orchestrator/scripts/verification/native-helper.sh\n' + sys.executable + ' preparation_suite.py\n')
        self.check('verification', 'check', '--project')
        self.check('reopen', '--reanchor', '--reason', 'accepted static helper')
        self.report.write_text('Command: ' + command + '\nstatus: passed\n')
        self.bind()
        self.check('lifecycle', 'execute', '--command', command, '--report', 'openspec/completion.md')
        self.bind()
        helper.write_text('# helper changed after execution\n')
        from scripts.native_execution import receipt_matches
        work = self.work()
        self.assertFalse(receipt_matches(self.repo, work, work['sdd']['completion_evidence']['reports']))
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')[0])
