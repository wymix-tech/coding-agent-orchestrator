"""Review continuation and route-specific frozen classification regressions."""
import json
from scripts.cli_output import summarize
from tests import test_native_omissions
from tests.repair_helpers import RepairCase
from tests.helpers import complete_acceptance


class ProtocolConsistency(RepairCase):
    setup_oneshot = test_native_omissions.NativeOmissionTests.setup_oneshot
    review = test_native_omissions.NativeOmissionTests.review
    def test_oneshot_prepare_reuses_and_patch_submit_binds_new_snapshot(self):
        self.setup_oneshot()
        complete_acceptance(self.repo, lambda repo, *a: self.run_cli(*a), prepare_only=True)
        path = '_bmad/render/cao-fixture/step-oneshot.md'
        source = self.repo / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('#### Blind (`blind-hunter`)\nReview.\n')
        args = ('sdd', 'review', 'prepare', '--instructions', path, '--model', 'fixture-model', '--external-session')
        rc, first = self.run_cli(*args)
        self.assertEqual(0, rc, first)
        rc, second = self.run_cli(*args)
        self.assertEqual(first['reviews'][0]['run_id'], second['reviews'][0]['run_id'])
        report = self.fresh_report()
        data = json.loads(report.read_text())
        data['findings'][0].update(verdict='low', disposition='patch')
        report.write_text(json.dumps(data))
        rc, out = self.run_cli('sdd', 'review', 'classify', '--layer', 'blind-hunter', '--report', report.relative_to(self.repo))
        self.assertEqual(0, rc, out)
        self.assertIn('review submit', out['next'])
        self.assertNotIn('review resolve', out['next'])
        rc, resumed = self.run_cli(*args)
        self.assertEqual(0, rc, resumed)
        self.assertIn('Do not classify again', resumed['reviews'][0]['next'])
        (self.repo / 'product.py').write_text('value = 2\n')
        data['findings'][0]['resolved'] = True
        final = report.parent / 'final.json'
        final.write_text(json.dumps(data))
        rc, out = self.run_cli('sdd', 'review', 'submit', '--layer', 'blind-hunter', '--report', final.relative_to(self.repo))
        self.assertEqual(0, rc, out)

    def test_compact_keeps_navigation(self):
        self.start()
        _, out = self.run_cli('bootstrap')
        compact = summarize(out)
        for key in ('guidance', 'mode', 'next'):
            self.assertEqual(out[key], compact[key])

    def test_controlled_configuration(self):
        self.assertEqual(0, self.run_cli('knowledge', 'configure', '--enabled', 'false')[0])
        self.assertEqual(0, self.run_cli('runtime', 'configure', '--mode', 'off')[0])
        before = (self.repo / '.orchestrator/config.json').read_bytes()
        self.assertNotEqual(0, self.run_cli('runtime', 'configure', '--segment-bytes', '-1')[0])
        self.assertEqual(before, (self.repo / '.orchestrator/config.json').read_bytes())

    def test_wrong_finding_id_is_rejected(self):
        self.setup_oneshot()
        report = self.fresh_report()
        data = json.loads(report.read_text())
        data['findings'][0]['id'] = 'unrelated-id'
        report.write_text(json.dumps(data))
        rc, out = self.run_cli('sdd', 'review', 'submit', '--layer', 'blind-hunter', '--report', report.relative_to(self.repo))
        self.assertEqual(2, rc)
        self.assertEqual('NATIVE_REVIEW_FINDING_MAPPING_INVALID', out['error'])

    def fresh_report(self):
        from tests.helpers import bind_fixture_reviewer
        path = '_bmad/render/cao-fixture/step-oneshot.md'
        source = self.repo / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('#### Blind (`blind-hunter`)\nReview.\n')
        rc, out = self.run_cli('sdd', 'review', 'begin', '--restart', '--layer', 'blind-hunter', '--instructions', path, '--model', 'fixture-model')
        self.assertEqual(0, rc, out)
        run = out['review']
        reviewer = bind_fixture_reviewer(self.repo, run, 'blind-hunter', 'fixture-model')
        raw = '_bmad-output/protocol-raw.md'
        (self.repo / raw).write_text('- Actual fixture finding\n')
        report = self.repo / '_bmad-output/protocol-triage.json'
        report.write_text(json.dumps({'run_id': run['id'], 'input_sha256': run['input_sha256'], 'reviewer': reviewer, 'findings_file': raw, 'findings': [{'id': 'finding-1', 'verdict': 'false', 'disposition': 'reject', 'evidence': 'Inspected fixture source'}]}))
        return report

    def test_conflict_blocks_submit_and_resolve_rejects_oneshot(self):
        self.setup_oneshot()
        report = self.fresh_report()
        data = json.loads(report.read_text())
        data['intent_conflicts'] = ['Conflicting native constraints']
        report.write_text(json.dumps(data))
        rc, out = self.run_cli('sdd', 'review', 'classify', '--layer', 'blind-hunter', '--report', report.relative_to(self.repo))
        self.assertEqual(0, rc, out)
        self.assertTrue(out['review']['loopback_required'])
        self.assertEqual('NATIVE_REVIEW_LOOPBACK_REQUIRED', self.run_cli('sdd', 'review', 'submit', '--layer', 'blind-hunter', '--report', report.relative_to(self.repo))[1]['error'])
        self.assertEqual('NATIVE_REVIEW_RESOLVE_NOT_APPLICABLE', self.run_cli('sdd', 'review', 'resolve', '--layer', 'blind-hunter', '--report', report.relative_to(self.repo))[1]['error'])

    def test_restart_required_and_retains_history(self):
        self.setup_oneshot()
        self.fresh_report()
        before = self.read_work(self.wid)['sdd']['native_reviews']['blind-hunter']['id']
        (self.repo / 'product.py').write_text('value = 7\n')
        args = ('sdd', 'review', 'begin', '--layer', 'blind-hunter', '--instructions', '_bmad/render/cao-fixture/step-oneshot.md', '--model', 'fixture-model')
        rc, out = self.run_cli(*args)
        self.assertEqual(2, rc)
        self.assertEqual('NATIVE_REVIEW_RESTART_REQUIRED', out['error'])
        rc, out = self.run_cli(*args, '--restart')
        self.assertEqual(0, rc, out)
        self.assertNotEqual(before, out['review']['id'])
        self.assertEqual(before, self.read_work(self.wid)['sdd']['native_review_history'][-1]['review']['id'])

    def test_compact_review_check_preserves_diagnostics(self):
        out = summarize({'ok': False, 'disposable_copy': True, 'gate_evidence': False, 'stdout': 'assertion failed', 'stderr': 'trace'})
        self.assertEqual('trace', out['stderr'])

    def test_next_routes_missing_coverage_to_acceptance(self):
        self.setup_oneshot()
        _, out = self.run_cli('next')
        self.assertEqual('TASK_ACCEPTANCE_COVERAGE_INCOMPLETE', out['action']['reason'])
        self.assertTrue(any(p.endswith('sdd-acceptance-protocol.md') for p in out['guidance']['read_before_action']))
