"""Project baselines precede Work; managed inputs keep narrow host permissions."""
import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
from argparse import Namespace

import test_project_setup_lifecycle as lifecycle
from scripts import project_setup as setup
from scripts import managed_verification as managed
from scripts import verification_probe as probe
from scripts.cao_snapshot import verification_snapshot
from scripts.project_verification import verification_binding
from scripts.state_store import atomic_json
from scripts.cao_runtime import config


class ProjectVerificationBootstrap(unittest.TestCase):
    setUp = lifecycle.ProjectSetupLifecycle.setUp
    context = lifecycle.ProjectSetupLifecycle.context
    plan = lifecycle.ProjectSetupLifecycle.plan
    install = lifecycle.ProjectSetupLifecycle.install

    def call(self, *args, payload=None, expected=0):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CAO_', 'PI_', 'CODEX_', 'CLAUDE_'))}
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/cao.py'),
                  *args, '--repo', str(self.repo)], input=json.dumps(payload) if payload else None,
                  capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def existing(self):
        tests = self.repo / 'tests'
        tests.mkdir()
        (tests / 'test_existing.py').write_text('import unittest\nclass Existing(unittest.TestCase):\n def test_ok(self): self.assertEqual(2 + 2, 4)\n')
        (self.repo / 'pyproject.toml').write_text('[project]\nname="existing"\nversion="0.1"\n')
        (self.repo / 'openspec').mkdir()
        (self.repo / 'openspec/config.yaml').write_text('context: fixture\n')
        (self.repo / 'openspec/feature.md').write_text('# Feature\n## Acceptance\n- Current operation works.\n')
        self.call('init')
        self.call('sdd', 'select', '--provider', 'openspec')

    def test_existing_project_cli_requires_actual_entries_and_real_probe_before_start(self):
        self.existing()
        self.call('verification', 'configure', '--plan-only', '--test-script', 'python3 -m unittest discover -s tests -v')
        out = self.call('start', '--requirement', 'openspec/feature.md', expected=2)
        self.assertEqual('VERIFICATION_BASELINE_REQUIRED', out['error'])
        self.call('verification', 'configure', '--test-script', 'python3 -m unittest discover -s tests -v')
        out = self.call('start', '--requirement', 'openspec/feature.md', expected=2)
        self.assertEqual('PROJECT_VERIFICATION_CHECK_REQUIRED', out['error'])
        self.assertEqual(out['error'], self.call('bootstrap')['next']['reason'])
        out = self.call('verification', 'check')
        self.assertEqual('passed', out['status'])
        self.assertEqual('project', out['owner']['kind'])
        self.assertFalse((self.repo / '.orchestrator/work').exists())
        first = self.call('start', '--requirement', 'openspec/feature.md')['work_item']
        self.assertEqual('active', first['status'])
        work = json.loads((self.repo / '.orchestrator/work' / (first['id'] + '.json')).read_text())
        self.assertIsNone(work['verification'])
        self.assertNotIn('passed', work.get('gate_status', {}))
        self.assertEqual('baseline_preparation', self.call('verification', 'check')['purpose'])
        before = (self.repo / '.orchestrator/work' / (first['id'] + '.json')).read_bytes()
        self.call('verification', 'check', '--project')
        self.assertEqual(before, (self.repo / '.orchestrator/work' / (first['id'] + '.json')).read_bytes())

    def test_managed_wrapper_and_helper_fingerprints_and_host_exact_grants(self):
        self.existing()
        name = managed.ROOT + '/root/check.py'
        self.call('verification', 'prepare', '--path', name)
        event = {'event': 'pre_tool', 'session_id': 'managed-fixture', 'toolName': 'write', 'input': {'path': name, 'content': 'pass'}}
        self.assertEqual('allow', self.call('host-event', '--host', 'pi', payload=event)['decision'])
        for forbidden in (managed.ROOT + '/unprepared.py', '.orchestrator/config.json', 'src/business.py'):
            event['input']['path'] = forbidden
            self.assertEqual('deny', self.call('host-event', '--host', 'pi', payload=event)['decision'])
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('print("wrapper")\n')
        old = verification_snapshot(config(self.repo), self.repo)
        path.write_text('print("changed")\n')
        self.assertNotEqual(old, verification_snapshot(config(self.repo), self.repo))
        helper = path.with_name('helper.py')
        helper.write_text('SETTING = 1\n')
        old = verification_snapshot(config(self.repo), self.repo)
        helper.write_text('SETTING = 2\n')
        self.assertNotEqual(old, verification_snapshot(config(self.repo), self.repo))
        helper.unlink()
        helper.symlink_to(self.repo / 'pyproject.toml')
        with self.assertRaisesRegex(ValueError, 'SYMLINK'):
            verification_snapshot(config(self.repo), self.repo)

    def test_setup_accepts_only_verification_purpose_in_managed_tree(self):
        raw = self.plan()
        raw['files'].append({'path': managed.ROOT + '/root/check.py', 'purpose': 'verification', 'content': 'print("load")\n'})
        self.install(raw)
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])
        old = setup.baseline_snapshot(self.repo, setup.read(setup.scope_path(self.repo, 'root'))['plan'])
        (self.repo / managed.ROOT / 'root/check.py').write_text('print("changed")\n')
        self.assertFalse(setup.state(self.repo)['ready'])
        for name in ('.orchestrator/config.json', managed.ROOT + '/../../../config.json'):
            with self.assertRaises(ValueError):
                managed.safe_script(self.repo, name)

    def test_migration_is_owned_only_conflict_safe_and_idempotent(self):
        raw = self.plan()
        old = 'scripts/verification/check.py'
        raw['files'].append({'path': old, 'purpose': 'verification', 'content': 'print("load")\n'})
        raw['checks'][0]['command'] = 'python3 ' + old
        self.install(raw)
        target = self.repo / managed.ROOT / 'root/check.py'
        target.parent.mkdir(parents=True)
        target.write_text('user file')
        with self.assertRaisesRegex(ValueError, 'CONFLICT'):
            managed.migrate(self.repo)
        self.assertEqual('user file', target.read_text())
        target.unlink()
        extra = self.repo / 'scripts/verification/user.py'
        extra.write_text('user-owned')
        out = managed.migrate(self.repo)
        self.assertIn(old, out['migrated'])
        self.assertTrue((self.repo / old).exists())
        self.assertEqual('user-owned', extra.read_text())
        self.assertIn(managed.ROOT, config(self.repo)['verification']['scripts'][0]['command'])
        managed.migrate(self.repo)
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])

    def test_zero_tests_no_work_does_not_establish_readiness(self):
        self.existing()
        self.call('verification', 'configure', '--test-script', 'true')
        out = self.call('verification', 'check', expected=2)
        self.assertEqual('blocked', out['status'])
        self.assertFalse(probe.project_state(self.repo)['ready'])

    def test_prepared_scripts_are_trackable_but_runtime_is_ignored(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        (self.repo / '.gitignore').write_text('.orchestrator/\n!.orchestrator/notes/\n')
        name = managed.ROOT + '/root/test.py'
        managed.prepare(self.repo, [name], 'root')
        result = subprocess.run(['git', 'check-ignore', name], cwd=self.repo, capture_output=True)
        self.assertEqual(1, result.returncode)
        result = subprocess.run(['git', 'check-ignore', '.orchestrator/work/example.json'], cwd=self.repo, capture_output=True)
        self.assertEqual(0, result.returncode)
        result = subprocess.run(['git', 'check-ignore', '.orchestrator/notes/example.md'], cwd=self.repo, capture_output=True)
        self.assertEqual(1, result.returncode)

    def test_multiple_scopes_bind_inputs_without_invalidating_unrelated_preparation(self):
        from scripts.project_context import load_context, commit_context
        ctx = load_context(self.repo)
        for scope in ('api', 'worker'):
            ctx['scopes'][scope] = copy.deepcopy(ctx['scopes']['root'])
            ctx['scopes'][scope]['paths'] = [scope]
            tests = self.repo / scope / 'tests'
            tests.mkdir(parents=True)
            (tests / 'test_ok.py').write_text('import unittest\nclass Baseline(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        commit_context(self.repo, ctx, ctx['revision'])
        cfg = config(self.repo)
        cfg['verification']['context_binding'] = verification_binding(self.repo, ['api', 'worker'])
        cfg['verification']['scripts'] = []
        for scope in ('api', 'worker'):
            name = managed.ROOT + '/' + scope + '/helper.py'
            managed.prepare(self.repo, [name], scope)
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('VALUE = 1\n')
            cfg['verification']['scripts'].append({'name': 'setup-' + scope + '-test',
                'command': 'python3 -m unittest discover -s ' + scope + '/tests -v', 'required': True, 'gates': ['tests']})
        atomic_json(self.repo / '.orchestrator/config.json', cfg)
        with redirect_stdout(StringIO()):
            self.assertEqual(0, probe.command(Namespace(repo=str(self.repo), work=None, project=True, scope=['api'])))
        self.assertTrue(probe.project_state(self.repo, ['api'])['ready'])
        self.assertFalse(probe.project_state(self.repo, ['worker'])['ready'])
        (self.repo / managed.ROOT / 'worker/helper.py').write_text('VALUE = 2\n')
        self.assertTrue(probe.project_state(self.repo, ['api'])['ready'])
        (self.repo / managed.ROOT / 'api/helper.py').write_text('VALUE = 3\n')
        self.assertFalse(probe.project_state(self.repo, ['api'])['ready'])

    def test_migration_retry_after_partial_state_write(self):
        from unittest.mock import patch
        raw = self.plan()
        old = 'scripts/verification/check.py'
        raw['files'].append({'path': old, 'purpose': 'verification', 'content': 'print("loaded")\n'})
        raw['checks'][0]['command'] = 'python3 ' + old
        self.install(raw)
        original = managed.atomic_json
        def interrupted(path, value):
            original(path, value)
            if path == setup.scope_path(self.repo, 'root'):
                raise OSError('simulated interruption after updating setup')
        with patch.object(managed, 'atomic_json', side_effect=interrupted):
            with self.assertRaises(OSError):
                managed.migrate(self.repo)
        managed.migrate(self.repo)
        self.assertIn(managed.ROOT, config(self.repo)['verification']['scripts'][0]['command'])
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])

    def test_second_work_inherits_standards_without_work_results(self):
        self.existing()
        self.call('verification', 'configure', '--test-script', 'python3 -m unittest discover -s tests -v')
        self.call('verification', 'check')
        first = self.call('start', '--requirement', 'openspec/feature.md')['work_item']['id']
        declarations = config(self.repo)['verification']['scripts']
        self.call('block', '--work', first, '--reason', 'Waiting for external information', '--suspend')
        (self.repo / 'openspec/second.md').write_text('# Second task\n## Acceptance\n- Another local operation works.\n')
        second = self.call('start', '--requirement', 'openspec/second.md')['work_item']['id']
        self.assertNotEqual(first, second)
        work = json.loads((self.repo / '.orchestrator/work' / (second + '.json')).read_text())
        self.assertIsNone(work['verification'])
        self.assertEqual(declarations, config(self.repo)['verification']['scripts'])

    def test_real_imported_helper_cache_does_not_invalidate_project_checks(self):
        self.existing()
        root = managed.ROOT + '/root/'
        self.call('verification', 'prepare', '--path', root + 'check.py', '--path', root + 'helper.py')
        directory = self.repo / root
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'helper.py').write_text('VALUE = 42\n')
        (directory / 'check.py').write_text('import helper\nimport unittest\nclass Wrapper(unittest.TestCase):\n def test_value(self): self.assertEqual(helper.VALUE, 42)\nunittest.main(verbosity=2)\n')
        self.call('verification', 'configure', '--test-script', 'python3 ' + root + 'check.py')
        self.call('verification', 'check')
        self.assertTrue(probe.project_state(self.repo)['ready'])
        self.assertTrue((directory / '__pycache__').exists())
        self.call('verification', 'check')

    def test_existing_work_missing_baseline_repairs_without_restart_or_progress_loss(self):
        self.existing()
        self.call('verification', 'configure', '--test-script', 'python3 -m unittest discover -s tests -v')
        self.call('verification', 'check')
        wid = self.call('start', '--requirement', 'openspec/feature.md')['work_item']['id']
        path = self.repo / '.orchestrator/work' / (wid + '.json')
        work_before = path.read_bytes()
        cfg = config(self.repo)
        cfg['verification']['scripts'] = []
        atomic_json(self.repo / '.orchestrator/config.json', cfg)
        self.call('verification', 'prepare', '--path', managed.ROOT + '/root/repair.py')
        repair = self.repo / managed.ROOT / 'root/repair.py'
        repair.parent.mkdir(parents=True, exist_ok=True)
        repair.write_text('print("repair helper")\n')
        self.call('verification', 'configure', '--test-script', 'python3 -m unittest discover -s tests -v')
        self.call('verification', 'check', '--project')
        self.assertEqual(work_before, path.read_bytes())

    def test_managed_plan_cannot_mislabel_business_files_or_claim_other_scope(self):
        raw = self.plan()
        raw['files'].append({'path': managed.ROOT + '/root/bad.py', 'purpose': 'entry', 'content': 'pass\n'})
        with self.assertRaisesRegex(ValueError, 'PROTECTED_PATH'):
            setup.normalize_plan(self.repo, 'root', raw)

    def test_migrate_module_preserves_quoted_working_directory(self):
        from scripts.project_context import load_context, commit_context
        ctx = load_context(self.repo)
        ctx['scopes']['root']['paths'] = ['api module']
        commit_context(self.repo, ctx, ctx['revision'])
        raw = self.plan()
        for entry in raw['files']:
            entry['path'] = 'api module/' + entry['path']
        for check in raw['checks']:
            check['cwd'] = 'api module'
        raw['files'].append({'path': 'api module/scripts/verification/check.py', 'purpose': 'verification', 'content': 'print("loaded")\n'})
        raw['checks'][0]['command'] = 'python3 scripts/verification/check.py'
        self.install(raw)
        managed.migrate(self.repo)
        command = config(self.repo)['verification']['scripts'][0]['command']
        self.assertIn("cd 'api module' &&", command)
        self.assertIn('../.orchestrator/scripts/verification/root/check.py', command)
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])

    def pending_work(self):
        self.existing()
        secret = 'BUSINESS_ACCEPTANCE_ONLY_8d32f1'
        (self.repo / 'openspec/feature.md').write_text('# Feature\n## Acceptance\n- ' + secret + '\n')
        self.call('verification', 'configure', '--test-script', 'python3 -m unittest discover -s tests -v')
        self.call('verification', 'check')
        wid = self.call('start', '--requirement', 'openspec/feature.md')['work_item']['id']
        return wid, secret, self.repo / '.orchestrator/work' / (wid + '.json')

    def test_existing_active_work_baseline_context_excludes_business_requirement(self):
        wid, secret, path = self.pending_work()
        cfg = config(self.repo)
        cfg['verification']['scripts'] = []
        atomic_json(self.repo / '.orchestrator/config.json', cfg)
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.repo, wid)
        work['sdd']['native_note'] = secret
        save_work(self.repo, work)
        before = path.read_bytes()
        out = self.call('bootstrap')
        self.assertEqual('project', out['next']['owner'])
        self.assertIsNone(out['work_item'])
        self.assertEqual(wid, out['pending_work']['id'])
        self.assertNotIn(secret, json.dumps(out))
        self.assertNotIn('requirement', out['context'])
        next_out = self.call('next')
        self.assertIsNone(next_out['work_item'])
        self.assertEqual('project', next_out['action']['owner'])
        self.assertNotIn(secret, json.dumps(next_out))
        event = self.call('host-event', '--host', 'pi', payload={'event': 'prompt_submit',
            'session_id': 'pending-project-fixture', 'prompt': 'continue'})
        self.assertIn('# CAO Project Preparation', event['additional_context'])
        self.assertNotIn(secret, event['additional_context'])
        self.assertEqual(before, path.read_bytes())

    def test_resume_does_not_reactivate_or_register_work_before_project_preparation(self):
        wid, secret, path = self.pending_work()
        work = json.loads(path.read_text())
        work['status'] = 'suspended'
        from scripts.cao_work_store import save_work
        save_work(self.repo, work)
        cfg = config(self.repo)
        cfg['verification']['scripts'] = []
        atomic_json(self.repo / '.orchestrator/config.json', cfg)
        before = path.read_bytes()
        out = self.call('resume', '--work', wid, '--reactivate', '--session', 'deferred-project-session', expected=2)
        self.assertEqual('project', out['next']['owner'])
        self.assertEqual(wid, out['context']['pending_work']['id'])
        self.assertNotIn(secret, json.dumps(out))
        self.assertEqual(before, path.read_bytes())
        from scripts.cao_sessions import load_session
        self.assertIsNone(load_session(self.repo, 'deferred-project-session', host='unknown'))
        self.call('verification', 'configure', '--test-script', 'python3 -m unittest discover -s tests -v')
        self.call('verification', 'check', '--project')
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/cao.py'),
            'resume', '--work', wid, '--reactivate', '--session', 'deferred-project-session', '--repo', str(self.repo)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual('active', json.loads(path.read_text())['status'])

    def test_configured_but_unchecked_baseline_blocks_existing_work_resume(self):
        wid, secret, path = self.pending_work()
        probe.project_result_path(self.repo, 'root').unlink()
        before = path.read_bytes()
        out = self.call('resume', '--work', wid, '--session', 'unchecked-project-session', expected=2)
        self.assertEqual('PROJECT_VERIFICATION_CHECK_REQUIRED', out['error'])
        self.assertIn('check --project', out['next']['instruction'])
        self.assertEqual(before, path.read_bytes())
        self.assertNotIn(secret, json.dumps(out))

    def test_previous_readiness_policy_requires_project_review_before_resume(self):
        wid, secret, path = self.pending_work()
        result_path = probe.project_result_path(self.repo, 'root')
        result = json.loads(result_path.read_text())
        result['standard'].pop('preparation_policy')
        atomic_json(result_path, result)
        before = path.read_bytes()
        out = self.call('resume', '--work', wid, '--session', 'previous-policy-session', expected=2)
        self.assertEqual('PROJECT_VERIFICATION_CHECK_REQUIRED', out['error'])
        self.assertIn('project-wide quality standards', out['next']['instruction'])
        self.assertEqual(before, path.read_bytes())
        self.call('verification', 'check', '--project')
        self.assertTrue(probe.project_state(self.repo)['ready'])
