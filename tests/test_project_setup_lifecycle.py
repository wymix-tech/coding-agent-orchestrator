"""No-Work initialization executes real project checks; fake exits never certify it."""
import argparse
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import project_setup as setup
from scripts.cao_defaults import DEFAULT_CONFIG
from scripts.project_context import commit_context
from scripts.project_sources import observe_prompt
from scripts.setup_rules import describe, scaffold
from scripts.state_store import atomic_json


class ProjectSetupLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        atomic_json(self.repo / '.orchestrator/config.json', copy.deepcopy(DEFAULT_CONFIG))
        self.context('Python')

    def context(self, language):
        from scripts.project_context import load_context
        old = load_context(self.repo)
        values = {'language': language, 'build_tool': 'setuptools' if language == 'Python' else 'npm',
                  'test_framework': 'unittest' if language == 'Python' else 'node:test'}
        candidate = {'scopes': {'root': {'paths': ['.'], 'technology': {
            key: {'value': value, 'status': 'accepted'} for key, value in values.items()}}}, 'evidence': []}
        return commit_context(self.repo, candidate, old['revision'] if old else None)

    def plan(self, rule='python-unittest', identity=None):
        identity = identity or {'name': 'demo-dist', 'import_name': 'demo_import'}
        raw = scaffold(rule, identity)
        raw['evidence'] = observe_prompt(self.repo, 'Initialize project: ' + ' '.join(identity.values()), 'pi', 'setup-session')
        return raw

    def install(self, raw):
        plan = setup.normalize_plan(self.repo, 'root', raw)
        atomic_json(setup.scope_path(self.repo, 'root'), {'plan': plan})
        setup.apply(self.repo, 'root')
        return plan

    def test_python_real_no_work_check_and_business_change_does_not_reset_setup(self):
        self.install(self.plan())
        result = setup.check(self.repo, 'root')
        self.assertEqual('passed', result['status'], result)
        self.assertEqual({'kind': 'project', 'scope': 'root'}, result['owner'])
        self.assertFalse((self.repo / '.orchestrator/work').exists())
        self.assertTrue(setup.state(self.repo)['ready'])
        (self.repo / 'demo_import/__init__.py').write_text('def ready():\n    return 42\n')
        self.assertTrue(setup.state(self.repo)['ready'])
        self.assertIsNone(setup.prerequisite(self.repo))

    def test_javascript_real_checks_with_scoped_package_local_path(self):
        import shutil
        if not shutil.which('node') or not shutil.which('npm'):
            self.skipTest('Node/npm unavailable')
        self.context('JavaScript')
        self.install(self.plan('node-test', {'name': '@acme/demo-service'}))
        self.assertTrue((self.repo / 'src/index.mjs').exists())
        self.assertFalse((self.repo / '@acme').exists())
        result = setup.check(self.repo, 'root')
        self.assertEqual('passed', result['status'], result)

    def test_explicit_plan_uses_same_real_execution_lifecycle(self):
        raw = self.plan()
        raw['rule'] = {'id': 'explicit', 'version': 1}
        raw['basis'] = 'User-authorized custom flat-layout Python project with unittest checks.'
        self.install(raw)
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])

    def test_no_test_zero_exit_is_blocked(self):
        raw = self.plan()
        raw['checks'] = [{'name': 'test', 'command': 'true', 'capability': 'test'}]
        raw['required_capabilities'] = ['test']
        self.install(raw)
        result = setup.check(self.repo, 'root')
        self.assertEqual('blocked', result['status'])
        self.assertFalse(setup.state(self.repo)['ready'])

    def test_failure_repair_then_retry(self):
        raw = self.plan()
        test = next(f for f in raw['files'] if f['purpose'] == 'test')
        test['content'] = test['content'].replace('assertTrue(ready())', 'assertFalse(ready())')
        self.install(raw)
        self.assertEqual('failed', setup.check(self.repo, 'root')['status'])
        path = self.repo / test['path']
        path.write_text(path.read_text().replace('assertFalse', 'assertTrue'))
        self.assertEqual('passed', setup.check(self.repo, 'root')['status'])

    def test_existing_file_and_symlink_never_overwritten(self):
        raw = self.plan()
        (self.repo / 'pyproject.toml').write_text('user-owned')
        with self.assertRaisesRegex(ValueError, 'FILE_CONFLICT'):
            setup.normalize_plan(self.repo, 'root', raw)
        (self.repo / 'pyproject.toml').unlink()
        with tempfile.TemporaryDirectory() as outside:
            (self.repo / 'tests').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'PROJECT_SCOPE_INVALID|SYMLINK'):
                setup.normalize_plan(self.repo, 'root', raw)

    def test_paths_case_collisions_output_and_state_boundaries(self):
        for path in ('../escape.py', '/tmp/escape.py', '.orchestrator/config.json', 'CON.txt'):
            raw = self.plan()
            raw['files'][0]['path'] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                setup.normalize_plan(self.repo, 'root', raw)
        raw = self.plan()
        raw['files'].append({**raw['files'][0], 'path': 'PYPROJECT.TOML'})
        with self.assertRaisesRegex(ValueError, 'COLLISION'):
            setup.normalize_plan(self.repo, 'root', raw)
        raw = self.plan()
        raw['outputs'].append('tests/')
        with self.assertRaisesRegex(ValueError, 'OVERLAPS_INPUT'):
            setup.normalize_plan(self.repo, 'root', raw)

    def test_actual_tools_not_model_claims_authorize_identity(self):
        raw = self.plan()
        raw['identity']['name'] = 'unapproved-name'
        with self.assertRaisesRegex(ValueError, 'FIELD_NOT_COVERED'):
            setup.normalize_plan(self.repo, 'root', raw)

    def test_apply_is_idempotent_and_scope_conflict_does_not_write_any_file(self):
        plan = self.install(self.plan())
        before = {f['path']: (self.repo / f['path']).read_bytes() for f in plan['files']}
        setup.apply(self.repo, 'root')
        self.assertEqual(before, {f['path']: (self.repo / f['path']).read_bytes() for f in plan['files']})
        (self.repo / 'pyproject.toml').write_text('external change')
        with self.assertRaisesRegex(ValueError, 'FILE_CONFLICT'):
            setup.apply(self.repo, 'root')
        self.assertEqual('external change', (self.repo / 'pyproject.toml').read_text())

    def test_existing_work_is_not_reset_by_new_setup_gate(self):
        self.assertIsNone(setup.prerequisite(self.repo, {'status': 'active', 'id': 'old-work'}))
        self.assertIsNotNone(setup.prerequisite(self.repo))

    def test_all_ecosystem_identity_layout_rules(self):
        ids = {
            'python-unittest': {'name': 'demo-dist', 'import_name': 'demo_import'},
            'node-test': {'name': '@acme/demo'},
            'typescript-node': {'name': 'demo', 'typescript_version': '5.7.3'},
            'go-module': {'name': 'demo', 'module_path': 'example.org/acme/demo', 'go_version': '1.22'},
            'java-maven': {'name': 'demo', 'group_id': 'org.acme', 'artifact_id': 'demo-service', 'namespace': 'org.acme.demo', 'java_version': '21', 'junit_version': '5.11.3'},
            'java-gradle': {'name': 'demo', 'group_id': 'org.acme', 'namespace': 'org.acme.demo', 'java_version': '21', 'junit_version': '5.11.3'},
            'rust-cargo': {'name': 'demo-service', 'crate_name': 'demo_service', 'edition': '2021'},
            'dotnet-library': {'name': 'Demo', 'namespace': 'Acme.Demo', 'target_framework': 'net8.0'},
            'cpp-cmake': {'name': 'demo', 'target_name': 'demo', 'cpp_standard': '17'},
        }
        self.assertEqual(set(describe()), set(ids))
        for rule, identity in ids.items():
            with self.subTest(rule=rule):
                raw = scaffold(rule, identity)
                self.assertTrue(raw['files'])
                self.assertTrue(raw['checks'])
                self.assertEqual(identity, raw['identity'])
                self.assertTrue(set(raw['required_capabilities']) <= {c['capability'] for c in raw['checks']})

    def test_actual_cli_identity_is_inherited_by_first_business_work(self):
        import os
        cli = Path(__file__).resolve().parents[1] / 'scripts/cao.py'
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CAO_', 'PI_', 'CODEX_', 'CLAUDE_'))}
        (self.repo / 'openspec').mkdir()
        (self.repo / 'openspec/config.yaml').write_text('context: fixture\n')
        def call(*args, payload=None):
            result = subprocess.run([sys.executable, str(cli), *args, '--repo', str(self.repo)],
                         input=json.dumps(payload) if payload else None, text=True, capture_output=True, env=env, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return json.loads(result.stdout)
        call('init')
        call('host-event', '--host', 'pi', payload={'event': 'prompt_submit', 'session_id': 'actual-cli-fixture',
             'prompt': 'Initialize sample-dist with import sample_import. Use Python setuptools unittest.'})
        evidence = call('project', 'inspect')['user_evidence'][-1]['id']
        call('project', 'setup', 'draft', '--rule', 'python-unittest',
             '--identity', '{"name":"sample-dist","import_name":"sample_import"}', '--evidence', evidence)
        call('project', 'setup', 'plan')
        background = call('project', 'context', 'show')['context']
        self.assertEqual('sample_import', background['scopes']['root']['identity']['values']['import_name'])
        call('project', 'setup', 'apply')
        call('project', 'setup', 'check')
        self.assertTrue(call('project', 'setup', 'status')['ready'])
        (self.repo / 'openspec/feature.md').write_text('# Feature\n## Acceptance\n- The new operation works.\n')
        work = call('start', '--requirement', 'openspec/feature.md')['work_item']
        self.assertEqual('active', work['status'])
        self.assertNotEqual('passed', work.get('verification'))
