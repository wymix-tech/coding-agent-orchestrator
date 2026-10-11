"""Repository transaction locks and durable, atomic JSON replacement."""

from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_registry_guard = threading.Lock()
_registry: dict[str, threading.RLock] = {}
_local = threading.local()


def _lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def repo_transaction(repo: Path, timeout: float = 5.0):
    key = str(repo.resolve())
    with _registry_guard:
        mutex = _registry.setdefault(key, threading.RLock())
    deadline = time.monotonic() + timeout
    # Serialize threads too; only the owning thread may reuse its OS lock.
    if not mutex.acquire(timeout=max(0, deadline - time.monotonic())):
        raise RuntimeError("STATE_LOCK_TIMEOUT")
    depths = getattr(_local, "depths", {})
    _local.depths = depths
    fd = None
    acquired = False
    try:
        if not depths.get(key):
            folder = Path(key) / ".orchestrator"
            folder.mkdir(parents=True, exist_ok=True)
            fd = os.open(folder / "state.lock", os.O_CREAT | os.O_RDWR, 0o600)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            while True:
                try:
                    _lock(fd)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise RuntimeError("STATE_LOCK_TIMEOUT") from exc
                    time.sleep(.025)
        depths[key] = depths.get(key, 0) + 1
        try:
            yield
        finally:
            depths[key] -= 1
            if not depths[key]:
                depths.pop(key)
    finally:
        try:
            if fd is not None:
                try:
                    if acquired:
                        _unlock(fd)
                finally:
                    os.close(fd)
        finally:
            mutex.release()
    # The persistent file is not ownership evidence; the OS releases dead owners.


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Commit only a complete, flushed file on the same filesystem.
        os.replace(name, path)
        if os.name != "nt":
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)
