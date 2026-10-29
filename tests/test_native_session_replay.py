"""Sanitized control-flow replay: interrupted BMAD review through NO_VCS completion.

Reviewer outputs are local fixtures, not claims of a live model's semantic review.
Product tests execute for real; this reconstructs the archived failure boundaries.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from tests import test_conformance_knowledge as fixtures
from tests.helpers import bind_fixture_reviewer, publish_fixture_review
from scripts.cao_runtime import config
from scripts.cao_sessions import load_session, save_session
from scripts.lifecycle_contract import inputs

NATIVE = '''## RULES
- Language: Chinese.
- Run reviewers synchronously and wait for all results.
## INSTRUCTIONS
### Review
#### Blind (`blind-hunter`)
Launch a context-free subagent with this prompt:

Review the unified diff at `{diff_file}`. Return findings only.
#### Edge (`edge-case-hunter`)
Launch a context-free subagent with this prompt:

Trace the code from `{diff_file}`, then read claims_file: {claims_file}.
#### Gap (`verification-gap`)
Launch a context-free subagent with this prompt:

Read `{diff_file}` and verify assertions covering changed behavior.
### Classify
Classify all layers before repairs.
'''


class NativeSessionReplay(unittest.TestCase):
    def test_three_layers_resume_patch_native_evidence_and_no_vcs_done(self):
        fixture = fixtures.SessionConformance()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        t = fixture.t
        t.run_cli('lifecycle', 'migrate')
        t.write(fixture.instructions, NATIVE)
        t.write('auth.py', "sessions = {}\ndef login(user, password):\n    if user != 'alice' or password != 'correct':\n        return None\n    sessions['opaque-token'] = user\n    return 'opaque-token'\ndef valid(token):\n    return token in sessions\ndef revoke(token):\n    return None\n")
        t.write('tests/test_auth.py', "import unittest, auth\nclass Login(unittest.TestCase):\n def test_credentials(self):\n  self.assertIsNotNone(auth.login('alice', 'correct'))\n  self.assertIsNone(auth.login('alice', 'wrong'))\n")
        # Native status is implementation metadata; frozen intent is preserved.
        spec = t.repo / t.spec
        spec.write_text(spec.read_text().replace("status: 'draft'", "status: 'in-review'").replace('[ ]', '[x]'))
        packages = fixture.prepare('--external-session')['reviews']
        self.assertEqual(3, len(packages))
        self.assertFalse((t.repo / '.git').exists())
        counts = t.run_cli('sdd', 'review', 'status')['review_counts']
        self.assertEqual(1, counts['observed_round_count'])
        for package in packages:
            run = t.work()['sdd']['native_reviews'][package['layer']]
            reviewer = bind_fixture_reviewer(t.repo, run, package['layer'], 'fixture-model')
            raw = '- Revocation leaves the original token usable; add a regression assertion.'
            publish_fixture_review(t.repo, reviewer, raw)
            if package['layer'] == 'blind-hunter':
                # Reconstruct pre-collector legacy state and the real native archive.
                child = load_session(t.repo, reviewer['session_id'], host='pi')
                archive = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'bound-child.jsonl'
                archive.write_text(json.dumps({'type': 'session', 'id': reviewer['session_id']}) + '\n' + json.dumps({'type': 'message', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': raw}]}}) + '\n')
                child.pop('review_message_artifact')
                child['native_session_file'] = str(archive)
                save_session(t.repo, child)
        t.hook('session_end', reason='new')
        t.env['PI_SESSION_ID'] = 'resumed-primary'
        t.run_cli('host-event', '--host', 'pi', raw={'event': 'session_start', 'session_id': 'resumed-primary', 'model': 'fixture-model'})
        resumed = t.run_cli('sdd', 'review', 'resume')
        self.assertTrue(all(row['phase'] == 'drafted' for row in resumed['layers']))
        for row in resumed['layers']:
            path = t.repo / row['report']
            report = json.loads(path.read_text())
            report['findings'][0].update(verdict='medium', disposition='patch', evidence='At revoke, the token is never removed from sessions.')
            path.write_text(json.dumps(report))
            t.run_cli('sdd', 'review', 'classify', '--layer', row['layer'], '--report', row['report'])
        t.write('auth.py', (t.repo / 'auth.py').read_text().replace('def revoke(token):\n    return None', 'def revoke(token):\n    sessions.pop(token, None)'))
        t.write('tests/test_auth.py', (t.repo / 'tests/test_auth.py').read_text() + " def test_revocation(self):\n  token = auth.login('alice', 'correct')\n  self.assertTrue(auth.valid(token))\n  auth.revoke(token)\n  self.assertFalse(auth.valid(token))\n")
        command = config(t.repo)['verification']['scripts'][0]['command']
        executed = t.run_cli('lifecycle', 'execute', '--command', command, '--report', '_bmad-output/native-tests.log')
        self.assertEqual('passed', executed['execution']['status'])
        test_ref = executed['execution']['reports'][0]
        from scripts.native_obligations import review_inputs
        from scripts.cao_snapshot import code_snapshot
        snapshot = review_inputs(t.repo, t.work(), code_snapshot)['code_snapshot']
        auth_sha = hashlib.sha256((t.repo / 'auth.py').read_bytes()).hexdigest()
        for row in resumed['layers']:
            drafted = t.run_cli('sdd', 'review', 'resolution-draft', '--layer', row['layer'])
            path = t.repo / drafted['report']
            resolution = json.loads(path.read_text())
            resolution['resolutions'][0].update(changes=[{'path': 'auth.py', 'sha256': auth_sha, 'reason': 'Remove revoked token from the active session map'}],
                verification=[{'kind': 'test', 'code_snapshot': snapshot, 'assertion': 'A revoked token becomes invalid',
                               'observed': 'Login.test_revocation passed', 'evidence': [test_ref]}])
            path.write_text(json.dumps(resolution))
            t.run_cli('sdd', 'review', 'resolve', '--layer', row['layer'], '--report', drafted['report'])
        spec.write_text(spec.read_text().replace("status: 'in-review'", "status: 'done'"))
        t.run_cli('sdd', 'workflow', 'execute', '--command', 'bmad-build', '--exit-code', '0', '--output', 'Native fixture workflow completed implementation, review, repair and assertions.')
        t.run_cli('sdd', 'workflow', 'complete', '--artifact', t.spec)
        t.run_cli('gate', 'satisfy', '--gate', 'scope_review', '--evidence', 'Native frozen scope and all review findings were inspected.')
        # SPEC done cannot satisfy actual acceptance evidence.
        rejected = t.run_cli('lifecycle', 'bind', '--report', t.spec, '--gate', 'sdd_acceptance', expected=2)
        self.assertIn('CHECK_EVIDENCE_REQUIRED', rejected['error'])
        current = inputs(t.repo, t.work())
        acceptance = {'evidence_type': 'acceptance_result', 'status': 'passed', 'work_id': t.wid,
                      'requirement_sha256': t.work()['requirement']['sha256'], 'code_snapshot': current['code_snapshot'],
                      'checks': [{'id': 'credentials', 'description': 'Correct credentials succeed and incorrect passwords are rejected',
                                  'status': 'passed', 'gates': ['sdd_acceptance'], 'evidence': 'Login.test_credentials passed in native-tests.log'}]}
        t.write('_bmad-output/acceptance.json', json.dumps(acceptance))
        t.run_cli('lifecycle', 'bind', '--report', '_bmad-output/native-tests.log', '--report', '_bmad-output/acceptance.json', '--gate', 'sdd_acceptance')
        t.run_cli('conformance', 'check')
        t.run_cli('verify')
        t.run_cli('done')
        self.assertEqual('completed', t.work()['status'])
        self.assertEqual(1, t.run_cli('sdd', 'review', 'status', expected=2).get('review_counts', counts)['observed_round_count'])
