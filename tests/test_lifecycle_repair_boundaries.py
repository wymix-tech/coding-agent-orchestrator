"""Explicit evidence repair and preparation do not erase lifecycle requirements."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.native_completion import state as terminal_state
from scripts.route_repair import relocate, migrate_evidence
from scripts.cao_tool_policy import readonly_shell
from tests.repair_helpers import RepairCase


class EvidenceRelocation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for directory in ('old', 'new'):
            root = self.repo / directory
            root.mkdir()
            (root / 'step-02-plan.md').write_text('Dispatch rule\n')
            for name in ('step-03-implement.md', 'step-04-review.md', 'step-05-finish.md'):
                (root / name).write_text('Actual native obligation\n')
        sha = hashlib.sha256(b'Dispatch rule\n').hexdigest()
        self.old = {'id': 'route-original', 'provider': 'bmad', 'reason': 'old location',
                    'rules': [{'path': 'old/step-02-plan.md', 'sha256': sha, 'quote': 'Dispatch rule'}]}
        self.new = {'provider': 'bmad', 'reason': 'equivalent evidence relocation',
                    'rules': [{'path': 'new/step-02-plan.md', 'sha256': sha, 'quote': 'Dispatch rule'}]}

    def test_byte_equivalent_contracts_keep_route_identity_and_handoff(self):
        result, moves = relocate(self.repo, self.old, self.new)
        self.assertEqual('route-original', result['id'])
        sdd = {'spec_approval': {'route_decision_id': 'route-original'},
               'native_implementation': {'contract': {'path': 'old/step-03-implement.md'}, 'route_id': 'route-original'}}
        approval = copy.deepcopy(sdd['spec_approval'])
        migrate_evidence(sdd, moves)
        self.assertEqual(approval, sdd['spec_approval'])
        self.assertEqual('new/step-03-implement.md', sdd['native_implementation']['contract']['path'])

    def test_same_rule_with_changed_downstream_review_is_not_equivalent(self):
        (self.repo / 'new/step-04-review.md').write_text('A new required review layer')
        with self.assertRaisesRegex(ValueError, 'CONTRACT_NOT_EQUIVALENT'):
            relocate(self.repo, self.old, self.new)

    def test_changed_intent_and_old_source_tampering_are_rejected(self):
        new = {**self.new, 'build_route': 'oneshot'}
        with self.assertRaisesRegex(ValueError, 'SEMANTIC_CHANGE'):
            relocate(self.repo, self.old, new)
        (self.repo / 'old/step-02-plan.md').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'SOURCE_STALE'):
            relocate(self.repo, self.old, self.new)

    def test_build_and_test_are_never_generic_readonly(self):
        for command in ('pytest', 'python -m unittest', 'mvn test', 'gradle test', 'npm test', 'bash scripts/verification/test.sh'):
            self.assertFalse(readonly_shell(command), command)

    def test_bmad_terminal_status_is_a_distinct_completion_obligation(self):
        source = self.repo / '_bmad-output/spec.md'
        source.parent.mkdir()
        work = {'sdd': {'provider': 'bmad', 'route_decision': {'build_route': 'dispatch', 'primary_artifact': '_bmad-output/spec.md'}}}
        source.write_text('---\nstatus: in-review\n---\n## Intent\nWork.\n')
        self.assertEqual('NATIVE_FINALIZE_REQUIRED', terminal_state(self.repo, work)['reason'])
        source.write_text(source.read_text().replace('in-review', 'done'))
        self.assertTrue(terminal_state(self.repo, work)['ready'])
        work['sdd']['provider'] = 'openspec'
        self.assertEqual('not_applicable', terminal_state(self.repo, work)['state'])


class BaselinePreparation(RepairCase):
    def test_probe_runs_before_review_and_does_not_grant_formal_verification(self):
        wid = self.start(profile='standard')
        before = self.read_work(wid)
        rc, out = self.run_cli('verification', 'check', '--work', wid)
        self.assertEqual(0, rc, out)
        self.assertEqual('baseline_preparation', out['purpose'])
        after = self.read_work(wid)
        self.assertEqual(before.get('verification'), after.get('verification'))
        self.assertEqual(before.get('gate_status'), after.get('gate_status'))
        self.assertEqual(before.get('execution'), after.get('execution'))
        rc, _ = self.run_cli('done', '--work', wid)
        self.assertEqual(2, rc)
