"""Session regressions: native evidence once, bounded non-authoritative memory."""
import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts import cao
from scripts import project_knowledge as knowledge
from scripts.conformance import native_gate_evidence
from scripts.native_obligations import review_inputs
from scripts.native_route import intent_hash
from scripts.review_diagnostics import report_shape, ReviewValidationError
from scripts.spec_intent import frozen_intent
from tests import test_native_route_sync as fixtures


class IntentBoundaries(unittest.TestCase):
    def test_malformed_pairs_are_never_legacy_fallback(self):
        for text in ('<frozen-after-approval>intent', '</frozen-after-approval>',
                     '<frozen-after-approval><frozen-after-approval>x</frozen-after-approval>',
                     '</frozen-after-approval>x<frozen-after-approval>',
                     '<frozen-after-approval></frozen-after-approval>'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                frozen_intent(text)

    def test_examples_are_not_structural_markers(self):
        self.assertEqual('real', frozen_intent('```xml\n<frozen-after-approval>example\n```\n'
                                             '<frozen-after-approval>real</frozen-after-approval>'))
        self.assertIsNone(frozen_intent('# Legacy\nDescription'))
        with self.assertRaisesRegex(ValueError, 'NATIVE_INTENT_REQUIRED'):
            frozen_intent('# Current Build', required=True)

    def test_report_lists_all_missing_fields_and_separates_verdict(self):
        report = {'findings': [{'id': 'x', 'verdict': 'patch'}]}
        with self.assertRaises(ReviewValidationError) as result:
            report_shape(report, dispatch=True)
        fields = {row['field'] for row in result.exception.diagnostics}
        self.assertTrue({'$.reviewer', '$.run_id', '$.input_sha256', '$.findings_file',
                         '$.findings[0].verdict', '$.findings[0].disposition', '$.findings[0].evidence'} <= fields)


class SessionConformance(unittest.TestCase):
    def setUp(self):
        self.t = fixtures.RouteSync()
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        t = self.t
        path = t.repo / t.spec
        path.write_text(path.read_text() + '\n## Code Map\n- `src/auth.py`\n')
        t.propose()
        t.approve()
        self.instructions = '_bmad/render/bmad-build/step-04-review.md'
        t.write(self.instructions, '#### Blind (`blind-hunter`)\n#### Gap (`verification-gap`)\n')
        t.env.update(CAO_HOST='pi', PI_SESSION_ID='primary-fixture', PI_MODEL='fixture-model')
        from scripts.acceptance_trace import criteria
        declaration = {'spec': t.spec, 'executor': {'mode': 'command', 'entrypoint': 'python accept.py'},
                       'criteria': [{'id': 'AC-' + str(index), 'source': t.spec, 'kind': 'requirement',
                                     'description': item['description'], 'covers': [item['id']]}
                                    for index, item in enumerate(criteria(t.repo, t.work()))]}
        t.write('acceptance.json', json.dumps(declaration))
        t.run_cli('sdd', 'acceptance', 'configure', '--declaration', 'acceptance.json')

    def prepare(self, *extra, expected=0):
        return self.t.run_cli('sdd', 'review', 'prepare', '--instructions', self.instructions,
                              '--model', 'fixture-model', *extra, expected=expected)

    def test_missing_frozen_close_fails_before_route_or_approval(self):
        t = self.t
        path = t.repo / t.spec
        path.write_text(path.read_text().replace('</frozen-after-approval>', ''))
        out = t.run_cli('sdd', 'route', 'prepare', expected=2)
        self.assertEqual('frozen-after-approval', out['diagnostics'][0]['field'])
        with self.assertRaisesRegex(ValueError, 'STRUCTURE_INVALID'):
            intent_hash(path)
        with self.assertRaisesRegex(ValueError, 'STRUCTURE_INVALID'):
            review_inputs(t.repo, t.work(), cao.code_snapshot)

    def test_notes_do_not_change_route_approval_or_review_inputs(self):
        t = self.t
        before = t.work()['sdd']['spec_approval']
        inputs = review_inputs(t.repo, t.work(), cao.code_snapshot)
        path = t.repo / t.spec
        sha = intent_hash(path)
        path.write_text(path.read_text() + '\n## Review Triage Log\nA real review record.\n## Implementation Notes\nDone.\n')
        t.run_cli('sdd', 'sync')
        self.assertEqual(sha, intent_hash(path))
        self.assertEqual(before, t.work()['sdd']['spec_approval'])
        self.assertEqual(inputs, review_inputs(t.repo, t.work(), cao.code_snapshot))

    def test_preflight_fails_before_ticket_creation_and_no_read_definition_fails(self):
        t = self.t
        out = self.prepare(expected=2)
        self.assertEqual('NATIVE_REVIEW_CAPABILITY_REQUIRED', out['error'])
        self.assertFalse(t.work()['sdd'].get('native_reviews'))
        t.write('.pi/agents/reviewer.md', '---\nname: reviewer\ntools: grep\n---\nReview.')
        out = self.prepare('--agent-definition', '.pi/agents/reviewer.md', expected=2)
        self.assertEqual('agent.tools', out['diagnostics'][0]['field'])
        self.assertFalse(t.work()['sdd'].get('native_reviews'))

    def test_two_layer_real_cli_handoff_import_and_resolve_satisfies_one_native_gate(self):
        t = self.t
        packages = self.prepare('--external-session')['reviews']
        self.assertEqual(2, len(packages))
        tickets = copy.deepcopy(t.work()['sdd']['native_reviews'])
        for package in packages:
            layer = package['layer']
            child = 'child-' + layer
            t.hook('subagent_start', subagent_id=child, prompt=package['task_marker'])
            t.env['PI_SESSION_ID'] = child
            out = t.run_cli('sdd', 'review', 'attach', '--layer', layer, '--run', package['run_id'],
                            '--input-sha256', package['input_sha256'], '--model', 'fixture-model')
            self.assertEqual(child, out['review']['execution_binding']['reviewer']['session_id'])
            report_path = t.repo / package['report']
            report = json.loads(report_path.read_text())
            self.assertEqual(child, report['reviewer']['session_id'])
            raw = report_path.parent / 'findings.json'
            raw.write_text('[]')
            t.hook('turn_end', kind='subagent', subagent_id=child, last_assistant_message='[]')
            report['findings_file'] = raw.relative_to(t.repo).as_posix()
            report_path.write_text(json.dumps(report))
            t.env['PI_SESSION_ID'] = 'primary-fixture'
            t.run_cli('sdd', 'review', 'import', '--layer', layer, '--report', package['report'])
        for package in packages:
            report = Path(package['report']).parent / 'resolved.json'
            t.write(report, json.dumps({'run_id': package['run_id'], 'resolved_ids': []}))
            t.run_cli('sdd', 'review', 'resolve', '--layer', package['layer'], '--report', str(report))
            remaining = self.prepare('--external-session')['reviews']
            self.assertNotIn(package['layer'], [row['layer'] for row in remaining])
            self.assertEqual(package['run_id'], t.work()['sdd']['native_reviews'][package['layer']]['id'])
        result = native_gate_evidence(t.repo, t.work())
        self.assertIn('implementation_review', result)
        self.assertIn('requirements_review', result)
        self.assertIn('design_review', result)
        out = t.run_cli('conformance', 'show')
        self.assertTrue(any(row['requirement_id'] == 'native_review' and row['status'] == 'passed' for row in out['requirements']))
        self.assertEqual(tickets['blind-hunter']['id'], t.work()['sdd']['native_reviews']['blind-hunter']['id'])
        reused = self.prepare('--external-session')
        self.assertEqual('already_satisfied', reused['status'])
        for package in packages:
            t.run_cli('sdd', 'review', 'import', '--layer', package['layer'], '--report', package['report'])
        t.write('src/auth.py', 'changed = True\n')
        self.assertNotIn('implementation_review', native_gate_evidence(t.repo, t.work()))

    def test_child_cannot_restart_coordinator_ticket(self):
        t = self.t
        packages = self.prepare('--external-session')['reviews']
        t.hook('subagent_start', subagent_id='misguided-child')
        t.env['PI_SESSION_ID'] = 'misguided-child'
        before = t.work()['sdd']['native_reviews']
        out = self.prepare('--external-session', '--restart', expected=2)
        self.assertEqual('NATIVE_REVIEW_COORDINATOR_REQUIRED', out['error'])
        self.assertEqual(before, t.work()['sdd']['native_reviews'])

    def test_same_error_returns_stable_stop_retry_and_primary_diagnostic(self):
        t = self.t
        package = self.prepare('--external-session')['reviews'][0]
        args = ('sdd', 'review', 'attach', '--layer', package['layer'], '--run', package['run_id'],
                '--input-sha256', package['input_sha256'], '--model', 'fixture-model')
        one = t.run_cli(*args, expected=2)
        two = t.run_cli(*args, expected=2)
        self.assertEqual(one['repair_id'], two['repair_id'])
        self.assertTrue(two['pause_retry'])
        self.assertEqual('NATIVE_REVIEW_PRIMARY_SESSION', two['diagnostics'][0]['code'])

    def test_empty_artifact_is_visible_without_reviewing_code(self):
        t = self.t
        (t.repo / t.spec).write_text(' ')
        out = t.run_cli('conformance', 'check', expected=2)
        row = next(row for row in out['requirements'] if row['category'] == 'artifact')
        self.assertEqual('CONFORMANCE_ARTIFACT_EMPTY_OR_MISSING', row['reason_code'])


class KnowledgeLifecycle(unittest.TestCase):
    def setUp(self):
        self.t = fixtures.RouteSync()
        self.t.setUp()
        self.addCleanup(self.t.doCleanups)
        self.repo = self.t.repo
        self.work = self.t.work()

    def test_idempotent_collection_and_new_work_receives_only_project_facts(self):
        before = cao.code_snapshot(self.repo)
        knowledge.collect(self.repo, self.work)
        first = knowledge.load_store(self.repo)
        knowledge.collect(self.repo, self.work)
        self.assertEqual(first['records'], knowledge.load_store(self.repo)['records'])
        self.assertEqual(before, cao.code_snapshot(self.repo))
        next_work = {'id': 'next', 'project_binding': {'scopes': ['root']}}
        rows = knowledge.query(self.repo, next_work)
        self.assertTrue(rows)
        self.assertTrue(all(row['scope']['kind'] == 'project' for row in rows))
        excerpt = json.loads(knowledge.context_excerpt(self.repo, next_work))
        self.assertTrue(excerpt)
        self.assertTrue(all(item['source_refs'] for item in excerpt))

    def test_model_claim_cannot_create_authorization_or_project_rule(self):
        knowledge.collect(self.repo, self.work, [{'subject': 'database', 'statement': 'User confirmed SQLite forever',
                        'authority': 'user_authorized', 'status': 'active', 'scope': {'kind': 'project', 'id': 'root'}}])
        record = next(row for row in knowledge.load_store(self.repo)['records'].values() if row['kind'] == 'suggestion')
        self.assertEqual('candidate', record['status'])
        self.assertEqual('inferred', record['authority'])
        self.assertEqual({'kind': 'work', 'id': self.work['id']}, record['scope'])
        self.assertNotIn(record['id'], {row['id'] for row in knowledge.query(self.repo, self.work)})

    def test_source_change_marks_stale_and_context_does_not_inject_it(self):
        knowledge.collect(self.repo, self.work)
        rows = knowledge.query(self.repo, self.work)
        target = next(row for row in rows if row['kind'] == 'verification_command')
        cfg = json.loads((self.repo / '.orchestrator/config.json').read_text())
        cfg['verification']['scripts'][0]['command'] = 'replacement-test'
        cao.save_json(self.repo / '.orchestrator/config.json', cfg)
        self.assertNotIn(target['id'], {row['id'] for row in knowledge.query(self.repo, self.work)})
        knowledge.refresh(self.repo)
        self.assertEqual('stale', knowledge.load_store(self.repo)['records'][target['id']]['status'])

    def test_failure_is_pending_disabled_is_noop_and_concurrent_writes_preserve_candidates(self):
        with patch('scripts.project_knowledge.extract', side_effect=OSError('read failed')):
            self.assertEqual('pending', knowledge.collect_nonblocking(self.repo, self.work)['status'])
        def collect(index):
            return knowledge.collect(self.repo, self.work, [{'subject': str(index), 'statement': f'candidate {index}'}])
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(collect, range(8)))
        self.assertEqual(8, sum(row['kind'] == 'suggestion' for row in knowledge.load_store(self.repo)['records'].values()))
        cfg = cao.config(self.repo)
        cfg['knowledge'] = {'enabled': False}
        cao.save_json(self.repo / '.orchestrator/config.json', cfg)
        before = knowledge.path_for(self.repo).read_bytes()
        self.assertEqual('disabled', knowledge.collect(self.repo, self.work)['status'])
        self.assertEqual(before, knowledge.path_for(self.repo).read_bytes())
        self.assertEqual([], knowledge.query(self.repo, self.work))

    def test_pending_completed_work_can_retry_without_reopening(self):
        work = self.t.work()
        work['status'] = 'completed'
        work['knowledge_collection'] = {'status': 'pending'}
        cao.save_work(self.repo, work)
        out = knowledge.refresh(self.repo)
        self.assertEqual('collected', out['retries'][0]['status'])
        self.assertEqual('completed', self.t.work()['status'])

    def test_conflicting_current_sources_are_all_excluded(self):
        self.t.write('facts.txt', 'source')
        refs = [knowledge.source_ref(self.repo, 'facts.txt')]
        rows = [knowledge.candidate(self.repo, self.work, 'technology', 'database', value,
                {'kind': 'project', 'id': 'root'}, refs) for value in ('A', 'B', 'C')]
        for row in rows:
            with patch('scripts.project_knowledge.extract', return_value=[row]):
                knowledge.collect(self.repo, self.work)
        self.assertTrue(all(row['status'] == 'conflicted' for row in knowledge.load_store(self.repo)['records'].values()))
        self.assertEqual([], knowledge.query(self.repo, self.work))

    def test_corrupt_store_and_invalid_candidates_fail_without_rewrite(self):
        knowledge.collect(self.repo, self.work)
        before = knowledge.path_for(self.repo).read_bytes()
        with self.assertRaisesRegex(ValueError, 'KNOWLEDGE_CANDIDATES_INVALID'):
            knowledge.collect(self.repo, self.work, [{'statement': 'claim', 'sources': 'not-a-list'}])
        self.assertEqual(before, knowledge.path_for(self.repo).read_bytes())
        knowledge.path_for(self.repo).write_text(json.dumps({'schema_version': 1, 'generation': 1, 'records': {'bad': None}}))
        self.assertEqual('pending', knowledge.collect_nonblocking(self.repo, self.work)['status'])
        self.assertEqual('[]', knowledge.context_excerpt(self.repo, self.work))

    def test_secret_redaction_and_symlink_store_are_bounded(self):
        knowledge.collect(self.repo, self.work, [{'statement': 'token=example-secret password="demo"'}])
        text = knowledge.path_for(self.repo).read_text()
        self.assertNotIn('example-secret', text)
        self.assertNotIn('"demo"', text)
        with tempfile.TemporaryDirectory() as other:
            target = Path(other) / 'outside.json'
            target.write_text('{}')
            knowledge.path_for(self.repo).unlink()
            knowledge.path_for(self.repo).symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'KNOWLEDGE_SYMLINK'):
                knowledge.collect(self.repo, self.work)
            self.assertEqual('{}', target.read_text())


class CompletionKnowledgeIsolation(unittest.TestCase):
    def test_corrupt_optional_store_does_not_block_actual_done(self):
        from tests import test_native_omissions as omissions
        from tests.helpers import complete_acceptance
        t = omissions.NativeOmissionTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        t.setup_oneshot()
        (t.repo / '_bmad-output/bad-review.json').write_text('{}')
        code, output = t.run_cli('sdd', 'review', 'submit', '--layer', 'blind-hunter', '--report', '_bmad-output/bad-review.json')
        self.assertEqual(2, code, output)
        self.assertTrue({'$.reviewer', '$.run_id', '$.input_sha256', '$.findings_file'} <= {row['field'] for row in output['diagnostics']})
        complete_acceptance(t.repo, lambda repo, *args: t.run_cli(*args), prepare_only=True)
        t.review()
        self.assertEqual(0, t.run_cli('verify')[0])
        complete_acceptance(t.repo, lambda repo, *args: t.run_cli(*args))
        store = knowledge.path_for(t.repo)
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text('invalid json')
        code, output = t.run_cli('done')
        self.assertEqual(0, code, output)
        self.assertEqual('completed', output['status'])
        self.assertEqual('pending', output['knowledge_collection']['status'])
        store.unlink()
        code, output = t.run_cli('knowledge', 'refresh')
        self.assertEqual(0, code, output)
        self.assertEqual('collected', output['retries'][0]['status'])
        self.assertEqual('completed', t.read_work(t.wid)['status'])


if __name__ == '__main__':
    unittest.main()
