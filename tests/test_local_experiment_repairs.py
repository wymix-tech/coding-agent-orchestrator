"""Regressions for the archived local Pi/BMAD experiment, not its final status."""
import hashlib
import json
import shlex
import tempfile
import unittest
from pathlib import Path

from scripts import cao, native_obligations as native
from tests.repair_helpers import RepairCase, ROOT


class ShellAndBaseline(RepairCase):
    def hook(self, command):
        return self.run_cli('host-event', '--host', 'pi', payload={
            'event': 'pre_tool', 'session_id': 'real-local', 'toolName': 'bash',
            'input': {'command': command}})[1]

    def test_logged_wrappers_and_probes_work_before_intake(self):
        launcher = shlex.quote(str(ROOT / 'cao'))
        for command in (f'cd {shlex.quote(str(self.repo))} && {launcher} bootstrap --repo . --host pi 2>&1 | head -100',
                        f'{launcher} list; echo "EXIT=$?"', f'{launcher} --help',
                        'git rev-parse --show-toplevel; git rev-parse --is-inside-work-tree'):
            with self.subTest(command=command):
                self.assertEqual(self.hook(command)['decision'], 'allow')
        for command in (f'{launcher} list 2>&1; touch product.py',
                        f'{launcher} list | tee product.py',
                        'echo "EXIT=$(touch product.py)"', 'git rev-parse --sq-quote evil'):
            self.assertEqual(self.hook(command)['decision'], 'deny', command)

    def test_preparation_helpers_have_bounded_identity_args_and_outputs(self):
        helpers = ['_bmad/scripts/resolve_customization.py', '_bmad/scripts/resolve_config.py',
                   '_bmad/scripts/memlog.py', '.agents/skills/bmad-architecture/scripts/lint_spine.py',
                   '.agents/skills/bmad-sprint-planning/scripts/sprint_plan.py']
        for name in helpers:
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('# installed helper fixture')
        skill = self.repo / '.agents/skills/bmad-prd'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('Installed PRD')
        commands = ['uv run _bmad/scripts/resolve_config.py --project-root .',
                    'uv run _bmad/scripts/resolve_customization.py --project-root . --skill .agents/skills/bmad-prd --key workflow',
                    'uv run _bmad/scripts/memlog.py init --workspace _bmad-output/prd --field topic=login',
                    'uv run .agents/skills/bmad-architecture/scripts/lint_spine.py --workspace _bmad-output/architecture',
                    'uv run .agents/skills/bmad-sprint-planning/scripts/sprint_plan.py generate --epic-file _bmad-output/epics.md --status-file _bmad-output/sprint-status.yaml --stories-dir _bmad-output --project demo --date today']
        for command in commands:
            self.assertEqual(self.hook(command)['decision'], 'allow', command)
        for command in ('uv run _bmad/scripts/memlog.py init --workspace src',
                        'uv run _bmad/scripts/resolve_config.py --project-root /tmp',
                        'uv run --with evil _bmad/scripts/resolve_config.py --project-root .',
                        'uv run _bmad/scripts/memlog.py init --path .orchestrator/index.json'):
            self.assertEqual(self.hook(command)['decision'], 'deny', command)

    def test_same_wrapper_path_change_requires_new_human_baseline_approval(self):
        script = self.repo / 'scripts/verification/test.sh'
        script.parent.mkdir(parents=True)
        script.write_text('#!/bin/sh\nexit 1\n')
        self.run_cli('verification', 'configure', '--test-script', 'scripts/verification/test.sh', '--replace')
        wid = self.start()
        work = self.read_work(wid)
        work['sdd']['native_route_required'] = True
        cao.save_work(self.repo, work)
        script.write_text('#!/bin/sh\nexit 0\n')
        from tests.helpers import seed_project_preparation
        seed_project_preparation(self.repo)
        self.assertEqual(cao.verification_baseline_state(self.repo, self.read_work(wid))['reason'], 'VERIFICATION_STANDARD_CHANGED')
        rc, out = self.run_cli('verification', 'configure', '--test-script', 'scripts/verification/test.sh')
        self.assertTrue(out['acceptance_review_required'], out)
        rc, out = self.run_cli('reopen', '--reanchor', '--reason', 'Agent claims approval')
        self.assertEqual(out['error'], 'VERIFICATION_REVIEW_REQUIRED')

    def test_child_is_not_primary_and_cannot_approve_or_create_handoff(self):
        wid = self.start()
        for event in ('subagent_start', 'prompt_submit', 'turn_end', 'subagent_stop'):
            self.run_cli('host-event', '--host', 'pi', payload={
                'event': event, 'session_id': 'child-native', 'subagent_id': 'child-native',
                'kind': 'subagent', 'parent_session_id': 'parent-native', 'prompt': '批准并继续'})
        session = cao.load_session(self.repo, 'child-native', host='pi')
        self.assertEqual(session['kind'], 'subagent')
        self.assertEqual(session['parent_session_id'], cao.stable_session_id('pi', 'parent-native'))
        self.assertEqual(session['status'], 'ended')
        self.assertFalse(list((self.repo / '.orchestrator/handoffs').rglob('*.json')))

    def test_security_capability_must_be_bound_and_can_reuse_test_wrapper(self):
        from scripts.verification_setup import declared_scripts
        scripts = declared_scripts([], ['checks'], ['checks'])
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]['gates'], ['security_verification', 'tests'])
        work = {'path_assessment': {'required_gates': ['security_verification']},
                'verification': {'results': [{'required': True, 'status': 'passed', 'gates': ['tests']}]}}
        self.assertEqual(cao.gate_state(work, True)['pending'], ['security_verification'])
        work['verification']['results'][0]['gates'].append('security_verification')
        self.assertFalse(cao.gate_state(work, True)['pending'])

    def test_renderer_with_pseudo_step_suffix_is_not_workflow_execution(self):
        self.start(profile='standard')
        rc, out = self.run_cli('sdd', 'workflow', 'select', '--name', 'bmad-build', '--entrypoint', 'bmad-build')
        self.assertEqual(rc, 0, out)
        command = 'uv run _bmad/scripts/render_skill.py --skill .agents/skills/bmad-build -> step-01..step-05'
        rc, out = self.run_cli('sdd', 'workflow', 'execute', '--command', command, '--exit-code', '0')
        self.assertEqual(rc, 2, out)
        self.assertEqual(out['error'], 'RENDERER_IS_NOT_WORKFLOW_EXECUTION')


class DispatchReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.spec = self.write('_bmad-output/spec.md', '<frozen-after-approval>Login timing is equivalent.</frozen-after-approval>\n')
        self.rule = self.write('_bmad/render/build/run/step-04-review.md', '#### Blind (`blind-hunter`)\n#### Gap (`verification-gap`)\n')
        self.product = self.write('product.py', 'old')
        self.work = {'id': 'w', 'requirement': {'path': 'requirement.md', 'sha256': 'r'},
                     'sdd': {'provider': 'bmad', 'route_decision': {'provider': 'bmad', 'build_route': 'dispatch',
                      'requirement_sha256': 'r', 'primary_artifact': '_bmad-output/spec.md',
                      'rules': [{'path': self.rule.relative_to(self.repo).as_posix()}]}, 'native_reviews': {
                          '_contract': {'sources': [self.rule.relative_to(self.repo).as_posix()], 'layers': ['blind-hunter', 'verification-gap']}}}}
        self.snapshot = lambda *args: hashlib.sha256(self.product.read_bytes()).hexdigest()
        self.begin()

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def begin(self):
        self.inputs = native.review_inputs(self.repo, self.work, self.snapshot)
        for layer in native.active_layers(self.repo, self.work):
            self.work['sdd']['native_reviews'][layer] = {'id': layer, 'status': 'running', 'model': 'current',
                                                        'input_sha256': native.digest(self.inputs),
                                                        'execution_binding': {'input_sha256': native.digest(self.inputs),
                                                            'reviewer': {'model': 'current', 'mode': 'subagent', 'session_id': layer + '-child'}},
                                                        'primary_session_ids': ['primary'], 'inputs': self.inputs}

    def classify(self, layer, findings=None, raw=None):
        if findings is None:
            findings = [{'id': 'f1', 'verdict': 'medium', 'disposition': 'patch', 'evidence': 'Observed login bug'}]
        if raw is None:
            raw = [{'id': finding['id'], 'claim': 'Original independent finding'} for finding in findings]
        rawpath = self.write('_bmad-output/' + layer + '-raw.json', json.dumps(raw))
        report = {'run_id': layer, 'input_sha256': native.digest(self.inputs),
                  'reviewer': {'model': 'current', 'mode': 'subagent', 'session_id': layer + '-child'},
                  'findings_file': rawpath.relative_to(self.repo).as_posix(), 'findings': findings}
        path = self.write('_bmad-output/' + layer + '-triage.json', json.dumps(report))
        return native.classify_review(self.repo, self.work, layer, path.relative_to(self.repo), self.snapshot)

    def test_patches_require_all_layers_classified_then_resolution_binds_final_code(self):
        state = native.review_state(self.repo, self.work, self.snapshot)
        self.assertFalse(state['ready'])
        self.assertIn('review classify', state['next'])
        self.assertIn('review resolve', state['next'])
        self.assertIn('ALL layers before product patches', state['next'])
        self.assertNotIn('review submit', state['next'])
        self.assertFalse(native.dispatch_patch_state(self.repo, self.work, self.snapshot)['ready'])
        self.classify('blind-hunter')
        self.assertFalse(native.dispatch_patch_state(self.repo, self.work, self.snapshot)['ready'])
        self.classify('verification-gap', [])
        self.assertTrue(native.dispatch_patch_state(self.repo, self.work, self.snapshot)['ready'])
        self.product.write_text('fixed')
        for layer, ids in [('blind-hunter', ['f1']), ('verification-gap', [])]:
            path = self.write('_bmad-output/' + layer + '-resolved.json', json.dumps({'run_id': layer, 'resolved_ids': ids}))
            native.resolve_review(self.repo, self.work, layer, path.relative_to(self.repo), self.snapshot)
        self.assertTrue(native.review_state(self.repo, self.work, self.snapshot)['ready'])
        self.product.write_text('regression')
        self.assertFalse(native.review_state(self.repo, self.work, self.snapshot)['ready'])

    def test_post_patch_classification_is_rejected(self):
        self.product.write_text('premature patch')
        with self.assertRaisesRegex(ValueError, 'NATIVE_REVIEW_STALE'):
            self.classify('blind-hunter')

    def test_missing_or_duplicate_raw_findings_cannot_be_hidden_by_grouping(self):
        for raw in ([{'id': 'f1'}, {'id': 'missing'}], [{'id': 'f1'}, {'id': 'f1'}]):
            with self.assertRaisesRegex(ValueError, 'FINDING_MAPPING_INVALID'):
                self.classify('blind-hunter', raw=raw)

    def test_frozen_violation_cannot_be_deferred_and_loopback_blocks_patches(self):
        finding = {'id': 'f1', 'verdict': 'low', 'disposition': 'defer', 'scope': 'outside-intent',
                   'violates_frozen_intent': True, 'evidence': 'Frozen timing promise fails for cost 12'}
        with self.assertRaisesRegex(ValueError, 'FROZEN_INTENT_LOOPBACK'):
            self.classify('blind-hunter', [finding])
        finding['disposition'] = 'intent_gap'
        self.classify('blind-hunter', [finding])
        self.classify('verification-gap', [])
        self.assertEqual(native.dispatch_patch_state(self.repo, self.work, self.snapshot)['reason'], 'NATIVE_REVIEW_LOOPBACK_REQUIRED')

    def test_full_planning_draft_cannot_be_certified_final(self):
        decision = self.work['sdd']['route_decision']
        decision.update(route='full-planning', required_artifacts=[{'path': '_bmad-output/prd.md', 'role': 'prd'}])
        path = self.write('_bmad-output/prd.md', '---\nstatus: draft\n---\nReal requirements')
        self.assertFalse(native.planning_state(self.repo, self.work)['ready'])
        path.write_text('---\nstatus: final\n---\nReal requirements')
        self.assertTrue(native.planning_state(self.repo, self.work)['ready'])

    def test_spec_activity_is_visible_but_never_completes_unfinished_plan(self):
        self.spec.write_text('---\nstatus: in-progress\n---\n')
        plan = {'canonical': 'ready'}
        observation = cao.bmad_progress_observation(self.repo, self.work, plan)
        self.assertEqual(observation['canonical'], 'in_progress')
        self.assertEqual(observation['progress_conflict']['plan_state'], 'ready')
        self.spec.write_text('---\nstatus: done\n---\n')
        self.assertNotIn('canonical', cao.bmad_progress_observation(self.repo, self.work, plan))


class ImplementationHandoff(unittest.TestCase):
    def test_primary_cannot_replace_available_native_implementation_child(self):
        from tests.test_native_route_sync import RouteSync
        fixture = RouteSync()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = '_bmad/render/bmad-build/step-03-implement.md'
        text = '# Implement\nLaunch a subagent with no prior conversation context.\nRead the SPEC and implement it.\n'
        fixture.write(source, text)
        fixture.write('_bmad/render/bmad-build/step-04-review.md', '#### Blind (`blind-hunter`)\nFollow step-05-present.md\n')
        fixture.write('_bmad/render/bmad-build/step-05-present.md', 'Finalize the native workflow.\n')
        fixture.propose()
        fixture.approve()
        spec = fixture.repo / fixture.spec
        spec.write_text(spec.read_text().replace('status: \'draft\'', "status: 'in-progress'\nbaseline_commit: NO_VCS"))
        self.assertEqual(fixture.product_write()['decision'], 'deny')
        fixture.hook('post_tool', toolName='read', input={'path': source}, content=[{'type': 'text', 'text': text}])
        self.assertEqual(fixture.product_write()['decision'], 'deny')
        fixture.run_cli('host-event', '--host', 'pi', raw={
            'event': 'subagent_start', 'kind': 'subagent', 'subagent_id': 'implementation-child',
            'session_id': 'implementation-child', 'parent_session_id': 'primary-fixture'})
        reply = fixture.run_cli('host-event', '--host', 'pi', raw={
            'event': 'pre_tool', 'kind': 'subagent', 'subagent_id': 'implementation-child',
            'session_id': 'implementation-child', 'parent_session_id': 'primary-fixture',
            'host_tools': ['read', 'write', 'subagent'], 'toolName': 'write',
            'input': {'path': 'src/auth.py', 'content': 'implemented'}})
        self.assertEqual(reply['decision'], 'allow', reply)
        self.assertEqual(fixture.work()['sdd']['native_implementation']['mode'], 'subagent')
