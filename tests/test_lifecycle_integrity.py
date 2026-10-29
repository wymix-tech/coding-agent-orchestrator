"""Completion report adapters, dependency domains and durable recovery boundaries."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_native_lifecycle_contract as native


class Integrity(unittest.TestCase):
    def setUp(self):
        self.n = native.NativeLifecycle('runTest')
        self.n.setUp()
        self.addCleanup(self.n.doCleanups)
        self.repo = self.n.repo

    def legacy(self):
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.repo, self.n.wid)
        work['lifecycle_contract'] = 1
        save_work(self.repo, work)

    def tree(self):
        return {p.relative_to(self.repo).as_posix(): p.read_bytes()
                for p in (self.repo / '.orchestrator').rglob('*') if p.is_file()}

    def test_unchanged_failed_binding_pauses_and_corrected_original_can_resume(self):
        original = self.n.report.read_text()
        self.n.report.write_text('Result: failed\n')
        args = ('lifecycle', 'bind', '--report', 'openspec/completion.md')
        self.assertEqual(2, self.n.cli(*args)[0])
        rc, result = self.n.cli(*args)
        self.assertEqual(2, rc)
        self.assertEqual('NATIVE_COMPLETION_REPAIR_PAUSED', result['error'])
        before = self.n.work()
        self.assertEqual(result, self.n.cli(*args)[1])
        self.assertEqual(before, self.n.work())
        self.n.report.write_text(original)
        self.n.bind()
        self.assertNotIn('completion_binding_failure', self.n.work()['sdd'])
        self.n.check('conformance', 'check')

    def test_preflight_and_check_are_readonly_and_inventory_unfinished_native_work(self):
        self.legacy()
        before = self.tree()
        report = self.n.check('lifecycle', 'preflight')
        self.assertTrue(report['eligible'])
        self.assertIn('native_inventory', report)
        self.assertEqual(before, self.tree())
        self.n.check('lifecycle', 'migrate', '--check')
        self.assertEqual(before, self.tree())

    def test_full_checkpoint_rollback_preserves_original_fields_and_advances_generation(self):
        self.legacy()
        before = self.n.work()
        migration = self.n.check('lifecycle', 'migrate')
        journal = json.loads((self.repo / migration['journal']).read_text())
        self.assertEqual(before, journal['previous_work'])
        current = self.n.work()
        tree = self.tree()
        self.assertTrue(self.n.check('lifecycle', 'rollback', '--check')['eligible'])
        self.assertEqual(tree, self.tree())
        self.assertEqual('rolled_back', self.n.check('lifecycle', 'rollback')['status'])
        restored = self.n.work()
        for key in ('id', 'requirement', 'sdd', 'decisions', 'resume', 'verification', 'status'):
            self.assertEqual(before.get(key), restored.get(key), key)
        self.assertGreater(restored['execution_generation'], current['execution_generation'])
        self.assertEqual('unchanged', self.n.check('lifecycle', 'rollback')['status'])

    def test_rollback_refuses_new_progress_and_product_changes(self):
        self.legacy()
        self.n.check('lifecycle', 'migrate')
        self.n.check('checkpoint', '--step', 'implementation', '--summary', 'New progress after migration')
        (self.repo / 'new_feature.py').write_text('value = 42\n')
        before = self.n.work()
        rc, result = self.n.cli('lifecycle', 'rollback')
        self.assertEqual(2, rc, result)
        self.assertIn('LIFECYCLE_ROLLBACK_PROGRESS_CHANGED', result['errors'])
        self.assertIn('LIFECYCLE_ROLLBACK_SOURCES_CHANGED', result['errors'])
        self.assertEqual(before, self.n.work())

    def test_migration_recovers_after_work_commit_before_journal_seal(self):
        from scripts.lifecycle_migration import command
        self.legacy()
        args = argparse.Namespace(repo=str(self.repo), work=self.n.wid, lifecycle_command='migrate', check=False)
        with patch('scripts.lifecycle_migration.seal', side_effect=OSError('injected seal failure')):
            with self.assertRaises(OSError):
                command(args)
        self.assertEqual(2, self.n.work()['lifecycle_contract'])
        self.assertEqual('unchanged', self.n.check('lifecycle', 'migrate')['status'])
        journals = list((self.repo / '.orchestrator/lifecycle/migrations' / self.n.wid).glob('*.json'))
        self.assertEqual(1, len(journals))
        self.assertEqual('committed', json.loads(journals[0].read_text())['status'])

    def test_migration_retries_the_same_checkpoint_after_precommit_interruption(self):
        from scripts.lifecycle_migration import command
        self.legacy()
        args = argparse.Namespace(repo=str(self.repo), work=self.n.wid, lifecycle_command='migrate', check=False)
        with patch('scripts.cao_work_store.save_work', side_effect=OSError('injected Work write failure')):
            with self.assertRaises(OSError):
                command(args)
        self.assertEqual(1, self.n.work()['lifecycle_contract'])
        result = self.n.check('lifecycle', 'migrate')
        self.assertEqual('migrated', result['status'])
        journals = list((self.repo / '.orchestrator/lifecycle/migrations' / self.n.wid).glob('*.json'))
        self.assertEqual(1, len(journals))

    def test_rollback_retries_after_restoration_before_journal_seal(self):
        import scripts.lifecycle_migration as migration
        self.legacy()
        self.n.check('lifecycle', 'migrate')
        write = migration.atomic_json
        def interrupted(path, value):
            if value.get('status') == 'rolled_back':
                raise OSError('injected rollback journal failure')
            return write(path, value)
        args = argparse.Namespace(repo=str(self.repo), work=self.n.wid, lifecycle_command='rollback', check=False)
        with patch.object(migration, 'atomic_json', side_effect=interrupted):
            with self.assertRaises(OSError):
                migration.command(args)
        self.assertEqual(1, self.n.work()['lifecycle_contract'])
        self.assertEqual('unchanged', self.n.check('lifecycle', 'rollback')['status'])

    def test_corrupt_unsupported_writer_bytes_are_preserved_by_recovery(self):
        target = self.repo / '.orchestrator/work' / (self.n.wid + '.json')
        target.write_bytes(b'broken old writer output')
        rc, result = self.n.cli('status')
        self.assertEqual(2, rc, result)
        self.assertIn('LIFECYCLE_UNSUPPORTED_WRITER', result['error'])
        restored = self.n.check('lifecycle', 'recover', '--work', self.n.wid)
        self.assertEqual(b'broken old writer output', (self.repo / restored['quarantine']).read_bytes())
        self.assertEqual(2, self.n.work()['lifecycle_contract'])

    def test_unsupported_old_writer_is_rejected_and_quarantined_without_losing_authority(self):
        before = self.n.work()
        target = self.repo / '.orchestrator/work' / (self.n.wid + '.json')
        unsupported = deepcopy(before)
        unsupported['lifecycle_contract'] = 1
        unsupported['status'] = 'completed'
        target.write_text(json.dumps(unsupported))
        rc, out = self.n.cli('status')
        self.assertEqual(2, rc, out)
        self.assertIn('LIFECYCLE_UNSUPPORTED_WRITER', out['error'])
        tree = self.tree()
        self.n.check('lifecycle', 'recover', '--work', self.n.wid, '--check')
        self.assertEqual(tree, self.tree())
        result = self.n.check('lifecycle', 'recover', '--work', self.n.wid)
        self.assertEqual(unsupported, json.loads((self.repo / result['quarantine']).read_text()))
        restored = self.n.work()
        self.assertEqual(before['status'], restored['status'])
        self.assertEqual(before['sdd'], restored['sdd'])
        self.assertEqual(2, restored['lifecycle_contract'])

    def test_report_only_change_preserves_project_baseline_and_explains_domains(self):
        from scripts.cao_completion import verification_inputs
        from scripts.completion_policy import verification_fresh
        from scripts.cao_runtime import config
        from scripts.cao_work_store import load_work
        self.n.bind()
        self.n.check('verify')
        self.n.report.write_text(self.n.report.read_text() + '\nNew report annotation.\n')
        _, result = self.n.cli('conformance', 'check')
        evidence = result['native_completion_evidence']
        self.assertIn('reports', evidence['changed_domains'])
        self.assertIn('openspec/completion.md', evidence['changed_paths'])
        self.assertIn('project_baseline', evidence['still_valid_evidence'])
        work = load_work(self.repo, self.n.wid)
        self.assertTrue(verification_fresh(work, verification_inputs(self.repo, work, config(self.repo))))

    def test_native_wrapper_and_sourced_helper_changes_invalidate_native_evidence(self):
        root = '.orchestrator/scripts/verification/root/'
        self.n.check('verification', 'prepare', '--path', root + 'native.sh', '--path', root + 'helper.sh')
        (self.repo / root / 'native.sh').write_text('. ./helper.sh\n')
        (self.repo / root / 'helper.sh').write_text('exit 0\n')
        self.n.check('verification', 'check', '--project')
        self.n.check('reopen', '--reanchor', '--reason', 'Review registered fixture baseline inputs')
        self.n.check('lifecycle', 'dependencies', '--path', root + 'native.sh')
        self.n.bind()
        (self.repo / root / 'helper.sh').write_text('exit 1\n')
        _, out = self.n.cli('conformance', 'check')
        result = out['native_completion_evidence']
        self.assertFalse(result['ready'])
        self.assertIn('native_dependencies', result['changed_domains'])
        self.assertIn(root + 'helper.sh', result['changed_paths'])
        self.assertIn('native_completion', result['affected_evidence'])

    def test_product_change_lists_exact_path_and_all_affected_evidence(self):
        self.n.bind()
        (self.repo / 'product.py').write_text('changed = True\n')
        _, result = self.n.cli('conformance', 'check')
        evidence = result['native_completion_evidence']
        self.assertIn('product.py', evidence['changed_paths'])
        self.assertEqual({'native_completion', 'cao_conformance', 'project_baseline'}, set(evidence['affected_evidence']))

    def test_real_cli_openspec_and_speckit_complete_all_native_tasks_before_cao(self):
        import subprocess
        import sys
        from tests.test_task_provider_verify import TaskProviderVerifyTests
        for provider in ('openspec', 'speckit'):
            fixture = TaskProviderVerifyTests('runTest')
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.prepare(provider)
            (fixture.repo / ('.specify' if provider == 'speckit' else 'openspec')).mkdir(exist_ok=True)
            rc, assessed = fixture.run_cli('path', 'assess', '--profile', 'light', '--reason', 'Provider-specific native lifecycle fixture')
            self.assertEqual(0, rc, assessed)
            report = fixture.root / 'native-result.log'
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'acceptance-tests'],
                                    cwd=fixture.repo, capture_output=True, text=True, check=True)
            report.write_text(result.stdout + result.stderr)
            rc, out = fixture.run_cli('lifecycle', 'migrate')
            self.assertEqual(0, rc, out)
            rc, out = fixture.run_cli('lifecycle', 'bind', '--report', report.relative_to(fixture.repo))
            self.assertEqual(2, rc, out)
            fixture.tasks.write_text(fixture.tasks.read_text().replace('[ ]', '[x]'))
            from copy import deepcopy
            from scripts.lifecycle_contract import inputs
            candidate = deepcopy(fixture.read_work(fixture.wid))
            candidate['sdd']['completion_evidence'] = {'reports': [{'path': report.relative_to(fixture.repo).as_posix()}]}
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'acceptance-tests'], cwd=fixture.repo, capture_output=True, text=True, check=True)
            report.write_text(f"work_id: {fixture.wid}\ncode_snapshot: {inputs(fixture.repo, candidate)['code_snapshot']}\n" + result.stdout + result.stderr)
            for args in [('lifecycle', 'bind', '--report', report.relative_to(fixture.repo)),
                         ('conformance', 'check'), ('verify',), ('done',)]:
                rc, out = fixture.run_cli(*args)
                self.assertEqual(0, rc, (provider, args, out))
            self.assertEqual('completed', fixture.read_work(fixture.wid)['status'])

    def test_native_review_dependencies_are_distinct_from_baseline_transport(self):
        from tests.test_native_route_sync import RouteSync
        from scripts.native_obligations import review_inputs
        from scripts.cao_snapshot import code_snapshot
        fixture = RouteSync('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.propose()
        fixture.approve()
        fixture.run_cli('lifecycle', 'migrate')
        root = '.orchestrator/scripts/verification/root/'
        fixture.run_cli('verification', 'prepare', '--path', root + 'review.sh')
        fixture.write(root + 'review.sh', 'exit 0\n')
        fixture.run_cli('lifecycle', 'dependencies', '--review', '--path', root + 'review.sh')
        before = review_inputs(fixture.repo, fixture.work(), code_snapshot)
        fixture.write(root + 'review.sh', 'exit 1\n')
        after = review_inputs(fixture.repo, fixture.work(), code_snapshot)
        self.assertEqual(before['code_snapshot'], after['code_snapshot'])
        self.assertNotEqual(before['native_review_dependencies_sha256'], after['native_review_dependencies_sha256'])


class ReportAdapters(unittest.TestCase):
    def test_all_providers_accept_known_native_results_without_json_conversion(self):
        from scripts.native_reports import read
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            for provider in ('bmad', 'openspec', 'speckit'):
                work = {'id': 'work', 'sdd': {'provider': provider}, 'requirement': {'sha256': 'request'}}
                for suffix, raw in [('.md', '**Result:** passed\n'), ('.yaml', 'status: completed\n'),
                                    ('.json', json.dumps({'provider': provider, 'work_id': 'work', 'status': 'passed'})),
                                    ('.log', 'Ran 3 tests in 0.1s\n\nOK\n')]:
                    path = repo / ('report' + suffix)
                    path.write_text(raw)
                    result = read(repo, work, path)
                    self.assertEqual('passed', result['outcome'])
                    self.assertTrue(result['adapter'].startswith(provider + ':'))
                    self.assertEqual(raw, path.read_text())

    def test_failed_unknown_and_wrong_scope_results_are_not_machine_passes(self):
        from scripts.native_reports import read
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            work = {'id': 'work', 'sdd': {'provider': 'openspec'}, 'requirement': {'sha256': 'request'}}
            for suffix, raw in [('.md', 'All good, probably done.'), ('.yaml', 'status: failed\n'),
                                ('.md', '```text\nRan 1 test in 0.1s\nOK\n```'),
                                ('.json', '{"status":"passed","items":[{"status":"failed"}]}'),
                                ('.json', '{"work_id":"another","status":"passed"}'),
                                ('.json', '{"status":"passed","tests":{"count":0}}')]:
                path = repo / ('result' + suffix)
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    read(repo, work, path)

    def test_prepared_authority_guard_recovers_both_sides_of_an_interrupted_write(self):
        from scripts.lifecycle_guard import persist, verify
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            old = {'id': 'work', 'lifecycle_contract': 2, 'state_revision': 1}
            new = {**old, 'state_revision': 2}
            with self.assertRaises(OSError):
                persist(repo, 'work', old, new, lambda: (_ for _ in ()).throw(OSError('interrupted')))
            self.assertEqual(old, verify(repo, 'work', old))
            with self.assertRaises(OSError):
                persist(repo, 'work', old, new, lambda: (_ for _ in ()).throw(OSError('written then interrupted')))
            self.assertEqual(new, verify(repo, 'work', new))
