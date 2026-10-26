"""Optional actual compiler matrix. Missing tools skip explicitly, never pass."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from tests import test_project_setup_lifecycle as fixture


class RealToolchains(unittest.TestCase):
    setUp = fixture.ProjectSetupLifecycle.setUp
    context = fixture.ProjectSetupLifecycle.context
    plan = fixture.ProjectSetupLifecycle.plan
    install = fixture.ProjectSetupLifecycle.install

    def run_rule(self, rule, identity, language, build_tool, test_framework, raw=None):
        from scripts.project_context import commit_context, load_context
        cli = Path(__file__).resolve().parents[1] / 'scripts/cao.py'
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CAO_', 'PI_', 'CODEX_', 'CLAUDE_'))}
        def call(*args):
            result = subprocess.run([sys.executable, str(cli), *args, '--repo', str(self.repo)],
                         text=True, capture_output=True, env=env, timeout=660)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return json.loads(result.stdout)
        (self.repo / 'openspec').mkdir()
        (self.repo / 'openspec/config.yaml').write_text('context: fixture\n')
        call('init')
        previous = load_context(self.repo)
        values = {'language': language, 'build_tool': build_tool, 'test_framework': test_framework}
        candidate = {'scopes': {'root': {'paths': ['.'], 'technology': {
            k: {'value': v, 'status': 'not_applicable' if v == 'not_applicable' else 'accepted'}
            for k, v in values.items()}}}, 'evidence': []}
        commit_context(self.repo, candidate, previous['revision'])
        raw = raw or self.plan(rule, identity)
        for check in raw['checks']:
            check['timeout_seconds'] = 600
        (self.repo / 'setup-input.json').write_text(json.dumps(raw))
        call('project', 'setup', 'plan', '--declaration', 'setup-input.json')
        call('project', 'setup', 'apply')
        result = call('project', 'setup', 'check')
        self.assertEqual('passed', result['status'], result)
        self.assertTrue(call('project', 'setup', 'status')['ready'])
        self.assertFalse(list((self.repo / '.orchestrator/work').glob('*.json')))
        (self.repo / 'openspec/feature.md').write_text('# Feature\n## Acceptance\n- The new operation works.\n')
        work = call('start', '--requirement', 'openspec/feature.md')['work_item']
        self.assertEqual('active', work['status'])
        before = (self.repo / '.orchestrator/work' / (work['id'] + '.json')).read_text()
        result = call('verification', 'check', '--work', work['id'])
        self.assertEqual('passed', result['status'], result)
        after = json.loads((self.repo / '.orchestrator/work' / (work['id'] + '.json')).read_text())
        self.assertEqual(json.loads(before).get('verification'), after.get('verification'))
        context = call('project', 'context', 'show')['context']
        self.assertEqual(identity, context['scopes']['root']['identity']['values'])

    def test_real_maven_junit(self):
        if not shutil.which('mvn') or not shutil.which('javac'):
            self.skipTest('Maven/JDK unavailable')
        self.run_rule('java-maven', {'name': 'demo', 'group_id': 'org.acme', 'artifact_id': 'demo-service',
                      'namespace': 'org.acme.demo', 'java_version': '17', 'junit_version': '5.11.3'},
                      'Java', 'Maven', 'JUnit 5')

    def test_real_gradle_junit(self):
        if not shutil.which('gradle') or not shutil.which('javac'):
            self.skipTest('Gradle/JDK unavailable')
        self.run_rule('java-gradle', {'name': 'demo', 'group_id': 'org.acme',
                      'namespace': 'org.acme.demo', 'java_version': '17', 'junit_version': '5.11.3'},
                      'Java', 'Gradle', 'JUnit 5')

    def test_real_go_module(self):
        if not shutil.which('go'):
            self.skipTest('Go unavailable')
        self.run_rule('go-module', {'name': 'demo', 'module_path': 'example.org/acme/demo', 'go_version': '1.23'},
                      'Go', 'Go', 'Go test')

    def test_real_typescript_build_and_node_tests(self):
        compiler = Path(__file__).resolve().parents[1] / 'node_modules/typescript'
        if not shutil.which('node') or not shutil.which('npm') or not compiler.is_dir():
            self.skipTest('Node/npm and local TypeScript required')
        version = json.loads((compiler / 'package.json').read_text())['version']
        target = self.repo / 'node_modules/typescript'
        shutil.copytree(compiler, target)
        bin_dir = self.repo / 'node_modules/.bin'
        bin_dir.mkdir()
        if os.name == 'nt':
            (bin_dir / 'tsc.cmd').write_text('@echo off\r\nnode "%~dp0\\..\\typescript\\bin\\tsc" %*\r\n')
        else:
            (bin_dir / 'tsc').symlink_to('../typescript/bin/tsc')
        self.run_rule('typescript-node', {'name': '@acme/demo', 'typescript_version': version},
                      'TypeScript', 'npm', 'node:test')

    def test_explicit_shell_plan_through_first_work_and_probe(self):
        if os.name == 'nt' or not shutil.which('bash'):
            self.skipTest('POSIX bash unavailable')
        from scripts.project_sources import observe_prompt
        identity = {'name': 'shell-demo'}
        raw = {'schema_version': 1, 'rule': {'id': 'explicit', 'version': 1},
               'basis': 'User-selected Shell library with syntax and executable assertions.',
               'identity': identity,
               'evidence': observe_prompt(self.repo, 'Initialize shell-demo with Shell and shell assertions.', 'pi', 'shell-fixture'),
               'files': [
                   {'path': 'lib/ready.sh', 'purpose': 'entry', 'content': 'ready() { printf "%s" ready; }\n'},
                   {'path': 'tests/test_ready.sh', 'purpose': 'test',
                    'content': 'set -eu\n. ./lib/ready.sh\ntest "$(ready)" = ready\nprintf "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0\\n"\n'}],
               'checks': [{'name': 'syntax', 'command': 'bash -n lib/ready.sh', 'capability': 'syntax'},
                          {'name': 'test', 'command': 'bash tests/test_ready.sh', 'capability': 'test'}],
               'required_capabilities': ['syntax', 'test'], 'outputs': []}
        self.run_rule('explicit', identity, 'Shell', 'not_applicable', 'shell assertions', raw)
