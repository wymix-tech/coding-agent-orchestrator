import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from scripts.lock_diagnostics import inspect
from scripts.state_store import repo_transaction


def hold(root, ready, release):
    with repo_transaction(Path(root)):
        ready.set()
        release.wait(10)


class LockDiagnostics(unittest.TestCase):
    def test_inspection_survives_lock_and_owner_exit(self):
        with tempfile.TemporaryDirectory() as root:
            ready, release = multiprocessing.Event(), multiprocessing.Event()
            proc = multiprocessing.Process(target=hold, args=(root, ready, release))
            proc.start()
            try:
                self.assertTrue(ready.wait(5))
                state = inspect(Path(root))
                self.assertTrue(state['lock']['held'])
                self.assertEqual(proc.pid, state['lock']['owner_hint']['pid'])
                self.assertFalse(state['completion_certified'])
            finally:
                release.set()
                proc.join(5)
                if proc.is_alive():
                    proc.terminate()
                    proc.join()
            self.assertFalse(inspect(Path(root))['lock']['held'])
            self.assertTrue((Path(root) / '.orchestrator/state.lock').exists())
