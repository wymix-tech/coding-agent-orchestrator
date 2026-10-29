"""Slow preparation releases state locks and rejects concurrently changed inputs."""
import contextlib
import io
import json
import threading
import unittest
from argparse import Namespace
from unittest.mock import patch
from tests import test_native_lifecycle_contract as lifecycle
from scripts.cao_verification import _prepare_verification
from scripts.cao_work_store import load_work, save_work
from scripts.lock_diagnostics import inspect


class PreparationConcurrency(unittest.TestCase):
    def setUp(self):
        self.fixture = lifecycle.NativeLifecycle('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.bind()
        self.repo, self.wid = self.fixture.repo, self.fixture.wid

    def changed_during_check(self, change):
        from scripts.lifecycle_contract import conformance
        ready, release = threading.Event(), threading.Event()
        result, failure = [], []
        def slow(*args, **kwargs):
            answer = conformance(*args, **kwargs)
            ready.set()
            if not release.wait(10):
                raise AssertionError('test did not release preparation')
            return answer
        def prepare():
            try:
                result.append(_prepare_verification(Namespace(repo=str(self.repo), work=self.wid, script=None, command=None)))
            except BaseException as exc:
                failure.append(exc)
        output = io.StringIO()
        with patch('scripts.lifecycle_contract.conformance', side_effect=slow), contextlib.redirect_stdout(output):
            thread = threading.Thread(target=prepare)
            thread.start()
            try:
                self.assertTrue(ready.wait(10))
                self.assertFalse(inspect(self.repo, self.wid)['lock']['held'])
                change()
            finally:
                release.set()
                thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failure, failure)
        self.assertEqual([2], result)
        self.assertIn('VERIFICATION_PREPARATION_CHANGED', output.getvalue())
        self.assertNotEqual('running', (load_work(self.repo, self.wid).get('verification') or {}).get('status'))

    def test_work_update_is_preserved(self):
        def change():
            work = load_work(self.repo, self.wid)
            work['title'] = 'Concurrent update retained'
            save_work(self.repo, work)
        self.changed_during_check(change)
        self.assertEqual('Concurrent update retained', load_work(self.repo, self.wid)['title'])
        self.fixture.check('verify')
        self.fixture.check('done')

    def test_configuration_change_rejects_prepared_run(self):
        def change():
            path = self.repo / '.orchestrator/config.json'
            cfg = json.loads(path.read_text())
            cfg['verification']['timeout_seconds'] = 601
            path.write_text(json.dumps(cfg))
        self.changed_during_check(change)

    def test_product_change_rejects_prepared_run(self):
        self.changed_during_check(lambda: (self.repo / 'feature.py').write_text('answer = 13\n'))
