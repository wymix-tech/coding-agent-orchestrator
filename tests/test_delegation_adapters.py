"""Plugin contracts are explicit, schema-specific, and never session authority."""
import contextlib
import io
import json
import unittest
from argparse import Namespace
from copy import deepcopy
from unittest.mock import patch
from tests import test_review_context_packages as context_packages
from scripts.delegation_adapters import command, normalize, validate, REGISTRY
from scripts.review_packages import launch_guard
from scripts.state_store import atomic_json


class DelegationAdapters(unittest.TestCase):
    def setUp(self):
        self.fixture = context_packages.ContextPackages('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.work = self.fixture.repo, self.fixture.work
        run, output, _ = self.fixture.package('blind-hunter')
        run.update(status='running')
        run.pop('execution_binding')
        self.work['sdd']['native_reviews']['_contract'] = {'layers': ['blind-hunter']}
        self.prompt = (self.repo / output['prompt_file']).read_text()

    def call(self, action, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch('scripts.cao_work_store.resolve_work', return_value=self.work):
            command(Namespace(repo=str(self.repo), delegation_action=action, work=None, **kw))
        return json.loads(out.getvalue())

    def test_two_explicit_plugins_roundtrip_launch_guard_and_result_mapping(self):
        rows = [
            {'version': 1, 'plugin': 'fixture-a', 'tool': 'subagent', 'match': {'workflow': 'parallel'},
             'tasks_path': 'args.tasks', 'prompt_field': 'task', 'agent_field': 'agent',
             'blocking_path': 'async', 'blocking_value': False},
            {'version': 1, 'plugin': 'fixture-b', 'tool': 'delegate_jobs', 'match': {'operation': 'dispatch'},
             'tasks_path': 'jobs', 'prompt_field': 'message.text', 'agent_field': 'worker.name',
             'blocking_path': 'execution.mode', 'blocking_value': 'wait',
             'result_fields': {'run_id': 'job.handle', 'session_id': 'observer.session'}},
        ]
        for row in rows:
            (self.repo / 'manifest.json').write_text(json.dumps(row))
            self.call('register', manifest='manifest.json')
        for row in rows:
            output = self.call('launch', plugin=row['plugin'], agent='reviewer')
            event = {'toolName': output['tool'], 'input': output['input']}
            self.assertIsNone(launch_guard(self.repo, self.work, event))
            self.assertEqual([{'task': self.prompt}], normalize(self.repo, output['tool'], output['input']))
            from scripts.delegation_adapters import put
            put(output['input'], row['blocking_path'], True)
            self.assertIn('SYNCHRONOUS_REQUIRED', launch_guard(self.repo, self.work, event))
        (self.repo / 'result.json').write_text(json.dumps({'job': {'handle': 'plugin-run'}, 'observer': {'session': 'observed-session'}}))
        decoded = self.call('decode', plugin='fixture-b', result='result.json')
        self.assertEqual({'run_id': 'plugin-run', 'session_id': 'observed-session'}, decoded['identities'])
        self.assertNotIn('execution_binding', self.work['sdd']['native_reviews']['blind-hunter'])

    def test_no_implicit_pi_transport_or_nonreview_plugin_interference(self):
        (self.repo / REGISTRY).unlink()
        raw = {'toolName': 'subagent', 'input': {'tasks': [{'task': self.prompt}], 'async': False}}
        self.assertIn('ADAPTER_REQUIRED', launch_guard(self.repo, self.work, raw))
        raw['input'] = {'action': 'status', 'id': 'run'}
        self.assertIsNone(launch_guard(self.repo, self.work, raw))
        self.assertIsNone(launch_guard(self.repo, self.work, {'toolName': 'write', 'input': {'content': self.prompt}}))

    def test_ticket_text_in_search_or_parent_message_is_not_delegation(self):
        for tool, payload in [('grep', {'pattern': 'CAO_REVIEW_TICKET'}),
                              ('contact_supervisor', {'message': self.prompt, 'reason': 'progress_update'})]:
            self.assertIsNone(launch_guard(self.repo, self.work, {'toolName': tool, 'input': payload}))

    def test_forbidden_task_field_is_rejected_even_when_null(self):
        raw = {'toolName': 'subagent', 'input': {'tasks': [{'task': self.prompt, 'async': None}], 'async': False}}
        self.assertIn('ARGUMENT_INVALID', launch_guard(self.repo, self.work, raw))

    def test_ambiguous_and_invalid_contracts_rejected(self):
        rows = json.loads((self.repo / REGISTRY).read_text())
        rows.append({**deepcopy(rows[0]), 'plugin': 'other'})
        atomic_json(self.repo / REGISTRY, rows)
        with self.assertRaisesRegex(ValueError, 'AMBIGUOUS'):
            normalize(self.repo, 'subagent', {'tasks': [{'task': self.prompt}], 'async': False})
        for changes in ({'result_fields': []}, {'match': {'mode': []}}, {'blocking_value': 0}):
            with self.assertRaises(ValueError):
                validate({**rows[0], **changes})
