"""Stable provider contracts: scope, native rollback, story closure and config."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import bmad_project, bmad_stories, native_loopback
from scripts.cao_plans import bind_plan_scope, bmad_verification_plan_state
from scripts.cao_providers import detect_sdd, build_bmad_project_command
from scripts.cao_tool_policy import write_targets_native_sdd_output
from scripts.native_obligations import dispatch_patch_state
from tests.test_local_experiment_repairs import DispatchReview


class StableContracts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve()
        self.write('_bmad/config.toml', '[core]\noutput_folder="{project-root}/evidence/bmad"\n')
        self.write('requirement.md', 'Complete both slices.\n')
        self.work = {'id': 'stable', 'requirement': {'path': 'requirement.md', 'sha256': 'r'},
                     'sdd': {'provider': 'bmad', 'native_ref': 'evidence/bmad/specs/spec-demo/SPEC.md'}}
        self.write(self.work['sdd']['native_ref'], '# Canonical epic specification\n')
        self.stories = 'evidence/bmad/specs/spec-demo/stories.yaml'
        self.write(self.stories, '- id: "1"\n  title: First\n  description: >-\n    First slice, with a colon: meaningful.\n'
                   '- id: "2"\n  title: Second\n  description: Second independently reviewable slice.\n')

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def story(self, value, status):
        return self.write(f'evidence/bmad/specs/spec-demo/stories/{value}-slice.md', f'---\nroute: oneshot\nstatus: {status}\n---\n'
            '<frozen-after-approval>\n## Intent\nDeliver the slice.\n</frozen-after-approval>\n## Implementation Notes\nBuilt.\n')

    def test_inventory_is_discovered_and_cannot_finish_after_one_story(self):
        source = self.story('1', 'done')
        self.work['sdd']['route_decision'] = {'provider': 'bmad', 'route': 'direct-build', 'build_route': 'oneshot',
            'requirement_sha256': 'r', 'primary_artifact': source.relative_to(self.repo).as_posix()}
        state = bmad_verification_plan_state(self.repo, self.work)
        self.assertFalse(state['ready'])
        self.assertIn('#2 (backlog)', ' '.join(state['pending_plan_items']))

    def test_bound_inventory_tracks_additions_and_rejects_removal(self):
        scope = bind_plan_scope(self.repo, self.work, [self.stories], {}, False)
        self.work['sdd']['plan_scope'] = scope
        path = self.repo / self.stories
        path.write_text(path.read_text() + '- id: "3"\n  title: Third\n  description: New slice.\n')
        state = bmad_stories.state(self.repo, self.work)
        self.assertIn('3', state['observations'][self.stories]['states'])
        self.work['sdd']['plan_scope'] = state['scope']
        path.write_text('- id: "1"\n  title: First\n  description: Only one.\n')
        self.assertIn('BMAD_STORY_REMOVED', bmad_stories.state(self.repo, self.work)['reason'])

    def test_done_story_needs_preserved_review_evidence(self):
        first = self.story('1', 'done')
        second = self.story('2', 'done')
        self.work['sdd']['route_decision'] = {'primary_artifact': second.relative_to(self.repo).as_posix()}
        self.assertFalse(bmad_stories.state(self.repo, self.work)['ready'])
        relative = first.relative_to(self.repo).as_posix()
        self.work['sdd']['native_units'] = {relative: {'requirement_sha256': 'r',
            'evidence_hashes': {relative: hashlib.sha256(first.read_bytes()).hexdigest()}}}
        self.assertTrue(bmad_stories.state(self.repo, self.work)['ready'])
        first.write_text(first.read_text() + 'Substantive modification.\n')
        self.assertFalse(bmad_stories.state(self.repo, self.work)['ready'])

    def test_ambiguous_missing_and_regressed_story_records_are_not_done(self):
        first = self.story('1', 'done')
        self.work['sdd']['route_decision'] = {'primary_artifact': first.relative_to(self.repo).as_posix()}
        self.story('2', 'in-progress')
        self.assertFalse(bmad_stories.state(self.repo, self.work)['ready'])
        self.write('evidence/bmad/specs/spec-demo/stories/2-duplicate.md', '---\nstatus: done\n---\n')
        self.assertIn('AMBIGUOUS', bmad_stories.state(self.repo, self.work)['reason'])

    def test_bad_inventory_fails_closed(self):
        for value in ('stories:\n  - id: "1"\n', '- id: 1\n  title: A\n  description: B\n',
                      '- id: "1"\n  title: A\n  description: B\n  status: done\n', ''):
            with self.subTest(value=value):
                self.write(self.stories, value)
                self.assertFalse(bmad_stories.state(self.repo, self.work)['ready'])

    def test_sprint_review_gets_native_close_action_and_stays_pending(self):
        self.work['sdd']['native_ref'] = '_bmad-output/spec.md'
        self.write('_bmad-output/spec.md', '---\nstatus: done\n---\n')
        sprint = '_bmad-output/sprint-status.yaml'
        self.write(sprint, 'development_status:\n  1-1-login: review\n')
        self.work['sdd']['plan_scope'] = bind_plan_scope(self.repo, self.work, [], {sprint: ['1-1-login']}, False)
        state = bmad_verification_plan_state(self.repo, self.work)
        self.assertFalse(state['ready'])
        self.assertIn('bmad-code-review', state['next'])
        self.assertIn('matching story key', state['next'])

    def test_custom_output_and_overrides_are_shared_by_ownership_and_gate(self):
        self.write('_bmad/custom/config.user.toml', '[bmm]\nimplementation_artifacts="{project-root}/delivery/records"\n')
        from scripts.cao_provider_workflows import provider_output_dirs, validate_workflow_artifacts
        self.assertIn('delivery/records', provider_output_dirs('bmad', self.repo))
        path = self.write('delivery/records/spec.md', 'Native planning.\n')
        self.assertTrue(validate_workflow_artifacts(self.repo, 'bmad', [str(path)])['ok'])
        self.assertTrue(write_targets_native_sdd_output(self.repo, {'tool_name': 'write', 'tool_input': {'path': str(path)}}))
        self.assertFalse(write_targets_native_sdd_output(self.repo, {'tool_name': 'write', 'tool_input': {'path': str(self.repo / 'src/feature.py')}}))

    def test_output_escape_broad_source_roots_and_cycles_fail_closed(self):
        for value in ('../outside', 'src', '.', '{output_folder}'):
            with self.subTest(value=value):
                self.write('_bmad/config.toml', '[core]\noutput_folder=' + json.dumps(value) + '\n')
                with self.assertRaises(ValueError):
                    bmad_project.output_dirs(self.repo)

    def test_output_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.repo / 'evidence').mkdir(exist_ok=True)
            try:
                (self.repo / 'evidence/bmad').symlink_to(outside, target_is_directory=True)
            except FileExistsError:
                # Existing fixture tree: use an explicitly new linked output instead.
                (self.repo / 'linked').symlink_to(outside, target_is_directory=True)
                self.write('_bmad/config.toml', '[core]\noutput_folder="linked"\n')
            with self.assertRaises(ValueError):
                bmad_project.output_dirs(self.repo)

    def test_partial_install_is_visible_and_installer_targets_stable(self):
        state = detect_sdd(self.repo, 'bmad')
        self.assertTrue(state['detected'])
        self.assertFalse(state['installed'])
        self.assertFalse(state['initialized'])
        self.assertFalse(state['healthy'])
        self.assertIn('_bmad/scripts/render_skill.py', state['missing'])
        command = build_bmad_project_command(argparse.Namespace(tools=None, host='pi'), self.repo)
        self.assertEqual(['npx', 'bmad-method@6.12.4', 'install'], command[:3])

    def test_marker_does_not_authorize_an_automatic_provider_repair(self):
        from scripts.cao import main
        import io
        from contextlib import redirect_stdout
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(['init', '--repo', str(self.repo)])
        self.assertEqual(0, code)
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = main(['bootstrap', '--repo', str(self.repo)])
        out = json.loads(stream.getvalue())
        self.assertEqual(0, code, out)
        self.assertEqual('sdd_select', out['mode'])
        self.assertEqual('explicit_user_choice_only', out['selection_policy'])

    def test_official_stable_renderer_contract(self):
        root = os.environ.get('CAO_BMAD_STABLE_SOURCE')
        if not root:
            self.skipTest('set CAO_BMAD_STABLE_SOURCE to the official v6.12.4 checkout for cross-source verification')
        import importlib.util
        import sys
        source = Path(root).resolve()
        sys.path.insert(0, str(source / 'src/scripts'))
        self.addCleanup(lambda: sys.path.remove(str(source / 'src/scripts')))
        spec = importlib.util.spec_from_file_location('stable_renderer', source / 'src/scripts/render_skill.py')
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        self.write('_bmad/config.toml', 'communication_language="English"\ndocument_output_language="English"\nplanning_artifacts="{project-root}/evidence/planning"\nimplementation_artifacts="{project-root}/evidence/implementation"\n')
        entry = renderer.render(self.repo, source / 'src/bmm-skills/ship/bmad-build')
        from scripts.native_contracts import resolve
        contract = resolve(self.repo, [{'path': entry.relative_to(self.repo).as_posix()}], 'dispatch')
        self.assertEqual(['blind-hunter', 'edge-case-hunter', 'verification-gap'], contract['layers'])
        self.write('_bmad/custom/bmad-build.toml', '\n'.join('[[workflow.review_layers]]\nid="' + layer + '"\ninstruction=""\n'
                   for layer in contract['layers']))
        disabled = renderer.render(self.repo, source / 'src/bmm-skills/ship/bmad-build')
        with self.assertRaisesRegex(ValueError, 'NATIVE_REVIEW_DISABLED'):
            resolve(self.repo, [{'path': disabled.relative_to(self.repo).as_posix()}], 'dispatch')


class NativeLoopback(unittest.TestCase):
    def case(self, disposition='bad_spec'):
        case = DispatchReview()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.work['status'] = 'active'
        case.classify('blind-hunter', [{'id': 'f1', 'verdict': 'medium', 'disposition': disposition,
                                      'evidence': 'Real native design mismatch.'}])
        case.classify('verification-gap', [])
        return case

    def test_loopback_archives_every_layer_and_allows_only_recorded_product_paths(self):
        case = self.case()
        self.assertFalse(dispatch_patch_state(case.repo, case.work, case.snapshot)['ready'])
        result = native_loopback.begin(case.repo, case.work, 'Fix native design', 'Preserve working validation', ['product.py'])
        self.assertTrue(result['ok'])
        self.assertEqual(2, len(case.work['sdd']['native_review_history']))
        self.assertEqual({}, case.work['sdd']['native_reviews'])
        self.assertTrue(native_loopback.write_state(case.repo, case.work,
            {'tool_name': 'write', 'tool_input': {'path': str(case.product)}})['ready'])
        self.assertFalse(native_loopback.write_state(case.repo, case.work,
            {'tool_name': 'write', 'tool_input': {'path': str(case.repo / 'unrelated.py')}})['ready'])

    def test_intent_gap_revokes_old_human_approval(self):
        case = self.case('intent_gap')
        case.work['sdd']['spec_approval'] = {'status': 'approved'}
        native_loopback.begin(case.repo, case.work, 'Resolve original intent', 'Keep working checks', ['product.py'])
        self.assertNotIn('spec_approval', case.work['sdd'])
        self.assertTrue(case.work['sdd']['native_loopback']['human_resolution_required'])

    def test_loopback_is_not_a_general_bypass(self):
        case = self.case()
        before = json.dumps(case.work, sort_keys=True)
        for paths in ([], ['.orchestrator/work.json'], ['_bmad-output/spec.md'], ['.'], ['*.py'], [':(top)product.py']):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                native_loopback.begin(case.repo, case.work, 'Design', 'Keep', paths)
            self.assertEqual(before, json.dumps(case.work, sort_keys=True))

    def test_original_revision_restore_is_allowed_before_replanning_only_for_exact_files(self):
        case = self.case()
        subprocess.run(['git', 'init', '-q'], cwd=case.repo, check=True)
        subprocess.run(['git', 'add', 'product.py'], cwd=case.repo, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'baseline'], cwd=case.repo, check=True)
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=case.repo, text=True).strip()
        case.spec.write_text('---\nbaseline_commit: ' + revision + '\n---\n' + case.spec.read_text())
        native_loopback.begin(case.repo, case.work, 'Design', 'Keep working code', ['product.py'])
        def raw(command):
            return {'tool_name': 'bash', 'cwd': str(case.repo), 'tool_input': {'command': command}}
        self.assertTrue(native_loopback.rollback_write(case.repo, case.work, raw(f'git restore --source {revision} --worktree -- product.py')))
        for command in ('git reset --hard', f'git restore --source {revision} --worktree -- .',
                        f'git restore --source {revision} --worktree -- unrelated.py',
                        f'git restore --source {revision} --worktree -- product.py; touch unrelated.py'):
            self.assertFalse(native_loopback.rollback_write(case.repo, case.work, raw(command)), command)

    def test_no_vcs_restore_requires_exact_recorded_original_bytes(self):
        case = self.case()
        from scripts.native_obligations import digest
        manifest = {'changes': [{'status': 'M', 'path': 'product.py', 'before': {
            'sha256': hashlib.sha256(b'original implementation\n').hexdigest()}}]}
        path = case.write('_bmad-output/manifest.json', json.dumps(manifest))
        for key, run in case.work['sdd']['native_reviews'].items():
            if key != '_contract':
                run['input_manifest'] = {'path': path.relative_to(case.repo).as_posix(), 'content_sha256': digest(manifest)}
        native_loopback.begin(case.repo, case.work, 'Design', 'Keep', ['product.py'])
        raw = {'tool_name': 'write', 'tool_input': {'path': str(case.product), 'content': 'original implementation\n'}}
        self.assertTrue(native_loopback.rollback_write(case.repo, case.work, raw))
        raw['tool_input']['content'] = 'new implementation before human approval\n'
        self.assertFalse(native_loopback.rollback_write(case.repo, case.work, raw))

    def test_replanning_can_extend_literal_file_scope_without_reusing_old_reviews(self):
        case = self.case()
        case.work['execution'] = {'step': 'review', 'phase': 'review'}
        native_loopback.begin(case.repo, case.work, 'Replan', 'Keep original working validation', ['product.py'])
        original = json.dumps(case.work['sdd']['native_review_history'], sort_keys=True)
        native_loopback.begin(case.repo, case.work, 'Extract helper required by corrected design', 'Keep original working validation', ['helpers/parse.py'], extend=True)
        self.assertEqual('implementation', case.work['execution']['step'])
        self.assertEqual(original, json.dumps(case.work['sdd']['native_review_history'], sort_keys=True))
        self.assertTrue(native_loopback.write_state(case.repo, case.work,
            {'tool_name': 'bash', 'cwd': str(case.repo), 'tool_input': {'command': 'mkdir -p helpers'}})['ready'])
        from scripts.state_store import atomic_json
        atomic_json(case.repo / '.orchestrator/work/w.json', case.work)
        from scripts.cao_native_sync import reconcile_native_sdd_state
        self.assertFalse(reconcile_native_sdd_state(case.repo, case.work))
        self.assertEqual('implementation', case.work['execution']['step'])

    def test_new_requirement_revision_cannot_inherit_an_active_loopback_scope(self):
        case = self.case()
        native_loopback.begin(case.repo, case.work, 'Replan', 'Keep', ['product.py'])
        from scripts.completion_policy import reset_requirement
        reset_requirement(case.work, {'path': 'requirement.md', 'sha256': 'new-revision'})
        self.assertNotIn('native_loopback', case.work['sdd'])
        self.assertEqual('superseded', case.work['sdd']['native_loopback_history'][-1]['status'])


class NativePlanningAndUnits(unittest.TestCase):
    def test_full_planning_can_map_duties_but_cannot_omit_pending_spec_tasks(self):
        from tests import test_native_route_sync as fixtures
        case = fixtures.RouteSync()
        case.setUp()
        self.addCleanup(case.doCleanups)
        raw = case.declaration()
        raw.update(route='full-planning', omitted=[], planning_duties=[
            {'name': name, 'artifact': case.spec, 'native_rule': case.rule,
             'reason': 'The native SPEC carries the recorded ' + name + ' responsibility.'}
            for name in ('requirements', 'design', 'execution')])
        out = case.propose(raw)
        self.assertIn('planning_duties', out['route'])
        state = bmad_verification_plan_state(case.repo, case.work())
        self.assertFalse(state['ready'])
        self.assertIn('Implement login', ' '.join(state['pending_plan_items']))

    def test_real_cli_story_handoff_retains_prior_native_review(self):
        from tests import test_protocol_consistency as fixtures
        from tests.helpers import fixture_native_review
        case = fixtures.ProtocolConsistency()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.setup_oneshot()
        first = case.repo / '_bmad-output/specs/spec-epic/stories/1-first.md'
        first.parent.mkdir(parents=True)
        first.write_text(case.spec.read_text())
        inventory = first.parent.parent / 'stories.yaml'
        inventory.write_text('- id: "1"\n  title: First\n  description: First slice.\n- id: "2"\n  title: Second\n  description: Second slice.\n')
        source = case.repo / '_bmad/render/unit/rule.md'
        source.parent.mkdir(parents=True)
        source.write_text("Use route: 'oneshot' for the native fixture.\n")
        def propose(path):
            raw = {'provider': 'bmad', 'provider_version': 'stable-fixture', 'profile': 'light',
                'requirement_sha256': case.read_work(case.wid)['requirement']['sha256'], 'workflow': 'bmad-build',
                'route': 'direct-build', 'build_route': 'oneshot', 'reason': 'One native Build per story.',
                'primary_artifact': path.relative_to(case.repo).as_posix(),
                'required_artifacts': [{'path': path.relative_to(case.repo).as_posix(), 'role': 'spec'}],
                'rules': [{'path': source.relative_to(case.repo).as_posix(), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'quote': source.read_text().strip()}],
                'omitted': [{'name': 'separate project planning', 'reason': 'Epic intent supplies the shared context.', 'replaced_by': path.relative_to(case.repo).as_posix()}]}
            declaration = case.repo / '.orchestrator/inputs' / case.wid / 'route.json'
            declaration.parent.mkdir(parents=True, exist_ok=True)
            declaration.write_text(json.dumps(raw))
            rc, out = case.run_cli('sdd', 'route', 'propose', '--declaration', declaration.relative_to(case.repo))
            self.assertEqual(0, rc, out)
        rc, out = case.run_cli('sdd', 'spec', 'bind', '--spec', first.relative_to(case.repo), '--replace')
        self.assertEqual(0, rc, out)
        propose(first)
        fixture_native_review(case.repo, lambda _repo, *args: case.run_cli(*args), case.wid)
        second = first.with_name('2-second.md')
        second.write_text(first.read_text())
        propose(second)
        work = case.read_work(case.wid)
        self.assertIn(first.relative_to(case.repo).as_posix(), work['sdd']['native_units'])
        self.assertEqual({}, work['sdd']['native_reviews'])
        self.assertTrue(bmad_stories.state(case.repo, work)['ready'])
        from scripts.native_obligations import review_state
        from scripts.cao_snapshot import code_snapshot
        self.assertFalse(review_state(case.repo, work, code_snapshot)['ready'], 'the second story must get its own native review')


if __name__ == '__main__':
    unittest.main()
