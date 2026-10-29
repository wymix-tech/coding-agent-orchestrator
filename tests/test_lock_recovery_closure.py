"""Contended lock -> inspection -> authorized recovery -> verify -> done."""
import errno
import json
import multiprocessing
import time
import unittest
from unittest.mock import patch
from tests import test_native_lifecycle_contract as lifecycle
from tests.test_lock_diagnostics import hold
from scripts.state_store import repo_transaction


class LockRecoveryClosure(unittest.TestCase):
    def test_actual_work_diagnostics_and_recovery_preview_do_not_lock_or_mutate(self):
        fixture = lifecycle.NativeLifecycle('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.bind()
        from scripts.cao_work_store import work_path
        target = work_path(fixture.repo, fixture.wid)
        authorized = target.read_bytes()
        work = json.loads(authorized)
        work['title'] = 'unsupported external writer'
        target.write_text(json.dumps(work))
        ready, release = multiprocessing.Event(), multiprocessing.Event()
        proc = multiprocessing.Process(target=hold, args=(str(fixture.repo), ready, release))
        proc.start()
        try:
            self.assertTrue(ready.wait(5))
            before = {str(p): p.read_bytes() for p in (fixture.repo / '.orchestrator').rglob('*') if p.is_file()}
            started = time.monotonic()
            for args in (('diagnose',), ('diagnose', '--work', fixture.wid)):
                result = fixture.check(*args)
                self.assertTrue(result['lock']['held'])
                self.assertFalse(result['completion_certified'])
            preview = fixture.check('lifecycle', 'recover', '--check', '--work', fixture.wid)
            self.assertTrue(preview['recovery_required'])
            self.assertTrue(preview['inspection_only'])
            self.assertLess(time.monotonic() - started, 2)
            after = {str(p): p.read_bytes() for p in (fixture.repo / '.orchestrator').rglob('*') if p.is_file()}
            self.assertEqual(before, after)
        finally:
            release.set()
            proc.join(5)
            if proc.is_alive():
                proc.terminate()
                proc.join()
        result = fixture.check('lifecycle', 'recover', '--work', fixture.wid)
        self.assertEqual('recovered', result['status'])
        self.assertEqual(json.loads(authorized)['title'], fixture.work()['title'])
        fixture.check('conformance', 'check')
        fixture.check('verify')
        fixture.check('done')
        self.assertEqual('completed', fixture.work()['status'])

    def test_owner_metadata_write_failure_does_not_break_lock(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            with patch('scripts.state_store.atomic_json', side_effect=PermissionError(errno.EACCES, 'metadata unavailable')):
                with repo_transaction(Path(root), timeout=.1):
                    with repo_transaction(Path(root), timeout=.1):
                        pass
            with repo_transaction(Path(root), timeout=.1):
                pass
