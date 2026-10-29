"""A list of finding IDs cannot certify a product repair."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.review_resolution import validate


class FindingResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.file = self.repo / 'session_store.py'
        self.file.write_text('with lock:\n    evict_and_insert()\n')
        self.sha = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.original = {'path': self.file.name, 'sha256': hashlib.sha256(b'check_then_insert()\n').hexdigest()}
        (self.repo / 'manifest.json').write_text(json.dumps({'files': [self.original]}))
        self.run = {'resolution_binding_version': 1, 'input_manifest': {'path': 'manifest.json'},
                    'findings': [{'id': 'finding-1', 'content_sha256': 'content', 'disposition': 'patch'}]}
        self.current = {'code_snapshot': 'current'}
        self.work = {'sdd': {}}
        self.report = {'resolved_ids': ['finding-1'], 'code_snapshot': 'current', 'resolutions': [{
            'id': 'finding-1', 'content_sha256': 'content',
            'changes': [{'path': self.file.name, 'sha256': self.sha, 'reason': 'Serialize capacity check, eviction and insertion'}],
            'verification': [{'kind': 'manual', 'code_snapshot': 'current', 'assertion': 'Capacity modification executes under one lock',
                              'observed': 'All three operations are in the same critical section',
                              'evidence': [{'path': self.file.name, 'sha256': self.sha}]}]}]}

    def verify(self):
        with patch('scripts.review_changes.inventory', return_value=('current', [{'path': self.file.name, 'sha256': self.sha}])):
            return validate(self.repo, self.work, self.run, self.report, self.current)

    def test_resolved_ids_alone_cannot_pass(self):
        self.report.pop('resolutions')
        with self.assertRaisesRegex(ValueError, 'RESOLUTION_EVIDENCE_REQUIRED'):
            self.verify()

    def test_current_manual_verification_is_explicitly_declared(self):
        hashes, result = self.verify()
        self.assertEqual(self.sha, hashes[self.file.name])
        self.assertEqual(['native_manual_declaration'], result['items'][0]['verification_authorities'])

    def test_old_content_snapshot_and_unchanged_product_rejected(self):
        self.report['resolutions'][0]['content_sha256'] = 'old-finding'
        with self.assertRaisesRegex(ValueError, 'CONTENT_MISMATCH'):
            self.verify()
        self.report['resolutions'][0]['content_sha256'] = 'content'
        self.report['code_snapshot'] = 'old'
        with self.assertRaisesRegex(ValueError, 'SNAPSHOT_STALE'):
            self.verify()
        self.report['code_snapshot'] = 'current'
        (self.repo / 'manifest.json').write_text(json.dumps({'files': [{'path': self.file.name, 'sha256': self.sha}]}))
        with self.assertRaisesRegex(ValueError, 'CHANGE_REQUIRED'):
            self.verify()

    def test_test_or_static_claim_requires_actual_current_execution(self):
        for kind in ('test', 'static'):
            self.report['resolutions'][0]['verification'][0]['kind'] = kind
            with patch('scripts.native_execution.receipt_matches', return_value=False):
                with self.assertRaisesRegex(ValueError, 'EXECUTION_REQUIRED'):
                    self.verify()
            with patch('scripts.native_execution.receipt_matches', return_value=True):
                self.assertEqual(['observed_execution'], self.verify()[1]['items'][0]['verification_authorities'])

    def test_missing_assertions_or_changed_evidence_rejected(self):
        self.report['resolutions'][0]['verification'][0]['assertion'] = ''
        with self.assertRaisesRegex(ValueError, 'VERIFICATION_REQUIRED'):
            self.verify()
        self.report['resolutions'][0]['verification'][0]['assertion'] = 'capacity'
        self.file.write_text('different repair')
        with self.assertRaisesRegex(ValueError, 'REFERENCE_CHANGED'):
            self.verify()

    def test_legacy_is_not_recertified_and_zero_patch_findings_need_no_fake_checks(self):
        self.run.pop('resolution_binding_version')
        self.assertEqual('legacy_declared_ids', self.verify()[1]['protocol'])
        self.run['resolution_binding_version'] = 1
        self.run['findings'] = []
        self.report = {'resolved_ids': []}
        self.assertEqual([], self.verify()[1]['items'])
