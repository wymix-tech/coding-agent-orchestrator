"""Manual, local runtime benchmark; no model, provider install, or external API."""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def command(code):
    argv = [sys.executable, '-c', code]
    return subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)


def worker(size, mode):
    from scripts.runtime_execution import execute
    from scripts.runtime_store import start_run
    with tempfile.TemporaryDirectory() as folder:
        repo = Path(folder)
        (repo / '.orchestrator').mkdir()
        (repo / '.orchestrator/config.json').write_text(json.dumps({'runtime': {'logs': {'mode': mode}}}))
        start_run(repo, 'w', 'run', 'verification', {})
        code = f"import os\nb=b'x'*4095+b'\\n'\nfor i in range({size * 1024 * 1024 // 4096}):\n os.write(1,b)\n os.write(2,b)\n"
        tracemalloc.start()
        clock = time.perf_counter()
        result = execute(repo, 'w', 'run', 'command-0001', command(code), 120)
        elapsed = time.perf_counter() - clock
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        metadata = result['runtime']['logs']
        assert result['exit_code'] == 0, result
        assert all(stream['observed_bytes'] == size * 1024 * 1024 for stream in metadata.values())
        rss = None
        if os.name != 'nt':
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
        return {'mib_per_stream': size, 'mode': mode, 'seconds': round(elapsed, 4),
                'python_peak_bytes': peak, 'rss_peak_bytes': rss,
                'stored_log_bytes': sum(item['captured_bytes'] for item in metadata.values())}


def pct(values, percentile):
    return sorted(values)[min(len(values)-1, int(len(values) * percentile))]


def observation_bench():
    from scripts.runtime_store import observation
    from scripts.runtime_queries import events, page
    results, queries = [], []
    for concurrency in (1, 4, 16):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            def write(i):
                start = time.perf_counter()
                observation(repo, 'w', f'obs-{i}', {'event': 'tool.finished', 'source': 'host_observation', 'occurred_at': i})
                return (time.perf_counter() - start) * 1000
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                times = list(pool.map(write, range(64)))
            results.append({'concurrency': concurrency, 'count': 64,
                            'p50_ms': round(statistics.median(times), 3), 'p95_ms': round(pct(times, .95), 3)})
    with tempfile.TemporaryDirectory() as folder:
        repo = Path(folder); (repo / '.orchestrator').mkdir()
        (repo / '.orchestrator/config.json').write_text(json.dumps({'runtime': {'max_observations': 12000, 'max_observation_bytes': 33554432}}))
        previous = 0
        for count in (100, 1000, 10000):
            for i in range(previous, count):
                observation(repo, 'w', f'obs-{i:05d}', {'event': 'tool.finished', 'source': 'host_observation', 'occurred_at': i})
            tracemalloc.start(); clock = time.perf_counter()
            result = page(events(repo, 'w'), 50, None)
            elapsed = time.perf_counter() - clock
            _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
            assert len(result['items']) == 50
            queries.append({'records': count, 'milliseconds': round(elapsed * 1000, 3), 'python_peak_bytes': peak})
            previous = count
    return results, queries


def hook_bench():
    with tempfile.TemporaryDirectory() as folder:
        repo = Path(folder)
        cli = [sys.executable, str(ROOT / 'scripts/cao.py')]
        env = dict(os.environ)
        for key in ('CAO_HOST','CLAUDE_CODE_SESSION_ID','CLAUDE_SESSION_ID','CODEX_THREAD_ID','CODEX_SANDBOX','PI_SESSION_ID','PI_CODING_AGENT'):
            env.pop(key, None)
        subprocess.run([*cli, 'init', '--repo', folder, '--host', 'codex'], env=env, capture_output=True, check=True)
        rows = []
        for mode in ('off', 'meaningful'):
            path = repo / '.orchestrator/config.json'
            cfg = json.loads(path.read_text()); cfg['runtime']['host_observation'] = mode; path.write_text(json.dumps(cfg))
            times = []
            for i in range(25):
                payload = {'event': 'pre_tool', 'session_id': 'bench', 'toolCallId': f'{mode}-{i}',
                           'toolName': 'write', 'input': {'path': '_bmad-output/note.md', 'content': 'not captured'}}
                clock = time.perf_counter()
                result = subprocess.run([*cli, 'host-event', '--repo', folder, '--host', 'pi'],
                                        env=env, input=json.dumps(payload), text=True, capture_output=True, timeout=10)
                assert result.returncode == 0, result.stdout
                times.append((time.perf_counter() - clock) * 1000)
            rows.append({'mode': mode, 'count': 25, 'p50_ms': round(statistics.median(times), 3), 'p95_ms': round(pct(times, .95), 3)})
        return rows


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        print(json.dumps(worker(int(sys.argv[2]), sys.argv[3])))
        return
    streams = []
    for size in (1, 10, 100):
        for mode in ('off', 'bounded'):
            result = subprocess.run([sys.executable, __file__, 'worker', str(size), mode], capture_output=True, text=True, check=True, timeout=180)
            streams.append(json.loads(result.stdout))
    observations, queries = observation_bench()
    print(json.dumps({'streams': streams, 'observations': observations, 'queries': queries, 'hooks': hook_bench()}, indent=2))


if __name__ == '__main__':
    main()
