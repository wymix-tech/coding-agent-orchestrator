"""Round boundaries and shared presentation regressions."""
import json
from unittest.mock import patch
from tests import test_protocol_consistency as protocol
from tests.repair_helpers import RepairCase
from scripts import cao
from scripts.native_obligations import review_integrity, review_next, review_state, dispatch_patch_state
from scripts.cli_output import summarize


class RoundIntegrity(RepairCase):
    setup_oneshot = protocol.ProtocolConsistency.setup_oneshot
    fresh_report = protocol.ProtocolConsistency.fresh_report
    def running(self):
        self.setup_oneshot()
        return self.fresh_report()

    def test_other_provider_rejects_prepare_without_tickets(self):
        for provider in ('openspec', 'speckit'):
            work = {'sdd': {'provider': provider}}
            from scripts.review_dispatch import prepare_batch
            with self.assertRaisesRegex(ValueError, 'NATIVE_REVIEW_NOT_APPLICABLE'):
                prepare_batch(self.repo, work, None, None)
            self.assertNotIn('native_reviews', work['sdd'])

    def multi_reports(self):
        from tests.helpers import bind_fixture_reviewer
        self.setup_oneshot()
        path = '_bmad/render/cao-fixture/step-oneshot.md'
        source = self.repo / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('#### A (`blind-hunter`)\nReview.\n#### B (`second`)\nReview.\n')
        reports = {}
        for layer in ('blind-hunter', 'second'):
            rc, out = self.run_cli('sdd', 'review', 'begin', '--layer', layer,
                                   '--instructions', path, '--model', 'fixture-model')
            self.assertEqual(0, rc, out)
        for layer in ('blind-hunter', 'second'):
            run = self.read_work(self.wid)['sdd']['native_reviews'][layer]
            reviewer = bind_fixture_reviewer(self.repo, run, layer, 'fixture-model')
            raw = self.repo / ('_bmad-output/' + layer + '-raw.md')
            raw.write_text('- Actual fixture finding\n')
            report = raw.with_name(layer + '-triage.json')
            report.write_text(json.dumps({'reviewer': reviewer, 'findings_file': raw.relative_to(self.repo).as_posix(),
                'findings': [{'id': 'finding-1', 'verdict': 'false', 'disposition': 'reject', 'evidence': 'Inspected source'}]}))
            reports[layer] = report
        return reports

    def classify(self, layer, report):
        rc, out = self.run_cli('sdd', 'review', 'classify', '--layer', layer, '--report', report.relative_to(self.repo))
        self.assertEqual(0, rc, out)
        return out

    def test_mixed_models_rejected_at_readiness(self):
        self.multi_reports()
        work = self.read_work(self.wid)
        # Corrupt historical record: creation APIs must never produce this mixture.
        work['sdd']['native_reviews']['second']['model'] = 'different'
        self.assertEqual('NATIVE_REVIEW_MODEL_MISMATCH', review_integrity(self.repo, work)['reason'])
        self.assertFalse(review_state(self.repo, work, cao.code_snapshot)['ready'])

    def test_same_contract_cannot_mix_models_through_begin(self):
        self.multi_reports()
        before = self.read_work(self.wid)['sdd']['native_reviews']
        rc, out = self.run_cli('sdd', 'review', 'begin', '--layer', 'second',
            '--instructions', '_bmad/render/cao-fixture/step-oneshot.md', '--model', 'other-model')
        self.assertEqual(2, rc, out)
        self.assertEqual('NATIVE_REVIEW_RESTART_REQUIRED', out['error'])
        self.assertEqual(before, self.read_work(self.wid)['sdd']['native_reviews'])

    def test_removed_layer_archived_and_remaining_layer_can_finish(self):
        from tests.helpers import bind_fixture_reviewer
        reports = self.multi_reports()
        before = self.read_work(self.wid)['sdd']['native_reviews']
        source = self.repo / '_bmad/render/cao-fixture/step-oneshot.md'
        source.write_text('#### A (`blind-hunter`)\nReview.\n')
        rc, out = self.run_cli('sdd', 'review', 'begin', '--restart', '--layer', 'blind-hunter',
            '--instructions', source.relative_to(self.repo), '--model', 'fixture-model')
        self.assertEqual(0, rc, out)
        run = out['review']
        reviewer = bind_fixture_reviewer(self.repo, run, 'blind-hunter', 'fixture-model')
        work = self.read_work(self.wid)
        self.assertNotIn('second', work['sdd']['native_reviews'])
        self.assertIn(before['second']['id'], [entry['review']['id'] for entry in work['sdd']['native_review_history']])
        report = reports['blind-hunter']
        data = json.loads(report.read_text())
        data['reviewer'] = reviewer
        report.write_text(json.dumps(data))
        self.classify('blind-hunter', report)
        final = report.with_name('remaining-final.json')
        final.write_bytes(report.read_bytes())
        rc, out = self.run_cli('sdd', 'review', 'submit', '--layer', 'blind-hunter', '--report', final.relative_to(self.repo))
        self.assertEqual(0, rc, out)
        self.assertTrue(review_state(self.repo, self.read_work(self.wid), cao.code_snapshot)['ready'])

    def test_oneshot_write_denied_until_classified(self):
        report = self.running()
        self.assertFalse(dispatch_patch_state(self.repo, self.read_work(self.wid), cao.code_snapshot)['ready'])
        rc, out = self.run_cli('sdd','review','classify','--layer','blind-hunter','--report',report.relative_to(self.repo))
        self.assertEqual(0, rc, out)
        self.assertTrue(dispatch_patch_state(self.repo, self.read_work(self.wid), cao.code_snapshot)['ready'])

    def test_stale_triage_cannot_be_reused(self):
        report = self.running()
        rc,out=self.run_cli('sdd','review','classify','--layer','blind-hunter','--report',report.relative_to(self.repo))
        self.assertEqual(0,rc,out)
        report.write_text(report.read_text()+'\n')
        rc,out=self.run_cli('sdd','review','prepare','--instructions','_bmad/render/cao-fixture/step-oneshot.md','--model','fixture-model','--external-session')
        # Coverage can precede ticket reuse; low-level begin must also reject stale evidence.
        rc,out=self.run_cli('sdd','review','begin','--layer','blind-hunter','--instructions','_bmad/render/cao-fixture/step-oneshot.md','--model','fixture-model')
        self.assertEqual('NATIVE_REVIEW_RESTART_REQUIRED',out['error'])
        state=review_state(self.repo,self.read_work(self.wid),cao.code_snapshot)
        self.assertEqual('stale',state['state'])
        self.assertIn('restart',state['next'])

    def test_oneshot_low_reject_classifies_and_submits(self):
        report=self.running();data=json.loads(report.read_text());data['findings'][0]['verdict']='low';report.write_text(json.dumps(data))
        rc,out=self.run_cli('sdd','review','classify','--layer','blind-hunter','--report',report.relative_to(self.repo));self.assertEqual(0,rc,out)
        final=report.with_name('final.json');final.write_text(json.dumps(data))
        rc,out=self.run_cli('sdd','review','submit','--layer','blind-hunter','--report',final.relative_to(self.repo));self.assertEqual(0,rc,out)

    def test_inactive_record_does_not_block_submit(self):
        report = self.running()
        self.classify('blind-hunter', report)
        work = self.read_work(self.wid)
        # Historical fixture: inactive entries were retained by older versions.
        work['sdd']['native_reviews']['retired'] = {'status': 'running', 'model': 'old'}
        cao.save_work(self.repo, work)
        rc, out = self.run_cli('sdd', 'review', 'begin', '--layer', 'blind-hunter',
            '--instructions', '_bmad/render/cao-fixture/step-oneshot.md', '--model', 'fixture-model')
        self.assertEqual(0, rc, out)
        self.assertTrue(out['reused'])
        rc, out = self.run_cli('sdd', 'review', 'submit', '--layer', 'blind-hunter', '--report', report.relative_to(self.repo))
        self.assertEqual(0, rc, out)

    def test_classified_reviewer_exit_does_not_block_remaining_layer(self):
        reports = self.multi_reports()
        self.classify('blind-hunter', reports['blind-hunter'])
        work = self.read_work(self.wid)
        binding = work['sdd']['native_reviews']['blind-hunter']['execution_binding']
        from scripts.cao_sessions import load_session
        def only_running(*args, **kwargs):
            self.assertNotEqual(binding['reviewer']['session_id'], args[1])
            return load_session(*args, **kwargs)
        with patch('scripts.cao_sessions.load_session', side_effect=only_running):
            self.assertIn('Continue existing', review_next(self.repo, work, cao.review_inputs(self.repo, work, cao.code_snapshot)))

    def test_two_layers_protect_product_then_patch_and_submit(self):
        reports = self.multi_reports()
        self.classify('blind-hunter', reports['blind-hunter'])
        before = (self.repo / 'product.py').read_bytes()
        for host in ('pi', 'codex', 'claude-code'):
            rc, out = self.run_cli('host-event', '--host', host, payload={
                'event': 'pre_tool', 'session_id': 'coordinator-' + host, 'tool_name': 'write',
                'tool_input': {'path': 'product.py', 'content': 'value = 2'}})
            self.assertEqual(0, rc, out)
            decision = out.get('decision') or (out.get('hookSpecificOutput') or {}).get('permissionDecision')
            self.assertEqual('deny', decision, out)
        self.assertEqual(before, (self.repo / 'product.py').read_bytes())
        self.classify('second', reports['second'])
        self.assertTrue(dispatch_patch_state(self.repo, self.read_work(self.wid), cao.code_snapshot)['ready'])
        (self.repo / 'product.py').write_text('value = 2\n')
        for layer, report in reports.items():
            final = report.with_name(layer + '-final.json')
            final.write_bytes(report.read_bytes())
            rc, out = self.run_cli('sdd', 'review', 'submit', '--layer', layer, '--report', final.relative_to(self.repo))
            self.assertEqual(0, rc, out)
        self.assertTrue(review_state(self.repo, self.read_work(self.wid), cao.code_snapshot)['ready'])

    def test_common_input_restart_archives_all_layers(self):
        self.multi_reports()
        before = self.read_work(self.wid)['sdd']['native_reviews']
        (self.repo / 'product.py').write_text('value = 3\n')
        args = ('sdd', 'review', 'begin', '--layer', 'blind-hunter', '--instructions',
                '_bmad/render/cao-fixture/step-oneshot.md', '--model', 'fixture-model')
        rc, out = self.run_cli(*args)
        self.assertEqual('NATIVE_REVIEW_RESTART_REQUIRED', out['error'])
        self.assertEqual(before, self.read_work(self.wid)['sdd']['native_reviews'])
        rc, out = self.run_cli(*args, '--restart')
        self.assertEqual(0, rc, out)
        work = self.read_work(self.wid)
        self.assertNotIn('second', work['sdd']['native_reviews'])
        self.assertEqual({'blind-hunter', 'second'}, {h['layer'] for h in work['sdd']['native_review_history']})
        self.assertFalse(dispatch_patch_state(self.repo, work, cao.code_snapshot)['ready'])

    def test_raw_triage_and_final_tampering_routes_to_recovery(self):
        report = self.running()
        self.classify('blind-hunter', report)
        raw = self.repo / json.loads(report.read_text())['findings_file']
        for path in (raw, report):
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            rc, out = self.run_cli('sdd', 'review', 'status')
            self.assertEqual(0, rc, out)
            self.assertEqual('stale', out['state']['state'])
            self.assertIn('restart', out['state']['next'])
            self.assertFalse(dispatch_patch_state(self.repo, self.read_work(self.wid), cao.code_snapshot)['ready'])
            path.write_bytes(original)
        final = report.with_name('final.json')
        final.write_bytes(report.read_bytes())
        rc, out = self.run_cli('sdd', 'review', 'submit', '--layer', 'blind-hunter', '--report', final.relative_to(self.repo))
        self.assertEqual(0, rc, out)
        original = final.read_bytes()
        final.write_bytes(original + b'\n')
        rc, out = self.run_cli('sdd', 'review', 'status')
        self.assertEqual('stale', out['state']['state'])
        self.assertIn('restart', out['state']['next'])
        final.write_bytes(original)
        self.assertTrue(review_state(self.repo, self.read_work(self.wid), cao.code_snapshot)['ready'])

    def test_restart_cannot_erase_known_intent_conflict(self):
        report = self.running()
        data = json.loads(report.read_text())
        data['intent_conflicts'] = ['Conflicting frozen requirements']
        report.write_text(json.dumps(data))
        self.classify('blind-hunter', report)
        before = self.read_work(self.wid)['sdd']['native_reviews']
        rc, out = self.run_cli('sdd', 'review', 'begin', '--restart', '--layer', 'blind-hunter',
            '--instructions', '_bmad/render/cao-fixture/step-oneshot.md', '--model', 'fixture-model')
        self.assertEqual('NATIVE_REVIEW_LOOPBACK_REQUIRED', out['error'])
        self.assertEqual(before, self.read_work(self.wid)['sdd']['native_reviews'])

    def test_failed_historical_ticket_does_not_suggest_verify(self):
        self.running()
        work = self.read_work(self.wid)
        work['sdd']['native_reviews']['blind-hunter']['status'] = 'failed'
        state = review_state(self.repo, work, cao.code_snapshot)
        self.assertFalse(state['ready'])
        self.assertIn('restart', state['next'])
        self.assertNotIn('then `cao verify`', state['next'])

    def test_removing_conflicted_layer_cannot_bypass_native_clarification(self):
        reports = self.multi_reports()
        report = reports['second']
        data = json.loads(report.read_text())
        data['intent_conflicts'] = ['Frozen requirement still needs clarification']
        report.write_text(json.dumps(data))
        self.classify('second', report)
        before = self.read_work(self.wid)['sdd']['native_reviews']
        source = self.repo / '_bmad/render/cao-fixture/step-oneshot.md'
        source.write_text('#### A (`blind-hunter`)\nReview.\n')
        rc, out = self.run_cli('sdd', 'review', 'begin', '--restart', '--layer', 'blind-hunter',
            '--instructions', source.relative_to(self.repo), '--model', 'fixture-model')
        self.assertEqual(2, rc, out)
        self.assertEqual('NATIVE_REVIEW_LOOPBACK_REQUIRED', out['error'])
        self.assertEqual(before, self.read_work(self.wid)['sdd']['native_reviews'])

    def test_compact_preserves_nested_state_and_bounds_logs(self):
        state={'ready':False,'missing_fields':['language'],'conflicts':['stack'],'revision':'v1'}
        full={'ok':False,'context':state,'results':[{'status':'failed','exit_code':2,'context':state}],'stderr':'x'*9000}
        result=summarize(full)
        self.assertEqual(state,result['context'])
        self.assertEqual(full['results'],result['results'])
        self.assertEqual(4000,len(result['stderr']))
        self.assertTrue(result['stderr_truncated'])
        self.assertEqual(9000,len(full['stderr']))

    def test_model_change_requires_explicit_round_restart(self):
        self.running()
        path='_bmad/render/cao-fixture/step-oneshot.md'
        source=self.repo/path
        source.write_text(source.read_text()+'\n#### Other (`second`)\nReview.\n')
        rc,out=self.run_cli('sdd','review','begin','--layer','second','--instructions',path,'--model','other-model')
        self.assertEqual('NATIVE_REVIEW_RESTART_REQUIRED',out['error'])
        rc,out=self.run_cli('sdd','review','begin','--restart','--layer','second','--instructions',path,'--model','other-model')
        self.assertEqual(0,rc,out)
        work=self.read_work(self.wid)
        self.assertNotIn('blind-hunter',work['sdd']['native_reviews'])
        self.assertEqual('blind-hunter',work['sdd']['native_review_history'][-1]['layer'])

    def test_other_providers_navigation_and_compact_context(self):
        for provider in ('openspec','speckit'):
            case=RepairCase();case.setUp()
            try:
                (case.repo/('openspec' if provider=='openspec' else '.specify')).mkdir(exist_ok=True)
                rc,out=case.run_cli('sdd','select','--provider',provider);self.assertEqual(0,rc,out)
                for profile in ('light','standard','comprehensive'):
                    if profile=='light':
                        wid=case.start()
                    else:
                        rc,out=case.run_cli('path','assess','--profile',profile,'--reason','Fixture scope');self.assertEqual(0,rc,out)
                    rc,out=case.run_cli('next','--compact-json');self.assertEqual(0,rc,out)
                    self.assertFalse(any('bmad-' in p for p in out['guidance']['read_before_action']))
                    self.assertNotIn('native_reviews',case.read_work(wid)['sdd'])
                rc,out=case.run_cli('project','context','diff','--compact-json');self.assertEqual(0,rc,out)
                self.assertIn('ready',out['context'])
                self.assertIn('revision',out['context'])
            finally:
                case.doCleanups()
