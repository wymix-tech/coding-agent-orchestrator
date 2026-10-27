"""Real CLI native evidence lifecycle: no duplicate functional executor."""
import json
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
        self.report.write_text('# Native completion\nImplementation and native tests completed successfully.\n')

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
