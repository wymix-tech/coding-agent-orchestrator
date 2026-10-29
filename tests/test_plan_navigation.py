"""Plan scope is explicit before implementation and has actionable recovery."""
import unittest
from tests import test_native_route_sync as fixtures
from scripts.cao_plans import bind_plan_scope
from scripts.plan_navigation import establish, repair


class PlanNavigation(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RouteSync()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.propose()
        self.repo = self.fixture.repo

    def test_direct_route_resolves_no_independent_plan_with_audited_basis(self):
        work = self.fixture.work()
        scope = work['sdd']['plan_scope']
        self.assertEqual('none', scope['mode'])
        self.assertEqual(work['sdd']['route_decision']['id'], scope['basis']['route_id'])
        repeated = bind_plan_scope(self.repo, work, [], {}, False)
        self.assertEqual(scope, repeated)
        self.assertFalse(establish(self.repo, work))

    def test_old_unresolved_scope_can_be_repaired_without_binding_spec_as_tasks(self):
        work = self.fixture.work()
        work['sdd']['plan_scope'] = {'mode': 'unresolved', 'plans': []}
        self.assertEqual('cao sdd plan bind --work ' + work['id'] + ' --no-plan', repair(self.repo, work))
        self.assertTrue(establish(self.repo, work))
        self.assertEqual('none', work['sdd']['plan_scope']['mode'])

    def test_full_planning_cannot_be_waived_and_completed_work_is_immutable(self):
        work = self.fixture.work()
        work['sdd']['route_decision']['route'] = 'full-planning'
        with self.assertRaisesRegex(ValueError, 'REQUIRED_BY_ROUTE'):
            bind_plan_scope(self.repo, work, [], {}, True)
        work['sdd']['route_decision']['route'] = 'direct-build'
        work['status'] = 'completed'
        work['sdd'].pop('plan_scope')
        self.assertFalse(establish(self.repo, work))
        self.assertNotIn('plan_scope', work['sdd'])

    def test_independent_tasks_are_discovered_and_cannot_be_removed(self):
        work = self.fixture.work()
        path = self.repo / '_bmad-output/implementation-artifacts/tasks.md'
        path.write_text('- [ ] 1.1 Actual native task\n')
        self.assertTrue(establish(self.repo, work))
        self.assertEqual('unresolved', work['sdd']['plan_scope']['mode'])
        self.assertIn(path.relative_to(self.repo).as_posix(), repair(self.repo, work))
        work['sdd']['plan_scope'] = bind_plan_scope(self.repo, work, [path.relative_to(self.repo).as_posix()], {}, False)
        with self.assertRaisesRegex(ValueError, 'REMOVAL_REQUIRES'):
            bind_plan_scope(self.repo, work, [], {}, True)
