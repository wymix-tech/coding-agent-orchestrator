"""Task routing and best-effort diagnostic errors preserve host authorization."""
import json
from unittest.mock import patch

from scripts import cao
from scripts.runtime_host import observe
from scripts.runtime_queries import events
from tests.repair_helpers import RepairCase


class HostTests(RepairCase):
    def event(self, event, **fields):
        rc, out = self.run_cli('host-event', '--host', 'pi', payload={
            'event': event, 'session_id': 'host-session', **fields})
        self.assertEqual(rc, 0, out)
        return out

    def test_post_tool_after_task_switch_stays_with_original_work(self):
        first = self.start('a')
        self.event('pre_tool', toolName='write', toolCallId='call-1', input={
            'path': '.orchestrator/requirements/note.md', 'content': 'do not record full content'})
        # The write must settle before a safe switch; a delayed duplicate still belongs to A.
        self.event('post_tool', toolName='write', toolCallId='call-1', isError=True)
        req = self.repo / '.orchestrator/requirements/b.md'; req.write_text('# b\n')
        rc, out = self.run_cli('start', '--replace', '--requirement', '.orchestrator/requirements/b.md')
        self.assertEqual(rc, 0, out)
        second = out['work_item']['id']
        payload = {'toolName': 'write', 'toolCallId': 'call-1', 'input': {
            'path': '.orchestrator/requirements/note.md', 'content': 'do not record full content'}, 'isError': True}
        self.event('post_tool', **payload)
        self.event('post_tool', **payload)  # native event replay is idempotent
        finished = [e for e in events(self.repo, first) if e['event'] == 'tool.finished']
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]['source'], 'host_observation')
        self.assertTrue(finished[0]['is_error'])
        self.assertFalse([e for e in events(self.repo, second) if e['event'] == 'tool.finished'])
        self.assertNotIn('do not record full content', json.dumps(finished))

    def test_denied_tool_has_no_execution_receipt_and_simple_reads_are_skipped(self):
        wid = self.start()
        out = self.event('pre_tool', toolName='write', toolCallId='denied', input={
            'path': '.orchestrator/work/bad.json', 'content': '{}'})
        self.assertEqual(out['decision'], 'deny')
        self.event('pre_tool', toolName='read', toolCallId='read', input={'path': 'app.py'})
        self.event('pre_tool', toolName='bash', toolCallId='status', input={'command': 'cao runtime status'})
        observations = [e for e in events(self.repo, wid) if e['record_type'] == 'observation']
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]['event'], 'gate.denied')
        self.assertFalse((self.repo / f'.orchestrator/runtime/{wid}/runs').exists())

    def test_failed_optional_observation_preserves_deny(self):
        wid = self.start()
        with patch('scripts.runtime_host.observation', side_effect=OSError('full')):
            result = {'decision': 'deny', 'reason': 'protected state'}
            diagnostic = observe(self.repo, {'id': wid}, {'toolName': 'write', 'toolCallId': 'one'},
                                 'pi', 'host', 'pre_tool', result)
        self.assertIn('OBSERVATION_FAILED', diagnostic)
        self.assertEqual(result, {'decision': 'deny', 'reason': 'protected state'})

    def test_partial_events_do_not_claim_execution_or_correlation(self):
        wid = self.start()
        self.event('pre_tool', toolName='write', input={
            'path': '.orchestrator/requirements/note.md', 'content': 'x'})
        observation = [e for e in events(self.repo, wid) if e['record_type'] == 'observation'][0]
        self.assertEqual(observation['coverage'], 'partial')
        self.assertIsNone(observation['tool_call_id'])

    def test_cli_precondition_denial_is_recorded_without_a_fake_start(self):
        wid = self.start()
        rc, _ = self.run_cli('done')
        self.assertEqual(rc, 2)
        denied = [item for item in events(self.repo, wid) if item['event'] == 'gate.denied']
        self.assertEqual(denied[0]['source'], 'cao_command')
        self.assertEqual(denied[0]['reason_code'], 'COMPLETION_BLOCKED')
        self.assertFalse((self.repo / f'.orchestrator/runtime/{wid}/runs').exists())

    def test_unassigned_denial_can_be_inspected_without_creating_a_work(self):
        self.event('pre_tool', toolName='write', toolCallId='no-work', input={'path': 'product.py', 'content': 'x'})
        rc, out = self.run_cli('runtime', 'events', '--work', '_unassigned')
        self.assertEqual(rc, 0, out)
        self.assertIn('gate.denied', [item['event'] for item in out['items']])
        self.assertEqual(cao.load_index(self.repo)['work_items'], [])
