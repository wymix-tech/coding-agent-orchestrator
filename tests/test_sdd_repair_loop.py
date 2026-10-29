"""Native failures remain repairable before CAO conformance/verification.

Real CLI/Pi events with the existing native-provider fixture. The local build
runner copies a JVM resource then compiles Python: no Maven/network dependency.
"""
import json
import subprocess
import sys
import unittest

from tests import test_native_route_sync as native


class SddRepairLoop(unittest.TestCase):
    def setUp(self):
        class BuildFixture(native.RouteSync):
            def run_cli(self, *args, **kwargs):
                if args[:2] == ('verification', 'configure'):
                    args = (*args, '--output', 'target/')
                result = super().run_cli(*args, **kwargs)
                if args and args[0] == 'init':
                    path = self.repo / '.orchestrator/config.json'
                    cfg = json.loads(path.read_text())
                    cfg['lifecycle_contract'] = 2
                    path.write_text(json.dumps(cfg))
                return result

        self.f = BuildFixture('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_failed_native_build_allows_edit_and_retest_without_cao_completion(self):
        f = self.f
        f.propose()
        f.approve()
        f.write(f.spec, (f.repo / f.spec).read_text().replace("status: 'draft'", "status: 'in-progress'"))
        f.write('pom.xml', '<project/>')
        f.write('src/main/resources/application.yml', 'spring:\n  application:\n    name: fixture\n')
        f.write('src/auth.py', 'def broken(:\n')
        f.write('native_build.py', "from pathlib import Path\nimport shutil\n"
                "output = Path('target/classes/application.yml')\n"
                "output.parent.mkdir(parents=True, exist_ok=True)\n"
                "shutil.copyfile('src/main/resources/application.yml', output)\n"
                "compile(Path('src/auth.py').read_text(), 'src/auth.py', 'exec')\n")
        child = {'kind': 'subagent', 'subagent_id': 'implementation-child',
                 'session_id': 'implementation-child', 'parent_session_id': 'primary-fixture'}
        started = f.hook('subagent_start', **child)
        self.assertIn('do not restart intake', started['additional_context'])
        command = f'{sys.executable} native_build.py'
        tool = {'tool_name': 'bash', 'tool_input': {'command': command}}
        self.assertEqual('allow', f.hook('pre_tool', **child, **tool)['decision'])
        failed = subprocess.run([sys.executable, 'native_build.py'], cwd=f.repo, capture_output=True, text=True)
        self.assertNotEqual(0, failed.returncode)
        self.assertIn('SyntaxError', failed.stderr)
        result = f.hook('post_tool', **child, **tool, exit_code=failed.returncode, is_error=True)
        self.assertEqual('allow', result['decision'])
        for path in ('src/auth.py', 'src/main/resources/application.yml'):
            result = f.hook('pre_tool', **child, tool_name='edit', tool_input={'path': path, 'new_string': 'fixed'})
            self.assertEqual('allow', result['decision'], result)
        f.write('src/auth.py', 'def ready():\n    return True\n')
        self.assertEqual('allow', f.hook('pre_tool', **child, **tool)['decision'])
        fixed = subprocess.run([sys.executable, 'native_build.py'], cwd=f.repo, capture_output=True, text=True)
        self.assertEqual(0, fixed.returncode, fixed.stderr)
        f.hook('post_tool', **child, **tool, exit_code=0)
        self.assertEqual('implementation', f.work()['execution']['phase'])
        self.assertEqual('active', f.work()['status'])
        self.assertFalse(f.work().get('verification'))
        self.assertNotIn('completion_evidence', f.work()['sdd'])
        action = f.run_cli('next')['action']
        self.assertEqual('execution', action['stage'])
        self.assertIn('Native compile/test failures stay inside SDD implementation', action['instruction'])
        self.assertEqual('CONFORMANCE_NOT_READY', f.run_cli('verify', expected=2)['error'])
        self.assertEqual('deny', f.hook('pre_tool', **child, tool_name='edit',
                         tool_input={'path': f'.orchestrator/work/{f.wid}.json', 'new_string': '{}'})['decision'])

    def test_snapshot_fault_cannot_block_diagnosis_or_parent_communication(self):
        f = self.f
        f.propose()
        f.approve()
        f.write('target/undeclared-source.py', 'cannot hide arbitrary source')
        launcher = (f.repo / 'cao-unrelated').as_posix()
        from tests.repair_helpers import ROOT
        for tool, payload in [
            ('read', {'path': 'target/undeclared-source.py'}),
            ('search_code', {'pattern': 'source'}),
            ('contact_supervisor', {'reason': 'need_decision', 'message': 'Snapshot recovery needed'}),
            ('bash', {'command': f'{ROOT / "cao"} status --work {f.wid}'}),
            ('bash', {'command': f'{ROOT / "cao"} host install --host pi'}),
            ('bash', {'command': 'nl -ba pom.xml | sed -n 1,20p'}),
        ]:
            result = f.hook('pre_tool', tool_name=tool, tool_input=payload)
            self.assertEqual('allow', result['decision'], (tool, payload, result))
        # Mutations and unrecognized lookalike launchers still fail closed.
        for tool, payload in [('subagent', {'action': 'status', 'id': 'implementation-child'}),
                              ('edit', {'path': 'src/auth.py', 'new_string': 'changed'}),
                              ('bash', {'command': launcher + ' status'}),
                              ('bash', {'command': 'cao status > src/auth.py'})]:
            result = f.run_cli('host-event', '--host', 'pi', expected=2,
                              raw={'event': 'pre_tool', 'session_id': 'primary-fixture',
                                   'tool_name': tool, 'tool_input': payload})
            self.assertIn('SNAPSHOT_SCOPE_PROTECTED', result['error'])
        # The diagnostic CLI itself is reachable and does not need a snapshot.
        self.assertTrue(f.run_cli('host', 'status', '--host', 'pi')['ok'])
        before = f.work()
        status = f.run_cli('status', '--work', f.wid)
        self.assertTrue(status['inspection_only'])
        self.assertIn('SNAPSHOT_SCOPE_PROTECTED', status['diagnostic'])
        self.assertEqual(f.wid, status['work']['work_item']['id'])
        self.assertFalse(status['work']['completion']['ready'])
        self.assertEqual(before, f.work())
        self.assertEqual('active', f.work()['status'])

