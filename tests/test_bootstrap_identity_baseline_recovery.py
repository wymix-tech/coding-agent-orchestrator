"""Regression for the 2026-10-27 Pi bootstrap/clarification session."""
import argparse
import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import project_setup
from scripts.cao_defaults import DEFAULT_CONFIG
from scripts.identity_interview import collect
from scripts.project_context import commit_context
from scripts.project_sources import observe_prompt, user_evidence
from scripts.project_baseline import materialize, readiness
from scripts.setup_rules import scaffold
from scripts.state_store import atomic_json
from tests.repair_helpers import RepairCase


class BootstrapPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        atomic_json(self.repo / '.orchestrator/config.json', copy.deepcopy(DEFAULT_CONFIG))
        values = {'language': 'Python', 'build_tool': 'setuptools', 'test_framework': 'unittest'}
        proof = observe_prompt(self.repo, 'Python setuptools unittest', 'pi', 'initial')
        commit_context(self.repo, {'scopes': {'root': {'paths': ['.'], 'technology': {
            k: {'value': v, 'status': 'accepted'} for k, v in values.items()}}},
            'evidence': [user_evidence(self.repo, proof, values)]}, None)

    def interview(self, field=None, value=None, evidence=None, **kwargs):
        args = dict(scope='root', rule='python-unittest', fields=None, field=field,
                    value=value, evidence=evidence)
        args.update(kwargs)
        return collect(self.repo, argparse.Namespace(**args))

    def test_interview_persists_one_answer_per_field_across_sessions(self):
        self.assertEqual(self.interview()['next_field'], 'name')
        first = observe_prompt(self.repo, 'acme-product', 'pi', 'first-session')
        answer = self.interview('name', 'acme-product', first)
        self.assertEqual(answer['next_field'], 'import_name')
        self.assertEqual(self.interview()['values']['name'], 'acme-product')
        invalid = observe_prompt(self.repo, 'bad/import', 'pi', 'first-session')
        with self.assertRaisesRegex(ValueError, 'PROJECT_IDENTITY_INVALID'):
            self.interview('import_name', 'bad/import', invalid)
        self.assertEqual(self.interview()['next_field'], 'import_name')
        second = observe_prompt(self.repo, 'acme_import', 'codex', 'new-session')
        self.assertTrue(self.interview('import_name', 'acme_import', second)['ready'])
        with self.assertRaisesRegex(ValueError, 'FIELD_NOT_COVERED'):
            self.interview('name', 'invented-name', first)
        self.assertEqual(self.interview()['values']['name'], 'acme-product')

    def test_interview_is_generic_for_explicit_stack_fields(self):
        fields = ['distribution_name', 'module_path']
        self.assertEqual(self.interview(rule='explicit', fields=fields)['next_field'], fields[0])
        proof = observe_prompt(self.repo, 'org/product', 'pi', 'custom')
        result = self.interview('distribution_name', 'org/product', proof, rule='explicit', fields=fields)
        self.assertEqual(result['next_field'], 'module_path')

    def install(self):
        identity = {'name': 'demo-dist', 'import_name': 'demo_import'}
        raw = scaffold('python-unittest', identity)
        raw['evidence'] = observe_prompt(self.repo, 'demo-dist demo_import', 'pi', 'identity')
        plan = project_setup.normalize_plan(self.repo, 'root', raw)
        atomic_json(project_setup.scope_path(self.repo, 'root'), {'plan': plan})
        project_setup.apply(self.repo, 'root')
        self.assertEqual(project_setup.check(self.repo, 'root')['status'], 'passed')

    def test_scaffold_success_does_not_unlock_work_and_baseline_really_runs(self):
        self.install()
        from scripts.project_verification import project_prerequisite
        self.assertEqual(project_prerequisite(self.repo)['reason'], 'PROJECT_VERIFICATION_SCRIPTS_REQUIRED')
        out = materialize(self.repo)
        self.assertTrue(out['files'])
        self.assertTrue(project_setup.state(self.repo)['ready'])
        self.assertEqual(project_prerequisite(self.repo)['reason'], 'PROJECT_VERIFICATION_CHECK_REQUIRED')
        from scripts.verification_probe import command
        args = argparse.Namespace(repo=str(self.repo), scope=['root'], work=None, project=True)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(command(args), 0)
        self.assertIsNone(project_prerequisite(self.repo))
        self.assertEqual(materialize(self.repo)['status'], 'unchanged')
        # Editing an executed script invalidates readiness; empty/deleted scripts block.
        script = self.repo / out['files'][0]
        script.write_text(script.read_text() + '\n# changed\n')
        self.assertEqual(project_prerequisite(self.repo)['reason'], 'PROJECT_VERIFICATION_CHECK_REQUIRED')
        script.unlink()
        self.assertEqual(project_prerequisite(self.repo)['reason'], 'PROJECT_VERIFICATION_SCRIPTS_REQUIRED')

    def test_failed_project_baseline_cannot_unlock_work(self):
        self.install()
        out = materialize(self.repo)
        for name in out['files']:
            (self.repo / name).write_text('raise SystemExit(7)\n')
        from scripts.verification_probe import command
        from scripts.project_verification import project_prerequisite
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(command(argparse.Namespace(repo=str(self.repo), scope=['root'], work=None, project=True)), 0)
        self.assertEqual(project_prerequisite(self.repo)['reason'], 'PROJECT_VERIFICATION_CHECK_REQUIRED')


class RequirementRecoveryTests(RepairCase):
    def preview(self, wid, candidate):
        rc, report = self.run_cli('amend', '--work', wid, '--requirement', candidate,
                                  '--request', 'original request', '--check')
        self.assertEqual(rc, 0, report)
        return report

    def apply(self, wid, candidate, report):
        return self.run_cli('amend', '--work', wid, '--requirement', candidate,
                            '--request', 'original request', '--apply', '--expected-token', report['expected_token'])

    def test_hook_storage_updates_do_not_invalidate_amend_but_business_changes_do(self):
        wid = self.start()
        candidate = self.repo / '.orchestrator/requirements/candidate.md'
        candidate.write_text('# Clarified\nConfirmed login scope\n')
        report = self.preview(wid, candidate)
        from scripts.cao_work_store import load_work, save_work
        # Same save as the host post-tool path, including outbox/storage revision.
        for _ in range(3):
            save_work(self.repo, load_work(self.repo, wid))
        for event in ('pre_tool', 'post_tool'):
            rc, out = self.run_cli('host-event', '--host', 'pi', payload={
                'event': event, 'session_id': 'actual-pi-session', 'tool_name': 'bash',
                'tool_input': {'command': 'cao amend --check'}, 'tool_call_id': 'amend-preview'})
            self.assertEqual(rc, 0, out)
        self.assertEqual(self.apply(wid, str(candidate), report)[0], 0)
        candidate.write_text('# Another revision\nChanged scope again\n')
        report = self.preview(wid, candidate)
        work = load_work(self.repo, wid)
        work['blockers'].append({'reason': 'concurrent user decision'})
        save_work(self.repo, work)
        rc, out = self.apply(wid, str(candidate), report)
        self.assertNotEqual(rc, 0)
        self.assertIn('STALE_WORK_TOKEN', str(out))

    def test_legacy_in_place_edit_can_import_only_exact_anchored_original(self):
        wid = self.start()
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.repo, wid)
        work.pop('requirement_original_base64')
        save_work(self.repo, work)
        source = self.repo / work['requirement']['path']
        recovered = self.repo / '.orchestrator/requirements/recovered.md'
        recovered.write_bytes(source.read_bytes())
        source.write_text('# Clarified\nActual expanded requirement\n')
        args = ('amend', '--work', wid, '--requirement', str(source),
                '--request', 'original request', '--original', str(recovered))
        rc, report = self.run_cli(*args, '--check')
        self.assertEqual(rc, 0, report)
        rc, out = self.run_cli(*args, '--apply', '--expected-token', report['expected_token'])
        self.assertEqual(rc, 0, out)
        self.assertEqual(base64.b64decode(self.read_work(wid)['requirement_history'][-1]['original_base64']), recovered.read_bytes())

    def test_candidate_edit_after_preview_requires_a_new_preview(self):
        wid = self.start()
        candidate = self.repo / '.orchestrator/requirements/candidate.md'
        candidate.write_text('# Candidate\nReviewed scope\n')
        report = self.preview(wid, str(candidate))
        candidate.write_text('# Candidate\nUnreviewed additional scope\n')
        rc, out = self.apply(wid, str(candidate), report)
        self.assertNotEqual(rc, 0)
        self.assertIn('STALE_WORK_TOKEN', str(out))

    def test_in_place_clarification_preserves_original_and_completes_amend(self):
        wid = self.start()
        before = self.read_work(wid)
        source = self.repo / before['requirement']['path']
        original = source.read_bytes()
        source.write_text('# Clarified\nUser confirmed expanded scope\n')
        edited = source.read_bytes()
        report = self.preview(wid, str(source))
        self.assertEqual(source.read_bytes(), edited)
        rc, out = self.apply(wid, str(source), report)
        self.assertEqual(rc, 0, out)
        after = self.read_work(wid)
        self.assertEqual(base64.b64decode(after['requirement_history'][-1]['original_base64']), original)
        self.assertEqual(base64.b64decode(after['requirement_original_base64']), edited)
        self.assertIsNone(after['path_assessment'])
        self.assertEqual(self.run_cli('path', 'assess', '--work', wid, '--profile', 'light', '--reason', 'clarification incorporated')[0], 0)
