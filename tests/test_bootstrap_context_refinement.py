"""Replay broad JUnit choice -> generation answer -> first project baseline.

CLI/host events are real. The isolated Maven fixture executes structural unittest
checks; actual Maven/JDK execution remains in test_setup_toolchains.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from scripts.setup_rules import scaffold, validate_identity

CLI = Path(__file__).resolve().parents[1] / 'scripts/cao.py'


class ContextRefinement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('CAO_', 'PI_', 'CLAUDE_', 'CODEX_'))}
        (self.repo / 'openspec').mkdir()
        (self.repo / 'openspec/config.yaml').write_text('context: fixture\n')
        self.call('init')
        # This suite exercises legacy setup refinements before architecture adoption.
        cfg_path = self.repo / '.orchestrator/config.json'
        cfg = json.loads(cfg_path.read_text())
        cfg.pop('architecture_policy', None)
        cfg_path.write_text(json.dumps(cfg))
        proof = self.proof('Java SpringBoot JUnit Maven')
        self.call('project', 'context', 'set', '--language', 'Java', '--framework', 'SpringBoot',
                  '--test-framework', 'JUnit', '--build-tool', 'Maven', '--source', 'user-specified', '--evidence', proof)
        self.answers = {'name': 'login-service', 'group_id': 'com.example', 'artifact_id': 'login-service',
                        'namespace': 'com.example.login', 'java_version': '17', 'junit_version': '5', 'framework_version': '3.5.16'}

    def call(self, *args, payload=None, expected=0):
        result = subprocess.run([sys.executable, str(CLI), *args, '--repo', str(self.repo)],
                                input=json.dumps(payload) if payload else None, text=True, capture_output=True,
                                env=self.env, timeout=40)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def proof(self, quote):
        self.call('host-event', '--host', 'pi', payload={'event': 'prompt_submit', 'session_id': 'bootstrap-test', 'prompt': quote})
        return next(e['id'] for e in self.call('project', 'inspect')['user_evidence'] if e['quote'] == quote)

    def answer(self, field, value=None):
        value = value or self.answers[field]
        return self.call('project', 'setup', 'identity', '--rule', 'java-maven', '--field', field,
                         '--value', value, '--evidence', self.proof(value))

    def prepare(self):
        for field in self.answers:
            self.answer(field)
        self.call('project', 'setup', 'draft', '--rule', 'java-maven')
        self.call('project', 'setup', 'plan')
        self.call('project', 'setup', 'apply')

    def maven_fixture(self):
        if os.name == 'nt':
            self.skipTest('POSIX fixture launcher')
        folder = self.repo / 'fixture-tools'
        folder.mkdir()
        script = folder / 'maven_checks.py'
        script.write_text("import pathlib, unittest, xml.etree.ElementTree as ET\n"
                          "class GeneratedProject(unittest.TestCase):\n"
                          " def test_framework_manifest_and_entry(self):\n"
                          "  root=ET.parse('pom.xml').getroot(); ns={'m':'http://maven.apache.org/POM/4.0.0'}\n"
                          "  self.assertEqual('3.5.16',root.find('m:parent/m:version',ns).text)\n"
                          "  self.assertNotIn('<version>5</version>',pathlib.Path('pom.xml').read_text())\n"
                          "  self.assertIn('@SpringBootApplication',pathlib.Path('src/main/java/com/example/login/Application.java').read_text())\n"
                          "  self.assertIn('@SpringBootTest',pathlib.Path('src/test/java/com/example/login/ApplicationTests.java').read_text())\n"
                          "unittest.main(verbosity=2)\n")
        launcher = folder / 'mvn'
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(script)) + '\n')
        launcher.chmod(0o755)
        self.env['PATH'] = str(folder) + os.pathsep + self.env.get('PATH', '')

    def finish_baseline(self):
        self.maven_fixture()
        self.assertEqual('passed', self.call('project', 'setup', 'check')['status'])
        self.call('verification', 'materialize')
        self.assertEqual('passed', self.call('verification', 'check', '--project')['status'])

    def test_logged_interview_generates_framework_scaffold_and_reaches_first_work(self):
        for field in list(self.answers)[:-1]:
            result = self.answer(field)
        self.assertEqual('framework_version', result['next_field'])
        context = self.call('project', 'context', 'show')['context']
        self.assertEqual('JUnit 5', context['scopes']['root']['technology']['test_framework']['value'])
        self.assertNotIn('framework', context['scopes']['root'].get('versions', {}))
        blocked = self.call('project', 'setup', 'draft', '--rule', 'java-maven', expected=2)
        self.assertIn('project setup identity', blocked['next']['instruction'])
        self.answer('framework_version')
        raw = self.call('project', 'setup', 'draft', '--rule', 'java-maven')['plan']
        self.assertEqual('SpringBoot', raw['framework_support'])
        self.assertEqual(['build', 'load', 'test'], raw['required_capabilities'])
        self.call('project', 'setup', 'plan')
        self.call('project', 'setup', 'apply')
        self.finish_baseline()
        self.assertFalse(list((self.repo / '.orchestrator/work').glob('*.json')))
        (self.repo / 'openspec/login.md').write_text('# Login\nImplement the approved login behavior.\n')
        work = self.call('start', '--requirement', 'openspec/login.md')['work_item']
        self.assertEqual('active', work['status'])
        self.assertFalse(work.get('verification'))

    def test_legacy_initialized_family_can_be_corrected_without_migration_work(self):
        self.prepare()
        self.call('project', 'context', 'set', '--test-framework', 'JUnit', '--source', 'user-specified', '--evidence', self.proof('JUnit'))
        error = self.call('verification', 'materialize', expected=2)
        self.assertEqual('project_initialization', error['recovery']['owner'])
        self.assertEqual('root.test_framework', error['diagnostics'][0]['field'])
        self.assertIn('Do not read/search CAO implementation scripts', error['next']['instruction'])
        self.assertFalse(self.call('project', 'context', 'diff')['context']['ready'])
        self.call('project', 'context', 'set', '--test-framework', 'JUnit 5', '--source', 'user-specified', '--evidence', self.proof('JUnit 5'))
        self.call('project', 'setup', 'plan')
        self.call('project', 'setup', 'apply')
        self.finish_baseline()

    def test_preexisting_proposal_has_work_free_initialization_apply_and_cas(self):
        self.prepare()
        self.call('project', 'context', 'set', '--test-framework', 'JUnit', '--source', 'user-specified', '--evidence', self.proof('JUnit'))
        proposal = self.call('project', 'context', 'change', 'propose', '--test-framework', 'JUnit 5',
                             '--source', 'user-specified', '--evidence', self.proof('JUnit 5'))
        self.assertEqual('project_initialization', proposal['owner'])
        self.assertNotIn('--work <id>', proposal['next'])
        applied = self.call('project', 'context', 'change', 'apply', '--change', proposal['change']['id'])
        self.assertEqual('JUnit 5', applied['context']['scopes']['root']['technology']['test_framework']['value'])
        self.assertFalse(list((self.repo / '.orchestrator/work').glob('*.json')))
        self.assertEqual('unchanged', self.call('project', 'context', 'change', 'apply', '--change', proposal['change']['id'])['status'])
        self.call('project', 'context', 'set', '--delivery', 'Docker', '--source', 'user-specified', '--evidence', self.proof('Docker'))
        self.assertIn('REVISION_CONFLICT', self.call('project', 'context', 'change', 'apply', '--change', proposal['change']['id'], expected=2)['error'])

    def test_existing_product_does_not_gain_bootstrap_migration_exemption(self):
        self.prepare()
        # No CAO-owned setup record: this now models an established product.
        (self.repo / '.orchestrator/project/setup/root.json').unlink()
        error = self.call('project', 'context', 'set', '--test-framework', 'JUnit 4',
                          '--source', 'user-specified', '--evidence', self.proof('JUnit 4'), expected=2)
        self.assertIn('PROJECT_CHANGE_REQUIRED', error['error'])
        self.assertEqual('migration_work', error['recovery']['owner'])

    def test_version_generation_is_not_a_dependency_release(self):
        identity = {k: v for k, v in self.answers.items() if k != 'framework_version'}
        for rule in ('java-maven', 'java-gradle'):
            with self.subTest(rule=rule), self.assertRaisesRegex(ValueError, 'PROJECT_DEPENDENCY_VERSION_REQUIRED'):
                scaffold(rule, identity)
            raw = scaffold(rule, self.answers, framework='Spring Boot')
            self.assertIn('3.5.16', '\n'.join(f['content'] for f in raw['files']))
            self.assertFalse(any('<version>5</version>' in f['content'] or 'junit-jupiter:5\'' in f['content'] for f in raw['files']))
        for value in ('5', '5.10', 'latest', '5.+'):
            with self.assertRaises(ValueError):
                validate_identity('java-maven', {'junit_version': value}, partial=True)

    def test_unconfirmed_or_mismatched_framework_release_cannot_be_applied(self):
        for field in self.answers:
            self.answer(field)
        draft = self.call('project', 'setup', 'draft', '--rule', 'java-maven')
        path = self.repo / draft['input']
        raw = json.loads(path.read_text())
        manifest = next(entry for entry in raw['files'] if entry['path'] == 'pom.xml')
        manifest['content'] = manifest['content'].replace('<version>3.5.16</version>', '<version>3.3.5</version>') + '\n<!-- 3.5.16 -->\n'
        path.write_text(json.dumps(raw))
        failure = self.call('project', 'setup', 'plan', expected=2)
        self.assertIn('FRAMEWORK_VERSION_MISMATCH', failure['error'])
        self.assertIn('draft --rule java-maven', failure['next']['instruction'])
        self.call('project', 'setup', 'draft', '--rule', 'java-maven', '--refresh')
        self.call('project', 'setup', 'plan')
        # Source labels cannot substitute for actual user answers.
        error = self.call('project', 'context', 'set', '--storage', 'Redis', '--source', 'user-specified',
                          '--evidence', self.proof('PostgreSQL'), expected=2)
        self.assertIn('FIELD_NOT_COVERED', error['error'])

    def test_bound_work_and_explicit_generations_keep_migration_guards(self):
        self.prepare()
        self.finish_baseline()
        (self.repo / 'openspec/login.md').write_text('# Login\nImplement login.\n')
        work = self.call('start', '--requirement', 'openspec/login.md')['work_item']
        proof = self.proof('JUnit 4')
        error = self.call('project', 'context', 'set', '--test-framework', 'JUnit 4',
                          '--source', 'user-specified', '--evidence', proof, expected=2)
        self.assertIn('PROJECT_CHANGE_REQUIRED', error['error'])
        proposal = self.call('project', 'context', 'change', 'propose', '--test-framework', 'JUnit 4',
                             '--source', 'user-specified', '--evidence', proof)
        self.assertEqual('migration_work', proposal['owner'])
        self.call('project', 'context', 'change', 'apply', '--change', proposal['change']['id'], expected=0)
        self.assertEqual([proposal['change']['id']], self.call('project', 'context', 'show')['context']['active_changes'])
        record = json.loads((self.repo / '.orchestrator/project/changes' / (proposal['change']['id'] + '.json')).read_text())
        self.assertEqual(work['id'], record['owner_work'])

    def test_confirmed_framework_version_is_reused_and_jupiter_alias_is_consistent(self):
        proof = self.proof('Spring Boot 3.5.16')
        self.call('project', 'context', 'set', '--framework', 'Spring Boot', '--version', 'framework=3.5.16',
                  '--source', 'user-specified', '--evidence', proof)
        for field in list(self.answers)[:-1]:
            result = self.answer(field)
        self.assertTrue(result['ready'])
        self.assertEqual('3.5.16', result['values']['framework_version'])
        self.assertEqual(proof, result['sources']['framework_version']['id'])
        self.call('project', 'context', 'set', '--test-framework', 'JUnit Jupiter', '--source', 'user-specified',
                  '--evidence', self.proof('JUnit Jupiter'))
        self.call('project', 'setup', 'draft', '--rule', 'java-maven')
        self.call('project', 'setup', 'plan')
        self.call('project', 'setup', 'apply')
        self.assertFalse(self.call('project', 'context', 'diff')['differences'])

    def test_resume_saved_generation_refines_legacy_context_without_reasking(self):
        self.prepare()
        self.call('project', 'context', 'set', '--test-framework', 'JUnit', '--source', 'user-specified', '--evidence', self.proof('JUnit'))
        answers = self.call('project', 'setup', 'identity', '--rule', 'java-maven')
        self.assertTrue(answers['ready'])
        self.assertIsNone(answers['next_field'])
        self.assertEqual('JUnit 5', self.call('project', 'context', 'show')['context']['scopes']['root']['technology']['test_framework']['value'])

    def test_framework_load_capability_cannot_be_omitted(self):
        for field in self.answers:
            self.answer(field)
        draft = self.call('project', 'setup', 'draft', '--rule', 'java-maven')
        path = self.repo / draft['input']
        raw = json.loads(path.read_text())
        raw['checks'] = [c for c in raw['checks'] if c['capability'] != 'load']
        raw['required_capabilities'].remove('load')
        path.write_text(json.dumps(raw))
        failure = self.call('project', 'setup', 'plan', expected=2)
        self.assertIn('FRAMEWORK_CHECK_REQUIRED', failure['error'])
        self.assertIn('load check', failure['next']['instruction'])

    def test_newer_confirmed_version_is_not_overwritten_by_saved_interview(self):
        self.prepare()
        proof = self.proof('Spring Boot 3.5.17')
        self.call('project', 'context', 'set', '--framework', 'Spring Boot', '--version', 'framework=3.5.17',
                  '--source', 'user-specified', '--evidence', proof)
        resumed = self.call('project', 'setup', 'identity', '--rule', 'java-maven')
        self.assertEqual('3.5.17', resumed['values']['framework_version'])
        self.assertEqual(proof, resumed['sources']['framework_version']['id'])
        self.assertEqual('3.5.17', self.call('project', 'context', 'show')['context']['scopes']['root']['versions']['framework'])

    def test_invalid_prepared_input_has_structured_recovery_without_source_search(self):
        self.prepare()
        (self.repo / '.orchestrator/inputs/project-root/setup.json').write_text('[1]')
        failure = self.call('project', 'setup', 'plan', expected=2)
        self.assertEqual('PROJECT_SETUP_PLAN_INVALID', failure['error'])
        self.assertIn('Do not read/search CAO implementation scripts', failure['next']['instruction'])
