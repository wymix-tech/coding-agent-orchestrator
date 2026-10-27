"""Continuity is visible without promoting historical declarations to gates."""
from tests.repair_helpers import RepairCase
from scripts.continuity_view import view


class ContinuityTests(RepairCase):
    def test_resume_displays_completed_pending_and_handoff(self):
        wid = self.start()
        self.assertEqual(self.run_cli('checkpoint', '--completed', 'slice completed',
                                      '--pending', 'inspect interrupted test')[0], 0)
        rc, out = self.run_cli('handoff', '--summary', 'transfer snapshot')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out['handoff']['version'], 2)
        self.assertIn('native', out['handoff']['continuity'])
        rc, out = self.run_cli('resume', '--work', wid, '--host', 'codex', '--session', 'actual-session')
        self.assertEqual(rc, 0, out)
        self.assertIn('slice completed', out['raw'])
        self.assertIn('inspect interrupted test', out['raw'])
        self.assertIn('transfer snapshot', out['raw'])

    def test_historical_pending_never_replaces_current_and_large_lists_are_bounded(self):
        work = {'id': 'a', 'resume': {'pending': [str(i) for i in range(100)]},
                'execution_generation': 3}
        old = {'id': 'h', 'version': 1, 'pending': ['old item'], 'execution_generation': 1}
        result = view(work, old)
        self.assertEqual(result['pending']['omitted'], 80)
        self.assertNotIn('old item', result['pending']['items'])
        self.assertIn('execution_generation_changed', result['handoff']['differences'])
        self.assertIn('legacy_handoff_missing_source_snapshot', result['handoff']['differences'])

    def test_process_evidence_accepts_failure_without_granting_completion(self):
        wid = self.start()
        path = self.repo / '_bmad-output/result.md'
        path.parent.mkdir(exist_ok=True)
        path.write_text('status: failed\n')
        before = self.read_work(wid)
        rc, out = self.run_cli('evidence', 'attach', '--kind', 'test', '--path', '_bmad-output/result.md')
        self.assertEqual(rc, 0, out)
        evidence = out['evidence']['id']
        rc, out = self.run_cli('evidence', 'attach', '--kind', 'test', '--path', '_bmad-output/result.md')
        self.assertEqual(out['status'], 'unchanged')
        self.assertEqual(out['evidence_id'], evidence)
        after = self.read_work(wid)
        self.assertEqual(before.get('verification'), after.get('verification'))
        self.assertEqual(before.get('gate_status'), after.get('gate_status'))
        self.assertNotIn('completion_evidence', after['sdd'])
        path.write_text('status: passed\n')
        rc, out = self.run_cli('evidence', 'list')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out['items'][0]['content_state'], 'changed')

    def test_process_evidence_rejects_paths_outside_provider(self):
        self.start()
        (self.repo / 'outside.md').write_text('status: passed\n')
        self.assertNotEqual(self.run_cli('evidence', 'attach', '--kind', 'test', '--path', 'outside.md')[0], 0)
