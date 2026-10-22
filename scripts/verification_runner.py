"""Run trusted verification commands with bounded tails and owned-tree cleanup."""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from scripts.completion_policy import normalize_timeout


def run_script(command: str, repo: Path, timeout: float = 300.0, max_bytes: int = 4000,
               env: dict[str, str] | None = None, log_sink=None) -> dict:
    timeout = normalize_timeout(timeout)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("VERIFICATION_LIMIT_INVALID")
    if os.name == "nt":
        launch = [sys.executable, str(Path(__file__).with_name("windows_supervisor.py")), command]
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP, "shell": False}
    else:
        launch = command
        kwargs = {"start_new_session": True, "shell": True}
    try:
        child = subprocess.Popen(launch, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **kwargs)
    except OSError as exc:
        return {"exit_code": 127, "stdout_tail": "", "stderr_tail": str(exc)[-max_bytes:],
                "timed_out": False, "process_status": "launch_failed"}
    tails = [bytearray(), bytearray()]
    byte_counts = [0, 0]
    errors = []

    def kill_group():
        if os.name == "nt":
            # The supervisor owns a kill-on-close Job before launching the command.
            if child.poll() is None:
                child.kill()
        else:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def read(stream, tail, index):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                byte_counts[index] += len(chunk)
                if log_sink is not None:
                    try:
                        log_sink.write(index, chunk)
                    except Exception:
                        # Optional diagnostics must not stop draining the pipe.
                        pass
                tail.extend(chunk)
                if len(tail) > max_bytes:
                    del tail[:-max_bytes]
        except Exception as exc:
            errors.append(str(exc))
            kill_group()
        finally:
            try:
                stream.close()
            except Exception as exc:
                errors.append(str(exc))

    readers = [threading.Thread(target=read, args=(stream, tail, index), daemon=True)
               for index, (stream, tail) in enumerate(zip((child.stdout, child.stderr), tails))]
    started_readers = []
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        for thread in readers:
            thread.start()
            started_readers.append(thread)
        child.wait(timeout=max(0, deadline - time.monotonic()))
        for thread in readers:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        timed_out = any(thread.is_alive() for thread in readers)
    except subprocess.TimeoutExpired:
        timed_out = True
    except Exception as exc:
        errors.append("VERIFICATION_RUN_FAILED: " + str(exc))
    finally:
        # Even a successful parent can leave descendants retaining stdout/stderr.
        try:
            kill_group()
            child.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append("VERIFICATION_PROCESS_CLEANUP_FAILED: " + str(exc))
            try:
                child.kill()
                child.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as retry:
                errors.append("VERIFICATION_FINAL_WAIT_FAILED: " + str(retry))
        for thread in started_readers:
            try:
                thread.join(timeout=5)
            except RuntimeError as exc:
                errors.append("VERIFICATION_READER_CLEANUP_FAILED: " + str(exc))
        for stream, thread in zip((child.stdout, child.stderr), readers):
            if thread not in started_readers or getattr(thread, "ident", None) is None:
                stream.close()
    if any(thread.is_alive() for thread in started_readers):
        errors.append("VERIFICATION_PIPE_CLEANUP_FAILED")
    stdout, stderr = (tail.decode("utf-8", errors="replace") for tail in tails)
    if errors:
        stderr = (stderr + "\nVERIFICATION_ERROR: " + "; ".join(errors))[-max_bytes:]
    if timed_out:
        stderr = (stderr + "\nVERIFICATION_TIMEOUT")[-max_bytes:]
    return {"exit_code": 125 if errors else 124 if timed_out else child.returncode,
            "process_status": "timed_out" if timed_out else "exited",
            "stdout_tail": stdout, "stderr_tail": stderr, "timed_out": timed_out,
            "stdout_bytes": byte_counts[0], "stderr_bytes": byte_counts[1]}
