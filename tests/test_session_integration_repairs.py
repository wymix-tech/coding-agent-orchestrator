"""Regressions from the 2026-10-16 14:54 Pi/BMAD session."""
import copy
import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import cao, native_obligations as native
from scripts.native_route import approve_baseline_response, bind_approval_question
from tests.repair_helpers import RepairCase
from tests import test_native_route_sync as route_fixtures


class ApprovalChoices(unittest.TestCase):
    def test_bound_recommendation_badges_keep_original_evidence_and_reject_other_contexts(self):
        work = {'id': 'w', 'verification_review': {'id': 'r', 'work_item_id': 'w',
                'status': 'pending', 'config_sha256': 'h', 'contexts': [{'host': 'pi', 'session_id': 'p'}]}}
        question = {'toolName': 'ask_user_question', 'toolCallId': 'q'}
        bind_approval_question(work['verification_review'], 'w', question, 'pi', 'p')
        for label in ('批准验证基线 (Recommended)', '批准验证基线（推荐）', 'approve verification baseline (recommended)'):
            for valid in (True, False):
                current = copy.deepcopy(work)
                result = approve_baseline_response(current, {**question, 'toolCallId': 'q' if valid else 'old',
                    'details': {'answers': [{'selectedOptions': [{'label': label}]}]}}, 'post_tool', 'pi', 'p', 'h', 1)
                self.assertEqual(valid, bool(result))
                if valid:
                    self.assertEqual(label, result['evidence']['response'])
                    self.assertEqual(native.digest(label), result['evidence']['response_sha256'])
        for label in ('批准验证基线 (Recommended)', '批准验证基线，但先别执行', '不批准验证基线'):
            self.assertIsNone(approve_baseline_response(copy.deepcopy(work), {'prompt': label},
                                                       'prompt_submit', 'pi', 'p', 'h', 1))


class LoggedReadCommands(RepairCase):
    def event(self, command):
        return self.run_cli('host-event', '--host', 'pi', payload={
            'event': 'pre_tool', 'session_id': 'session', 'toolName': 'bash', 'input': {'command': command}})[1]

    def test_logged_read_probes_are_allowed_before_work_but_mutations_are_not(self):
        for command in ('ls -la && git log --oneline -5 2>&1 | head -10 && git ls-files 2>/dev/null | head -40',
                        'date +%Y-%m-%d', 'printenv PI_MODEL', 'git -C . ls-files',
                        'wc -m -w _bmad-output/spec.md'):
            with self.subTest(command=command):
                self.assertEqual('read', cao.tool_effect({'toolName': 'bash', 'input': {'command': command}}, self.repo)['effect'])
                self.assertEqual('allow', self.event(command)['decision'])
        for command in ('date --set=tomorrow', 'git ls-files > .orchestrator/index.json',
                        'diff a b > product.py', 'date +%s; touch product.py',
                        "python3 -c \"open('product.py', 'w').write('code')\""):
            self.assertEqual('deny', self.event(command)['decision'], command)

    def test_diff_inputs_and_exclusions_are_not_write_targets_and_temp_links_cannot_escape(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'review.diff'
            command = f'diff -ruN --new-file /tmp/empty . -x .orchestrator > {shlex.quote(str(dest))}'
            effect = cao.tool_effect({'toolName': 'bash', 'input': {'command': command}}, self.repo)
            self.assertEqual([str(dest)], effect['targets'])
            self.assertEqual('allow', self.event(command)['decision'])
            self.assertEqual('deny', self.event(command + '; touch product.py')['decision'])
            dest.symlink_to(self.repo / '.orchestrator/index.json')
            self.assertEqual('deny', self.event(command)['decision'])


class PiChildProvisioning(unittest.TestCase):
    def test_new_project_default_preserves_inherited_global_child_extensions(self):
        with tempfile.TemporaryDirectory() as d:
            repo, agent_dir = Path(d) / 'project', Path(d) / 'global-agent'
            repo.mkdir()
            cao.save_json(agent_dir / 'settings.json', {'subagents': {'defaultSubagentOnlyExtensions': ['/global-hook.ts']}})
            result = subprocess.CompletedProcess([], 0, '', '')
            with patch.dict(cao.os.environ, {'PI_CODING_AGENT_DIR': str(agent_dir)}), \
                    patch("scripts.cao_instructions.command_exists", return_value=True), \
                    patch.object(cao.subprocess, 'run', return_value=result):
                self.assertEqual('installed', cao.install_host_adapter(repo, 'pi')['status'])
            extensions = json.loads((repo / '.pi/settings.json').read_text())['subagents']['defaultSubagentOnlyExtensions']
            self.assertEqual('/global-hook.ts', extensions[0])
            self.assertEqual(2, len(extensions))

    def test_install_merges_child_hooks_without_overwriting_existing_settings(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            original = {'packages': ['other-package'], 'theme': 'existing', 'subagents': {
                'defaultSubagentOnlyExtensions': ['/existing.ts'],
                'agentOverrides': {'custom': {'tools': ['read']}}}}
            cao.save_json(repo / '.pi/settings.json', original)
            result = subprocess.CompletedProcess([], 0, '', '')
            with patch("scripts.cao_instructions.command_exists", return_value=True), patch.object(cao.subprocess, 'run', return_value=result):
                for _ in range(2):
                    self.assertEqual('installed', cao.install_host_adapter(repo, 'pi')['status'])
            saved = json.loads((repo / '.pi/settings.json').read_text())
            hook = str(cao.package_root_dir() / 'hosts/pi/coding-orchestrator.ts')
            self.assertEqual(['/existing.ts', hook], saved['subagents']['defaultSubagentOnlyExtensions'])
            self.assertEqual(original['packages'], saved['packages'])
            self.assertEqual(original['subagents']['agentOverrides'], saved['subagents']['agentOverrides'])
            self.assertTrue(cao.host_adapter_status(repo, 'pi')['child_hooks_installed'])


class ReviewLaunchAndRecovery(unittest.TestCase):
    def setUp(self):
        self.t = route_fixtures.RouteSync()
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        self.t.propose(); self.t.approve()
        self.source = '_bmad/render/bmad-build/step-04-review.md'
        self.t.write(self.source, '#### Blind (`blind-hunter`)\n')
        self.t.env.update(CAO_HOST='pi', PI_SESSION_ID='primary-fixture', PI_MODEL='deepseek-flash', PI_PROVIDER='deepseek')

    def begin(self, model='deepseek/deepseek-flash', *extra):
        return self.t.run_cli('sdd', 'review', 'begin', '--layer', 'blind-hunter',
                              '--model', model, '--instructions', self.source, *extra)

    def test_qualified_model_is_normalized_before_launch_and_other_models_fail_early(self):
        first = self.begin()
        self.assertEqual('deepseek-flash', first['review']['model'])
        again = self.begin()
        self.assertEqual(first['review']['id'], again['review']['id'])
        self.assertTrue(again['reused'])
        restarted = self.begin('deepseek-flash', '--restart')
        self.assertNotEqual(first['review']['id'], restarted['review']['id'])
        out = self.t.run_cli('sdd', 'review', 'begin', '--layer', 'blind-hunter', '--model', 'other/deepseek-flash',
                             '--instructions', self.source, expected=2)
        self.assertEqual('NATIVE_REVIEW_MODEL_MISMATCH', out['error'])
        self.assertEqual('deepseek-flash', out['expected_model'])
        self.assertEqual(restarted['review']['id'], self.t.work()['sdd']['native_reviews']['blind-hunter']['id'])

    def test_native_markdown_survives_attach_classify_patch_and_idempotent_resume(self):
        t = self.t
        ticket = self.begin()['review']
        t.hook('subagent_start', subagent_id='review-child')
        t.env['PI_SESSION_ID'] = 'review-child'
        t.run_cli('bootstrap', '--host', 'pi')
        self.assertEqual('subagent', cao.load_session(t.repo, 'review-child', host='pi')['kind'])
        attached = t.run_cli('sdd', 'review', 'attach', '--layer', 'blind-hunter', '--run', ticket['id'],
                            '--input-sha256', ticket['input_sha256'], '--model', 'deepseek/deepseek-flash')['review']
        t.env['PI_SESSION_ID'] = 'primary-fixture'
        raw = '# Findings\n\n- Real bug\n  - Nested proof\n\n```python\n- example\n```\n\n- Another bug\n'
        path = '_bmad-output/reviews/raw.md'
        t.write(path, raw)
        before = (t.repo / '.orchestrator/work' / (t.wid + '.json')).read_bytes()
        parsed = t.run_cli('sdd', 'review', 'findings', '--source', path)
        self.assertEqual(['finding-1', 'finding-2'], [f['id'] for f in parsed['findings']])
        self.assertIn('Nested proof', parsed['findings'][0]['content'])
        self.assertEqual(before, (t.repo / '.orchestrator/work' / (t.wid + '.json')).read_bytes())
        report = {'run_id': ticket['id'], 'input_sha256': ticket['input_sha256'],
                  'reviewer': attached['execution_binding']['reviewer'], 'findings_file': path,
                  'findings': [{'id': f['id'], 'verdict': 'medium', 'disposition': 'patch', 'evidence': 'Confirmed current code'}
                               for f in parsed['findings']]}
        t.write('_bmad-output/triage.json', json.dumps(report))
        t.run_cli('sdd', 'review', 'classify', '--layer', 'blind-hunter', '--report', '_bmad-output/triage.json')
        t.write('product.py', 'patched = True\n')
        again = self.begin()
        self.assertEqual(ticket['id'], again['review']['id'])
        self.assertTrue(again['reused'])
        self.assertIn('Do not begin or classify again', again['next'])
        t.write('_bmad-output/resolve.json', json.dumps({'run_id': ticket['id'], 'resolved_ids': ['finding-1', 'finding-2']}))
        t.run_cli('sdd', 'review', 'resolve', '--layer', 'blind-hunter', '--report', '_bmad-output/resolve.json')
        self.assertEqual(raw, (t.repo / path).read_text())
        self.assertTrue(native.review_state(t.repo, t.work(), cao.code_snapshot)['ready'])


class NativeTriagePolicy(unittest.TestCase):
    def setUp(self):
        from tests.test_local_experiment_repairs import DispatchReview
        self.t = DispatchReview()
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)

    def test_absent_from_scope_is_not_a_valid_defer_and_native_low_reject_is_supported(self):
        finding = {'id': 'f1', 'verdict': 'low', 'disposition': 'defer', 'scope': 'outside-intent',
                   'violates_frozen_intent': False, 'evidence': 'Missing from acceptance list'}
        with self.assertRaisesRegex(ValueError, 'DEFER_REASON_REQUIRED'):
            self.t.classify('blind-hunter', [finding])
        finding.update(disposition='reject', reject_reason='unlikely-use-nontrivial-fix',
                       evidence='Rare operator-only configuration; fix adds a new guard and policy')
        self.assertEqual('classified', self.t.classify('blind-hunter', [finding])['status'])

    def test_declared_frozen_contradiction_blocks_patching_even_with_empty_findings(self):
        t = self.t
        t.classify('blind-hunter', [])
        path = t.repo / '_bmad-output/blind-hunter-triage.json'
        report = json.loads(path.read_text())
        report['intent_conflicts'] = ['Frozen Always forbids all credentials in responses but login matrix requires a credential response']
        path.write_text(json.dumps(report))
        t.begin()
        native.classify_review(t.repo, t.work, 'blind-hunter', path.relative_to(t.repo), t.snapshot)
        t.classify('verification-gap', [])
        self.assertEqual('NATIVE_REVIEW_LOOPBACK_REQUIRED', native.dispatch_patch_state(t.repo, t.work, t.snapshot)['reason'])
        self.assertIn('human', native.review_state(t.repo, t.work, t.snapshot)['next'])

    def test_long_fence_with_short_fences_inside_preserves_separate_findings(self):
        path = self.t.write('_bmad-output/raw.md', '- First issue\n  ````markdown\n  ```\n  ~~~\n  ````\n\n- Second independent issue\n')
        findings = native.load_findings(path)
        self.assertEqual(2, len(findings))
        self.assertIn('Second independent issue', findings[1]['content'])

    def test_stale_running_ticket_requests_new_round_but_classified_patch_keeps_ticket(self):
        t = self.t
        t.product.write_text('changed after begin')
        self.assertIn('stale', native.review_state(t.repo, t.work, t.snapshot)['next'])
        t.begin()
        t.classify('blind-hunter')
        t.classify('verification-gap', [])
        t.product.write_text('legitimate patches')
        self.assertIn('Do not begin or classify again', native.review_state(t.repo, t.work, t.snapshot)['next'])


class InstallPlanWithoutDependencies(unittest.TestCase):
    def test_missing_uv_keeps_plan_inspectable_but_blocks_real_installation(self):
        t = route_fixtures.RouteSync()
        t.setUp()
        self.addCleanup(t.doCleanups)
        t.env['PATH'] = ''
        out = t.run_cli('sdd', 'install', '--provider', 'speckit', '--integration', 'copilot')
        self.assertEqual('confirmation_required', out['status'])
        self.assertFalse(out['prerequisites']['ok'])
        self.assertIn('copilot', out['install_plan'][-1]['command'])
        blocked = t.run_cli('sdd', 'install', '--provider', 'speckit', '--integration', 'copilot', '--yes', expected=2)
        self.assertEqual('SDD_PREREQUISITE_FAILED', blocked['error'])
        self.assertFalse((t.repo / '.specify').exists())
