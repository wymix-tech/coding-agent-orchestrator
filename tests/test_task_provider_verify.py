"""OpenSpec changes and Spec Kit user stories cannot be partially verified as a Work."""
import copy
import shlex
import sys
import tempfile
from pathlib import Path
from tests.repair_helpers import RepairCase
from scripts import cao
from scripts.dashboard import build_dashboard_overview


class TaskProviderVerifyTests(RepairCase):
    def prepare(self, provider='openspec', profile='light'):
        if not hasattr(self, "wid"):
            self.wid = self.start(profile=profile)
        self.provider = provider
        self.folder = ('openspec/changes/login' if provider == 'openspec' else 'specs/001-login')
        self.root = self.repo / self.folder
        self.root.mkdir(parents=True, exist_ok=True)
        self.spec = self.root / ('specs/auth/spec.md' if provider == 'openspec' else 'spec.md')
        self.spec.parent.mkdir(parents=True, exist_ok=True)
        self.spec.write_text('# Login\n## Acceptance\n- Valid credentials create a session.\n')
        self.tasks = self.root / 'tasks.md'
        self.tasks.write_text('# Phase 1\n- [x] T001 Shared setup\n## US1\n- [x] T002 [US1] Login\n## US2\n- [ ] T003 [US2] Logout\n')
        work = cao.load_work(self.repo, self.wid)
        work['sdd'] = {'provider': provider, 'native_ref': None, 'artifacts': []}
        work['execution']['step'] = 'path-selected'
        work['verification'] = None
        cao.save_work(self.repo, work)
        rc, out = self.run_cli('sdd', 'spec', 'bind', '--spec', self.spec.relative_to(self.repo), '--criteria-section', 'Acceptance')
        self.assertEqual(rc, 0, out)

    def verify(self, configured=False):
        if not hasattr(self, 'marker'):
            directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
            self.marker = Path(directory.name) / 'ran'
        if configured:
            return self.run_cli('verify')
        command = shlex.join([sys.executable, '-c', f"from pathlib import Path; Path({str(self.marker)!r}).write_text('ran')"])
        return self.run_cli('verify', '--script', command)

    def bind(self, *paths):
        args = ['sdd', 'plan', 'bind']
        for path in paths:
            args += ['--plan', path]
        rc, out = self.run_cli(*args)
        self.assertEqual(rc, 0, out)
        return out

    def test_partial_feature_blocks_both_config_and_cli_before_any_script(self):
        for provider in ('openspec', 'speckit'):
            with self.subTest(provider=provider):
                self.prepare(provider)
                before = self.read_work(self.wid)
                for configured in (False, True):
                    rc, out = self.verify(configured)
                    self.assertEqual((rc, out.get('error')), (2, 'SDD_PLAN_NOT_DONE'), out)
                    self.assertEqual(len(out['verification_plan']['pending_plan_items']), 1)
                self.assertEqual(self.read_work(self.wid), before)
                self.assertFalse(self.marker.exists())

    def test_every_story_and_shared_task_checked_then_verify_runs(self):
        for provider in ('openspec', 'speckit'):
            self.prepare(provider)
            self.tasks.write_text(self.tasks.read_text().replace('[ ]', '[X]'))
            self.assertEqual(self.verify()[0], 0)
            self.assertEqual(self.read_work(self.wid)['execution']['step'], 'verification')

    def test_multiple_associated_plans_aggregate_not_just_last_feature(self):
        for provider in ('openspec', 'speckit'):
            self.prepare(provider)
            second = ('openspec/changes/logout/tasks.md' if provider == 'openspec' else 'specs/002-logout/tasks.md')
            path = self.repo / second;path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('- [x] Completed second feature\n')
            self.bind(self.tasks.relative_to(self.repo), second)
            work = cao.load_work(self.repo, self.wid)
            work['sdd']['native_ref'] = second
            cao.save_work(self.repo, work)
            self.assertEqual(self.verify()[1].get('error'), 'SDD_PLAN_NOT_DONE')
            work = self.read_work(self.wid)
            self.assertEqual(work['sdd']['native_state']['canonical'], 'in_progress')

    def test_unrelated_feature_and_checklist_do_not_block(self):
        for provider in ('openspec', 'speckit'):
            self.prepare(provider)
            self.tasks.write_text(self.tasks.read_text().replace('[ ]', '[x]'))
            other = self.root.parent / 'unrelated/tasks.md';other.parent.mkdir(exist_ok=True);other.write_text('- [ ] unrelated\n')
            checklists = self.root / 'checklists';checklists.mkdir(exist_ok=True)
            (checklists / 'requirements.md').write_text('- [ ] Reviewer owned quality criterion\n')
            self.assertEqual(self.verify()[0], 0)

    def test_native_markers_plus_ordered_nested_and_blank_boxes_do_not_hide_open_tasks(self):
        self.prepare()
        for line in ('+ [ ] pending', '1. [ ] pending', '1) [ ] pending', '  - [ ] nested', '- [] pending', '- [~] working', '- [?] unknown', '- [ ]'):
            with self.subTest(line=line):
                self.tasks.write_text('# All existing tasks done\n- [x] T001 Shared setup\n- [x] T002 [US1] Login\n- [x] T003 [US2] Logout\n' + line + '\n')
                self.assertEqual(self.verify()[1].get('error'), 'SDD_PLAN_NOT_DONE')

    def test_empty_or_link_only_task_file_cannot_certify_completion(self):
        self.prepare()
        for text in ('# Tasks\n', '- [A](https://example.com)\n', ''):
            self.tasks.write_text(text)
            self.assertEqual(self.verify()[1].get('error'), 'PLAN_TASKS_UNRECOGNIZED')

    def test_no_plan_does_not_bypass_existing_associated_tasks(self):
        self.prepare()
        work = cao.load_work(self.repo, self.wid)
        work['sdd']['plan_scope'] = {'mode': 'none', 'plans': []}
        cao.save_work(self.repo, work)
        self.assertEqual(self.verify()[1].get('error'), 'SDD_PLAN_NOT_DONE')

    def test_deleted_auto_associated_plan_and_product_symlink_fail_closed(self):
        self.prepare()
        contents = self.tasks.read_text()
        self.tasks.unlink()
        self.assertEqual(self.verify()[1].get('error'), 'PLAN_SOURCE_MISSING')
        product = self.repo / 'product.md';product.write_text(contents.replace('[ ]', '[x]'))
        self.tasks.symlink_to(product)
        self.assertNotEqual(self.verify()[0], 0)

    def test_new_task_after_verification_invalidates_and_blocks_done(self):
        self.prepare('speckit')
        self.tasks.write_text(self.tasks.read_text().replace('[ ]', '[x]'))
        self.assertEqual(self.verify()[0], 0)
        work = cao.load_work(self.repo, self.wid)
        inputs = cao.verification_inputs(self.repo, work, cao.config(self.repo))
        self.tasks.write_text(self.tasks.read_text()+'- [ ] T004 [US2] added\n')
        self.assertNotEqual(inputs, cao.verification_inputs(self.repo, work, cao.config(self.repo)))
        self.assertEqual(cao.completion_status(self.repo, work)['reason'], 'SDD_PLAN_NOT_DONE')

    def test_scope_additions_during_verify_produce_stale_result(self):
        self.prepare()
        self.tasks.write_text(self.tasks.read_text().replace('[ ]', '[x]'))
        work = cao.load_work(self.repo, self.wid)
        inputs = cao.verification_inputs(self.repo, work, cao.config(self.repo))
        second = self.root.parent / 'new/tasks.md';second.parent.mkdir(exist_ok=True);second.write_text('- [ ] New related change\n')
        self.bind(second.relative_to(self.repo))
        result = cao.commit_verification(self.repo, self.wid, inputs, [], [{'required': True, 'status': 'passed'}], 'config', cao.now())
        self.assertEqual(result['status'], 'stale')

    def test_custom_openspec_tracked_glob_includes_new_shards_and_keeps_deleted_paths(self):
        self.prepare()
        self.tasks.unlink()
        scope = cao.load_work(self.repo, self.wid)
        scope['sdd'].pop('plan_scope', None);cao.save_work(self.repo, scope)
        folder = self.root / 'work';folder.mkdir()
        first = folder / 'first.md';first.write_text('- [x] First shard\n')
        self.bind(self.folder + '/work/*.md')
        self.assertEqual(self.verify()[0], 0)
        second = folder / 'second.md';second.write_text('+ [ ] New shard\n')
        self.assertEqual(self.verify()[1].get('error'), 'SDD_PLAN_NOT_DONE')
        first.unlink()
        self.assertEqual(self.verify()[1].get('error'), 'PLAN_SOURCE_MISSING')

    def test_explicit_repeat_binding_does_not_reduce_scope_or_reanchor(self):
        self.prepare()
        second = self.root.parent / 'second/tasks.md';second.parent.mkdir(exist_ok=True);second.write_text('- [x] second\n')
        before = copy.deepcopy(self.read_work(self.wid)['acceptance'])
        self.bind(self.tasks.relative_to(self.repo), second.relative_to(self.repo))
        self.bind(second.relative_to(self.repo))
        self.assertEqual(self.verify()[1].get('error'), 'SDD_PLAN_NOT_DONE')
        self.assertEqual(self.read_work(self.wid)['acceptance'], before)

    def test_invalid_pattern_or_cross_provider_source_is_rejected(self):
        self.prepare('speckit')
        wrong = self.repo / 'openspec/changes/other/tasks.md';wrong.parent.mkdir(parents=True);wrong.write_text('- [x] Other provider\n')
        for path in ('openspec/changes/other/tasks.md', 'specs/../product/*.md'):
            self.assertNotEqual(self.run_cli('sdd', 'plan', 'bind', '--plan', path)[0], 0)

    def test_no_native_tasks_keeps_light_minimal(self):
        self.prepare()
        self.tasks.unlink()
        work = cao.load_work(self.repo, self.wid);work['sdd'].pop('plan_scope', None);cao.save_work(self.repo, work)
        self.assertEqual(self.verify()[0], 0)

    def test_dashboard_does_not_project_last_done_plan_as_whole_requirement_review(self):
        self.prepare()
        second = self.root.parent / 'done/tasks.md';second.parent.mkdir(exist_ok=True);second.write_text('- [x] done\n')
        self.bind(second.relative_to(self.repo))
        work = cao.load_work(self.repo, self.wid);work['execution']['step'] = 'review';work['sdd']['native_ref'] = second.relative_to(self.repo).as_posix();cao.save_work(self.repo, work)
        view = next(w for w in build_dashboard_overview(self.repo)['work_items'] if w['id'] == self.wid)
        self.assertEqual(view['execution']['current_phase'], 'implementation')
        self.assertFalse(view['native_sync']['verification_plan']['ready'])

    def test_removed_task_or_duplicate_native_task_id_cannot_shrink_requirement(self):
        for provider in ('openspec', 'speckit'):
            self.prepare(provider)
            self.tasks.write_text('- [x] T001 Shared setup\n- [x] T002 [US1] Login\n')
            self.assertEqual(self.verify()[1].get('error'), 'PLAN_TASK_REMOVED')
            self.tasks.write_text('- [x] T001 Shared setup\n- [x] T001 Duplicate\n- [x] T003 Logout\n')
            self.assertEqual(self.verify()[1].get('error'), 'PLAN_TASK_ID_DUPLICATE')

    def test_checkbox_parser_matches_official_edge_case_contract(self):
        # OpenSpec 9111a7654d7800391459431fff4eaf66e33a3d2e,
        # src/utils/task-progress.ts: parseTaskLines. Spec Kit's template uses
        # standard - [ ] T001 [US1] tasks covered by the same parser.
        content = '- [ x ] done\r\n+ [~] pending\n1) [] empty\n  * [X] nested\n- [A](doc)\n- [1][ref]\n- [ ](...)\n- [x]done\n- [ ]\n'
        tasks = cao._checkbox_tasks(content)
        self.assertEqual([(task['done'], task['description']) for task in tasks],
                         [(True, 'done'), (False, 'pending'), (False, 'empty'), (True, 'nested'),
                          (False, '(...)'), (True, 'done'), (False, '')])
