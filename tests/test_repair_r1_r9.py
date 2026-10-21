"""Integrity regressions: real CLI/process boundaries and isolated filesystem/Git inputs.

Reviewer fixtures are local contract fixtures, never online review evidence.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from tests.repair_helpers import RepairCase, ROOT
from scripts import cao
from scripts.native_obligations import commit_state
from tests.test_provider_light_adaptation import ONESHOT


def command(code):
    argv = [sys.executable, '-c', code]
    return subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)


class VerifyIntegrity(RepairCase):
    def minimal(self):
        wid = self.start()
        spec = self.repo / '_bmad-output/spec.md'
        spec.parent.mkdir(exist_ok=True)
        spec.write_text('# Scope\n\n## Acceptance criteria\n- Preserves scope.\n')
        rc, out = self.run_cli('sdd', 'spec', 'bind', '--spec', '_bmad-output/spec.md', '--criteria-section', 'Acceptance criteria')
        self.assertEqual(rc, 0, out)
        return wid

    def configure(self, code):
        # cmd.exe splits multiline -c strings differently from POSIX shells.
        # A real wrapper keeps the race fixture alive until its release marker
        # on every platform instead of accidentally returning after p.touch().
        script = self.repo / 'scripts/verification/integrity_fixture.py'
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(code, encoding='utf-8')
        argv = [sys.executable, str(script)]
        wrapper = subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)
        rc, out = self.run_cli('verification', 'configure', '--test-script', wrapper, '--replace')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.run_cli('reopen', '--reanchor', '--reason', 'Fixture baseline')[0], 0)

    def launch(self):
        proc = subprocess.Popen([sys.executable, str(ROOT / 'scripts/cao.py'), 'verify', '--repo', str(self.repo)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if proc.poll() is None: proc.kill()
            proc.communicate(timeout=10)
        self.addCleanup(cleanup)
        return proc

    def wait_marker(self, marker):
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline: time.sleep(.02)
        self.assertTrue(marker.exists(), 'verification never reached signal')

    def test_r1_empty_and_optional_baselines_never_grant_done(self):
        wid = self.minimal()
        # Simulate legacy declarations, not forged verification state.
        cfg = cao.config(self.repo)
        for scripts in ([], [{'command': command('raise SystemExit(0)'), 'required': False}],
                        [{'command': command('raise SystemExit(1)'), 'required': False}]):
            with self.subTest(scripts=scripts):
                cfg['verification']['scripts'] = scripts
                cao.save_json(self.repo / cao.CONFIG_REL, cfg)
                self.run_cli('reopen', '--reanchor', '--reason', 'Legacy optional fixture')
                self.assertEqual(self.run_cli('verify')[0], 2)
                self.assertEqual(self.run_cli('done')[0], 2)
        self.configure('raise SystemExit(0)')
        self.assertEqual(self.run_cli('verify')[0], 0)
        self.assertEqual(self.run_cli('done')[0], 0)

    def test_r8_completed_and_suspended_are_immutable_to_verify(self):
        wid = self.minimal()
        self.assertEqual(self.run_cli('verify')[0], 0)
        self.assertEqual(self.run_cli('done')[0], 0)
        before = self.read_work(wid)
        self.assertEqual(self.run_cli('verify', '--work', wid)[0], 2)
        self.assertEqual(self.read_work(wid), before)
        self.run_cli('reopen', '--work', wid, '--reason', 'More work')
        self.run_cli('block', '--suspend', '--reason', 'Pause')
        before = self.read_work(wid)
        self.assertEqual(self.run_cli('verify', '--work', wid)[0], 2)
        self.assertEqual(self.read_work(wid), before)
        self.run_cli('unblock', '--work', wid)
        self.assertEqual(self.run_cli('verify')[0], 0)

    def test_r7_real_process_old_success_cannot_override_new_failure(self):
        wid = self.minimal()
        with tempfile.TemporaryDirectory() as outside:
            marker, release = Path(outside) / 'started', Path(outside) / 'release'
            self.configure(f"from pathlib import Path; import time; p=Path({str(marker)!r}); first=not p.exists(); p.touch();\nif first:\n while not Path({str(release)!r}).exists(): time.sleep(.02)\nraise SystemExit(0 if first else 1)")
            first = self.launch(); self.wait_marker(marker)
            self.assertEqual(self.run_cli('done')[0], 2)
            self.assertEqual(self.run_cli('decision', '--decision', 'Concurrent decision')[0], 0)
            self.assertEqual(self.run_cli('verify')[0], 1)
            release.touch()
            out, err = first.communicate(timeout=10)
            self.assertEqual(first.returncode, 2, err)
            self.assertEqual(json.loads(out)['verification']['status'], 'superseded')
            work = self.read_work(wid)
            self.assertEqual(work['verification']['status'], 'failed')
            self.assertEqual(work['decisions'][0]['decision'], 'Concurrent decision')
            self.assertEqual(self.run_cli('done')[0], 2)

    def test_r7_r8_old_generation_cannot_return_after_reanchor_or_suspend(self):
        wid = self.minimal()
        for mode in ('reanchor', 'suspend'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as outside:
                marker, release = Path(outside) / 'started', Path(outside) / 'release'
                self.configure(f"from pathlib import Path; import time; Path({str(marker)!r}).touch();\nwhile not Path({str(release)!r}).exists(): time.sleep(.02)")
                proc = self.launch(); self.wait_marker(marker)
                if mode == 'reanchor': self.run_cli('reopen', '--reanchor', '--reason', 'Same inputs new generation')
                else:
                    self.run_cli('block', '--suspend', '--reason', 'Pause')
                    self.run_cli('unblock', '--work', wid)
                release.touch(); out, err = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 2, err)
                self.assertEqual(json.loads(out)['verification']['status'], 'superseded')
                self.assertEqual(self.run_cli('done')[0], 2)
                self.assertEqual(self.run_cli('verify')[0], 0)

    @unittest.skipIf(os.name == 'nt', 'POSIX signal fixture')
    def test_r7_crash_can_be_superseded_without_faking_success(self):
        self.minimal()
        with tempfile.TemporaryDirectory() as outside:
            marker, release = Path(outside) / 'started', Path(outside) / 'release'
            self.configure(f"from pathlib import Path; import time; p=Path({str(marker)!r}); first=not p.exists(); p.touch();\nif first:\n while not Path({str(release)!r}).exists(): time.sleep(.02)")
            proc = self.launch(); self.wait_marker(marker); proc.kill(); proc.communicate(timeout=10)
            self.assertEqual(self.run_cli('done')[0], 2)
            release.touch()  # Allow the orphan fixture script to exit too.
            self.assertEqual(self.run_cli('verify')[0], 0)
            self.assertEqual(self.run_cli('done')[0], 0)

    def test_r5_chmod_and_legacy_snapshot_require_reverify(self):
        wid = self.minimal()
        path = self.repo / 'run.sh'; path.write_text('echo fixture\n'); path.chmod(0o755)
        self.assertEqual(self.run_cli('verify')[0], 0)
        if os.name != 'nt':
            path.chmod(0o644)
            self.assertEqual(self.run_cli('done')[0], 2)
            self.assertEqual(self.run_cli('verify')[0], 0)
        work = cao.load_work(self.repo, wid)
        # Explicit legacy-state fixture, not real acceptance evidence.
        work['verification'].pop('snapshot_format')
        work['verification']['code_snapshot'] = work['verification']['code_snapshot'].removeprefix('v4:')
        cao.save_work(self.repo, work)
        self.assertEqual(self.run_cli('done')[0], 2)
        self.assertEqual(self.run_cli('verify')[0], 0)
        self.assertEqual(self.run_cli('done')[0], 0)

    def test_r7_new_success_supersedes_old_failure_and_previous_pass_is_unavailable(self):
        wid = self.minimal()
        with tempfile.TemporaryDirectory() as outside:
            marker, release = Path(outside) / 'started', Path(outside) / 'release'
            self.configure(f"from pathlib import Path; import time; p=Path({str(marker)!r}); first=not p.exists(); p.touch();\nif first:\n while not Path({str(release)!r}).exists(): time.sleep(.02)\nraise SystemExit(1 if first else 0)")
            marker.touch()  # Establish a prior genuine script success.
            self.assertEqual(self.run_cli('verify')[0], 0)
            marker.unlink()
            first = self.launch(); self.wait_marker(marker)
            self.assertEqual(self.run_cli('done')[0], 2)
            self.assertEqual(self.run_cli('verify')[0], 0)
            release.touch(); out, err = first.communicate(timeout=10)
            self.assertEqual(first.returncode, 2, err)
            self.assertEqual(self.read_work(wid)['verification']['status'], 'passed')
            self.assertEqual(self.run_cli('done')[0], 0)



class ScopeIntegrity(RepairCase):
    def test_r2_failed_verify_observation_persists_new_story(self):
        wid = self.start()
        path = self.repo / '_bmad-output/sprint-status.yml'; path.parent.mkdir(exist_ok=True)
        def write(extra=''):
            path.write_text('development_status:\n  epic-1: done\n  1-1-a: done\n'+extra)
        write()
        self.assertEqual(self.run_cli('sdd', 'plan', 'bind', '--plan-item', '_bmad-output/sprint-status.yml#epic-1')[0], 0)
        write('  1-2-new: backlog\n')
        self.assertEqual(self.run_cli('verify')[0], 2)
        ids = self.read_work(wid)['sdd']['plan_scope']['plans'][0]['item_ids']
        self.assertIn('1-2-new', ids)
        write()
        rc, out = self.run_cli('verify')
        self.assertEqual((rc, out['error']), (2, 'PLAN_ITEM_MISSING'))
        self.assertIn('1-2-new', out['verification_plan']['reason'])
        write('  1-2-new: done\n  9-1-unrelated: backlog\n')
        self.assertEqual(self.run_cli('verify')[0], 0)

    def test_r2_all_observation_boundaries_and_retry_are_monotone(self):
        from scripts.dashboard import build_dashboard_overview
        from concurrent.futures import ThreadPoolExecutor
        wid = self.start()
        path = self.repo / '_bmad-output/sprint-status.yml'; path.parent.mkdir(exist_ok=True)
        known = {}
        def write(extra=''):
            path.write_text('development_status:\n  epic-1: done\n  1-1-a: done\n'+
                            ''.join(f'  {key}: done\n' for key in known)+extra)
        write()
        self.run_cli('sdd', 'plan', 'bind', '--plan-item', '_bmad-output/sprint-status.yml#epic-1')
        for index, entry in enumerate(('status', 'bootstrap', 'resume', 'next', 'dashboard', 'host-event'), 2):
            story = f'1-{index}-observed'
            write(f'  {story}: backlog\n')
            if entry == 'dashboard': build_dashboard_overview(self.repo)
            elif entry == 'host-event':
                self.run_cli('host-event', '--host', 'pi', payload={'event':'post_tool','session_id':'fixture','tool_name':'read','tool_input':{'path': str(path)}})
            else: self.run_cli(entry)
            self.assertIn(story, self.read_work(wid)['sdd']['plan_scope']['plans'][0]['item_ids'], entry)
            write()
            self.assertEqual(self.run_cli('verify')[1]['error'], 'PLAN_ITEM_MISSING', entry)
            known[story] = True; write()
        write('  1-99-concurrent: backlog\n')
        with ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(lambda _: self.run_cli('status'), range(4)))
        self.assertTrue(all(rc == 0 for rc, _ in outputs), outputs)
        self.assertIn('1-99-concurrent', self.read_work(wid)['sdd']['plan_scope']['plans'][0]['item_ids'])
        events = cao.list_lifecycle_events(self.repo, wid, 1000)
        self.run_cli('status'); self.run_cli('status')
        self.assertEqual(cao.list_lifecycle_events(self.repo, wid, 1000), events)



class ToolIntegrity(RepairCase):
    def event(self, cmd, **kwargs):
        return self.run_cli('host-event', '--host', 'pi', payload={'event': 'pre_tool', 'session_id': 'fixture',
            'tool_name': 'exec_command', 'tool_input': {'cmd': cmd, **kwargs}})[1]

    def test_r3_static_mutation_is_not_readonly(self):
        for cmd in ("find src -type f -delete", "sed -n 'w .orchestrator/config.json' input.txt",
                    'git diff --output=.orchestrator/config.json',
                    'find src -fprint .orchestrator/config.json',
                    "sed -n 'e touch .orchestrator/config.json' input.txt", 'find src -exec touch product.py \\;',
                    'cat README.md; printf x > .orchestrator/index.json'):
            with self.subTest(cmd=cmd): self.assertEqual(self.event(cmd)['decision'], 'deny')
        for cmd in ('find src -type f', "sed -n '1,3p' README.md", 'grep a README.md', 'git status'):
            with self.subTest(cmd=cmd): self.assertEqual(self.event(cmd)['decision'], 'allow')

    def test_r3_unknown_targets_do_not_gain_native_output_exemption(self):
        (self.repo / '_bmad-output').mkdir(exist_ok=True)
        for cmd in ('cd _bmad-output; touch $DESTINATION',
                    'cd _bmad-output; python3 -c "import pathlib; pathlib.Path(\"../product.py\").touch()"',
                    'cd _bmad-output; ./unknown.sh; touch spec.md',
                    'cd _bmad-output; find . -exec python3 -c \"write elsewhere\" \\;',
                    "cd _bmad-output; sed -n 'e unknown.sh' input.txt"):
            with self.subTest(cmd=cmd): self.assertEqual(self.event(cmd)['decision'], 'deny')
        # Literal quoted input data is not an unknown output target.
        self.assertEqual(self.event("cd _bmad-output; printf '%s' '$X' > spec.md")['decision'], 'allow')
        self.start()
        self.assertEqual(self.event('./unknown.sh')['decision'], 'allow')

    def test_r4_workdir_cd_links_and_structured_targets_agree(self):
        self.start(); (self.repo / 'sub').mkdir()
        for cmd, wd in [('printf x > ../.orchestrator/config.json', str(self.repo / 'sub')),
                        ('cd sub; printf x > ../.orchestrator/config.json', str(self.repo)),
                        ('cd ..; printf x > .orchestrator/config.json', str(self.repo / 'sub'))]:
            with self.subTest(cmd=cmd): self.assertEqual(self.event(cmd, workdir=wd)['decision'], 'deny')
        raw = {'tool_name': 'write', 'cwd': str(self.repo / 'sub'), 'input': {'path': '../.orchestrator/config.json', 'content': 'x'}}
        self.assertTrue(cao.write_targets_protected_state(self.repo, raw))
        self.assertEqual(self.event('printf x > ../_bmad-output/spec.md', workdir=str(self.repo / 'sub'))['decision'], 'allow')

    def test_r4_host_shapes_aliases_and_relative_workdir(self):
        self.start(); sub = self.repo / 'sub'; sub.mkdir()
        raw = {'tool_name': 'exec_command', 'cwd': str(sub),
               'arguments': {'cmd': 'printf x > .orchestrator/config.json', 'workdir': '..'}}
        self.assertTrue(cao.write_targets_protected_state(self.repo, raw))
        try: (self.repo / 'alias').symlink_to(sub, target_is_directory=True)
        except OSError as exc: self.skipTest(str(exc))
        for host, tool, fields in [('pi', 'bash', {'input': {'command': 'printf x > ../.orchestrator/config.json'}}),
                                   ('codex', 'exec_command', {'arguments': {'cmd': 'printf x > ../.orchestrator/config.json'}}),
                                   ('claude-code', 'Bash', {'tool_input': {'command': 'printf x > ../.orchestrator/config.json'}})]:
            rc, out = self.run_cli('host-event', '--host', host, payload={'event':'pre_tool', 'session_id':'fixture',
                'tool_name':tool, 'cwd':str(self.repo / 'alias'), **fields})
            decision = out.get('decision') or out.get('hookSpecificOutput', {}).get('permissionDecision')
            self.assertEqual(decision, 'deny', (host, out))



class SnapshotIntegrity(RepairCase):
    def test_r5_permissions_links_cycles_external_and_version(self):
        source = self.repo / 'run.sh'; source.write_text('echo ok\n'); source.chmod(0o755)
        initial = cao.code_snapshot(self.repo)
        self.assertTrue(initial.startswith('v4:'))
        if os.name != 'nt':
            source.chmod(0o644); self.assertNotEqual(initial, cao.code_snapshot(self.repo))
        folder = self.repo / 'src'; folder.mkdir(); (folder / 'app.py').write_text('x=1')
        link = self.repo / 'linked'
        try: link.symlink_to(folder, target_is_directory=True)
        except OSError as exc: self.skipTest(str(exc))
        before = cao.code_snapshot(self.repo); (folder / 'app.py').write_text('x=2')
        self.assertNotEqual(before, cao.code_snapshot(self.repo))
        link.unlink(); link.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'CYCLE'): cao.code_snapshot(self.repo)
        link.unlink()
        with tempfile.TemporaryDirectory() as external:
            link.symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'EXTERNAL_SYMLINK'): cao.code_snapshot(self.repo)
        link.unlink(); link.symlink_to(self.repo / 'missing')
        with self.assertRaisesRegex(RuntimeError, 'READ_FAILED'): cao.code_snapshot(self.repo)


class CommitIntegrity(RepairCase):
    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)


    def test_r6_repository_subdir_and_git_worktree(self):
        self.git('init', '-q'); self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.test')
        sub = self.repo / 'project'; (sub / '_bmad-output').mkdir(parents=True)
        spec = sub / '_bmad-output/spec 中文.md'; spec.write_text(ONESHOT)
        (self.repo / '.gitignore').write_text('.orchestrator/\n')
        self.git('add', '-A'); self.git('commit', '-qm', 'Subproject fixture')
        work = {'sdd': {'provider':'bmad', 'spec_binding': {'spec': {'path':'_bmad-output/spec 中文.md'}}}}
        self.assertTrue(commit_state(sub, work, cao.SNAPSHOT_IGNORE_DIRS)['ready'])
        with tempfile.TemporaryDirectory() as outside:
            checkout = Path(outside) / 'linked-worktree'
            self.git('worktree', 'add', '--detach', str(checkout), 'HEAD')
            try:
                self.assertTrue(commit_state(checkout / 'project', work, cao.SNAPSHOT_IGNORE_DIRS)['ready'])
            finally: self.git('worktree', 'remove', '--force', str(checkout))

    def test_r6_spec_must_be_tracked_in_head_and_clean(self):
        wid = self.start()
        spec = self.repo / '_bmad-output/spec 中文.md'; spec.parent.mkdir(exist_ok=True); spec.write_text(ONESHOT)
        self.run_cli('sdd', 'spec', 'bind', '--spec', spec.relative_to(self.repo))
        self.git('init', '-q'); self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.test')
        (self.repo / '.gitignore').write_text('_bmad-output/\n.orchestrator/\n')
        self.git('add', '-A'); self.git('commit', '-qm', 'Business fixture')
        work = cao.load_work(self.repo, wid)
        def state(): return commit_state(self.repo, work, cao.SNAPSHOT_IGNORE_DIRS)
        self.assertEqual(state()['reason'], 'NATIVE_SPEC_IGNORED')
        (self.repo / '.gitignore').write_text('.orchestrator/\n'); self.git('add', '.gitignore'); self.git('commit', '-qm', 'Track native SPEC policy')
        self.assertEqual(state()['reason'], 'NATIVE_SPEC_UNTRACKED')
        self.git('add', '--', str(spec.relative_to(self.repo)))
        self.assertEqual(state()['reason'], 'NATIVE_SPEC_NOT_IN_HEAD')
        self.git('commit', '-qm', 'SPEC fixture')
        self.assertTrue(state()['ready'], state())
        (self.repo / '.gitattributes').write_text('*.md text eol=crlf\n')
        self.git('add', '.gitattributes'); self.git('commit', '-qm', 'Normalize fixture text')
        spec.write_bytes(ONESHOT.replace('\n', '\r\n').encode())
        self.assertTrue(state()['ready'], state())
        spec.write_text(ONESHOT+'\nChanged\n'); self.assertEqual(state()['reason'], 'NATIVE_SPEC_UNCOMMITTED')
        self.git('add', '--', str(spec.relative_to(self.repo))); self.assertEqual(state()['reason'], 'NATIVE_SPEC_UNCOMMITTED')
