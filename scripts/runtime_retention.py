"""Explicit cleanup of expired, unreferenced diagnostic bodies only."""
from __future__ import annotations

import time
from pathlib import Path

from scripts.runtime_queries import runs, timestamp
from scripts.runtime_store import run_path, safe_path, settings, write_record


def prune(repo: Path, work: dict, apply: bool = False) -> dict:
    wid = work["id"]
    cfg = settings(repo)
    current = {str((work.get("verification") or {}).get("run_id")),
               str((((work.get("sdd") or {}).get("acceptance") or {}).get("run") or {}).get("id"))}
    cutoff = time.time() - cfg["retention"]["closed_work_days"] * 86400
    candidates, protected = [], []
    # Caller holds the repository lock; starts, receipts, and pruning serialize.
    for run in runs(repo, work):
        rid = run["run_id"]
        if (work.get("status") != "completed" or rid in current or run["owner"] == "running"
                or run["recovery_status"] not in {"complete", "projection_missing"}
                or timestamp(work.get("completed_at", work.get("updated_at"))) > cutoff
                or timestamp(run["started_at"]) > cutoff):
            protected.append(rid)
            continue
        folder = run_path(repo, wid, rid, "commands")
        files = []
        if folder.exists():
            for command in folder.iterdir():
                command = run_path(repo, wid, rid, "commands", command.name)
                if not command.is_dir():
                    continue
                for log in command.iterdir():
                    checked = run_path(repo, wid, rid, "commands", command.name, log.name)
                    if checked.suffix == ".log" and checked.is_file():
                        files.append(checked)
        if files:
            candidates.append({"run_id": rid, "files": [str(p.relative_to(safe_path(repo, wid))) for p in files],
                               "bytes": sum(p.stat().st_size for p in files)})
            if apply:
                # Mark before deleting, so interrupted pruning remains explainable.
                write_record(run_path(repo, wid, rid, "pruned.json"), {
                    "schema_version": 1, "run_id": rid, "pruned_at": time.time()}, immutable=False)
                for path in files:
                    path.unlink(missing_ok=True)
    return {"dry_run": not apply, "candidates": candidates, "protected_runs": protected,
            "bytes": sum(item["bytes"] for item in candidates)}
