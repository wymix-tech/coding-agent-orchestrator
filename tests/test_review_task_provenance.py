"""Real host metadata must establish layer identity, never session ordering."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.native_obligations import digest
from scripts.review_binding import attach_reviewer
from scripts.review_observation import observe, task_marker, ticket, validate_source


class ReviewTaskProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.inputs = {'work_id': 'work-1'}
        self.runs = {layer: {'id': 'run-' + layer, 'status': 'running', 'inputs': self.inputs,
                            'input_sha256': digest(self.inputs), 'existing_session_ids': [],
                            'model': 'model', 'layer': layer, 'task_binding_version': 1}
                     for layer in ['blind-hunter', 'edge-case-hunter', 'verification-gap']}
        self.work = {'sdd': {'native_reviews': self.runs}}

    def child(self, layer):
        return {'id': 'session-' + layer, 'status': 'active', 'work_item_id': 'work-1',
                'kind': 'subagent', 'review_ticket': ticket(self.runs[layer], layer)}

    def test_every_cross_layer_attachment_rejected_before_mutation(self):
        for layer, run in reversed(list(self.runs.items())):
            for actual in self.runs:
                child = self.child(actual)
                before = copy.deepcopy(run)
                if layer != actual:
                    with self.assertRaisesRegex(ValueError, 'NATIVE_REVIEW_TASK_MISMATCH'):
                        attach_reviewer(run, self.inputs, ('pi', child['id']), child,
                                        'model', run['id'], run['input_sha256'])
                    self.assertEqual(before, run)
                else:
                    attach_reviewer(run, self.inputs, ('pi', child['id']), child,
                                    'model', run['id'], run['input_sha256'])

    def test_missing_task_metadata_cannot_be_filled_by_coordinator_claim(self):
        run = self.runs['blind-hunter']
        child = self.child('blind-hunter')
        child.pop('review_ticket')
        with self.assertRaisesRegex(ValueError, 'NATIVE_REVIEW_TASK_MISMATCH'):
            attach_reviewer(run, self.inputs, ('pi', child['id']), child,
                            'model', run['id'], run['input_sha256'])

    def test_task_prompt_observation_and_original_output_hash(self):
        run = self.runs['blind-hunter']
        child = self.child('blind-hunter')
        child.pop('review_ticket')
        with patch('scripts.review_observation.save_session'):
            observe(self.repo, self.work, child, {'prompt': task_marker(run, 'blind-hunter') + '\nReview.'}, 'prompt_submit')
            self.assertEqual(ticket(run, 'blind-hunter'), child['review_ticket'])
            observe(self.repo, self.work, child, {'prompt': task_marker(self.runs['edge-case-hunter'], 'edge-case-hunter')}, 'prompt_submit')
            self.assertEqual('blind-hunter', child['review_ticket']['layer'])
            observe(self.repo, self.work, child, {'last_assistant_message': '- Original finding\n'}, 'turn_end')
        run['execution_binding'] = {'session_id': child['id'], 'host': 'pi'}
        source = self.repo / 'raw.md'
        source.write_text('- Original finding\n')
        with patch('scripts.cao_sessions.load_session', return_value=child):
            validate_source(self.repo, run, source)
            source.write_text('- A different reviewer output')
            with self.assertRaisesRegex(ValueError, 'OUTPUT_SOURCE_MISMATCH'):
                validate_source(self.repo, run, source)
