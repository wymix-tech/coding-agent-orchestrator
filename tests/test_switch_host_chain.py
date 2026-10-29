"""Real host hooks wrap task switches; unfinished execution stays visible."""
import json
from argparse import Namespace
from unittest.mock import patch
from tests import test_continuity_operations as fixtures
from scripts.runtime_host import observe, _meaningful
from scripts.host_effects import effects
from scripts.cao_work_store import load_work
from scripts.cao_sessions import load_session
from scripts.effect_resolution import settle
from tests.repair_helpers import ROOT
import unittest


class SwitchHostChain(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OperationsTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.repo = self.f.repo
        self.first = self.f.start()
        self.target = self.f.queue()
        self.hook('session_start')

    def hook(self, event, **kw):
        rc, out = self.f.run_cli('host-event', '--host', 'pi', payload={
            'event': event, 'session_id': 'current-pi', **kw})
        self.assertEqual(0, rc, out)
        return out

    def test_absolute_switch_under_real_hooks_roundtrips_session_and_preserves_late_result(self):
        self.hook('pre_tool', toolName='write', toolCallId='old', input={'path': '.orchestrator/requirements/note.md'})
        self.hook('post_tool', toolName='write', toolCallId='old', isError=True)
        for index, target in enumerate((self.target, self.first)):
            command = f"'{ROOT / 'cao'}' switch --to {target}"
            out = self.hook('pre_tool', toolName='bash', toolCallId=f'switch-{index}', input={'command': command})
            self.assertEqual('allow', out['decision'])
            rc, result = self.f.run_cli('switch', '--to', target)
            self.assertEqual(0, rc, result)
            self.hook('post_tool', toolName='bash', toolCallId=f'switch-{index}', input={'command': command})
            self.assertEqual(target, load_session(self.repo, 'current-pi', 'pi')['work_item_id'])
        self.assertFalse(effects(self.repo, self.first, True))
        self.hook('post_tool', toolName='write', toolCallId='old', isError=True)
        self.assertFalse(effects(self.repo, self.target))

    def test_real_effect_blocks_with_exact_identity_and_resolved_calls_are_idempotent(self):
        raw = {'toolName': 'custom_plugin', 'toolCallId': 'pending', 'input': {'operation': 'execute'}}
        observe(self.repo, load_work(self.repo, self.first), raw, 'pi', 'current-pi', 'pre_tool', {'decision': 'allow'})
        # Unknown tools may be classified read by legacy policy: create a known mutating call.
        raw['toolName'] = 'write'
        observe(self.repo, load_work(self.repo, self.first), raw, 'pi', 'current-pi', 'pre_tool', {'decision': 'allow'})
        rc, out = self.f.run_cli('switch', '--to', self.target)
        self.assertEqual(2, rc, out)
        self.assertEqual('pending', out['unresolved_effects'][0]['tool_call_id'])
        self.assertEqual('current-pi', out['unresolved_effects'][0]['session_id'])
        rc, listing = self.f.run_cli('runtime', 'effects', '--work', self.first, '--pending')
        self.assertEqual(0, rc, listing)
        self.assertEqual(1, len(listing['items']))
        observe(self.repo, load_work(self.repo, self.first), raw, 'pi', 'current-pi', 'post_tool', {'decision': 'allow'})
        result = settle(self.repo, load_work(self.repo, self.first), Namespace(host='pi', session='current-pi', call='pending'))
        self.assertEqual('already_resolved', result['status'])
        self.assertEqual(0, self.f.run_cli('switch', '--to', self.target)[0])

    def test_trusted_launchers_only_and_mixed_shell_effects_remain_observed(self):
        for command in [f"'{ROOT / 'cao'}' switch --to x", f'sh "{ROOT / "cao"}" delegation show']:
            self.assertFalse(_meaningful({'toolName': 'bash', 'input': {'command': command}}, self.repo))
        for command in ['/tmp/cao switch --to x', f'{ROOT / "cao"} status; touch product.py']:
            self.assertTrue(_meaningful({'toolName': 'bash', 'input': {'command': command}}, self.repo))
        for suffix in ['delegation show', 'delegation --help']:
            self.assertEqual('allow', self.hook('pre_tool', toolName='bash', input={'command': f'{ROOT / "cao"} {suffix}'})['decision'])

    def test_compact_preview_preserves_full_snapshot_on_request(self):
        rc, small = self.f.run_cli('switch', '--to', self.target, '--check')
        rc2, full = self.f.run_cli('switch', '--to', self.target, '--check', '--full-json')
        self.assertEqual((0, 0), (rc, rc2))
        self.assertNotIn('sdd', small['source'])
        self.assertIn('sdd', full['source'])
        self.assertEqual(full['blockers'], small['blockers'])
