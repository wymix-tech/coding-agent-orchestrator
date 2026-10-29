"""Actual diff-only inputs, claim ordering and immutable native layer prompts."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.review_packages import layer_prompt, render, guard, observe_reads, prompt_matches
from scripts.review_changes import capture_baseline, unified_diff, freeze


NATIVE = '''# Step 4
## RULES
- Language: Chinese.
## INSTRUCTIONS
### Review
#### Blind (`blind-hunter`)
Launch a context-free subagent with this prompt:

Review only `{diff_file}`.
#### Edge (`edge-case-hunter`)
Launch a context-free subagent with this prompt:

Read `{diff_file}`. Trace product code before claims_file: {claims_file}
#### Gap (`verification-gap`)
Launch a context-free subagent with this prompt:

Review `{diff_file}` and relevant tests.
### Classify
Coordinator only.
'''


class ContextPackages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / 'src').mkdir()
        (self.repo / 'src/app.py').write_text('answer = 42\n')
        source = self.repo / '_bmad/step-04-review.md'
        source.parent.mkdir()
        source.write_text(NATIVE)
        self.instructions = '_bmad/step-04-review.md'
        (self.repo / '_bmad-output').mkdir()
        (self.repo / '_bmad-output/spec.md').write_text('SECRET CLAIMS')
        self.work = {'id': 'work-1', 'sdd': {'route_decision': {'primary_artifact': '_bmad-output/spec.md'}, 'native_reviews': {}}}
        self.manifest = {'files': [{'path': 'src/app.py'}], 'changes': [{'status': 'A', 'path': 'src/app.py'}], 'baseline': {'kind': 'work_start'}}

    def package(self, layer):
        run = {'id': 'review-1', 'layer': layer, 'input_sha256': 'sha', 'execution_binding': {'session_id': 'child'}}
        self.work['sdd']['native_reviews'][layer] = run
        with patch('scripts.review_changes.unified_diff', return_value='--- /dev/null\n+++ b/src/app.py\n+answer = 42\n'):
            output = render(self.repo, self.work, run, self.instructions, self.manifest)
        return run, output, {'id': 'child', 'review_ticket': {'layer': layer}}

    def test_launch_payload_contains_exact_frozen_prompts_in_one_batch(self):
        from scripts.review_packages import launch_payload, launch_guard
        layers = ['blind-hunter', 'edge-case-hunter', 'verification-gap']
        originals = []
        for layer in layers:
            run, output, _ = self.package(layer)
            run['status'] = 'running'
            run.pop('execution_binding')
            originals.append((self.repo / output['prompt_file']).read_text())
        self.work['sdd']['native_reviews']['_contract'] = {'layers': layers}
        payload = launch_payload(self.repo, self.work)
        self.assertFalse(payload['async'])
        self.assertEqual(originals, [task['task'] for task in payload['tasks']])
        self.assertIsNone(launch_guard(self.repo, self.work, {'toolName': 'subagent', 'input': payload}))

    def event(self, path, tool='read'):
        return {'toolName': tool, 'input': {'path': path}}

    def test_blind_prompt_and_guard_do_not_expose_spec_or_other_layers(self):
        run, output, session = self.package('blind-hunter')
        prompt = (self.repo / output['prompt_file']).read_text()
        self.assertNotIn('claims', prompt)
        self.assertNotIn('verification-gap', prompt)
        self.assertNotIn('Coordinator only', prompt)
        self.assertTrue(prompt_matches(self.repo, run, prompt))
        self.assertFalse(prompt_matches(self.repo, run, 'generic full review'))
        self.assertIsNone(guard(self.repo, self.work, session, self.event(output['diff_file'])))
        for path in ('_bmad-output/spec.md', 'src/app.py', self.instructions):
            self.assertIn('FORBIDDEN', guard(self.repo, self.work, session, self.event(path)))
        self.assertIn('UNSUPPORTED', guard(self.repo, self.work, session, {'toolName': 'bash', 'input': {'command': 'python inspect.py'}}))

    def test_edge_claims_open_only_after_observed_diff_and_product_reads(self):
        run, output, session = self.package('edge-case-hunter')
        claim = self.event('_bmad-output/spec.md')
        self.assertIn('FORBIDDEN', guard(self.repo, self.work, session, claim))
        with patch('scripts.cao_sessions.save_session'):
            observe_reads(self.repo, self.work, session, self.event(output['diff_file']))
            self.assertIn('FORBIDDEN', guard(self.repo, self.work, session, claim))
            observe_reads(self.repo, self.work, session, self.event('src/app.py'))
        self.assertIsNone(guard(self.repo, self.work, session, claim))

    def test_context_input_tampering_blocks_review(self):
        run, output, session = self.package('blind-hunter')
        (self.repo / output['diff_file']).write_text('different changes')
        with self.assertRaisesRegex(ValueError, 'CONTEXT_INPUT_CHANGED'):
            guard(self.repo, self.work, session, self.event(output['diff_file']))

    def test_no_vcs_diff_preserves_before_and_after_bytes(self):
        manifest = {'baseline': {'kind': 'work_start', 'reference': {'path': 'baseline.json'}},
                    'changes': [{'status': 'M', 'path': 'src/app.py', 'before': {'sha256': hashlib.sha256(b'answer = 1\n').hexdigest()}}]}
        (self.repo / 'old.bin').write_bytes(b'answer = 1\n')
        import json
        (self.repo / 'baseline.json').write_text(json.dumps({'bodies': {'src/app.py': 'old.bin'}}))
        diff = unified_diff(self.repo, self.work, manifest)
        self.assertIn('-answer = 1', diff)
        self.assertIn('+answer = 42', diff)
        (self.repo / 'old.bin').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'BASELINE_STALE'):
            unified_diff(self.repo, self.work, manifest)

    def test_launch_requires_exact_prompt_explicit_blocking_mode_and_valid_arguments(self):
        from scripts.review_packages import launch_guard
        run, output, session = self.package('blind-hunter')
        task = (self.repo / output['prompt_file']).read_text()
        raw = {'toolName': 'subagent', 'input': {'tasks': [{'task': task}], 'async': False}}
        self.assertIsNone(launch_guard(self.repo, self.work, raw))
        raw['input'].pop('async')
        self.assertIn('SYNCHRONOUS_REQUIRED', launch_guard(self.repo, self.work, raw))
        raw['input']['async'] = False
        raw['input']['tasks'][0]['worktree'] = True
        self.assertIn('ARGUMENT_INVALID', launch_guard(self.repo, self.work, raw))
        raw['input']['tasks'][0].pop('worktree')
        raw['input']['tasks'][0]['task'] += '\nRead SPEC and previous findings first.'
        self.assertIn('PACKAGE_REQUIRED', launch_guard(self.repo, self.work, raw))

    def test_shell_inspectors_cannot_execute_or_read_hidden_secondary_inputs(self):
        run, output, session = self.package('blind-hunter')
        for command in ("sed -n 'r _bmad-output/spec.md' " + output['diff_file'],
                        'rg --pre python pattern ' + output['diff_file'],
                        'find src -exec python inspect.py {} +'):
            reason = guard(self.repo, self.work, session, {'toolName': 'bash', 'input': {'command': command}})
            self.assertIn('UNSUPPORTED', reason)
        self.assertIsNone(guard(self.repo, self.work, session, {'toolName': 'bash', 'input': {'command': 'cat ' + output['diff_file']}}))

    def test_missing_newline_and_hash_only_legacy_baseline_are_explicit(self):
        import json
        (self.repo / 'src/app.py').write_text('answer = 42')
        (self.repo / 'baseline.json').write_text(json.dumps({'files': [{'path': 'src/app.py', 'sha256': 'old'}]}))
        manifest = {'baseline': {'kind': 'work_start', 'reference': {'path': 'baseline.json'}},
                    'changes': [{'status': 'M', 'path': 'src/app.py'}]}
        diff = unified_diff(self.repo, self.work, manifest)
        self.assertIn('baseline unavailable', diff)
        self.assertIn('No newline at end of file', diff)
        self.assertIn('+answer = 42', diff)
