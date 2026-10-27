"""Real CLI native evidence lifecycle: no duplicate functional executor."""
import json
import subprocess
import sys
import unittest

from tests import test_project_context_acceptance as fixtures


class NativeLifecycle(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ProjectAcceptanceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.fixture.background()
        # Explicitly use the production default, not the legacy fixture policy.
        cfg_path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(cfg_path.read_text())
        cfg.pop('lifecycle_contract', None)
        cfg_path.write_text(json.dumps(cfg))
        self.wid = self.fixture.start()
        self.fixture.check('path', 'assess', '--profile', 'light', '--reason', 'native bounded change')
        (self.repo / 'openspec/spec.md').write_text('# SPEC\n## Acceptance\n- Correct behavior is observed.\n')
        self.fixture.check('sdd', 'spec', 'bind', '--spec', 'openspec/spec.md', '--criteria-section', 'Acceptance')
        self.report = self.repo / 'openspec/completion.md'
        run = subprocess.run([sys.executable, 'preparation_suite.py'], cwd=self.repo,
                             capture_output=True, text=True, check=True)
        self.report.write_text('# Native fixture completion\n\nCommand: python preparation_suite.py\n'
                               'Exit: 0\n\n' + run.stdout + run.stderr)

    def check(self, *args):
        return self.fixture.check(*args)

    def cli(self, *args):
        return self.fixture.cli(*args)

    def work(self):
        return self.fixture.work(self.wid)

    def bind(self):
        return self.check('lifecycle', 'bind', '--report', 'openspec/completion.md')

    def test_new_work_defaults_to_native_contract_and_no_executor(self):
        self.assertEqual(2, self.work()['lifecycle_contract'])
        self.assertNotIn('acceptance', self.work()['sdd'])
        rc, out = self.cli('verify')
        self.assertEqual(2, rc, out)
        self.assertEqual('CONFORMANCE_NOT_READY', out['error'])
        self.assertNotIn('verification', self.work().get('execution', {}).get('phase', ''))
        self.bind()
        self.check('conformance', 'check')
        self.check('verify')
        self.check('done')
        self.assertEqual('completed', self.work()['status'])
        self.assertNotIn('acceptance', self.work()['sdd'])
        self.assertFalse((self.repo / 'scripts/verification/acceptance-checks.json').exists())

    def test_report_tampering_blocks_conformance_and_formal_verify(self):
        self.bind()
        self.report.write_text('# Changed native result\n')
        rc, out = self.cli('conformance', 'check')
        self.assertEqual(2, rc, out)
        self.assertFalse(out['ready'])
        self.assertEqual(2, self.cli('verify')[0])

    def test_product_change_invalidates_completion_evidence(self):
        self.bind()
        (self.repo / 'feature.py').write_text('raise RuntimeError("changed")\n')
        rc, out = self.cli('conformance', 'check')
        self.assertEqual(2, rc, out)
        evidence = out['native_completion_evidence']
        self.assertEqual('stale', evidence['state'])
        self.assertIn('code_snapshot', evidence['changed_inputs'])

    def test_binding_is_idempotent_and_reports_remain_native(self):
        original = self.report.read_bytes()
        self.bind()
        self.assertEqual('unchanged', self.bind()['status'])
        self.assertEqual(original, self.report.read_bytes())
        self.assertNotIn('completion_evidence_history', self.work()['sdd'])

    def test_missing_empty_and_outside_provider_reports_are_rejected(self):
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'openspec/missing.md')[0])
        self.report.write_text('')
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')[0])
        (self.repo / 'outside.md').write_text('native result')
        self.assertNotEqual(0, self.cli('lifecycle', 'bind', '--report', 'outside.md')[0])
        self.assertNotIn('completion_evidence', self.work()['sdd'])

    def test_migration_keeps_identity_old_failures_and_is_idempotent(self):
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.repo, self.wid)
        work.pop('lifecycle_contract')
        old = {'run': {'status': 'failed', 'reason': 'original JSON error'}}
        work['sdd']['acceptance'] = old
        save_work(self.repo, work)
        out = self.check('lifecycle', 'migrate')
        self.assertEqual('migrated', out['status'])
        self.assertEqual(old, self.work()['sdd']['acceptance'])
        self.assertEqual(self.wid, self.work()['id'])
        self.assertEqual('unchanged', self.check('lifecycle', 'migrate')['status'])
        self.assertEqual(1, len(self.work()['lifecycle_migrations']))

    def test_historical_result_is_immutable_and_cannot_be_migrated(self):
        self.bind()
        self.check('verify')
        self.check('done')
        before = self.work()
        (self.repo / 'later.py').write_text('changed = True\n')
        result = self.check('acceptance', 'status', '--work', self.wid)
        self.assertTrue(result['historical'])
        self.assertNotEqual(0, self.cli('lifecycle', 'migrate', '--work', self.wid)[0])
        self.assertEqual(before, self.work())

    def test_native_review_preparation_needs_no_cao_functional_executor(self):
        from scripts.review_dispatch import acceptance_preflight
        self.assertTrue(acceptance_preflight(self.repo, self.work())['ready'])
        self.assertNotIn('acceptance', self.work()['sdd'])

    def test_split_native_tasks_cannot_be_waived_by_one_complete_unit(self):
        first = self.repo / 'openspec/changes/first/tasks.md'
        second = self.repo / 'openspec/changes/second/tasks.md'
        for path, checked in ((first, 'x'), (second, ' ')):
            path.parent.mkdir(parents=True)
            path.write_text(f'# Tasks\n- [{checked}] 1.1 Native fixture task\n')
        self.check('sdd', 'plan', 'bind', '--plan', 'openspec/changes/first/tasks.md',
                   '--plan', 'openspec/changes/second/tasks.md')
        rc, result = self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')
        self.assertEqual(2, rc, result)
        self.assertEqual('NATIVE_COMPLETION_REQUIRED', result['error'])
        self.assertNotIn('completion_evidence', self.work()['sdd'])
        second.write_text('# Tasks\n- [x] 1.1 Native fixture task\n')
        self.bind()
        self.check('conformance', 'check')
        second.write_text('# Tasks\n- [ ] 1.1 Native fixture task\n')
        self.assertEqual(2, self.cli('verify')[0])

    def test_running_legacy_execution_cannot_race_migration(self):
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.repo, self.wid)
        work.pop('lifecycle_contract')
        work['verification'] = {'status': 'running', 'run_id': 'old-run'}
        save_work(self.repo, work)
        before = self.work()
        rc, result = self.cli('lifecycle', 'migrate')
        self.assertEqual(2, rc, result)
        self.assertIn('LIFECYCLE_RUN_ACTIVE', result['error'])
        self.assertEqual(before, self.work())

    def test_work_risk_checks_do_not_reconfigure_project_baseline(self):
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.repo, self.wid)
        work['path_assessment']['required_gates'].append('security_verification')
        save_work(self.repo, work)
        self.bind()
        rc, out = self.cli('conformance', 'check')
        self.assertEqual(2, rc, out)
        self.assertEqual(['security_verification'], out['native_completion_evidence']['pending_gates'])
        config_before = (self.repo / '.orchestrator/config.json').read_bytes()
        self.check('lifecycle', 'bind', '--report', 'openspec/completion.md', '--gate', 'security_verification')
        self.check('conformance', 'check')
        self.assertEqual(config_before, (self.repo / '.orchestrator/config.json').read_bytes())

    def test_project_baseline_transport_change_preserves_native_completion(self):
        from scripts.cao_work_store import load_work
        from scripts.lifecycle_contract import evidence_state
        self.bind()
        path = self.repo / '.orchestrator/config.json'
        config = json.loads(path.read_text())
        config['verification']['timeout_seconds'] = 600
        path.write_text(json.dumps(config))
        self.assertTrue(evidence_state(self.repo, load_work(self.repo, self.wid))['ready'])
        self.assertEqual(2, self.cli('verify')[0], 'Changed baseline still needs its normal review/reanchor')

    def test_bmad_native_review_never_grants_cao_review_or_verification(self):
        from tests import test_native_route_sync as bmad
        fixture = bmad.RouteSync('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.propose()
        fixture.approve()
        fixture.run_cli('lifecycle', 'migrate')
        path = fixture.repo / fixture.spec
        path.write_text(path.read_text().replace("status: 'draft'", "status: 'in-review'"))
        fixture.run_cli('sdd', 'sync')
        work = fixture.work()
        self.assertEqual('implementation', work['execution']['phase'])
        self.assertEqual(2, work['lifecycle_contract'])
        out = fixture.run_cli('verify', expected=2)
        self.assertEqual('CONFORMANCE_NOT_READY', out['error'])
        self.assertFalse(work.get('verification'))
        self.assertEqual('execution', fixture.run_cli('next')['action']['stage'])

    def test_speckit_scope_uses_the_shared_native_policy(self):
        from unittest.mock import patch
        from scripts.lifecycle_contract import conformance
        from scripts.cao_work_store import load_work
        work = load_work(self.repo, self.wid)
        work['sdd']['provider'] = 'speckit'
        # Provider-specific task readers are already covered by their integration
        # suites; this checks that shared policy cannot turn pending scope into pass.
        observed = {'ready': False, 'obligations': [{'obligation': 'plan_scope',
                    'ready': False, 'reason': 'SPECKIT_PLAN_NOT_DONE'}],
                    'blockers': [{'obligation': 'plan_scope', 'reason': 'SPECKIT_PLAN_NOT_DONE'}]}
        with patch('scripts.lifecycle_contract.prerequisites', return_value=observed):
            result = conformance(self.repo, work)
        self.assertFalse(result['ready'])
        self.assertEqual('speckit', result['requirements'][0]['owner'])

    def test_another_work_cannot_reuse_the_original_completion_result(self):
        self.bind()
        self.check('verify')
        self.check('done')
        path = self.repo / 'openspec/another.md'
        path.write_text('# Another operation\nImplement another observable operation.\n')
        self.check('start', '--requirement', 'openspec/another.md')
        self.check('path', 'assess', '--profile', 'light', '--reason', 'another bounded operation')
        self.check('sdd', 'spec', 'bind', '--spec', 'openspec/spec.md', '--criteria-section', 'Acceptance')
        rc, out = self.cli('lifecycle', 'bind', '--report', 'openspec/completion.md')
        self.assertEqual(2, rc, out)
        self.assertIn('NATIVE_COMPLETION_REPORT_OTHER_WORK', out['error'])

    def test_invalid_or_failed_native_json_cannot_be_bound_as_completion(self):
        path = self.repo / 'openspec/result.json'
        for content in ('invalid JSON', '{}', 'true', '{"status":[]}', '{"status":"failed"}'):
            path.write_text(content)
            rc, out = self.cli('lifecycle', 'bind', '--report', 'openspec/result.json')
            self.assertEqual(2, rc, (content, out))
            self.assertNotIn('completion_evidence', self.work()['sdd'])

    def test_incremental_evidence_can_be_selected_for_final_completion(self):
        result = self.check('evidence', 'attach', '--kind', 'test', '--path', 'openspec/completion.md')
        identity = result['evidence']['id']
        self.assertNotIn('completion_evidence', self.work()['sdd'])
        self.check('lifecycle', 'bind', '--evidence', identity)
        self.check('conformance', 'check')
        self.check('verify')
        self.check('done')
        self.assertEqual('completed', self.work()['status'])

    def test_later_registered_failure_blocks_existing_completion_binding(self):
        result = self.check('evidence', 'attach', '--kind', 'test', '--path', 'openspec/completion.md')
        self.check('lifecycle', 'bind', '--evidence', result['evidence']['id'])
        failed = self.repo / 'openspec/failed.md'
        failed.write_text('status: failed\n')
        self.check('evidence', 'attach', '--kind', 'test', '--path', 'openspec/failed.md')
        rc, out = self.cli('conformance', 'check')
        self.assertEqual(rc, 2, out)
        self.assertFalse(out['ready'])
