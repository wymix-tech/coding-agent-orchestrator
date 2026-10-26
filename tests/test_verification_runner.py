"""Bounded verification and descendant cleanup, including real PID liveness checks."""
import json
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from repair_helpers import RepairCase, ROOT
from scripts import cao, completion_policy as policy
from scripts.verification_runner import run_script


def command(code):
    argv = [sys.executable, "-c", code]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def alive(pid):
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, 0, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Linux may retain an orphan zombie until init reaps it; it is not a live process.
    stat = pathlib.Path(f"/proc/{pid}/stat")
    if stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"):
        return False
    return True


class RunnerTests(unittest.TestCase):
    def test_timeout_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            started = time.monotonic()
            result = run_script(command("import time; time.sleep(30)"), pathlib.Path(td), timeout=.2)
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["exit_code"], 124)
            self.assertLess(time.monotonic() - started, 8)

    def test_large_both_streams_are_bounded_and_utf8_is_retained(self):
        with tempfile.TemporaryDirectory() as td:
            result = run_script(command("import sys; sys.stdout.buffer.write(('中'*1000000+'\\n').encode()); sys.stderr.buffer.write(('文'*1000000+'\\n').encode())"), pathlib.Path(td))
            self.assertEqual(result["exit_code"], 0)
            for key, char in (("stdout_tail", "中"), ("stderr_tail", "文")):
                self.assertLessEqual(len(result[key].encode("utf-8")), 4000)
                self.assertIn(char, result[key])

    def test_exit_codes_preserve_protocol(self):
        with tempfile.TemporaryDirectory() as td:
            for code, status in ((0, "passed"), (1, "failed"), (2, "blocked"), (3, "error")):
                result = run_script(command(f"import sys; sys.exit({code})"), pathlib.Path(td))
                self.assertEqual(cao.verification_status_for_exit_code(result["exit_code"]), status)
                self.assertFalse(result["timed_out"])

    def test_invalid_limits_are_rejected_before_launch(self):
        for timeout in (0, -1, float("inf"), float("nan"), True, "1"):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(ValueError, "VERIFICATION_LIMIT_INVALID"):
                run_script("not launched", ROOT, timeout=timeout)
        for size in (0, -1, True, 1.5):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "VERIFICATION_LIMIT_INVALID"):
                run_script("not launched", ROOT, max_bytes=size)

    def test_spawn_failure_is_error(self):
        with mock.patch("scripts.verification_runner.subprocess.Popen", side_effect=OSError("spawn failed")):
            result = run_script("ignored", ROOT)
        self.assertEqual(result["exit_code"], 127)
        self.assertIn("spawn failed", result["stderr_tail"])

    def test_reader_failure_is_not_silent_success(self):
        import scripts.verification_runner as runner
        original = runner.subprocess.Popen
        class BrokenStream:
            def __init__(self, real): self.real = real
            def read(self, size): raise OSError("reader failed")
            def close(self): self.real.close()
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            child.stdout = BrokenStream(child.stdout)
            return child
        with tempfile.TemporaryDirectory() as td, mock.patch.object(runner.subprocess, "Popen", side_effect=launch):
            result = run_script(command("print('ok')"), pathlib.Path(td), timeout=1)
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("reader failed", result["stderr_tail"])

    def test_thread_start_failure_still_reaps_process(self):
        import scripts.verification_runner as runner
        original = runner.subprocess.Popen
        children = []
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        with tempfile.TemporaryDirectory() as td:
            try:
                with mock.patch.object(runner.subprocess, "Popen", side_effect=launch), \
                     mock.patch.object(runner.threading.Thread, "start", side_effect=RuntimeError("start failed")):
                    result = run_script(command("import time; time.sleep(30)"), pathlib.Path(td), timeout=1)
                self.assertNotEqual(result["exit_code"], 0)
                self.assertIn("start failed", result["stderr_tail"])
                self.assertIsNotNone(children[0].poll())
            finally:
                for child in children:
                    if child.poll() is None:
                        if os.name == "nt": child.kill()
                        else: os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                    child.stdout.close(); child.stderr.close()

    def test_pipe_cleanup_failure_is_error_not_an_uncaught_exception(self):
        import scripts.verification_runner as runner
        class StuckReader:
            def __init__(self, *args, **kwargs): pass
            def start(self): pass
            def join(self, timeout=None): pass
            def is_alive(self): return True
        with tempfile.TemporaryDirectory() as td, mock.patch.object(runner.threading, "Thread", StuckReader):
            result = run_script(command("print('ok')"), pathlib.Path(td), timeout=.2)
        self.assertEqual(result["exit_code"], 125)
        self.assertIn("VERIFICATION_PIPE_CLEANUP_FAILED", result["stderr_tail"])

    def test_reader_runtime_failure_is_reported(self):
        import scripts.verification_runner as runner
        original = runner.subprocess.Popen
        class BrokenStream:
            def __init__(self, real): self.real = real
            def read(self, size): raise RuntimeError("unexpected reader failure")
            def close(self): self.real.close()
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            child.stdout = BrokenStream(child.stdout)
            return child
        with tempfile.TemporaryDirectory() as td, mock.patch.object(runner.subprocess, "Popen", side_effect=launch):
            result = run_script(command("print('ok')"), pathlib.Path(td), timeout=1)
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("unexpected reader failure", result["stderr_tail"])

    def check_descendants(self, parent_exits):
        with tempfile.TemporaryDirectory() as td:
            repo = pathlib.Path(td)
            code = ("import subprocess,sys,pathlib,time,os; "
                    "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                    "pathlib.Path('pids.json').write_text(__import__('json').dumps([os.getpid(),child.pid])); "
                    + ("sys.exit(0)" if parent_exits else "time.sleep(30)"))
            pids = []
            try:
                result = run_script(command(code), repo, timeout=.8)
                pids = json.loads((repo / "pids.json").read_text())
                deadline = time.monotonic() + 3
                while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(any(alive(pid) for pid in pids), pids)
                if not parent_exits:
                    self.assertTrue(result["timed_out"])
                    self.assertEqual(result["exit_code"], 124)
                else:
                    self.assertIn(result["exit_code"], (0, 124))
            finally:
                for pid in pids:
                    if alive(pid):
                        os.kill(pid, signal.SIGTERM)

    def test_timeout_reaps_parent_and_grandchild(self):
        self.check_descendants(False)

    def test_exited_parent_cannot_leave_live_grandchild(self):
        self.check_descendants(True)


class RunnerBindingsTests(RepairCase):
    def set_timeout(self, timeout):
        cfg = cao.config(self.repo)
        cfg["verification"]["timeout_seconds"] = timeout
        cao.save_json(self.repo / ".orchestrator/config.json", cfg)
        from tests.helpers import seed_project_preparation
        seed_project_preparation(self.repo)

    def test_timeout_changes_standard_requires_reanchor_and_is_bound_on_both_sides(self):
        wid = self.start()
        self.set_timeout(1)
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.get("error"), "VERIFICATION_STANDARD_CHANGED")
        rc, out = self.run_cli("reopen", "--work", wid, "--reanchor", "--reason", "reviewed timeout")
        self.assertEqual(rc, 0, out)
        work = self.read_work(wid)
        self.assertEqual(work["acceptance"]["config_sha256"], cao.verification_snapshot(cao.config(self.repo)))
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["verification"]["verification_config_sha256"], work["acceptance"]["config_sha256"])
        self.assertNotEqual(policy.acceptance_hash([], 1), policy.acceptance_hash([], 2))

    def test_start_binds_configured_timeout_not_default(self):
        self.set_timeout(2)
        wid = self.start()
        self.assertEqual(self.read_work(wid)["acceptance"]["config_sha256"], cao.verification_snapshot(cao.config(self.repo)))
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)

    def test_cli_timeout_records_error_and_releases_repo_lock(self):
        self.set_timeout(.2)
        self.start()
        rc, out = self.run_cli("verify", "--command", command("import time; time.sleep(30)"))
        self.assertEqual(rc, 3, out)
        step = out["verification"]["results"][0]
        self.assertTrue(step["timed_out"])
        self.assertEqual(step["status"], "error")
        self.assertEqual(step["exit_code"], 124)
        rc, out = self.run_cli("decision", "--decision", "lock remains usable")
        self.assertEqual(rc, 0, out)
