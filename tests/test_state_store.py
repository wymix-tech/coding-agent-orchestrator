import errno
import importlib
import importlib.util
import json
import multiprocessing as mp
import pathlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from scripts import cao

state_store = (importlib.import_module("scripts.state_store")
               if importlib.util.find_spec("scripts.state_store") else None)


def hold_lock(repo, ready, release):
    with state_store.repo_transaction(pathlib.Path(repo)):
        ready.set()
        release.wait(10)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(state_store, "repository transaction and atomic JSON module is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name)
        self.path = self.repo / "state.json"

    def test_replace_failure_keeps_old_json(self):
        state_store.atomic_json(self.path, {"v": 1})
        with mock.patch.object(state_store.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                state_store.atomic_json(self.path, {"v": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"v": 1})
        self.assertEqual(list(self.repo.glob("*.tmp")), [])

    def test_fsync_failure_keeps_old_json(self):
        state_store.atomic_json(self.path, {"v": 1})
        with mock.patch.object(state_store.os, "fsync", side_effect=OSError("sync")):
            with self.assertRaises(OSError):
                state_store.atomic_json(self.path, {"v": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"v": 1})
        self.assertEqual(list(self.repo.glob("*.tmp")), [])

    def test_serialization_failure_cleans_temp_file(self):
        state_store.atomic_json(self.path, {"v": 1})
        with self.assertRaises(TypeError):
            state_store.atomic_json(self.path, {"v": object()})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"v": 1})
        self.assertEqual(list(self.repo.glob("*.tmp")), [])

    def test_save_json_uses_atomic_write(self):
        cao.save_json(self.path, {"v": 1})
        with mock.patch.object(state_store.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                cao.save_json(self.path, {"v": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"v": 1})
        self.assertEqual(list(self.repo.glob("*.tmp")), [])

    def test_utf8_sorted_json_in_new_parent(self):
        path = self.repo / "nested/state.json"
        state_store.atomic_json(path, {"z": "中文", "a": 1})
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text, '{\n  "a": 1,\n  "z": "中文"\n}\n')

    def test_lock_reenters_with_canonical_path(self):
        with state_store.repo_transaction(self.repo, timeout=.2):
            with state_store.repo_transaction(self.repo / "nested/..", timeout=.2):
                self.assertTrue((self.repo / ".orchestrator/state.lock").is_file())
        self.assertTrue((self.repo / ".orchestrator/state.lock").is_file())

    def test_threads_are_serialized(self):
        attempted = threading.Event()

        def attempt():
            attempted.set()
            with self.assertRaisesRegex(RuntimeError, "STATE_LOCK_TIMEOUT"):
                with state_store.repo_transaction(self.repo, timeout=.1):
                    pass

        with ThreadPoolExecutor(max_workers=1) as pool:
            with state_store.repo_transaction(self.repo):
                future = pool.submit(attempt)
                self.assertTrue(attempted.wait(5))
                future.result(timeout=5)
            with state_store.repo_transaction(self.repo, timeout=.2):
                pass

    def test_different_repositories_do_not_share_a_lock(self):
        def attempt():
            with state_store.repo_transaction(self.repo / "other", timeout=.1):
                return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            with state_store.repo_transaction(self.repo):
                self.assertTrue(pool.submit(attempt).result(timeout=5))

    def test_failed_lock_acquisition_releases_resources(self):
        with mock.patch.object(state_store, "_lock", side_effect=OSError(errno.EIO, "disk")):
            with self.assertRaises(OSError):
                with state_store.repo_transaction(self.repo):
                    pass
        with state_store.repo_transaction(self.repo, timeout=.2):
            pass

    def test_unlock_failure_releases_thread_mutex(self):
        with mock.patch.object(state_store, "_unlock", side_effect=OSError("unlock")):
            with self.assertRaises(OSError):
                with state_store.repo_transaction(self.repo):
                    pass
        with ThreadPoolExecutor(max_workers=1) as pool:
            def attempt():
                with state_store.repo_transaction(self.repo, timeout=.1):
                    return True
            try:
                acquired = pool.submit(attempt).result(timeout=5)
            except RuntimeError as exc:
                acquired = str(exc)
            self.assertIs(acquired, True)

    def test_exception_releases_transaction(self):
        with self.assertRaisesRegex(ValueError, "abort"):
            with state_store.repo_transaction(self.repo):
                with state_store.repo_transaction(self.repo):
                    raise ValueError("abort")
        with ThreadPoolExecutor(max_workers=1) as pool:
            def attempt():
                with state_store.repo_transaction(self.repo, timeout=.2):
                    return True
            self.assertTrue(pool.submit(attempt).result(timeout=5))


class ProcessLockTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(state_store, "repository transaction module is missing")

    def test_timeout_and_owner_death(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = mp.get_context("spawn")
            ready, release = ctx.Event(), ctx.Event()
            child = ctx.Process(target=hold_lock, args=(td, ready, release))
            child.start()
            try:
                self.assertTrue(ready.wait(5), "owner did not acquire the lock")
                with self.assertRaisesRegex(RuntimeError, "STATE_LOCK_TIMEOUT"):
                    with state_store.repo_transaction(pathlib.Path(td), timeout=.1):
                        pass
            finally:
                child.terminate()
                child.join(5)
                if child.is_alive():
                    child.kill()
                    child.join(5)
            self.assertFalse(child.is_alive())
            with state_store.repo_transaction(pathlib.Path(td), timeout=1):
                pass
            self.assertTrue((pathlib.Path(td) / ".orchestrator/state.lock").exists())


if __name__ == "__main__":
    unittest.main()
