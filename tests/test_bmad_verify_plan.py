"""Requirement-wide BMAD execution must finish before CAO verification starts."""
import copy
import shlex
import sys
import tempfile
from pathlib import Path
from tests.repair_helpers import RepairCase
from scripts import cao
from scripts.dashboard import build_dashboard_overview


class BmadVerifyPlanTests(RepairCase):
    def prepare(self, suffix='yml', profile='light'):
        self.wid = self.start(profile=profile)
        self.path = f'_bmad-output/implementation-artifacts/sprint-status.{suffix}'
        self.target = self.repo / self.path
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.write({'epic-1': 'in-progress', '1-1-first': 'done', '1-2-second': 'in_process',
                    'epic-1-retrospective': 'optional', 'epic-9': 'backlog', '9-1-unrelated': 'backlog'})

    def write(self, states):
        self.target.write_text('project: fixture\nmetadata:\n  outside: done\ndevelopment_status:\n' +
                               ''.join(f'  {key}: {state}\n' for key, state in states.items()))

    def bind(self, *ids):
        args = ['sdd', 'plan', 'bind']
        for item in ids:
            args.extend(['--plan-item', self.path + '#' + item])
        rc, out = self.run_cli(*args)
        self.assertEqual(rc, 0, out)
        return out

    def verify(self):
        if not hasattr(self, 'marker'):
            output = tempfile.TemporaryDirectory()
            self.addCleanup(output.cleanup)
            self.marker = Path(output.name) / 'ran-verification'
        command = shlex.join([sys.executable, '-c', f"from pathlib import Path; Path({str(self.marker)!r}).write_text('ran')"])
        return self.run_cli('verify', '--script', command)

    def test_both_suffixes_block_before_script_and_do_not_change_work(self):
        for suffix in ('yml', 'yaml'):
            with self.subTest(suffix=suffix):
                # Existing Work is resumed, not recreated by the plan operation.
                if not hasattr(self, 'wid'):
                    self.prepare(suffix)
                else:
                    self.target.unlink()
                    self.path = self.path.removesuffix('yml') + 'yaml'
                    self.target = self.repo / self.path
                    self.write({'1-1-first': 'done', '1-2-second': 'in-progress'})
                    work = cao.load_work(self.repo, self.wid)
                    work['sdd'].pop('plan_scope', None)
                    cao.save_work(self.repo, work)
                self.bind('1-1-first', '1-2-second')
                before = self.read_work(self.wid)
                rc, out = self.verify()
                self.assertEqual((rc, out['error']), (2, 'BMAD_PLAN_NOT_DONE'))
                self.assertEqual(len(out['verification_plan']['pending_plan_items']), 1)
                self.assertFalse(self.marker.exists())
                self.assertEqual(self.read_work(self.wid), before)

    def test_unbound_shared_plan_and_no_plan_declaration_do_not_bypass(self):
        self.prepare()
        for mode in (None, 'none', 'unresolved'):
            work = cao.load_work(self.repo, self.wid)
            work['sdd']['plan_scope'] = {'mode': mode, 'plans': []}
            cao.save_work(self.repo, work)
            rc, out = self.verify()
            self.assertEqual((rc, out['error']), (2, 'BMAD_PLAN_SCOPE_REQUIRED'))
        self.assertFalse(self.marker.exists())

    def test_all_thirteen_stories_done_allows_verify_unrelated_backlog_ignored(self):
        self.prepare('yaml', 'standard')
        states = {f'1-{n}-story': 'done' if n == 1 else 'backlog' for n in range(1, 14)}
        states.update({'9-1-unrelated': 'backlog', 'epic-1-retrospective': 'optional'})
        self.write(states)
        self.bind(*[f'1-{n}-story' for n in range(1, 14)])
        self.assertEqual(len(self.verify()[1]['verification_plan']['pending_plan_items']), 12)
        for key in list(states):
            if key.startswith('1-'):
                states[key] = 'done'
        self.write(states)
        rc, out = self.verify()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_work(self.wid)['execution']['step'], 'verification')
        self.assertTrue(self.marker.exists())

    def test_epic_selector_tracks_new_stories_and_retains_deleted_story_identity(self):
        self.prepare()
        self.bind('epic-1')
        self.write({'epic-1': 'in-progress', '1-1-first': 'done', '1-2-second': 'done',
                    '1-3-added': 'review', '9-1-unrelated': 'backlog'})
        self.assertEqual(self.verify()[1]['error'], 'BMAD_PLAN_NOT_DONE')
        self.write({'epic-1': 'done', '1-1-first': 'done', '1-3-added': 'done'})
        self.assertEqual(self.verify()[1]['error'], 'PLAN_ITEM_MISSING')

    def test_multiple_epics_must_all_complete(self):
        self.prepare()
        self.write({'epic-1': 'done', '1-1-first': 'done', 'epic-2': 'in-progress', '2-1-last': 'review'})
        self.bind('epic-1', 'epic-2')
        self.assertEqual(self.verify()[1]['error'], 'BMAD_PLAN_NOT_DONE')
        self.write({'epic-1': 'done', '1-1-first': 'done', 'epic-2': 'done', '2-1-last': 'done'})
        self.assertEqual(self.verify()[0], 0)

    def test_story_optional_unknown_and_in_process_are_not_done(self):
        self.prepare()
        self.bind('1-1-first', '1-2-second')
        for state in ('optional', 'in_process', 'in-progress', 'review', 'ready-for-dev', 'backlog', 'unknown'):
            self.write({'1-1-first': 'done', '1-2-second': state})
            self.assertEqual(self.verify()[1]['error'], 'BMAD_PLAN_NOT_DONE', state)

    def test_quotes_comments_supported_duplicate_and_malformed_status_fail_closed(self):
        self.prepare()
        self.bind('1-1-first', '1-2-second')
        self.target.write_text('development_status:\n  1-1-first: "done" # reviewed\n  1-2-second: \'done\'\n')
        self.assertEqual(self.verify()[0], 0)
        for text in ('development_status:\n  1-1-first: done\n  1-1-first: done\n  1-2-second: done\n',
                     'development_status:\n  1-1-first: done\n  1-2-second: [done]\n',
                     'other:\n  1-1-first: done\n  1-2-second: done\n'):
            self.target.write_text(text)
            self.assertEqual(self.verify()[1]['error'], 'PLAN_STATUS_INVALID')

    def test_bound_file_deletion_or_product_symlink_blocks(self):
        self.prepare()
        self.bind('1-1-first', '1-2-second')
        text = self.target.read_text()
        self.target.unlink()
        self.assertEqual(self.verify()[1]['error'], 'PLAN_SOURCE_MISSING')
        product = self.repo / 'business.yml'
        product.write_text(text.replace('in_process', 'done'))
        self.target.symlink_to(product)
        self.assertNotEqual(self.verify()[0], 0)

    def test_light_without_sprint_plan_still_runs_verify(self):
        self.start()
        self.assertEqual(self.verify()[0], 0)

    def test_plan_bind_adds_scope_without_narrowing_or_acceptance_reanchor(self):
        self.prepare()
        before = copy.deepcopy(self.read_work(self.wid)['acceptance'])
        self.bind('1-1-first', '1-2-second')
        self.bind('1-1-first')
        work = self.read_work(self.wid)
        self.assertEqual(work['acceptance'], before)
        self.assertEqual(work['sdd']['plan_scope']['plans'][0]['item_ids'], ['1-1-first', '1-2-second'])
        self.assertEqual(self.verify()[1]['error'], 'BMAD_PLAN_NOT_DONE')

    def test_scope_or_story_regression_invalidates_prior_verification(self):
        self.prepare()
        self.bind('epic-1')
        self.write({'epic-1': 'done', '1-1-first': 'done', '1-2-second': 'done'})
        self.assertEqual(self.verify()[0], 0)
        work = cao.load_work(self.repo, self.wid)
        inputs = cao.verification_inputs(self.repo, work, cao.config(self.repo))
        self.write({'epic-1': 'done', '1-1-first': 'done', '1-2-second': 'done', '1-3-new': 'backlog'})
        self.assertNotEqual(inputs, cao.verification_inputs(self.repo, work, cao.config(self.repo)))
        self.assertEqual(cao.completion_status(self.repo, work)['reason'], 'BMAD_PLAN_NOT_DONE')

    def test_single_spec_done_does_not_override_aggregate_pending_state(self):
        self.prepare()
        spec = self.target.parent / 'spec-a.md'
        spec.write_text('---\nstatus: done\n---\n')
        work = cao.load_work(self.repo, self.wid)
        work['sdd']['native_ref'] = spec.relative_to(self.repo).as_posix()
        cao.save_work(self.repo, work)
        cao.reconcile_native_sdd_state(self.repo, work)
        # The unbound shared tracker does not allow this SPEC to certify the
        # whole requirement; repair a cursor previously set by an older CAO.
        work = cao.load_work(self.repo, self.wid)
        work['execution']['step'] = 'review'
        cao.save_work(self.repo, work)
        self.bind('epic-1')
        overview = build_dashboard_overview(self.repo)
        view = next(w for w in overview['work_items'] if w['id'] == self.wid)
        self.assertEqual(self.read_work(self.wid)['sdd']['native_state']['canonical'], 'in_progress')
        self.assertFalse(view['native_sync']['verification_plan']['ready'])
        self.assertEqual(view['execution']['current_phase'], 'implementation')

    def test_explicit_workflow_rebinding_cannot_reduce_existing_requirement_scope(self):
        self.prepare()
        self.bind('1-1-first', '1-2-second')
        work = cao.load_work(self.repo, self.wid)
        work['sdd']['plan_scope'] = cao.bind_plan_scope(self.repo, work, [self.path],
                                                       {self.path: ['1-1-first']}, False)
        self.assertEqual(len(cao.provider_pending_plan_items(self.repo, work)), 1)

    def test_plan_regression_during_verification_makes_result_stale(self):
        self.prepare()
        self.bind('epic-1')
        self.write({'epic-1': 'done', '1-1-first': 'done', '1-2-second': 'done'})
        work = cao.load_work(self.repo, self.wid)
        inputs = cao.verification_inputs(self.repo, work, cao.config(self.repo))
        self.write({'epic-1': 'in-progress', '1-1-first': 'done', '1-2-second': 'review'})
        result = cao.commit_verification(self.repo, self.wid, inputs, [],
                                         [{'required': True, 'status': 'passed'}], 'config', cao.now())
        self.assertEqual(result['status'], 'stale')
        self.assertNotEqual(self.read_work(self.wid)['execution']['step'], 'verification')

    def test_binding_unstarted_stories_does_not_claim_implementation_started(self):
        self.prepare()
        self.write({'epic-1': 'backlog', '1-1-first': 'backlog', '1-2-second': 'ready-for-dev'})
        self.bind('epic-1')
        self.assertEqual(self.read_work(self.wid)['sdd']['native_state']['canonical'], 'ready')
        self.assertEqual(self.verify()[1]['error'], 'BMAD_PLAN_NOT_DONE')
