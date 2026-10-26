"""Executable scope of the accepted repair, excluding actual model hosts/workspaces."""
import argparse
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests import test_project_setup_lifecycle as setup_fixture
from scripts import project_setup as setup
from scripts.state_store import atomic_json


class SetupRecovery(unittest.TestCase):
    setUp = setup_fixture.ProjectSetupLifecycle.setUp
    context = setup_fixture.ProjectSetupLifecycle.context
    plan = setup_fixture.ProjectSetupLifecycle.plan
    install = setup_fixture.ProjectSetupLifecycle.install

    def test_input_error_releases_running_owner_and_preserves_stale_history(self):
        self.install(self.plan())
        original = setup.current_inputs
        calls = []
        def changed(repo, plan):
            calls.append(True)
            if len(calls) > 1:
                raise ValueError('unreadable source')
            return original(repo, plan)
        with patch.object(setup, 'current_inputs', side_effect=changed):
            self.assertEqual('stale', setup.check(self.repo, 'root')['status'])
        self.assertNotIn('running', setup.read(setup.scope_path(self.repo, 'root')))
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])

    def test_exact_abandoned_owner_recovery_and_live_owner_refusal(self):
        self.install(self.plan())
        path = setup.scope_path(self.repo, 'root')
        record = setup.read(path)
        record['running'] = {'id': 'dead-run', 'owner_pid': os.getpid()}
        atomic_json(path, record)
        with self.assertRaisesRegex(ValueError, 'OWNER_STILL_RUNNING'):
            setup.check(self.repo, 'root', 'dead-run')
        with patch('scripts.project_setup.os.kill', side_effect=ProcessLookupError):
            self.assertEqual('passed', setup.check(self.repo, 'root', 'dead-run')['status'])
        self.assertEqual('abandoned', setup.read(path)['history'][0]['status'])

    def test_plan_and_apply_cannot_hide_preexisting_generated_source(self):
        raw = self.plan()
        raw.update(outputs=['dist/'], generated_output_suffixes={'dist/': ['.js']})
        plan = setup.normalize_plan(self.repo, 'root', raw)
        atomic_json(setup.scope_path(self.repo, 'root'), {'plan': plan})
        (self.repo / 'dist').mkdir()
        (self.repo / 'dist/owned.js').write_text('user source')
        with self.assertRaisesRegex(ValueError, 'SCOPE_PROTECTED'):
            setup.apply(self.repo, 'root')
        self.assertFalse((self.repo / 'pyproject.toml').exists())

    def test_malformed_reserved_paths_and_rule_versions_fail_before_write(self):
        for field, value in [('path', None), ('purpose', []), ('path', 'folder./file.py')]:
            raw = self.plan()
            raw['files'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                setup.normalize_plan(self.repo, 'root', raw)
        raw = self.plan()
        raw['rule']['version'] = 2
        with self.assertRaisesRegex(ValueError, 'RULE_INVALID'):
            setup.normalize_plan(self.repo, 'root', raw)

    def test_dependency_missing_is_blocked_and_assertion_failure_is_failed(self):
        from scripts.preparation_result import status
        self.assertEqual('blocked', status({'exit_code': 1, 'stderr_tail': "ModuleNotFoundError: No module named 'missing'"}))
        self.assertEqual('failed', status({'exit_code': 1, 'stderr_tail': 'AssertionError: incorrect value'}))
        self.assertEqual('blocked', status({'exit_code': 124}))
        for output in ('no required module provides package example.test/missing',
                       'failed to get `missing` as a dependency',
                       'Unable to load the service index for source',
                       'Could not resolve dependencies for project'):
            self.assertEqual('blocked', status({'exit_code': 1, 'stderr_tail': output}))

    def test_filesystem_case_collision_is_rejected_before_scaffold_write(self):
        (self.repo / 'Tests').mkdir()
        with self.assertRaisesRegex(ValueError, 'CASE_COLLISION'):
            setup.normalize_plan(self.repo, 'root', self.plan())

    def test_setup_mutations_respect_pending_host_resource_restart(self):
        from scripts.provider_restart import require_restart
        require_restart(self.repo, 'bmad', ['pi'])
        for action in ('draft', 'plan', 'apply', 'check'):
            args = argparse.Namespace(repo=str(self.repo), setup_action=action, scope='root')
            with self.subTest(action=action), patch('scripts.cao_providers.detect_current_host', return_value='pi'):
                with self.assertRaisesRegex(ValueError, 'HOST_RESTART_REQUIRED'):
                    setup.dispatch(args)

    def test_patch_payload_can_only_edit_the_prepared_input(self):
        path = setup.input_path(self.repo, 'root')
        atomic_json(path, {})
        prefix = '*** Begin Patch\n*** Update File: '
        suffix = '\n@@\n-a\n+b\n*** End Patch'
        self.assertTrue(setup.writable_input(self.repo, {'toolName': 'apply_patch', 'input': {'patch': prefix + path.relative_to(self.repo).as_posix() + suffix}}))
        self.assertFalse(setup.writable_input(self.repo, {'toolName': 'apply_patch', 'input': {'patch': prefix + 'src/business.py' + suffix}}))

    def test_confirmed_identity_fields_reuse_their_actual_original_evidence(self):
        from scripts.project_context import load_context, commit_context
        from scripts.project_sources import observe_prompt
        raw = self.plan()
        plan = setup.normalize_plan(self.repo, 'root', raw)
        context = load_context(self.repo)
        candidate = copy.deepcopy(context)
        candidate['scopes']['root']['identity'] = {'values': plan['identity'], 'rule': plan['rule'], 'sources': plan['identity_sources']}
        commit_context(self.repo, candidate, context['revision'])
        raw['evidence'] = observe_prompt(self.repo, 'Change only the check command; preserve the existing identities.', 'pi', 'new-session')
        inherited = setup.normalize_plan(self.repo, 'root', raw)
        self.assertEqual(plan['identity_sources'], inherited['identity_sources'])
        raw['identity']['name'] = 'unauthorized-new-name'
        with self.assertRaisesRegex(ValueError, 'FIELD_NOT_COVERED'):
            setup.normalize_plan(self.repo, 'root', raw)

    def test_adapter_version_change_invalidates_ready_without_rewriting_files(self):
        from scripts.setup_rules import RULES
        self.install(self.plan())
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])
        with patch.dict(RULES['python-unittest'], {'version': 2}):
            self.assertEqual('PROJECT_SETUP_RULE_STALE', setup.state(self.repo)['reason'])

    def test_repair_authorizes_exact_planned_files_only_until_ready(self):
        self.install(self.plan())
        edit = {'toolName': 'edit', 'input': {'path': 'tests/test_baseline.py', 'oldText': 'True', 'newText': 'True'}}
        self.assertTrue(setup.writable_scaffold(self.repo, edit))
        edit['input']['path'] = 'demo_import/business.py'
        self.assertFalse(setup.writable_scaffold(self.repo, edit))
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])
        edit['input']['path'] = 'tests/test_baseline.py'
        self.assertFalse(setup.writable_scaffold(self.repo, edit))

    def test_runtime_rules_cannot_downgrade_required_test_capability(self):
        raw = self.plan()
        raw['required_capabilities'] = ['load']
        raw.update(delivery_kind='non-runtime', no_tests_reason='pretend documentation')
        with self.assertRaisesRegex(ValueError, 'CAPABILITIES_INCOMPLETE'):
            setup.normalize_plan(self.repo, 'root', raw)


class NativeContracts(unittest.TestCase):
    def test_current_renderer_requires_complete_review_and_referenced_finalize(self):
        from scripts.native_contracts import resolve, validate
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            root = repo / '_bmad/render/current'
            root.mkdir(parents=True)
            (root / 'step-02-plan.md').write_text('Native rule')
            (root / 'step-03-implement.md').write_text('Launch a subagent')
            rules = [{'path': '_bmad/render/current/step-02-plan.md'}]
            with self.assertRaisesRegex(ValueError, 'REVIEW_CONTRACT_REQUIRED'):
                resolve(repo, rules, 'dispatch')
            (root / 'step-04-review.md').write_text('#### Blind (`blind-hunter`)\nFollow step-05-present.md\n')
            with self.assertRaisesRegex(ValueError, 'CONTRACT_FILE_REQUIRED'):
                resolve(repo, rules, 'dispatch')
            (root / 'step-05-present.md').write_text('Finalize')
            contracts = resolve(repo, rules, 'dispatch')
            validate(repo, contracts)
            (root / 'step-05-present.md').write_text('Changed obligations')
            with self.assertRaisesRegex(ValueError, 'CONTRACT_STALE'):
                validate(repo, contracts)

    def test_explicit_missing_implementation_reference_is_rejected(self):
        from scripts.native_contracts import resolve
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            root = repo / '_bmad/render/current'
            root.mkdir(parents=True)
            (root / 'step-02-plan.md').write_text('Follow step-03-implement.md')
            with self.assertRaisesRegex(ValueError, 'CONTRACT_FILE_REQUIRED'):
                resolve(repo, [{'path': '_bmad/render/current/step-02-plan.md'}], 'dispatch')

    def test_only_versioned_primary_spec_status_transition_preserves_evidence(self):
        from scripts.native_metadata import POLICY, semantic_hash
        from scripts.completion_policy import workflow_ready
        from scripts.sdd_acceptance_files import source_hashes
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            spec = repo / '_bmad-output/spec.md'
            spec.parent.mkdir()
            spec.write_text('---\nstatus: in-review\ntitle: Example\n---\n## Intent\nA requirement.\n')
            declaration = {'spec': '_bmad-output/spec.md', 'lifecycle_metadata': POLICY,
                           'criteria': [{'id': 'AC-1', 'source': '_bmad-output/spec.md', 'kind': 'requirement', 'description': 'A requirement'}],
                           'executor': {'mode': 'command', 'entrypoint': 'python check.py'}}
            original = source_hashes(repo, declaration, ['_bmad-output'])
            work = {'requirement': {'sha256': 'intent'}, 'sdd': {'provider': 'bmad',
                    'route_decision': {'build_route': 'dispatch', 'primary_artifact': '_bmad-output/spec.md'},
                    'workflow': {'status': 'completed', 'requirement_sha256': 'intent', 'provider': 'bmad',
                                 'execution': {'exit_code': 0}, 'artifacts': ['_bmad-output/spec.md'],
                                 'artifact_hashes': {'_bmad-output/spec.md': hashlib.sha256(spec.read_bytes()).hexdigest()},
                                 'artifact_metadata_policy': POLICY,
                                 'artifact_semantic_hashes': {'_bmad-output/spec.md': semantic_hash(spec)}}}}
            spec.write_text(spec.read_text().replace('in-review', 'done'))
            self.assertEqual(original, source_hashes(repo, declaration, ['_bmad-output']))
            self.assertTrue(workflow_ready(work, repo)[0])
            spec.write_text(spec.read_text().replace('A requirement.', 'Different intent.'))
            self.assertFalse(workflow_ready(work, repo)[0])
            self.assertNotEqual(original, source_hashes(repo, declaration, ['_bmad-output']))

    def test_model_default_uses_current_host_observation_only(self):
        from scripts.review_dispatch import effective_model
        args = argparse.Namespace(model=None)
        with patch('scripts.review_dispatch.load_session', return_value={'observed_model': 'current-model'}):
            self.assertEqual(('current-model', 'host_observation'), effective_model(Path('.'), args, ('pi', 'parent')))
        with patch.dict(os.environ, {'PI_MODEL': 'old-worker-model'}), patch('scripts.review_dispatch.load_session', return_value={}):
            with self.assertRaisesRegex(ValueError, 'MODEL_UNOBSERVED'):
                effective_model(Path('.'), args, ('pi', 'parent'))

    def test_round_counts_do_not_invent_missing_historical_rounds(self):
        from scripts.review_dispatch import review_counts
        layers = ['blind-hunter', 'edge-case-hunter', 'verification-gap']
        work = {'sdd': {'native_reviews': {'_contract': {'layers': layers, 'round_id': 'round-2'},
                  **{layer: {'id': 'new-' + layer, 'round_id': 'round-2'} for layer in layers}},
                'native_review_history': [{'review': {'id': 'old-' + layer, 'round_id': 'round-1'}} for layer in layers]}}
        counts = review_counts(work)
        self.assertEqual((3, 2, 6), (counts['layer_count'], counts['observed_round_count'], counts['total_task_count']))


class AcceptanceBoundaries(unittest.TestCase):
    def test_catalog_separates_metadata_tasks_and_negative_constraints(self):
        from scripts.acceptance_trace import source_items
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / 'spec.md').write_text('---\nstatus: draft\n---\n## Acceptance criteria\n- Login succeeds.\n## Constraints\n- Never log passwords.\n## Tasks\n- Add the handler.\n')
            all_items = source_items(repo, 'spec.md', requirement=True, catalog=True)
            required = source_items(repo, 'spec.md', requirement=True)
            self.assertEqual({'functional', 'constraint', 'implementation_task', 'metadata'}, {i['category'] for i in all_items})
            self.assertEqual(2, len(required))
            self.assertTrue(all(i['line'] for i in all_items))

    def test_transport_change_keeps_standards_but_evaluator_stays_technical_input(self):
        from scripts.review_scope import acceptance_review_scope
        from scripts.sdd_acceptance_policy import normalize_declaration
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            executor = repo / 'scripts/verification/acceptance.mjs'
            executor.parent.mkdir(parents=True)
            executor.write_text('first evaluator')
            decl = normalize_declaration({'spec': 'openspec/spec.md', 'criteria': [
                {'id': 'AC-1', 'source': 'openspec/spec.md', 'kind': 'requirement', 'description': 'Login succeeds'}],
                'executor': {'mode': 'command', 'entrypoint': 'node scripts/verification/acceptance.mjs', 'review_impact': 'acceptance_only'}})
            work = {'sdd': {'acceptance': {'declaration': decl}}}
            excluded, before = acceptance_review_scope(repo, work)
            self.assertIn('scripts/verification/acceptance.mjs', excluded)
            (repo / 'declaration.json').write_text(json.dumps(decl))
            work['sdd']['acceptance']['declaration_file'] = 'declaration.json'
            self.assertEqual(before, acceptance_review_scope(repo, work)[1])
            work['sdd']['acceptance']['declaration']['criteria'][0]['description'] = 'Different intent'
            self.assertNotEqual(before, acceptance_review_scope(repo, work)[1])


class CompleteChangeInventory(unittest.TestCase):
    def test_git_baseline_includes_renames_deletions_and_untracked_resources(self):
        from scripts.review_changes import capture_baseline, freeze, inventory
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            def git(*args):
                return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
            git('init', '-q')
            (repo / 'old.txt').write_text('rename content')
            (repo / 'remove.bin').write_bytes(b'\x00old')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'baseline')
            work = {'id': 'work-git', 'requirement': {'path': '.orchestrator/requirements/request.md'}, 'sdd': {}}
            capture_baseline(repo, work)
            git('mv', 'old.txt', 'new.txt')
            (repo / 'remove.bin').unlink()
            (repo / 'data').mkdir()
            (repo / 'data/.gitkeep').write_bytes(b'')
            snapshot, _ = inventory(repo, work)
            value = freeze(repo, work, snapshot)
            changes = {item['path']: item for item in value['changes']}
            self.assertTrue(changes['new.txt']['status'].startswith('R'))
            self.assertEqual('old.txt', changes['new.txt']['previous_path'])
            self.assertEqual('D', changes['remove.bin']['status'])
            self.assertEqual('A', changes['data/.gitkeep']['status'])

    def test_no_vcs_baseline_includes_deleted_binary_and_empty_resource(self):
        from scripts.review_changes import capture_baseline, freeze, inventory
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / 'src').mkdir()
            (repo / 'src/old.txt').write_text('old resource')
            (repo / 'data').mkdir()
            (repo / 'data/.gitkeep').write_bytes(b'')
            (repo / 'data/image.bin').write_bytes(b'\x00\xffold')
            work = {'id': 'work-1', 'requirement': {'path': '.orchestrator/requirements/request.md'}, 'sdd': {}}
            capture_baseline(repo, work)
            (repo / 'src/old.txt').unlink()
            (repo / 'data/image.bin').write_bytes(b'\x00\xffnew')
            (repo / 'src/new.yaml').write_text('enabled: true\n')
            snapshot, files = inventory(repo, work)
            value = freeze(repo, work, snapshot)
            self.assertEqual('work_start', value['baseline']['kind'])
            self.assertIn('data/.gitkeep', {item['path'] for item in files})
            changes = {item['path']: item['status'] for item in value['changes']}
            self.assertEqual({'src/old.txt': 'D', 'data/image.bin': 'M', 'src/new.yaml': 'A'}, changes)
            baseline = repo / work['review_baseline']['path']
            baseline.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'BASELINE_STALE'):
                freeze(repo, work, snapshot)
