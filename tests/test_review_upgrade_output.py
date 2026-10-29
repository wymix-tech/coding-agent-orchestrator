"""Active protocol migration preserves progress; bounded views keep machine JSON available."""
import copy
import json
import unittest
from tests import test_conformance_knowledge as fixtures
from scripts.cli_output import agent_view


class AgentOutput(unittest.TestCase):
    def test_large_history_and_review_internals_do_not_swallow_navigation(self):
        run = {'id': 'current', 'task_binding_version': 1, 'input_sha256': 'sha', 'status': 'classified',
               'findings': [{'content': 'large' * 1000}] * 50}
        raw = {'ok': False, 'ready': False, 'error': 'BLOCKED', 'next': 'cao sdd review resume',
               'sdd': {'native_review_history': [run] * 100, 'native_reviews': {'blind-hunter': run}},
               'blockers': [{'reason': 'PLAN_SCOPE_UNRESOLVED', 'repair_action': 'cao sdd plan show'}]}
        result = agent_view(raw)
        self.assertFalse(result['ready'])
        self.assertFalse(result['ok'])
        self.assertEqual(raw['blockers'], result['blockers'])
        self.assertEqual(raw['next'], result['next'])
        self.assertLess(len(json.dumps(result)), 1500)
        self.assertEqual(100, result['sdd']['native_review_history_count'])
        self.assertIn('findings', raw['sdd']['native_reviews']['blind-hunter'])


class ReviewUpgrade(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SessionConformance()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.t = self.fixture.t
        self.packages = self.fixture.prepare('--external-session')['reviews']
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.t.repo, self.t.wid)
        for layer, run in work['sdd']['native_reviews'].items():
            if layer != '_contract':
                run.pop('resolution_binding_version', None)
        save_work(self.t.repo, work)

    def test_check_apply_idempotence_and_rollback(self):
        before = copy.deepcopy(self.t.work()['sdd']['native_reviews'])
        preview = self.t.run_cli('sdd', 'review', 'migrate', '--check')
        self.assertEqual(2, len(preview['upgrade_layers']))
        self.assertEqual(before, self.t.work()['sdd']['native_reviews'])
        upgraded = self.t.run_cli('sdd', 'review', 'migrate', '--apply')
        self.assertEqual('upgraded', upgraded['status'])
        self.assertEqual('unchanged', self.t.run_cli('sdd', 'review', 'migrate', '--apply')['status'])
        self.t.run_cli('sdd', 'review', 'migrate', '--rollback', upgraded['migration_id'])
        self.assertEqual(before, self.t.work()['sdd']['native_reviews'])

    def test_rollback_cannot_discard_later_progress(self):
        upgraded = self.t.run_cli('sdd', 'review', 'migrate', '--apply')
        from scripts.cao_work_store import load_work, save_work
        work = load_work(self.t.repo, self.t.wid)
        work['sdd']['native_reviews'][self.packages[0]['layer']]['model_source'] = 'later-observation'
        save_work(self.t.repo, work)
        result = self.t.run_cli('sdd', 'review', 'migrate', '--rollback', upgraded['migration_id'], expected=2)
        self.assertIn('PROGRESS_CHANGED', result['error'])

    def test_agent_full_and_history_views_are_explicit(self):
        full = self.t.run_cli('--full-json', 'next')
        compact = self.t.run_cli('--agent-json', 'next')
        self.assertEqual(full['action'], compact['action'])
        self.assertEqual('agent', compact['presentation']['mode'])
        self.assertNotIn('presentation', full)
        history = self.t.run_cli('sdd', 'review', 'status', '--history', '--limit', '1')
        self.assertIn('history_total', history)
        self.t.run_cli('sdd', 'plan', 'show')
