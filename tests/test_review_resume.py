"""Cross-session recovery does not recreate rounds or rewrite judgments."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.review_resume import archived_message, canonical_source
from scripts.review_observation import artifact_record
from tests import test_conformance_knowledge as fixtures


class ArchiveRecovery(unittest.TestCase):
    def test_only_bound_session_assistant_message_matching_observed_hash_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'native.jsonl'
            text = '- Original finding\n\n```acceptance-report\n{}\n```'
            session = {'host': 'pi', 'host_session_id': 'bound-child', 'review_output_sha256': hashlib.sha256(text.strip().encode()).hexdigest()}
            def write(identity, role):
                path.write_text(json.dumps({'type': 'session', 'id': identity}) + '\n' + json.dumps({'type': 'message', 'message': {'role': role, 'content': [{'type': 'text', 'text': text}]}}) + '\n')
            write('other-child', 'assistant')
            self.assertIsNone(archived_message(root, session, [str(path)]))
            write('bound-child', 'toolResult')
            self.assertIsNone(archived_message(root, session, [str(path)]))
            write('bound-child', 'assistant')
            self.assertEqual(text, archived_message(root, session, [str(path)]))


class ResumeCLI(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SessionConformance()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.t = self.fixture.t
        self.packages = self.fixture.prepare('--external-session')['reviews']

    def collect(self, package, text):
        from tests.helpers import bind_fixture_reviewer, publish_fixture_review
        run = self.t.work()['sdd']['native_reviews'][package['layer']]
        reviewer = bind_fixture_reviewer(self.t.repo, run, package['layer'], 'fixture-model')
        publish_fixture_review(self.t.repo, reviewer, text)
        return reviewer

    def test_resume_drafts_canonical_bytes_and_preserves_existing_triage_and_round(self):
        for package in self.packages:
            self.collect(package, '- Actual original finding')
        before = self.t.work()['sdd']['native_reviews']
        out = self.t.run_cli('sdd', 'review', 'resume')
        self.assertTrue(all(row['phase'] == 'drafted' for row in out['layers']))
        report_path = self.t.repo / out['layers'][0]['report']
        report = json.loads(report_path.read_text())
        report['findings'][0].update(verdict='false', disposition='reject', evidence='Original claim refuted by inspected guard')
        report_path.write_text(json.dumps(report))
        self.t.run_cli('sdd', 'review', 'resume')
        self.assertEqual(report, json.loads(report_path.read_text()))
        self.assertEqual(before, self.t.work()['sdd']['native_reviews'])
        package = self.packages[0]
        blocked = self.t.run_cli('sdd', 'review', 'begin', '--layer', package['layer'], '--instructions', self.fixture.instructions, '--model', 'fixture-model', '--restart', expected=2)
        self.assertEqual('NATIVE_REVIEW_EVIDENCE_RECOVERY_REQUIRED', blocked['error'])

    def test_legacy_full_transcript_recovery_and_other_layer_waiting(self):
        from scripts.cao_sessions import load_session, save_session
        package = self.packages[0]
        text = '- Original legacy finding\n\n```acceptance-report\n{}\n```'
        reviewer = self.collect(package, text)
        child = load_session(self.t.repo, reviewer['session_id'], host='pi')
        child.pop('review_message_artifact')
        save_session(self.t.repo, child)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'child.jsonl'
            archive.write_text(json.dumps({'type': 'session', 'id': reviewer['session_id']}) + '\n' + json.dumps({'type': 'message', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}}) + '\n')
            result = self.t.run_cli('sdd', 'review', 'resume', '--layer', package['layer'], '--transcript', str(archive))
        row = result['layers'][0]
        self.assertEqual('drafted', row['phase'])
        self.assertEqual('- Original legacy finding', (self.t.repo / row['source']).read_text())
        self.assertEqual(package['run_id'], self.t.work()['sdd']['native_reviews'][package['layer']]['id'])

    def test_draft_without_source_uses_collected_artifact(self):
        package = self.packages[0]
        self.collect(package, '- Original output')
        out = self.t.run_cli('sdd', 'review', 'draft', '--layer', package['layer'])
        self.assertEqual(1, len(out['findings']))

    def test_resume_repairs_source_pointer_without_changing_judgments(self):
        package = self.packages[0]
        self.collect(package, '- Original output')
        drafted = self.t.run_cli('sdd', 'review', 'draft', '--layer', package['layer'])
        path = self.t.repo / drafted['report']
        report = json.loads(path.read_text())
        report['findings'][0].update(verdict='false', disposition='reject', evidence='Original claim refuted by guard')
        copied = self.t.repo / '_bmad-output/copied.md'
        copied.write_text('- Original output')
        report['findings_file'] = copied.relative_to(self.t.repo).as_posix()
        path.write_text(json.dumps(report))
        copied.write_text('damaged external copy')
        result = self.t.run_cli('sdd', 'review', 'resume', '--layer', package['layer'])
        updated = json.loads(path.read_text())
        self.assertEqual(report['findings'], updated['findings'])
        self.assertEqual(result['layers'][0]['source'], updated['findings_file'])
        self.assertNotEqual(report['findings_file'], updated['findings_file'])

    def test_multi_layer_resume_failure_returns_a_diagnostic_instead_of_crashing(self):
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.t.repo, self.t.wid)
        work['sdd']['native_reviews']['_contract']['sources'] = ['missing-native-instructions.md']
        save_work(self.t.repo, work)
        result = self.t.run_cli('sdd', 'review', 'resume', '--layer', self.packages[0]['layer'], expected=2)
        self.assertFalse(result['ok'])
        self.assertIn('error', result)
