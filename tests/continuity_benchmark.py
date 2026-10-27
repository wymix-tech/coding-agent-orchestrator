"""Reproducible synthetic continuity scaling probe, not real model acceptance."""
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import tracemalloc
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.continuity_view import view
from scripts.cao_sessions import latest_handoff
from scripts.runtime_queries import events, page
from scripts.native_units import units


def measure(call, repeats=5):
    values = []
    tracemalloc.start()
    for _ in range(repeats):
        start = time.perf_counter()
        result = call()
        values.append(time.perf_counter() - start)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {'p50_seconds': statistics.median(values), 'p95_seconds': max(values),
            'peak_bytes': peak, 'result_bytes': len(json.dumps(result).encode())}


def main():
    rows = []
    for size in (100, 1000):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            handoffs = repo / '.orchestrator/handoffs/sample'
            observations = repo / '.orchestrator/runtime/sample/observations'
            handoffs.mkdir(parents=True)
            observations.mkdir(parents=True)
            for i in range(size):
                (handoffs / f'h{i}.json').write_text(json.dumps({'version': 1, 'id': f'h{i}', 'work_item': 'sample', 'sequence': i, 'created_at': i}))
            for i in range(size * 10):
                (observations / f'e{i}.json').write_text(json.dumps({'schema_version': 1, 'event_id': f'e{i}', 'event': 'fixture', 'occurred_at': i}))
            task_path = repo / 'openspec/changes/synthetic/tasks.md'
            task_path.parent.mkdir(parents=True)
            task_path.write_text('\n'.join(f'- [ ] T{i:04d} implement item {i}' for i in range(size)))
            work = {'id': 'sample', 'resume': {'pending': [f'unit-{i}' for i in range(size)]},
                    'sdd': {'provider': 'openspec', 'plan_scope': {'plans': [{'path': 'openspec/changes/synthetic/tasks.md'}]}}}
            rows.append({'native_units': size, 'handoffs': size, 'observations': size * 10,
                         'bounded_view': measure(lambda: view(work)),
                         'native_unit_parse': measure(lambda: units(repo, work)),
                         'latest_handoff': measure(lambda: latest_handoff(repo, 'sample')),
                         'event_page': measure(lambda: page(events(repo, 'sample'), 50, None))})
    print(json.dumps({'kind': 'synthetic_scaling_probe', 'repeats': 5, 'rows': rows}, indent=2))


if __name__ == '__main__':
    main()
