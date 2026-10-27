"""Controlled execution facts and owner locks; never decides gate acceptance."""
from __future__ import annotations

import errno
import os
import socket
import time
from contextlib import contextmanager
from pathlib import Path

from scripts.runtime_logs import LogSink, redact, redact_object
from scripts.runtime_store import (
    read_record, record_receipt, run_path, safe_path, settings, write_record,
)
from scripts.state_store import _lock, _unlock, repo_transaction


@contextmanager
def owner(repo: Path, wid: str, run_id: str):
    path = run_path(repo, wid, run_id, "owner.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        _lock(fd)
        try:
            write_record(run_path(repo, wid, run_id, "owner.json"),
                         {"schema_version": 1, "host": socket.gethostname(),
                          "pid": os.getpid(), "started_at": time.time()}, immutable=False)
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def owner_status(repo: Path, wid: str, run_id: str) -> str:
    try:
        info = read_record(run_path(repo, wid, run_id, "owner.json"))
    except OSError:
        return "unknown"
    if not info or info.get("host") != socket.gethostname():
        return "unknown"
    path = run_path(repo, wid, run_id, "owner.lock")
    try:
        fd = os.open(path, os.O_RDWR)
        try:
            try:
                _lock(fd)
            except OSError as exc:
                return "running" if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} else "unknown"
            _unlock(fd)
            return "released"
        finally:
            os.close(fd)
    except OSError:
        return "unknown"


def _reserve(repo: Path, wid: str, run_id: str, command_id: str, cfg: dict) -> int:
    if cfg["logs"]["mode"] == "off":
        return 0
    root = safe_path(repo, wid)
    used = reserved = 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(base) / name
            if path.is_symlink():
                raise ValueError("RUNTIME_SYMLINK")
        for name in names:
            path = Path(base) / name
            used += path.stat().st_size
            if name == "reservation.json":
                reserved += (read_record(path) or {}).get("bytes", 0)
    # A little headroom for receipts so optional logs do not consume every byte.
    remaining = max(0, cfg["retention"]["max_bytes_per_work"] - used - reserved - 65536)
    budget = min(remaining, 2 * cfg["logs"]["max_bytes_per_stream"]) if cfg["logs"]["mode"] == "bounded" else 0
    reservation = run_path(repo, wid, run_id, "commands", command_id, "reservation.json")
    write_record(reservation, {"schema_version": 1, "bytes": budget})
    return budget


def execute(repo: Path, wid: str, run_id: str, command_id: str,
            command: str, timeout: float, *, max_bytes: int = 4000, env=None,
            runner=None) -> dict:
    from scripts.verification_runner import run_script
    runner = runner or run_script
    cfg = settings(repo)
    secrets = cfg.get("redact_values", [])
    if not isinstance(secrets, list) or any(not isinstance(value, str) for value in secrets):
        raise ValueError("RUNTIME_CONFIG_INVALID")
    started = time.time()
    with repo_transaction(repo):
        folder = run_path(repo, wid, run_id, "commands", command_id)
        write_record(folder / "start.json", {
            "schema_version": 1, "work_item_id": wid, "run_id": run_id,
            "command_id": command_id, "command": redact(command, secrets),
            "cwd": ".", "started_at": started, "timeout_seconds": timeout,
            "source": "cao_executor",
        })
        capture_error = None
        try:
            budget = _reserve(repo, wid, run_id, command_id, cfg)
        except OSError as exc:
            budget, capture_error = 0, type(exc).__name__
    sink = LogSink(folder, cfg["logs"], budget, secrets)
    clock = time.monotonic()
    try:
        try:
            result = runner(command, repo, timeout=timeout, max_bytes=max_bytes, env=env, log_sink=sink)
        except Exception as exc:
            result = {"exit_code": 127, "stdout_tail": "", "stderr_tail": str(exc),
                      "timed_out": False, "process_status": "launch_failed"}
        logs = sink.close()
        if capture_error:
            for stream in logs.values():
                stream.update(capture_error=capture_error, truncated=True)
        result["diagnostic_tails"] = sink.tails()
        # Mock/alternative runners may not implement streaming diagnostics.
        if not any(stream["observed_bytes"] for stream in logs.values()):
            result["diagnostic_tails"] = {key: redact(result.get(key, ""), secrets)
                                         for key in ("stdout_tail", "stderr_tail")}
        receipt = redact_object({
            "source": "cao_executor", "command_id": command_id,
            "started_at": started, "finished_at": time.time(),
            "duration_seconds": time.monotonic() - clock, "exit_code": result["exit_code"],
            "process_status": result.get("process_status", "timed_out" if result.get("timed_out") else "exited"),
            "logs": logs,
        }, secrets)
        record_receipt(repo, wid, run_id, receipt, command_id)
        result["runtime"] = {"run_id": run_id, "command_id": command_id,
                             "logs": logs, "duration_seconds": receipt["duration_seconds"]}
        return result
    finally:
        sink.close()
        try:
            with repo_transaction(repo):
                run_path(repo, wid, run_id, "commands", command_id, "reservation.json").unlink(missing_ok=True)
        except OSError:
            pass  # Stale reservations conservatively reduce future log budgets.


def finish_execution(repo: Path, wid: str, run_id: str, results: list[dict], admission_sha256=None) -> None:
    record_receipt(repo, wid, run_id, {
        "source": "cao_executor", "finished_at": time.time(), "admission_sha256": admission_sha256,
        "commands": [{"command_id": result.get("runtime", {}).get("command_id"),
                      "exit_code": result.get("exit_code"),
                      "timed_out": result.get("timed_out", False),
                      "process_status": result.get("process_status", "exited")} for result in results],
    })

