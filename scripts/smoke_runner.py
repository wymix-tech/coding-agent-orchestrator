"""An isolated, owned-process HTTP smoke check; never reuse an existing service."""
import argparse
import http.client
import json
import os
import signal
import shlex
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def run_smoke(repo, command, path='/', method='GET', body=None, expected_status=200, timeout=10):
    if '{port}' not in command or not path.startswith('/') or path.startswith('//'):
        raise ValueError('SMOKE_DYNAMIC_PORT_AND_LOCAL_PATH_REQUIRED')
    timeout = float(timeout)
    if not 0 < timeout <= 300: raise ValueError('SMOKE_TIMEOUT_INVALID')
    tokens = shlex.split(command.replace('{port}', '1'), posix=os.name != 'nt')
    if any(token in {'&', '&&', '|', '||', ';', '>', '>>'} for token in tokens):
        raise ValueError('SMOKE_FOREGROUND_COMMAND_REQUIRED')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(shlex.split(command.replace('{port}', str(port)), posix=os.name != 'nt'), cwd=repo,
                                   stdout=output, stderr=output, start_new_session=os.name != 'nt')
        result = {'ok': False, 'port': port, 'pid': process.pid, 'status': None}
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    result['error'] = 'SMOKE_SERVER_EXITED'; break
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=min(1, max(.05, deadline-time.monotonic())))
                try:
                    connection.request(method, path, body=body, headers={'Content-Type': 'application/json'} if body else {})
                    response = connection.getresponse(); response.read(1024 * 1024)
                    result['status'] = response.status
                    if process.poll() is not None:
                        result['error'] = 'SMOKE_SERVER_EXITED'; break
                    result['ok'] = response.status == expected_status
                    if not result['ok']: result['error'] = 'SMOKE_HTTP_STATUS_MISMATCH'
                    break
                except (OSError, http.client.HTTPException):
                    time.sleep(.05)
                finally:
                    connection.close()
            else: result['error'] = 'SMOKE_STARTUP_TIMEOUT'
        finally:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            else:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            process.wait(timeout=5)
            cleanup_deadline = time.monotonic() + 3
            while time.monotonic() < cleanup_deadline:
                probe = http.client.HTTPConnection('127.0.0.1', port, timeout=.2)
                try:
                    probe.request('GET', '/')
                    probe.getresponse()
                except (OSError, http.client.HTTPException):
                    break
                finally:
                    probe.close()
                time.sleep(.02)
            else:
                result.update(ok=False, error='SMOKE_PROCESS_CLEANUP_FAILED')
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server-command', required=True)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--path', default='/')
    parser.add_argument('--method', choices=['GET', 'POST'], default='GET')
    parser.add_argument('--body');parser.add_argument('--expect-status', type=int, default=200)
    parser.add_argument('--timeout', type=float, default=10)
    args=parser.parse_args()
    try: result=run_smoke(Path(args.repo),args.server_command,args.path,args.method,args.body,args.expect_status,args.timeout)
    except (OSError,ValueError,subprocess.SubprocessError) as exc: result={'ok':False,'error':str(exc)}
    print(json.dumps(result));return 0 if result['ok'] else 2

if __name__=='__main__':raise SystemExit(main())
