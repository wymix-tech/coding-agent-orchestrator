"""Real CLI project setup and dual-acceptance regression tests."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.project_context import ROOT, commit_context, fingerprint, load_context
from scripts.project_sources import observe_prompt

CLI = Path(__file__).resolve().parents[1] / 'scripts/cao.py'


class ProjectAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('CAO_', 'PI_', 'CLAUDE_', 'CODEX_'))}
        (self.repo / 'openspec').mkdir()
        (self.repo / 'openspec/config.yaml').write_text('context: test\n')
        self.check('init')

    def cli(self, *args, payload=None):
        run = subprocess.run([sys.executable, str(CLI), *args, '--repo', str(self.repo)],
                             input=json.dumps(payload) if payload else None, capture_output=True,
                             text=True, timeout=30, env=self.env)
        try:
            out = json.loads(run.stdout)
        except ValueError:
            self.fail((run.stdout, run.stderr))
        return run.returncode, out

    def check(self, *args, payload=None):
        # These delivery fixtures explicitly accept changed technical standards;
        # baseline-review behavior is covered by the dedicated integrity suites.
        if args[:2] == ('verification', 'configure') and '--plan-only' not in args and '--replace' not in args:
            args = (*args, '--replace')
        rc, out = self.cli(*args, payload=payload)
        if rc == 0 and args[:2] == ('verification', 'configure') and out.get('acceptance_review_required'):
            self.check('reopen', '--reanchor', '--reason', 'Review delivery fixture verification changes')
        self.assertEqual(rc, 0, out)
        return out

    def background(self, scope='root', path=None, language='Python', build='setuptools', tests='unittest'):
        quote = f'Use {language}, {build}, {tests}; no application framework.'
        ref = observe_prompt(self.repo, quote, 'pi', 'setup')
        args = ['project', 'context', 'set', '--scope', scope, '--language', language,
                '--build-tool', build, '--test-framework', tests,
                '--source', 'user-specified', '--evidence', ref]
        if path:
            args += ['--path', path]
        return self.check(*args)

    def plan(self, *scopes):
        args = ['verification', 'configure', '--plan-only', '--test-script', 'python -m unittest discover -s tests -v']
        for scope in scopes:
            args += ['--scope', scope]
        return self.check(*args)

    def start(self, name='feature', scope=None):
        # These dual-acceptance fixtures model an existing product. Empty-project
        # initialization is tested separately through the real setup lifecycle.
        from scripts.project_context import load_context
        ctx = load_context(self.repo)
        selected = ctx['scopes'][scope or 'root'] if ctx else None
        if selected:
            base = self.repo / selected['paths'][0]
            base.mkdir(parents=True, exist_ok=True)
            if selected['technology']['language'].get('value') == 'Java':
                entry = base / 'src/main/java/Fixture.java'
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text('class Fixture {}\n')
            elif selected['technology']['language'].get('status') != 'not_applicable':
                manifest = base / 'pyproject.toml'
                if not manifest.exists():
                    manifest.write_text('[project]\nname = "fixture-product"\nversion = "0.1.0"\n')
        # Establish an actual preparation suite before the first business Work.
        # Business tests are deliberately created later by these SDD fixtures.
        cfg = json.loads((self.repo / '.orchestrator/config.json').read_text())
        if not cfg['verification'].get('scripts'):
            baseline = self.repo / 'preparation_suite.py'
            baseline.write_text('import unittest\nclass Preparation(unittest.TestCase):\n def test_load(self): self.assertEqual(2 + 2, 4)\nunittest.main(verbosity=2)\n')
            command = f'{shlex.quote(sys.executable)} preparation_suite.py'
            command_args = ['verification', 'configure', '--test-script', command]
            if scope:
                command_args += ['--scope', scope]
            self.check(*command_args)
        check_args = ['verification', 'check', '--project']
        if scope:
            check_args += ['--scope', scope]
        self.check(*check_args)
        path = self.repo / 'openspec' / (name + '.md')
        path.write_text('# Feature\n- Correct behavior is observed.\n')
        args = ['start', '--requirement', path.relative_to(self.repo).as_posix()]
        if scope:
            args += ['--scope', scope]
        return self.check(*args)['work_item']['id']

    def work(self, wid):
        return json.loads((self.repo / '.orchestrator/work' / (wid + '.json')).read_text())

    def prepare_delivery(self):
        self.background()
        self.plan()
        wid = self.start()
        tests = self.repo / 'tests'
        tests.mkdir()
        (tests / 'test_behavior.py').write_text('import unittest\nclass Behavior(unittest.TestCase):\n def test_behavior(self): self.assertEqual(2 + 2, 4)\n')
        self.check('verification', 'configure', '--test-script', f'{shlex.quote(sys.executable)} -m unittest discover -s tests -v')
        self.check('path', 'assess', '--profile', 'light', '--reason', 'bounded local behavior')
        (self.repo / 'openspec/spec.md').write_text('# SPEC\n## Acceptance\n- Correct behavior is observed.\n')
        self.check('sdd', 'spec', 'bind', '--spec', 'openspec/spec.md', '--criteria-section', 'Acceptance')
        items = self.check('acceptance', 'criteria')['criteria']
        script = self.repo / 'accept.py'
        script.write_text("""import json, os, subprocess, sys
r = json.loads(os.environ['CAO_SDD_ACCEPTANCE_REQUEST'])
p = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], stdout=sys.stderr, stderr=sys.stderr)
print(json.dumps({'run_id':r['run_id'], 'input_sha256':r['input_sha256'], 'provider':r['provider'], 'items':[{'id':c['id'],'check':{'criterion_id':c['id'],'run_id':r['run_id'],'selector':'fixture behavior assertion','assertion':c['description'],'observed':'actual fixture command result'},'status':'passed' if p.returncode == 0 else 'failed','evidence':[{'path':'tests/test_behavior.py','reason':'Executed behavior assertion'}]} for c in r['declaration']['criteria']]}))
sys.exit(p.returncode)
""")
        declaration = {'spec': 'openspec/spec.md',
                       'criteria': [{'id': item['id'], 'source': 'openspec/spec.md', 'kind': 'requirement',
                                     'description': item['description'], 'covers': [item['id']]} for item in items],
                       'executor': {'mode': 'command', 'entrypoint': f'{shlex.quote(sys.executable)} accept.py'}}
        (self.repo / 'declaration.json').write_text(json.dumps(declaration))
        self.check('acceptance', 'configure', '--declaration', 'declaration.json')
        self.check('reopen', '--reanchor', '--reason', 'Review real acceptance sources and checks')
        return wid

    def test_no_work_background_and_plan_do_not_write_product(self):
        self.background()
        self.plan()
        self.assertEqual(list((self.repo / '.orchestrator/work').glob('*.json')), [])
        self.assertFalse((self.repo / 'tests').exists())
        self.assertFalse((self.repo / 'pom.xml').exists())
        self.assertEqual(self.check('bootstrap')['next']['stage'], 'project_setup')

    def test_missing_context_blocks_start_with_recovery(self):
        (self.repo / 'openspec/a.md').write_text('Make feature')
        rc, out = self.cli('start', '--requirement', 'openspec/a.md')
        self.assertEqual(rc, 2)
        self.assertEqual(out['error'], 'PROJECT_CONTEXT_REQUIRED')
        self.assertEqual(self.check('bootstrap')['next']['stage'], 'project_setup')

    def test_background_without_plan_has_reachable_recovery(self):
        self.background()
        self.assertEqual(self.check('bootstrap')['next']['reason'], 'PROJECT_SETUP_REQUIRED')
        self.plan()
        self.start()

    def test_java_does_not_authorize_framework_or_build(self):
        ref = observe_prompt(self.repo, 'Use Java', 'pi', 's')
        rc, out = self.cli('project', 'context', 'set', '--language', 'Java', '--framework', 'Spring Boot', '--source', 'user-specified', '--evidence', ref)
        self.assertEqual(rc, 2)
        self.assertIn('PROJECT_SOURCE_FIELD_NOT_COVERED', out['error'])
        self.assertIsNone(load_context(self.repo))

    def test_continue_is_not_delegation(self):
        ref = observe_prompt(self.repo, '继续', 'pi', 's')
        rc, out = self.cli('project', 'context', 'set', '--language', 'Python', '--source', 'user-delegated', '--evidence', ref)
        self.assertEqual(rc, 2)
        self.assertIn('PROJECT_DELEGATION_REQUIRED', out['error'])

    def test_tooling_directories_are_not_product_evidence(self):
        path = self.repo / '_bmad'
        path.mkdir()
        (path / 'package.json').write_text('{"dependencies":{"typescript":"1"}}')
        self.assertTrue(self.check('project', 'inspect')['inspection']['empty_product'])

    def test_repository_established_source_is_checked(self):
        (self.repo / 'pom.xml').write_text('<project><artifactId>spring-boot-starter-test</artifactId></project>')
        self.check('project', 'context', 'set', '--language', 'Java', '--framework', 'Spring Boot',
                   '--build-tool', 'Maven', '--test-framework', 'JUnit 5', '--source', 'repository-established', '--evidence', 'pom.xml')
        self.assertTrue(self.check('project', 'context', 'show')['state']['ready'])

    def test_repeated_setting_is_idempotent(self):
        one = self.background()['context']
        two = self.background()['context']
        self.assertEqual(one, two)
        self.assertEqual(len(list((self.repo / ROOT / 'revisions').glob('*.json'))), 1)

    def test_evidence_only_change_keeps_semantic_fingerprint(self):
        one = self.background()['context']
        ref = observe_prompt(self.repo, 'I confirm Python again.', 'pi', 'new')
        two = self.check('project', 'context', 'set', '--language', 'Python', '--source', 'user-specified', '--evidence', ref)['context']
        self.assertEqual(one['content_sha256'], two['content_sha256'])
        self.assertNotEqual(one['revision'], two['revision'])

    def test_expected_revision_rejects_overwrite(self):
        self.background()
        ref = observe_prompt(self.repo, 'JAR', 'pi', 's')
        rc, out = self.cli('project', 'context', 'set', '--delivery', 'JAR', '--source', 'user-specified', '--evidence', ref, '--expected-revision', 'old')
        self.assertEqual(rc, 2)
        self.assertIn('PROJECT_CONTEXT_REVISION_CONFLICT', out['error'])

    def test_crash_before_pointer_never_accepts_orphan_revision(self):
        self.background()
        old = load_context(self.repo)
        candidate = json.loads(json.dumps(old))
        candidate['scopes']['root']['technology']['delivery'] = {'value': 'JAR', 'status': 'accepted'}
        from scripts.state_store import atomic_json
        def fail_pointer(path, value):
            if path.name == 'context.json':
                raise OSError('injected crash')
            atomic_json(path, value)
        with patch('scripts.project_context.atomic_json', side_effect=fail_pointer):
            with self.assertRaises(OSError):
                commit_context(self.repo, candidate, old['revision'])
        self.assertEqual(load_context(self.repo), old)
        self.assertEqual(len(self.check('project', 'decisions')['revisions']), 1)

    def test_corrupt_context_is_not_overwritten(self):
        self.background()
        path = self.repo / ROOT / 'context.json'
        path.write_text('{broken')
        rc, out = self.cli('project', 'inspect')
        self.assertEqual(rc, 2)
        self.assertEqual(path.read_text(), '{broken')

    def test_plan_is_not_executed_by_verify(self):
        self.background()
        self.plan()
        (self.repo / 'pyproject.toml').write_text('[project]\nname="existing"\nversion="0.1"\n')
        (self.repo / 'openspec/feature.md').write_text('# Feature\nActual requirement\n')
        rc, out = self.cli('start', '--requirement', 'openspec/feature.md')
        self.assertEqual(rc, 2, out)
        self.assertEqual('VERIFICATION_BASELINE_REQUIRED', out['error'])
        rc, out = self.cli('verify')
        self.assertNotEqual(rc, 0, out)
        self.assertFalse((self.repo / 'tests').exists())

    def test_light_needs_real_behavior_acceptance(self):
        wid = self.prepare_delivery()
        self.check('verify')
        rc, out = self.cli('done')
        self.assertEqual(rc, 2, out)
        state = self.check('acceptance', 'status')
        self.assertEqual(state['technical_acceptance']['status'], 'passed')
        self.assertFalse(state['task_acceptance']['fresh'])
        self.check('acceptance', 'run')
        self.assertTrue(self.check('acceptance', 'status')['ready'])
        self.check('done')
        self.assertEqual(self.work(wid)['status'], 'completed')

    def test_code_change_stales_both_layers(self):
        self.prepare_delivery()
        self.check('verify')
        self.check('acceptance', 'run')
        (self.repo / 'product.py').write_text('changed = True\n')
        state = self.check('acceptance', 'status')
        self.assertEqual(state['technical_acceptance']['status'], 'stale')
        self.assertFalse(state['task_acceptance']['fresh'])
        self.assertEqual(self.cli('done')[0], 2)

    def test_partial_coverage_cannot_pass(self):
        self.prepare_delivery()
        path = self.repo / 'declaration.json'
        declaration = json.loads(path.read_text())
        declaration['criteria'] = declaration['criteria'][-1:]
        path.write_text(json.dumps(declaration))
        self.check('acceptance', 'configure', '--declaration', 'declaration.json', '--replace')
        self.check('reopen', '--reanchor', '--reason', 'Review partial fixture')
        self.check('verify')
        rc, out = self.cli('acceptance', 'run')
        self.assertEqual(rc, 2, out)
        self.assertIn('TASK_ACCEPTANCE_COVERAGE_INCOMPLETE', out['error'])

    def test_bind_legacy_work_preserves_progress_and_run(self):
        self.background()
        self.plan()
        wid = self.start()
        path = self.repo / '.orchestrator/work' / (wid + '.json')
        work = self.work(wid)
        work.pop('project_binding')
        work['execution']['step'] = 'implementation'
        work['verification'] = {'version': 2, 'run_id': 'old-run', 'status': 'passed'}
        path.write_text(json.dumps(work))
        self.check('project', 'bind', '--work', wid)
        current = self.work(wid)
        self.assertEqual(current['execution'], work['execution'])
        self.assertEqual(current['verification'], work['verification'])

    def test_second_work_inherits_standard_without_pass(self):
        self.prepare_delivery()
        self.check('verify')
        self.check('acceptance', 'run')
        self.check('done')
        wid = self.start('second')
        self.assertIsNone(self.work(wid)['verification'])
        self.assertNotEqual(self.check('bootstrap')['next']['stage'], 'project_setup')

    def test_module_selection_is_explicit(self):
        self.background('backend', 'backend')
        self.background('frontend', 'frontend', 'TypeScript', 'npm', 'vitest')
        self.assertEqual(self.check('bootstrap')['next']['reason'], 'PROJECT_SCOPE_REQUIRED')
        self.plan('backend', 'frontend')
        self.start(scope='backend')

    def test_user_prompt_capture_keeps_original_request(self):
        first = 'Build a login feature with Java.'
        self.check('host-event', '--host', 'pi', payload={'event': 'prompt_submit', 'session_id': 's', 'prompt': first})
        self.check('host-event', '--host', 'pi', payload={'event': 'prompt_submit', 'session_id': 's', 'prompt': 'Use Maven and JUnit 5.'})
        request = json.loads((self.repo / '.orchestrator/startup-request.json').read_text())
        self.assertEqual(request['prompt'], first)
        self.assertEqual(len(self.check('project', 'inspect')['user_evidence']), 2)

    def test_negated_or_alternative_choices_are_not_authority(self):
        for quote, value in [('Do not use Java', 'Java'), ('Use JavaScript', 'Java'), ('Maven or Gradle?', 'Maven')]:
            with self.subTest(quote=quote):
                ref = observe_prompt(self.repo, quote, 'pi', 's')
                rc, out = self.cli('project', 'context', 'set', '--language', value, '--source', 'user-specified', '--evidence', ref)
                self.assertEqual(rc, 2, out)

    def test_bound_question_selection_preserves_actual_option(self):
        event = {'session_id': 's', 'tool_name': 'ask_user_question', 'toolCallId': 'stack-question'}
        self.check('host-event', '--host', 'pi', payload={**event, 'event': 'pre_tool',
                   'input': {'questions': [{'question': 'Which stack?', 'options': [
                       {'label': 'Python stack', 'description': 'Python, setuptools, unittest'}]}]}})
        self.check('host-event', '--host', 'pi', payload={**event, 'event': 'post_tool',
                   'details': {'answers': [{'selectedLabel': 'Python stack'}]}})
        evidence = self.check('project', 'inspect')['user_evidence']
        self.assertEqual(len(evidence), 1)
        self.check('project', 'context', 'set', '--language', 'Python', '--build-tool', 'setuptools',
                   '--test-framework', 'unittest', '--source', 'user-specified', '--evidence', evidence[0]['id'])

    def test_unbound_question_result_does_not_supply_choices(self):
        self.check('host-event', '--host', 'pi', payload={'event': 'post_tool', 'session_id': 's',
                   'tool_name': 'ask_user_question', 'toolCallId': 'unseen',
                   'details': {'answers': [{'answer': 'Python setuptools unittest'}]}})
        self.assertEqual(self.check('project', 'inspect')['user_evidence'], [])

    def test_requirement_change_can_retain_technical_run(self):
        self.prepare_delivery()
        self.check('verify')
        self.check('acceptance', 'run')
        (self.repo / 'openspec/feature.md').write_text('# Feature\n- Additional confirmed behavior.\n')
        state = self.check('acceptance', 'status')
        self.assertTrue(state['technical_acceptance']['fresh'])
        self.assertFalse(state['task_acceptance']['fresh'])
        self.assertEqual(self.cli('done')[0], 2)

    def test_external_report_change_stales_task(self):
        self.prepare_delivery()
        self.check('verify')
        self.check('acceptance', 'run')
        path = self.repo / 'tests/test_behavior.py'
        path.write_text(path.read_text() + '\n# changed evidence\n')
        self.assertFalse(self.check('acceptance', 'status')['task_acceptance']['fresh'])

    def test_both_acceptance_results_do_not_bypass_blocker(self):
        self.prepare_delivery()
        self.check('verify')
        self.check('acceptance', 'run')
        self.check('block', '--reason', 'Independent approval remains pending')
        state = self.check('acceptance', 'status')
        self.assertTrue(state['technical_acceptance']['fresh'])
        self.assertTrue(state['task_acceptance']['fresh'])
        self.assertFalse(state['lifecycle_ready'])
        self.assertEqual(self.cli('done')[0], 2)

    def test_one_real_run_can_supply_both_parts(self):
        self.prepare_delivery()
        script = self.repo / 'shared.py'
        script.write_text("""import json, pathlib, subprocess, sys
p=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'], stdout=sys.stderr, stderr=sys.stderr)
count=pathlib.Path('.orchestrator/shared-count.txt')
count.write_text(str(int(count.read_text())+1 if count.exists() else 1))
d=json.loads(pathlib.Path('declaration.json').read_text())
print(json.dumps({'items':[{'id':c['id'],'check':{'criterion_id':c['id'],'run_id':'shared-technical-fixture','selector':'fixture behavior assertion','assertion':c['description'],'observed':'actual fixture command result'},'status':'passed' if p.returncode==0 else 'failed','evidence':[{'path':'tests/test_behavior.py','reason':'Actual assertion in this shared run'}]} for c in d['criteria']]}))
sys.exit(p.returncode)
""")
        path = self.repo / 'declaration.json'
        declaration = json.loads(path.read_text())
        command = f'{shlex.quote(sys.executable)} shared.py'
        declaration['executor']['entrypoint'] = command
        path.write_text(json.dumps(declaration))
        self.check('acceptance', 'configure', '--declaration', 'declaration.json', '--replace')
        self.check('verification', 'configure', '--replace', '--test-script', command)
        self.check('reopen', '--reanchor', '--reason', 'Review shared command and assertion mapping')
        self.check('verify')
        self.check('acceptance', 'reuse', '--script', 'test-1')
        self.assertEqual((self.repo / '.orchestrator/shared-count.txt').read_text(), '1')
        self.assertTrue(self.check('acceptance', 'status')['ready'])

    def test_migration_owner_can_advance_and_other_scope_is_unchanged(self):
        self.background('backend', 'backend')
        self.background('frontend', 'frontend', 'TypeScript', 'npm', 'vitest')
        self.plan('backend', 'frontend')
        wid = self.start(scope='backend')
        old = load_context(self.repo)
        frontend = fingerprint(old, ['frontend'])
        ref = observe_prompt(self.repo, 'Change the build tool to uv.', 'pi', 'migration')
        change = self.check('project', 'context', 'change', 'propose', '--scope', 'backend', '--build-tool', 'uv',
                            '--source', 'user-specified', '--evidence', ref)['change']
        self.check('project', 'context', 'change', 'apply', '--change', change['id'], '--work', wid)
        new = load_context(self.repo)
        self.assertEqual(frontend, fingerprint(new, ['frontend']))
        self.assertNotEqual(old['content_sha256'], new['content_sha256'])
        self.assertEqual(self.work(wid)['project_change'], change['id'])
        from scripts.project_context import context_state
        self.assertTrue(context_state(self.repo, ['backend'], self.work(wid))['ready'])
        self.assertEqual(context_state(self.repo, ['backend'])['reason'], 'PROJECT_CONTEXT_CHANGE_IN_PROGRESS')
        self.assertTrue(context_state(self.repo, ['frontend'])['ready'])

    def test_registered_cross_install_launcher_keeps_write_checks(self):
        from scripts.cao_tool_policy import _readonly_tokens, tool_is_mutating
        package = Path(__file__).resolve().parents[1]
        settings = self.repo / '.pi/settings.json'
        settings.parent.mkdir()
        settings.write_text(json.dumps({'packages': [str(package)]}))
        with patch('scripts.cao_tool_policy.package_root_dir', return_value=self.repo / 'other-install'):
            self.assertTrue(_readonly_tokens([str(package / 'cao'), 'project', 'inspect'], self.repo))
            self.assertFalse(_readonly_tokens([str(self.repo / 'fake-cao'), 'project', 'inspect'], self.repo))
        self.assertTrue(tool_is_mutating({'tool_name': 'bash', 'tool_input': {'command': 'cao project inspect > product.py'}}))

    def test_known_runner_mismatch_is_rejected(self):
        self.background(language='Java', build='Maven', tests='JUnit 5')
        rc, out = self.cli('verification', 'configure', '--test-script', 'pytest -q')
        self.assertEqual(rc, 2, out)
        self.assertIn('PROJECT_VERIFICATION_CONTEXT_MISMATCH', out['error'])

    def test_technical_failure_does_not_turn_task_evidence_into_success(self):
        self.prepare_delivery()
        self.check('verify')
        self.check('acceptance', 'run')
        rc, out = self.cli('verify', '--script', f'{shlex.quote(sys.executable)} -c "raise SystemExit(1)"')
        self.assertEqual(rc, 1, out)
        state = self.check('acceptance', 'status')
        self.assertEqual(state['technical_acceptance']['status'], 'failed')
        self.assertTrue(state['task_acceptance']['fresh'])
        self.assertEqual(self.cli('done')[0], 2)

    def test_semantic_casing_change_does_not_change_fingerprint(self):
        before = self.background()['context']['content_sha256']
        ref = observe_prompt(self.repo, 'PYTHON', 'pi', 's')
        out = self.check('project', 'context', 'set', '--language', 'PYTHON', '--source', 'user-specified', '--evidence', ref)
        self.assertEqual(before, out['context']['content_sha256'])

    def test_version_is_explicit_and_source_checked(self):
        self.background()
        ref = observe_prompt(self.repo, 'Use Python 3.12.', 'pi', 's')
        context = self.check('project', 'context', 'set', '--language', 'Python', '--version', 'language=3.12',
                             '--source', 'user-specified', '--evidence', ref)['context']
        self.assertEqual(context['scopes']['root']['versions'], {'language': '3.12'})

    def test_non_object_configuration_is_preserved_as_error(self):
        path = self.repo / '.orchestrator/config.json'
        path.write_text('[]')
        rc, out = self.cli('project', 'inspect')
        self.assertEqual(rc, 2, out)
        self.assertEqual(path.read_text(), '[]')

    def test_concurrent_expected_revision_updates_do_not_overwrite(self):
        from concurrent.futures import ThreadPoolExecutor
        self.background()
        revision = load_context(self.repo)['revision']
        def update(value):
            ref = observe_prompt(self.repo, 'Use ' + value, 'pi', value)
            return self.cli('project', 'context', 'set', '--delivery', value, '--source', 'user-specified',
                            '--evidence', ref, '--expected-revision', revision)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ['JAR', 'ZIP']))
        self.assertEqual(sorted(code for code, _ in results), [0, 2])
        self.assertEqual(len(self.check('project', 'decisions')['revisions']), 2)

    def test_migration_stabilizes_only_after_actual_dual_acceptance(self):
        wid = self.prepare_delivery()
        ref = observe_prompt(self.repo, 'Change the delivery to ZIP.', 'pi', 'migration')
        change = self.check('project', 'context', 'change', 'propose', '--delivery', 'ZIP',
                            '--source', 'user-specified', '--evidence', ref)['change']
        self.check('project', 'context', 'change', 'apply', '--change', change['id'], '--work', wid)
        self.assertEqual(self.cli('done')[0], 2)
        (self.repo / 'package.py').write_text("import zipfile\nwith zipfile.ZipFile('delivery.zip','w') as z: z.write('tests/test_behavior.py')\nwith zipfile.ZipFile('delivery.zip') as z: assert z.testzip() is None\n")
        self.check('verification', 'configure', '--replace', '--test-script', f'{shlex.quote(sys.executable)} -m unittest discover -s tests -v',
                   '--verify-script', f'{shlex.quote(sys.executable)} package.py', '--output', 'delivery.zip')
        self.check('reopen', '--reanchor', '--reason', 'Review ZIP packaging and existing behavior checks')
        self.check('verify')
        self.assertTrue((self.repo / 'delivery.zip').is_file())
        self.check('acceptance', 'run')
        accepted = load_context(self.repo)['content_sha256']
        self.check('done')
        context = load_context(self.repo)
        self.assertEqual(context['active_changes'], [])
        self.assertEqual(context['content_sha256'], accepted)
        self.assertEqual(context['scopes']['root']['technology']['delivery']['value'], 'ZIP')
        self.assertEqual(self.work(wid)['status'], 'completed')

    def test_native_intent_can_derive_acceptance_without_handwritten_json(self):
        self.prepare_delivery()
        self.check('acceptance', 'configure', '--executor-command', f'{shlex.quote(sys.executable)} accept.py', '--replace')
        state = self.check('acceptance', 'status')
        self.assertEqual(state['task_acceptance']['native_formal_stage'], 'not_applicable')
        self.assertNotEqual(state['task_acceptance']['status'], 'passed')
        self.check('reopen', '--reanchor', '--reason', 'Review derived original requirement and native Intent coverage')
        self.check('verify')
        self.check('acceptance', 'run')
        self.assertTrue(self.check('acceptance', 'status')['ready'])

    def test_zero_tests_and_noop_cannot_prove_required_test_capability(self):
        self.background()
        self.plan()
        self.start()
        (self.repo / 'tests').mkdir()
        (self.repo / 'zero.py').write_text("import subprocess,sys\nsubprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'])\n")
        command = f'{shlex.quote(sys.executable)} zero.py'
        self.check('verification', 'configure', '--test-script', command)
        self.check('path', 'assess', '--profile', 'light', '--reason', 'bounded receipt test')
        rc, out = self.cli('verify')
        self.assertEqual(rc, 2, out)
        self.assertEqual(out['verification']['results'][0]['reason'], 'ZERO_TESTS_DISCOVERED')
        self.check('verification', 'configure', '--test-script', 'true', '--replace')
        self.check('reopen', '--reanchor', '--reason', 'Deliberately invalid fixture baseline')
        rc, out = self.cli('verify')
        self.assertEqual(rc, 2, out)
        self.assertEqual(out['verification']['results'][0]['reason'], 'TEST_EXECUTION_NOT_PROVEN')

    def test_acceptance_executor_cannot_claim_pass_from_zero_tests(self):
        self.prepare_delivery()
        (self.repo / 'empty-tests').mkdir()
        script = self.repo / 'accept.py'
        script.write_text(script.read_text().replace("'-s', 'tests'", "'-s', 'empty-tests'").replace("print(json.dumps(", "p.returncode = 0\nprint(json.dumps("))
        self.check('verify')
        rc, out = self.cli('acceptance', 'run')
        self.assertEqual(rc, 2, out)
        self.assertEqual(out['run']['status'], 'blocked')
        self.assertEqual(out['run']['reason'], 'SDD_ACCEPTANCE_ZERO_TESTS_DISCOVERED')

    def test_documentation_scope_uses_real_document_checks_without_unrelated_build(self):
        ref = observe_prompt(self.repo, '纯文档项目，语言、构建工具和测试框架不适用。', 'pi', 'docs')
        self.check('project', 'context', 'set', '--not-applicable', 'language', '--not-applicable', 'build_tool',
                   '--not-applicable', 'test_framework', '--source', 'user-specified', '--evidence', ref)
        command = f'{shlex.quote(sys.executable)} scripts/verification/docs.py'
        self.check('verification', 'configure', '--plan-only', '--verify-script', command)
        directory = self.repo / 'scripts/verification'
        directory.mkdir(parents=True)
        (directory / 'docs.py').write_text("from pathlib import Path\nassert 'Expected usage' in Path('guide.md').read_text()\n")
        (self.repo / 'guide.md').write_text('# Guide\nExpected usage\n')
        self.check('verification', 'configure', '--verify-script', command)
        self.start('docs')
        self.check('path', 'assess', '--profile', 'light', '--reason', 'Pure documentation scope')
        (self.repo / 'openspec/docs-spec.md').write_text('# Intent\n## Acceptance\n- Document contains Expected usage.\n')
        self.check('sdd', 'spec', 'bind', '--spec', 'openspec/docs-spec.md', '--criteria-section', 'Acceptance')
        (directory / 'accept.py').write_text("""import json,os,pathlib
r=json.loads(os.environ['CAO_SDD_ACCEPTANCE_REQUEST'])
ok='Expected usage' in pathlib.Path('guide.md').read_text()
print(json.dumps({'run_id':r['run_id'],'input_sha256':r['input_sha256'],'provider':r['provider'],'items':[{'id':c['id'],'check':{'criterion_id':c['id'],'run_id':r['run_id'],'selector':'fixture behavior assertion','assertion':c['description'],'observed':'actual fixture command result'},'status':'passed' if ok else 'failed','evidence':[{'path':'guide.md','reason':'Checked the actual usage document'}]} for c in r['declaration']['criteria']]}))
""")
        self.check('acceptance', 'configure', '--executor-command', f'{shlex.quote(sys.executable)} scripts/verification/accept.py')
        self.check('reopen', '--reanchor', '--reason', 'Review documentation applicability and assertions')
        self.check('verify')
        self.check('acceptance', 'run')
        self.assertTrue(self.check('acceptance', 'status')['ready'])
        self.check('done')


if __name__ == '__main__':
    unittest.main()
