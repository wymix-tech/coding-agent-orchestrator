"""Regressions from the demo session: real findings, stable scope, observed reviewers."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.acceptance_trace import source_items
from scripts.cli_output import summarize
from scripts.native_obligations import load_findings, review_inputs
from scripts.review_diagnostics import failure, paused_failure
from scripts.review_dispatch import agent_capability
from scripts.sdd_acceptance_policy import normalize_declaration
from tests import test_conformance_knowledge as fixtures


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)

    def test_metadata_is_not_acceptance_but_constraints_are(self):
        path = self.repo / 'requirement.md'
        path.write_text('# 登录\n需求编号: 001\n创建日期: 2026-10-23\n状态: draft\n'
                        '**已确认的产品决策：**\n- 登录使用用户名。\n- 密码不写日志。\n'
                        '## 不在范围\n- 不实现注册。\n```yaml\nstatus: test\n```\n')
        self.assertEqual(['登录使用用户名。', '密码不写日志。', '不实现注册。'],
                         [item['description'] for item in source_items(self.repo, 'requirement.md', requirement=True)])

    def test_four_heading_findings_are_not_exploded_into_explanation_bullets(self):
        path = self.repo / 'findings.md'
        path.write_text('\n'.join(f'### Gap {n}\n\n- Changed surface: auth\n- Missing verification: assertion\n'
                                 '  - nested detail\n```md\n### Fake heading\n- fake finding\n```\n'
                                 for n in range(4)))
        findings = load_findings(path)
        self.assertEqual(4, len(findings))
        self.assertIn('Missing verification', findings[0]['content'])
        self.assertEqual('finding-4', findings[-1]['id'])

    def test_report_wrapper_does_not_merge_list_or_heading_findings(self):
        path = self.repo / 'findings.md'
        path.write_text('## Findings\n- First defect\n- Second defect\n')
        self.assertEqual(2, len(load_findings(path)))
        path.write_text('## Findings\n### First defect\n- Surface: auth\n### Second defect\n- Surface: logout\n')
        self.assertEqual(2, len(load_findings(path)))

    def test_native_json_without_ids_is_not_rewritten(self):
        path = self.repo / 'findings.json'
        raw = '[{"location":"auth:42","trigger_condition":"bad input"},{"id":"native-7","content":"gap"}]'
        path.write_text(raw)
        self.assertEqual(['finding-1', 'native-7'], [item['id'] for item in load_findings(path)])
        self.assertEqual(raw, path.read_text())

    def test_read_only_reviewer_and_missing_backend_preflight(self):
        path = self.repo / '.pi/agents/reviewer.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\nname: reviewer\ntools: read, grep\n---\nReview.')
        with patch('scripts.review_dispatch.host_adapter_status', return_value={'installed': True, 'child_hooks_installed': True}):
            out = agent_capability(self.repo, '.pi/agents/reviewer.md', 'pi')
            self.assertEqual('coordinator', out['attach_mode'])
            self.assertEqual('reviewer', out['task_contract']['role'])
            path.write_text('---\ntools: read, bash\ncommand: missing-cao-test-executable\n---\nReview.')
            with self.assertRaisesRegex(ValueError, 'EXECUTABLE_UNAVAILABLE'):
                agent_capability(self.repo, '.pi/agents/reviewer.md', 'pi')
            path.write_text('---\nrunner:\n  type: external-cli\n  command: missing-cao-test-executable\n---\nReview.')
            with self.assertRaisesRegex(ValueError, 'EXECUTABLE_UNAVAILABLE'):
                agent_capability(self.repo, '.pi/agents/reviewer.md', 'pi')
            path.write_text('---\ntools: read, bash\nacceptanceRole: writer\n---\nImplement.')
            with self.assertRaisesRegex(ValueError, 'WRITER_CONTRACT'):
                agent_capability(self.repo, '.pi/agents/reviewer.md', 'pi')

    def test_global_summary_does_not_make_a_control_command_a_product_write(self):
        from scripts.cao_tool_policy import readonly_shell
        self.assertTrue(readonly_shell('cao --compact-json sdd review status', self.repo))

    def test_compact_output_preserves_boolean_failure_and_diagnostics(self):
        out = summarize({'ok': False, 'diagnostics': [{'field': 'x'}], 'verification':
                         {'status': 'stale', 'run_status': 'passed', 'changed_inputs': ['code_snapshot'], 'results': ['large output']}})
        self.assertFalse(out['ok'])
        self.assertEqual(['code_snapshot'], out['verification']['changed_inputs'])
        self.assertIn('diagnostics', out)
        self.assertEqual(['large output'], out['verification']['results'])
        self.assertFalse(summarize({'ok': True, 'ready': False, 'blockers': ['stale']})['ready'])
        self.assertEqual({'ok': True, 'sessions': ['session']}, summarize({'ok': True, 'sessions': ['session']}))

    def test_cosmetic_edits_do_not_reset_retry_and_identical_requests_pause(self):
        work = {'id': 'work-1', 'sdd': {'native_reviews': {'gap': {'id': 'review-1', 'input_sha256': 'input'}}}}
        args = argparse.Namespace(review_action='classify', layer='gap', report='triage.json')
        path = self.repo / args.report
        path.write_text('{"note":"one"}')
        first = failure(self.repo, work, args, ValueError('FAULT'))
        path.write_text('{"note":"two"}')
        second = failure(self.repo, work, args, ValueError('FAULT'))
        self.assertEqual(first['repair_id'], second['repair_id'])
        self.assertEqual(2, second['retry_count'])
        self.assertEqual(second, paused_failure(self.repo, work, args))
        path.write_text('{"corrected":true}')
        self.assertIsNone(paused_failure(self.repo, work, args))


    def test_corrected_raw_findings_unpause_an_unchanged_triage_request(self):
        work = {'id': 'work-1', 'sdd': {'native_reviews': {'gap': {'id': 'review-1', 'input_sha256': 'input'}}}}
        args = argparse.Namespace(review_action='classify', layer='gap', report='triage.json')
        (self.repo / 'triage.json').write_text(json.dumps({'findings_file': 'raw.md'}))
        raw = self.repo / 'raw.md'
        raw.write_text('- Old finding')
        failure(self.repo, work, args, ValueError('FAULT'))
        failure(self.repo, work, args, ValueError('FAULT'))
        self.assertIsNotNone(paused_failure(self.repo, work, args))
        raw.write_text('- Corrected original finding')
        self.assertIsNone(paused_failure(self.repo, work, args))


class ReviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SessionConformance()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.t = self.fixture.t

    def package(self):
        return self.fixture.prepare('--external-session')['reviews'][0]

    def child(self, package, name='readonly-child', model='fixture-model'):
        t = self.t
        t.hook('subagent_start', subagent_id=name, model=model, host_tools=['read', 'grep'], prompt=package['task_marker'])
        return t.run_cli('sdd', 'review', 'attach', '--reviewer-session', name, '--layer', package['layer'],
                         '--run', package['run_id'], '--input-sha256', package['input_sha256'], '--model', 'fixture-model')

    def test_coordinator_attaches_actual_child_and_drafts_original_report(self):
        t = self.t
        package = self.package()
        out = self.child(package)
        binding = out['review']['execution_binding']
        self.assertEqual('readonly-child', binding['reviewer']['session_id'])
        self.assertEqual('primary-fixture', binding['delegated_by'])
        source = '_bmad-output/raw-findings.md'
        raw = '### Real gap\n- Surface: login\n- Missing verification: cookie\n'
        t.write(source, raw)
        t.hook('turn_end', kind='subagent', subagent_id='readonly-child', last_assistant_message=raw)
        drafted = t.run_cli('sdd', 'review', 'draft', '--layer', package['layer'], '--source', source)
        report = json.loads((t.repo / drafted['report']).read_text())
        self.assertEqual(1, len(report['findings']))
        self.assertIsNone(report['findings'][0]['verdict'])
        self.assertEqual('readonly-child', report['reviewer']['session_id'])
        self.assertEqual(raw, (t.repo / source).read_text())
        status = t.run_cli('sdd', 'review', 'status')
        self.assertTrue(status['acceptance_preflight']['ready'])

    def test_host_binding_collection_and_classification_without_shell(self):
        t = self.t
        package = self.package()
        t.hook('subagent_start', subagent_id='auto-reviewer', model='fixture-model',
               host_tools=['read', 'grep'], prompt=package['task_marker'])
        status = t.run_cli('sdd', 'review', 'diagnose', '--layer', package['layer'])
        self.assertEqual('bound', status['phase'])
        t.hook('turn_end', kind='subagent', subagent_id='auto-reviewer',
               last_assistant_message='- Real original finding\n\n```acceptance-report\n{}\n```')
        status = t.run_cli('sdd', 'review', 'diagnose', '--layer', package['layer'])
        self.assertEqual('collected', status['phase'])
        drafted = t.run_cli('sdd', 'review', 'draft', '--layer', package['layer'],
                           '--source', status['artifacts'][0]['path'])
        report = json.loads((t.repo / drafted['report']).read_text())
        self.assertIn('source_sha256', report)
        self.assertIn('content_sha256', report['findings'][0])
        report['findings'][0].update(verdict='false', disposition='reject', evidence='Inspected original finding')
        t.write(drafted['report'], json.dumps(report))
        t.run_cli('sdd', 'review', 'classify', '--layer', package['layer'], '--report', drafted['report'])
        self.assertEqual('classified', t.run_cli('sdd', 'review', 'diagnose', '--layer', package['layer'])['phase'])

    def test_unobserved_child_or_model_cannot_be_attached(self):
        t = self.t
        package = self.package()
        args = ['sdd', 'review', 'attach', '--reviewer-session', 'missing-child', '--layer', package['layer'],
                '--run', package['run_id'], '--input-sha256', package['input_sha256'], '--model', 'fixture-model']
        self.assertEqual('NATIVE_REVIEW_CHILD_REQUIRED', t.run_cli(*args, expected=2)['error'])
        t.hook('subagent_start', subagent_id='wrong-model', model='other-model', host_tools=['read'])
        args[args.index('missing-child')] = 'wrong-model'
        self.assertEqual('NATIVE_REVIEW_MODEL_UNOBSERVED', t.run_cli(*args, expected=2)['error'])

    def test_mapping_only_change_preserves_review_inputs_but_standards_do_not(self):
        t = self.t
        from scripts.cao_snapshot import code_snapshot
        before = review_inputs(t.repo, t.work(), code_snapshot)
        raw = json.loads((t.repo / 'acceptance.json').read_text())
        raw['criteria'][0]['covers'] += raw['criteria'][-1]['covers']
        t.write('acceptance.json', json.dumps(raw))
        t.run_cli('sdd', 'acceptance', 'configure', '--declaration', 'acceptance.json', '--replace')
        from scripts.cao_snapshot import code_snapshot
        self.assertEqual(before, review_inputs(t.repo, t.work(), code_snapshot))
        raw['criteria'][0]['description'] = 'A different required behavior'
        t.write('acceptance.json', json.dumps(raw))
        t.run_cli('sdd', 'acceptance', 'configure', '--declaration', 'acceptance.json', '--replace')
        self.assertNotEqual(before, review_inputs(t.repo, t.work(), code_snapshot))

    def test_mapping_change_after_resolve_reuses_the_passed_native_reviews(self):
        t = self.t
        packages = self.fixture.prepare('--external-session')['reviews']
        reports = []
        for package in packages:
            self.child(package, name='child-' + package['layer'])
            source = '_bmad-output/' + package['layer'] + '.json'
            t.write(source, '[]')
            t.hook('turn_end', kind='subagent', subagent_id='child-' + package['layer'], last_assistant_message='[]')
            drafted = t.run_cli('sdd', 'review', 'draft', '--layer', package['layer'], '--source', source)
            reports.append(drafted['report'])
        manifest = '_bmad-output/classify-batch.json'
        t.write(manifest, json.dumps({package['layer']: report for package, report in zip(packages, reports)}))
        # A later layer failing validation cannot commit an earlier layer's classification.
        broken = json.loads((t.repo / reports[-1]).read_text())
        broken['reviewer']['session_id'] = 'wrong-session'
        t.write(reports[-1], json.dumps(broken))
        t.run_cli('sdd', 'review', 'classify-batch', '--report', manifest, expected=2)
        self.assertTrue(all(t.work()['sdd']['native_reviews'][package['layer']]['status'] == 'running' for package in packages))
        broken['reviewer']['session_id'] = 'child-' + packages[-1]['layer']
        t.write(reports[-1], json.dumps(broken))
        t.run_cli('sdd', 'review', 'classify-batch', '--report', manifest)
        for package in packages:
            resolved = '_bmad-output/' + package['layer'] + '-resolved.json'
            t.write(resolved, json.dumps({'resolved_ids': []}))
            t.run_cli('sdd', 'review', 'resolve', '--layer', package['layer'], '--report', resolved)
        raw = json.loads((t.repo / 'acceptance.json').read_text())
        raw['criteria'][0]['covers'] += raw['criteria'][-1]['covers']
        t.write('acceptance.json', json.dumps(raw))
        t.run_cli('sdd', 'acceptance', 'configure', '--declaration', 'acceptance.json', '--replace')
        t.run_cli('reopen', '--reanchor', '--reason', 'Review trace mapping only')
        status = t.run_cli('sdd', 'review', 'status')
        self.assertTrue(status['state']['ready'], status)
        self.assertEqual('already_satisfied', self.fixture.prepare('--external-session')['status'])
        for package in packages:
            self.assertEqual(package['run_id'], t.work()['sdd']['native_reviews'][package['layer']]['id'])

    def test_prepare_catches_missing_coverage_before_creating_tickets(self):
        t = self.t
        raw = json.loads((t.repo / 'acceptance.json').read_text())
        raw['criteria'][0]['covers'] = ['src-not-present']
        t.write('acceptance.json', json.dumps(raw))
        out = t.run_cli('sdd', 'acceptance', 'configure', '--declaration', 'acceptance.json', '--replace')
        self.assertTrue(out['coverage']['unknown'])
        out = self.fixture.prepare('--external-session', expected=2)
        self.assertEqual('TASK_ACCEPTANCE_COVERAGE_INCOMPLETE', out['error'])
        self.assertFalse(t.work()['sdd'].get('native_reviews'))

    def test_reviewer_check_runs_in_copy_and_does_not_satisfy_gates(self):
        t = self.t
        t.write('review_test.py', "from pathlib import Path\nPath('target').mkdir()\nPath('target/result').write_text('generated')\nprint('observed failure')\nraise SystemExit(1)\n")
        package = self.package()
        self.child(package)
        work = t.work()
        from scripts.review_checks import run_check
        configuration = {'verification': {'scripts': [{'name': 'experiment', 'command': sys.executable + ' review_test.py',
                                                        'required': True, 'gates': ['tests']}]}}
        with patch('scripts.review_checks.config', return_value=configuration), \
             patch('scripts.cao_tool_policy.verification_baseline_state', return_value={'ready': True}):
            out = run_check(t.repo, work, package['layer'], 'experiment', ('pi', 'primary-fixture'), 'readonly-child')
        self.assertFalse(out['ok'])
        self.assertEqual(1, out['exit_code'])
        self.assertIn('observed failure', out['stdout'])
        self.assertFalse(out['gate_evidence'])
        self.assertFalse((t.repo / 'target').exists())
        self.assertFalse(work.get('verification'))
        self.assertFalse(work.get('gate_status'))

    def test_review_check_releases_the_state_lock_before_executing_tests(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts.cao_workflow_commands import cmd_sdd_review
        from scripts.state_store import repo_transaction
        t = self.t
        args = argparse.Namespace(review_action='check', repo=str(t.repo), work=t.wid,
                                  layer='blind-hunter', script='experiment')
        def execute(*args):
            def acquire():
                with repo_transaction(t.repo, timeout=0.5):
                    return True
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertTrue(pool.submit(acquire).result(timeout=2))
            return {'ok': True, 'gate_evidence': False}
        with patch('scripts.review_checks.run_check', side_effect=execute), redirect_stdout(StringIO()):
            self.assertEqual(0, cmd_sdd_review(args))

    def test_summary_cli_keeps_error_and_exit_code(self):
        t = self.t
        package = self.package()
        out = t.run_cli('sdd', 'review', 'attach', '--layer', package['layer'], '--run', package['run_id'],
                        '--input-sha256', package['input_sha256'], '--model', 'fixture-model', '--compact-json', expected=2)
        self.assertFalse(out['ok'])
        self.assertIn('diagnostics', out)


class MavenScopeTests(unittest.TestCase):
    def setUp(self):
        from tests import test_project_context_acceptance as project_fixtures
        self.fixture = project_fixtures.ProjectAcceptanceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.t = self.fixture
        self.t.background(language='Java', build='Maven', tests='JUnit 5')
        from scripts.project_sources import observe_prompt
        evidence = observe_prompt(self.t.repo, 'Use Spring Boot for the Java project.', 'pi', 'setup-framework')
        self.t.check('project', 'context', 'set', '--scope', 'root', '--framework', 'Spring Boot',
                     '--source', 'user-specified', '--evidence', evidence)
        self.t.check('verification', 'configure', '--plan-only', '--test-script', 'mvn test')
        self.t.check('project', 'context', 'set', '--path', '.', '--language', 'Java', '--build-tool', 'Maven', '--test-framework', 'JUnit 5', '--framework', 'Spring Boot', '--source', 'user-specified', '--evidence', observe_prompt(self.t.repo, 'Java Maven JUnit 5 Spring Boot', 'pi', 'fixture'))
        self.t.start()

    def test_first_baseline_excludes_maven_outputs_but_not_tracked_inputs(self):
        import subprocess
        from scripts.cao_snapshot import code_snapshot
        t = self.t
        self.assertFalse((t.repo / 'pom.xml').exists())
        t.check('verification', 'configure', '--test-script', 'mvn test')
        raw = json.loads((t.repo / '.orchestrator/config.json').read_text())
        self.assertIn('target/', raw['snapshot']['exclude_paths'])
        before = code_snapshot(t.repo)
        (t.repo / 'target').mkdir()
        (t.repo / 'target/output.class').write_bytes(b'generated')
        self.assertEqual(before, code_snapshot(t.repo))
        subprocess.run(['git', 'init', '-q'], cwd=t.repo, check=True)
        subprocess.run(['git', 'add', 'target/output.class'], cwd=t.repo, check=True)
        tracked = code_snapshot(t.repo)
        (t.repo / 'target/output.class').write_bytes(b'changed tracked input')
        self.assertNotEqual(tracked, code_snapshot(t.repo))

    def test_existing_baseline_is_not_silently_reconfigured(self):
        t = self.t
        t.check('verification', 'configure', '--test-script', 'mvn test', '--exclude-path', 'reports/', '--replace')
        (t.repo / 'pom.xml').write_text('<project/>')
        t.check('verification', 'configure', '--test-script', 'mvn test')
        raw = json.loads((t.repo / '.orchestrator/config.json').read_text())
        self.assertNotIn('target/', raw.get('snapshot', {}).get('exclude_paths', []))
