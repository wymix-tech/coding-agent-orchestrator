"""Project-use regressions from demo2; no product implementation assertions."""
import copy
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from scripts import cao
from scripts.native_route import approve_baseline_response, bind_approval_question
from scripts.native_obligations import validate_reviewer
from scripts.review_binding import attach_reviewer
from scripts.startup_request import capture_request, pending_request, consume_request
from tests import test_native_route_sync as fixtures
from tests import test_provider_restart as restart_fixtures


class StartupFlow(unittest.TestCase):
    def setUp(self):
        self.case = restart_fixtures.ProviderRestartTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.repo = self.case.repo

    def test_original_request_survives_install_and_real_restart_then_is_consumed(self):
        t = self.case
        t.hook('session_start')
        t.hook('prompt_submit', prompt='开发登录系统；使用 Python；其余选型安装后确认')
        initial = pending_request(self.repo)
        out = t.cli('bootstrap', '--host', 'pi')[1]
        self.assertEqual('sdd_select', out['mode'])
        self.assertIn('AFTER', out['startup_order'])
        self.assertNotIn('ONE bundled question', t.hook('session_start')['additional_context'])
        t.hook('prompt_submit', session_id='child', kind='subagent', subagent_id='child', prompt='不要保存子会话')
        self.assertEqual(initial, pending_request(self.repo))
        t.install()
        self.assertEqual('host_restart_required', t.cli('bootstrap', '--host', 'pi')[1]['mode'])
        t.hook('session_start', session_id='restarted', reason='startup')
        t.hook('prompt_submit', session_id='restarted', prompt='继续')
        out = t.cli('bootstrap', '--host', 'pi')[1]
        self.assertEqual(initial, out['pending_request'])
        self.assertIn(initial['prompt'], t.hook('session_start', session_id='restarted')['additional_context'])
        req = self.repo / '.orchestrator/requirements/001-login.md'
        req.parent.mkdir(exist_ok=True)
        req.write_text('# Login\nPython; other choices pending.\n')
        code, out = t.cli('start', '--requirement', str(req))
        self.assertEqual(0, code, out)
        self.assertFalse(pending_request(self.repo))
        self.assertEqual(out['work_item']['id'], json.loads((self.repo / '.orchestrator/startup-request.json').read_text())['work_item_id'])

    def test_completed_intake_can_capture_another_request_without_overwriting_pending(self):
        capture_request(self.repo, 'first', 'pi', 's1')
        capture_request(self.repo, 'second', 'pi', 's2')
        self.assertEqual('first', pending_request(self.repo)['prompt'])
        consume_request(self.repo, 'w')
        capture_request(self.repo, 'second', 'pi', 's2')
        self.assertEqual('second', pending_request(self.repo)['prompt'])


class BaselineReplies(unittest.TestCase):
    def work(self):
        return {'id': 'w', 'verification_review': {'id': 'b', 'work_item_id': 'w', 'status': 'pending',
                'config_sha256': 'hash', 'contexts': [{'host': 'pi', 'session_id': 's'}]}}

    def test_bound_question_approves_once_but_unbound_stale_and_wrong_context_do_not(self):
        original = self.work()
        question = {'tool_name': 'ask_user_question', 'toolCallId': 'q'}
        bind_approval_question(original['verification_review'], 'w', question, 'pi', 's')
        answer = {**question, 'details': {'answers': [{'answer': '批准验证基线'}]}}
        for mode in ('valid', 'unbound', 'old-hash', 'other-session', 'other-work', 'tool-error'):
            w = copy.deepcopy(original); raw = copy.deepcopy(answer); identity = 's'; current_hash = 'hash'
            if mode == 'unbound': raw['toolCallId'] = 'other'
            elif mode == 'old-hash': current_hash = 'new'
            elif mode == 'other-session': identity = 'another'
            elif mode == 'other-work': w['id'] = 'other-work'
            elif mode == 'tool-error': raw['isError'] = True
            result = approve_baseline_response(w, raw, 'post_tool', 'pi', identity, current_hash, 1)
            self.assertEqual(mode == 'valid', bool(result), mode)
            if result:
                self.assertEqual('q', result['evidence']['tool_call_id'])
                self.assertIsNone(approve_baseline_response(w, raw, 'post_tool', 'pi', identity, current_hash, 2))

    def test_plain_approval_requires_no_competing_request_and_continue_is_never_approval(self):
        for text, competing, accepted in [('批准', False, True), ('继续', False, False),
                                          ('批准', True, False), ('批准验证基线', True, True)]:
            w = self.work()
            if competing: w['sdd'] = {'spec_approval': {'status': 'pending'}}
            self.assertEqual(accepted, bool(approve_baseline_response(
                w, {'prompt': text}, 'prompt_submit', 'pi', 's', 'hash', 1)))


class IndependentReviewBinding(unittest.TestCase):
    def fixture(self):
        from scripts.native_obligations import digest
        inputs = {'work_id': 'w', 'code_snapshot': 'code'}
        run = {'id': 'r', 'status': 'running', 'inputs': inputs, 'input_sha256': digest(inputs),
               'model': 'm', 'primary_session_ids': ['parent'], 'existing_session_ids': ['old']}
        session = {'id': 'new', 'work_item_id': 'w', 'status': 'active', 'kind': 'subagent'}
        return inputs, run, session

    def test_binding_rejects_old_report_parent_existing_session_and_changed_code(self):
        inputs, run, session = self.fixture()
        report = {'reviewer': {'mode': 'subagent', 'session_id': 'child', 'model': 'm'}}
        with self.assertRaisesRegex(ValueError, 'EXECUTION_UNBOUND'):
            validate_reviewer(run, report)
        legacy = copy.deepcopy(run)
        legacy.pop('existing_session_ids')
        with self.assertRaisesRegex(ValueError, 'RESTART_REQUIRED'):
            attach_reviewer(legacy, inputs, ('pi', 'child'), session, 'm', 'r', run['input_sha256'])
        for mode in ('parent', 'old-session', 'changed-code', 'old-ticket', 'wrong-model', 'other-work'):
            record = copy.deepcopy(run); state = dict(session); current = dict(inputs)
            identity = ('pi', 'child'); ticket = 'r'; model = 'm'
            if mode == 'parent': identity = ('pi', 'parent')
            elif mode == 'old-session': state['id'] = 'old'
            elif mode == 'changed-code': current['code_snapshot'] = 'changed'
            elif mode == 'old-ticket': ticket = 'old'
            elif mode == 'wrong-model': model = 'other'
            elif mode == 'other-work': state['work_item_id'] = 'other'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                attach_reviewer(record, current, identity, state, model, ticket, run['input_sha256'])
        attach_reviewer(run, inputs, ('pi', 'child'), session, 'm', 'r', run['input_sha256'])
        validate_reviewer(run, report)
        report['reviewer']['session_id'] = 'someone-else'
        with self.assertRaisesRegex(ValueError, 'EXECUTION_UNBOUND'):
            validate_reviewer(run, report)


class LiveWorkNavigation(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.RouteSync()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def change_baseline(self):
        return self.case.run_cli('verification', 'configure', '--test-script', 'changed-suite', '--replace')

    def test_baseline_question_reanchors_and_handoff_exposes_real_pending_action(self):
        t = self.case
        self.change_baseline()
        t.hook('pre_tool', tool_name='ask_user_question', toolCallId='baseline-q',
               input={'questions': [{'question': '批准验证基线？'}]})
        out = t.run_cli('handoff', '--summary', 'Waiting for baseline')
        handoff = out['handoff']
        self.assertEqual('human', handoff['next_action']['type'])
        self.assertIn(t.work()['verification_review']['id'], handoff['next_action']['instruction'])
        self.assertIn(handoff['next_action']['instruction'], handoff['pending'])
        t.hook('post_tool', tool_name='ask_user_question', toolCallId='baseline-q',
               details={'answers': [{'answer': '批准验证基线'}]})
        self.assertEqual('approved', t.work()['verification_review']['status'])
        t.run_cli('reopen', '--reanchor', '--reason', 'User reviewed')
        self.assertTrue(cao.verification_baseline_state(t.repo, t.work())['ready'])

    def test_replaced_baseline_does_not_accept_old_question(self):
        t = self.case
        self.change_baseline()
        t.hook('pre_tool', tool_name='ask_user_question', toolCallId='old-q',
               input={'questions': [{'question': '批准验证基线？'}]})
        t.run_cli('verification', 'configure', '--test-script', 'newer-suite', '--replace')
        t.hook('post_tool', tool_name='ask_user_question', toolCallId='old-q',
               details={'answers': [{'answer': '批准验证基线'}]})
        self.assertEqual('pending', t.work()['verification_review']['status'])

    def test_security_capability_is_requested_at_assessment_before_implementation(self):
        t = self.case
        out = t.run_cli('path', 'assess', '--profile', 'standard', '--risk-flag', 'security', '--reason', 'Auth')
        self.assertEqual('VERIFICATION_GATE_UNBOUND', out['next_action']['reason'])
        self.assertEqual('verification_setup', out['guidance']['stage'])
        self.assertIn('security-script', out['next_action']['instruction'])
        self.assertIn('security-script', t.run_cli('next')['action']['instruction'])
        self.assertEqual('allow', t.hook('pre_tool', tool_name='write', tool_input={'path': 'scripts/verification/security.sh'})['decision'])
        self.assertEqual('allow', t.hook('pre_tool', tool_name='write', tool_input={'path': t.spec})['decision'])

    def test_dispatch_guidance_is_visible_during_execution_and_begin_uses_right_protocol(self):
        t = self.case
        t.propose(); t.approve()
        text = cao.agent_guidance(t.repo, t.work(), next_action={'stage': 'execution', 'reason': ''})
        self.assertTrue(any(path.endswith('bmad-review-dispatch.md') for path in text['read_before_action']))
        t.write('_bmad/render/bmad-build/step-04-review.md', '#### Blind (`blind-hunter`)\n')
        out = t.run_cli('sdd', 'review', 'begin', '--layer', 'blind-hunter', '--model', 'm',
                        '--instructions', '_bmad/render/bmad-build/step-04-review.md')
        self.assertIn('native Markdown/JSON', out['next'])
        self.assertIn('review classify', out['next'])
        self.assertNotIn('review submit', out['next'])
        self.assertNotIn('resolved: true', out['next'])
        self.assertIn('review attach', out['reviewer_start'])

    def test_attach_cli_requires_real_new_independent_session_and_unchanged_snapshot(self):
        t = self.case
        t.propose(); t.approve()
        t.write('_bmad/render/bmad-build/step-04-review.md', '#### Blind (`blind-hunter`)\n#### Gap (`verification-gap`)\n')
        ticket = t.run_cli('sdd', 'review', 'begin', '--layer', 'blind-hunter', '--model', 'm',
                           '--instructions', '_bmad/render/bmad-build/step-04-review.md')['review']
        other = t.run_cli('sdd', 'review', 'begin', '--layer', 'verification-gap', '--model', 'm',
                          '--instructions', '_bmad/render/bmad-build/step-04-review.md')['review']
        args = ('sdd', 'review', 'attach', '--layer', 'blind-hunter', '--run', ticket['id'],
                '--input-sha256', ticket['input_sha256'], '--model', 'm')
        t.env.update(CAO_HOST='pi', PI_SESSION_ID='primary-fixture', PI_MODEL='m')
        self.assertEqual('NATIVE_REVIEW_INDEPENDENT_SESSION_REQUIRED', t.run_cli(*args, expected=2)['error'])
        t.hook('subagent_start', subagent_id='new-reviewer')
        t.env['PI_SESSION_ID'] = 'new-reviewer'
        attached = t.run_cli(*args)['review']['execution_binding']
        self.assertEqual('new-reviewer', attached['reviewer']['session_id'])
        out = t.run_cli('sdd', 'review', 'attach', '--layer', 'verification-gap', '--run', other['id'],
                        '--input-sha256', other['input_sha256'], '--model', 'm', expected=2)
        self.assertEqual('NATIVE_REVIEW_SESSION_REUSED', out['error'])
        t.write('product.py', 'changed = True\n')
        # Shared integrity detects changed round inputs before identity attachment.
        self.assertEqual('NATIVE_REVIEW_STALE', t.run_cli(*args, expected=2)['error'])


class RelativeLauncher(unittest.TestCase):
    def test_literal_cwd_resolves_real_launcher_but_not_same_named_script_or_mixed_write(self):
        root = pathlib.Path(cao.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            (repo / '.agents/skills').mkdir(parents=True)
            (repo / '.agents/skills/cao').symlink_to(root, target_is_directory=True)
            command = './.agents/skills/cao/cao bootstrap'
            self.assertTrue(cao.readonly_shell(command, repo))
            raw = {'toolName': 'bash', 'cwd': str(repo), 'input': {'command': f'cd {repo} && {command}'}}
            self.assertEqual('read', cao.tool_effect(raw, repo)['effect'])
            (repo / 'cao').write_text('unrelated')
            self.assertFalse(cao.readonly_shell('./cao bootstrap', repo))
            self.assertFalse(cao.readonly_shell(command + ' && echo changed > product.py', repo))
            self.assertFalse(cao.readonly_shell(command + ' > .orchestrator/index.json', repo))
