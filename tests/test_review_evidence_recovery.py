"""Regression cases from the three-round Pi review collection loop."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.review_observation import extract_findings, artifact_record, validate_source, ticket, observe
from scripts.native_obligations import digest, validate_finding_content


class ReviewEvidenceRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.run = {'id': 'run-1', 'layer': 'blind-hunter', 'input_sha256': 'input',
                    'model': 'fixture-model', 'task_binding_version': 1, 'finding_binding_version': 1,
                    'execution_binding': {'session_id': 'child', 'host': 'pi'}}
        self.child = {'id': 'child', 'kind': 'subagent', 'review_ticket': ticket(self.run, 'blind-hunter')}

    def test_transport_envelope_is_not_a_finding(self):
        text = '- Original finding\n\n```acceptance-report\n{"ok":true}\n```\n'
        self.assertEqual('- Original finding', extract_findings(text))
        self.assertEqual(text.replace('acceptance-report', 'json'), extract_findings(text.replace('acceptance-report', 'json')))

    def test_observed_file_can_differ_from_final_message_but_cannot_change(self):
        source = self.repo / 'findings.md'
        source.write_text('- Detailed original finding\n')
        snapshot = self.repo / 'snapshot.md'
        snapshot.write_bytes(source.read_bytes())
        self.child['review_artifacts'] = [artifact_record(self.repo, snapshot)]
        self.child['review_output_sha256'] = hashlib.sha256(b'Final summary plus transport').hexdigest()
        with patch('scripts.cao_sessions.load_session', return_value=self.child):
            validate_source(self.repo, self.run, source)
            source.write_text('- Rewritten finding\n')
            with self.assertRaisesRegex(ValueError, 'SOURCE_MISMATCH'):
                validate_source(self.repo, self.run, source)
            snapshot.write_text('- Altered snapshot')
            with self.assertRaisesRegex(ValueError, 'COLLECTED_EVIDENCE_CHANGED'):
                validate_source(self.repo, self.run, source)

    def test_wrong_ticket_cannot_supply_identical_findings(self):
        source = self.repo / 'raw.md'
        source.write_text('- Finding')
        self.child['review_artifacts'] = [artifact_record(self.repo, source)]
        self.child['review_ticket']['run_id'] = 'previous-run'
        with patch('scripts.cao_sessions.load_session', return_value=self.child):
            with self.assertRaisesRegex(ValueError, 'TASK_MISMATCH'):
                validate_source(self.repo, self.run, source)

    def test_old_judgments_with_same_numbers_rejected(self):
        source = self.repo / 'raw.md'
        source.write_text('- New finding')
        original = {'id': 'finding-1', 'content': '- New finding'}
        report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                  'findings': [{'id': 'finding-1', 'content_sha256': digest({'id': 'finding-1', 'content': '- Old finding'})}]}
        with self.assertRaisesRegex(ValueError, 'FINDING_CONTENT_MISMATCH'):
            validate_finding_content(self.run, report, source, [original])
        report['findings'][0]['content_sha256'] = digest(original)
        validate_finding_content(self.run, report, source, [original])
        source.write_text('- Changed source')
        with self.assertRaisesRegex(ValueError, 'SOURCE_DIGEST_MISMATCH'):
            validate_finding_content(self.run, report, source, [original])

    def test_end_collects_message_before_reviewer_exits(self):
        work = {'sdd': {'native_reviews': {'blind-hunter': self.run}}}
        message = '- Finding\n\n```acceptance-report\n{}\n```'
        with patch('scripts.review_observation.save_session'), patch('scripts.review_dispatch.bundle_root', return_value=self.repo / 'bundle'):
            observe(self.repo, work, self.child, {'last_assistant_message': message}, 'turn_end')
        source = self.repo / self.child['review_message_artifact']['path']
        self.assertEqual('- Finding', source.read_text())
        self.assertEqual(message, next((self.repo / 'bundle').glob('original-message-*.txt')).read_text())
        self.child['status'] = 'ended'
        with patch('scripts.cao_sessions.load_session', return_value=self.child):
            validate_source(self.repo, self.run, source)

    def test_recover_requires_exact_observed_message(self):
        from scripts.review_observation import recover, diagnose
        message = '- Original finding\n\n```acceptance-report\n{}\n```'
        self.child['review_output_sha256'] = hashlib.sha256(message.strip().encode()).hexdigest()
        work = {'sdd': {'native_reviews': {'blind-hunter': self.run}}}
        raw = self.repo / '_bmad-output/original.md'
        raw.parent.mkdir()
        raw.write_text(message)
        with patch('scripts.cao_sessions.load_session', return_value=self.child), patch('scripts.review_observation.save_session'), patch('scripts.review_dispatch.bundle_root', return_value=self.repo / '_bmad-output/bundle'):
            out = recover(self.repo, work, 'blind-hunter', raw.relative_to(self.repo).as_posix())
            self.assertEqual('- Original finding', (self.repo / out['findings_file']).read_text())
            self.assertEqual('collected', diagnose(self.repo, work, 'blind-hunter')['phase'])
            raw.write_text('- Original finding')
            with self.assertRaisesRegex(ValueError, 'SOURCE_MISMATCH'):
                recover(self.repo, work, 'blind-hunter', raw.relative_to(self.repo).as_posix())

    def test_retry_fault_survives_action_and_run_changes(self):
        import argparse
        from scripts.review_diagnostics import failure
        work = {'id': 'work-1', 'sdd': {'native_reviews': {'blind-hunter': self.run}}}
        args = argparse.Namespace(review_action='draft', layer='blind-hunter')
        first = failure(self.repo, work, args, ValueError('NATIVE_REVIEW_OUTPUT_SOURCE_MISMATCH'))
        args.review_action = 'classify'
        self.run['id'] = 'different-run'
        second = failure(self.repo, work, args, ValueError('NATIVE_REVIEW_OUTPUT_SOURCE_MISMATCH'))
        self.assertEqual(first['repair_id'], second['repair_id'])
        self.assertEqual(2, second['retry_count'])
        self.assertTrue(second['pause_retry'])

    def test_successful_bound_write_is_collected_without_trusting_summary(self):
        from scripts.review_observation import collect_written_artifact
        raw = self.repo / '_bmad-output/detail.md'
        raw.parent.mkdir()
        raw.write_text('- Detailed finding')
        work = {'sdd': {'native_reviews': {'blind-hunter': self.run}}}
        event = {'toolName': 'write', 'input': {'path': str(raw), 'content': raw.read_text()}}
        with patch('scripts.review_observation.save_session'), patch('scripts.review_dispatch.bundle_root', return_value=raw.parent / 'bundle'):
            collect_written_artifact(self.repo, work, self.child, event)
        self.assertEqual(1, len(self.child['review_artifacts']))
        with patch('scripts.cao_sessions.load_session', return_value=self.child):
            validate_source(self.repo, self.run, raw)

    def test_host_automatically_binds_observed_child_before_inspection(self):
        from scripts.review_observation import auto_bind
        self.run.pop('execution_binding')
        work = {'sdd': {'native_reviews': {'blind-hunter': self.run}}}
        parent = {'id': 'parent', 'host_session_id': 'parent-native', 'host': 'pi', 'kind': 'primary', 'status': 'active'}
        self.child.update(parent_session_id='parent', host='pi')
        with patch('scripts.cao_sessions.load_session', return_value=parent), patch('scripts.review_binding.delegated_reviewer') as attach, patch('scripts.native_obligations.review_inputs', return_value={}), patch('scripts.review_dispatch.stamp_report'), patch('scripts.cao_work_store.save_work') as save:
            auto_bind(self.repo, work, self.child)
            attach.assert_called_once()
            save.assert_called_once_with(self.repo, work)
