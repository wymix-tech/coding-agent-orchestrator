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
            command = f"cd '{self.repo}' && '{ROOT / 'cao'}' switch --to {target}"
            out = self.hook('pre_tool', toolName='bash', toolCallId=f'switch-{index}', input={'command': command})
            self.assertEqual('allow', out['decision'])
            rc, result = self.f.run_cli('switch', '--to', target)
            self.assertEqual(0, rc, result)
            self.hook('post_tool', toolName='bash', toolCallId=f'switch-{index}', input={'command': command})
            self.assertEqual(target, load_session(self.repo, 'current-pi', 'pi')['work_item_id'])
        self.assertFalse(effects(self.repo, self.first, True))
        self.hook('post_tool', toolName='write', toolCallId='old', isError=True)
        self.assertFalse(effects(self.repo, self.target))

    def test_shell_aliases_and_command_variants_switch_the_observed_session(self):
        from scripts.cao_tool_policy import SHELL_TOOLS
        from scripts.control_context import identity
        for index, tool in enumerate(sorted(SHELL_TOOLS)):
            with self.subTest(tool=tool):
                target = self.target if index % 2 == 0 else self.first
                option = f'--to={target}' if index % 2 == 0 else f'--to {target}'
                command = f'{ROOT / "cao"} status && {ROOT / "cao"} switch {option}'
                field = 'cmd' if index % 2 == 0 else 'command'
                self.assertEqual('allow', self.hook('pre_tool', toolName=tool,
                    toolCallId=f'alias-{index}', input={field: command})['decision'])
                self.assertEqual(('pi', 'current-pi'), identity(self.repo, target))
                rc, result = self.f.run_cli('switch', f'--to={target}')
                self.assertEqual(0, rc, result)
                self.assertEqual(target, load_session(self.repo, 'current-pi', 'pi')['work_item_id'])
                self.hook('post_tool', toolName=tool, toolCallId=f'alias-{index}')
                self.assertIsNone(identity(self.repo, target))
                self.assertFalse(effects(self.repo, target, True))

    def test_output_flags_before_and_after_switch_never_create_self_effects(self):
        from scripts.cli_output import OUTPUT_FLAGS
        for index, (flag, prefix) in enumerate((flag, prefix) for flag in sorted(OUTPUT_FLAGS) for prefix in (True, False)):
            with self.subTest(flag=flag, prefix=prefix):
                target = self.target if index % 2 == 0 else self.first
                args = ['switch', '--to', target]
                args = [flag, *args] if prefix else [*args, flag]
                command = f'{ROOT / "cao"} ' + ' '.join(args)
                self.assertEqual('allow', self.hook('pre_tool', toolName='bash',
                    toolCallId=f'flag-{index}', input={'command': command})['decision'])
                self.assertFalse(effects(self.repo, self.first, True))
                rc, result = self.f.run_cli(*args)
                self.assertEqual(0, rc, result)
                self.assertEqual(target, load_session(self.repo, 'current-pi', 'pi')['work_item_id'])
                self.hook('post_tool', toolName='bash', toolCallId=f'flag-{index}')

    def test_control_context_uses_switch_grammar_and_rejects_other_repositories(self):
        from scripts.control_context import switch_targets, identity
        launcher = ROOT / 'cao'
        for suffix in ('switch --to=x --check', 'switch --to=x --help',
                       'switch recover --operation x', 'status --reason switch --to x',
                       'switch --to=x --repo /tmp/other-project', 'switch --to=x; touch product.py'):
            with self.subTest(suffix=suffix):
                self.assertFalse(switch_targets(self.repo, f'{launcher} {suffix}', self.repo))
        self.assertEqual([self.target], switch_targets(self.repo,
            f'cd {self.repo} && {launcher} switch --to={self.target}', ROOT))
        raw = {'toolName': 'shell_command', 'toolCallId': 'workdir',
               'input': {'command': f'{launcher} switch --to={self.target}', 'workdir': str(self.repo)}}
        observe(self.repo, load_work(self.repo, self.first), raw, 'pi', 'current-pi', 'pre_tool', {'decision': 'allow'})
        with patch('scripts.control_context.time.time', return_value=0):
            with self.assertRaisesRegex(ValueError, 'INVOCATION_EXPIRED'):
                identity(self.repo, self.target)
        self.assertEqual(('pi', 'current-pi'), identity(self.repo, self.target))

    def test_stale_session_can_use_flagged_switch_to_reattach(self):
        self.assertEqual(0, self.f.run_cli('switch', '--to', self.target)[0])
        command = f'{ROOT / "cao"} --full-json switch --to={self.first}'
        self.assertEqual('allow', self.hook('pre_tool', toolName='exec_command',
            toolCallId='reattach', input={'command': command})['decision'])
        rc, result = self.f.run_cli('--full-json', 'switch', f'--to={self.first}')
        self.assertEqual(0, rc, result)
        self.assertEqual(self.first, load_session(self.repo, 'current-pi', 'pi')['work_item_id'])

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

    def test_invocation_identity_survives_observation_off_and_ambiguous_calls_require_selection(self):
        from scripts.control_context import identity
        raw = {'toolName': 'bash', 'toolCallId': 'control-one', 'input': {'command': f'{ROOT / "cao"} switch --to {self.target}'}}
        with patch('scripts.runtime_host.settings', return_value={'host_observation': 'off'}):
            observe(self.repo, load_work(self.repo, self.first), raw, 'pi', 'current-pi', 'pre_tool', {'decision': 'allow'})
        self.assertEqual(('pi', 'current-pi'), identity(self.repo, self.target))
        raw2 = {**raw, 'toolCallId': 'control-two'}
        observe(self.repo, load_work(self.repo, self.first), raw2, 'pi', 'other-session', 'pre_tool', {'decision': 'allow'})
        with self.assertRaisesRegex(ValueError, 'AMBIGUOUS'):
            identity(self.repo, self.target)
        # Result notifications can omit the original arguments.
        observe(self.repo, load_work(self.repo, self.first), {'toolName': 'bash', 'toolCallId': 'control-two'}, 'pi', 'other-session', 'post_tool', {'decision': 'allow'})
        self.assertEqual(('pi', 'current-pi'), identity(self.repo, self.target))

    def test_observed_child_blocks_switch_until_ended(self):
        from scripts.cao_sessions import register_session, end_session
        register_session(self.repo, load_work(self.repo, self.first), 'child', 'pi',
                         event='subagent_start', kind='subagent', parent_session_id='current-pi')
        rc, result = self.f.run_cli('switch', '--to', self.target)
        self.assertEqual(2, rc, result)
        self.assertIn('SOURCE_CHILD_SESSION_UNRESOLVED', result['blockers'])
        end_session(self.repo, 'child', 'pi', 'observed completion')
        self.assertEqual(0, self.f.run_cli('switch', '--to', self.target)[0])

    def test_settlement_requires_evidence_for_exact_call(self):
        raw = {'toolName': 'write', 'toolCallId': 'unresolved', 'input': {'path': 'feature.py'}}
        observe(self.repo, load_work(self.repo, self.first), raw, 'pi', 'current-pi', 'pre_tool', {'decision': 'allow'})
        path = self.repo / '_bmad-output/inspection.json'
        path.parent.mkdir(exist_ok=True)
        args = Namespace(host='pi', session='current-pi', call='unresolved', reason='inspected',
                         evidence='_bmad-output/inspection.json', resolution='observed-stopped')
        path.write_text('unrelated successful test log')
        with self.assertRaisesRegex(ValueError, 'IDENTITY_REQUIRED'):
            settle(self.repo, load_work(self.repo, self.first), args)
        record = dict(host='pi', session_id='current-pi', tool_call_id='wrong',
                      resolution='observed-stopped', observations='Inspected the plugin execution handle and confirmed termination.')
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'IDENTITY_MISMATCH'):
            settle(self.repo, load_work(self.repo, self.first), args)
        record['tool_call_id'] = 'unresolved'
        path.write_text(json.dumps(record))
        settle(self.repo, load_work(self.repo, self.first), args)
        self.assertFalse(effects(self.repo, self.first, True))

    def test_plugin_registration_remains_reachable_before_classification(self):
        import io
        from contextlib import redirect_stdout
        from scripts.cao_host_events import host_event
        for operation in ('show', '--help', 'register --manifest adapter.json'):
            payload = {'event': 'pre_tool', 'session_id': 'current-pi', 'toolName': 'bash',
                       'toolCallId': operation, 'input': {'command': f'{ROOT / "cao"} delegation {operation}'}}
            out = io.StringIO()
            with patch('sys.stdin', io.StringIO(json.dumps(payload))), redirect_stdout(out), patch(
                    'scripts.native_obligations.dispatch_patch_state', return_value={'ready': False, 'reason': 'NATIVE_REVIEW_CLASSIFY_REQUIRED'}):
                host_event(Namespace(repo=str(self.repo), host='pi'))
            self.assertEqual('allow', json.loads(out.getvalue())['decision'])

    def test_compact_preview_preserves_full_snapshot_on_request(self):
        rc, small = self.f.run_cli('switch', '--to', self.target, '--check')
        rc2, full = self.f.run_cli('switch', '--to', self.target, '--check', '--full-json')
        self.assertEqual((0, 0), (rc, rc2))
        self.assertNotIn('sdd', small['source'])
        self.assertIn('sdd', full['source'])
        self.assertEqual(full['blockers'], small['blockers'])
