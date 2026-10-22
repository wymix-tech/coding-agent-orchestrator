"""Runtime CLI: diagnostic facts, bounded queries, explicit cache repair/cleanup."""
from __future__ import annotations

import json
import time
import uuid

from scripts.cao_runtime import ensure_initialized, repo_root
from scripts.cao_work_store import load_index, load_work
from scripts.runtime_logs import redact_object
from scripts.runtime_queries import events, inspect_run, log_tail, page, rebuild, runs
from scripts.runtime_retention import prune
from scripts.runtime_store import identifier, observation, settings
from scripts.state_store import repo_transaction


def _query(repo, work, args):
    wid = work["id"]
    action = args.runtime_command
    if action == "events":
        return page(events(repo, wid), args.limit, args.cursor)
    if action == "runs":
        return page(runs(repo, work), args.limit, args.cursor)
    if action == "show":
        return inspect_run(repo, work, args.run)
    if action == "logs":
        return log_tail(repo, wid, args.run, args.command_id, args.stream, args.tail)
    counts, pending = {}, []
    for run in runs(repo, work):
        status = run["recovery_status"]
        counts[status] = counts.get(status, 0) + 1
        if status not in {"complete", "running"} and len(pending) < 50:
            pending.append({key: run[key] for key in ("run_id", "recovery_status", "admission_status", "next")})
    return {"counts": counts, "needs_attention": pending, "check_only": True,
            "human_summary": "Runtime facts never grant gates. Recovery checks do not execute commands or change Work state."}


def cmd_runtime(args) -> int:
    try:
        repo = repo_root(args.repo)
        ensure_initialized(repo)
        action = args.runtime_command
        with repo_transaction(repo):
            selected = args.work or load_index(repo).get("active_work")
            if not selected:
                raise ValueError("NO_WORK_ITEM: choose --work <id> or --work _unassigned for events")
            wid = identifier(selected)
            work = load_work(repo, wid) if wid != "_unassigned" else None
            if wid == "_unassigned" and action != "events":
                raise ValueError("RUNTIME_UNASSIGNED_EVENTS_ONLY")
            if wid != "_unassigned" and not work:
                raise ValueError("WORK_ITEM_NOT_FOUND")
            if action == "rebuild":
                result = rebuild(repo, work)
            elif action == "prune":
                result = prune(repo, work, args.apply)
        if action not in {"rebuild", "prune"}:
            # Read immutable facts against a Work snapshot without holding the
            # repository writer lock while scanning history or reading log bodies.
            result = page(events(repo, wid), args.limit, args.cursor) if wid == "_unassigned" else _query(repo, work, args)
        if action == "recover":
            identity = "recovery-" + uuid.uuid4().hex
            occurred = time.time()
            try:
                observation(repo, wid, identity, {"event": "run.recovery_checked", "source": "recovery",
                    "occurred_at": occurred, "recorded_at": occurred, "idempotency_key": identity,
                    "payload": {"counts": result["counts"], "state_revision": work.get("state_revision")}})
            except (ValueError, OSError, RuntimeError):
                result["runtime_diagnostic"] = "RUNTIME_RECOVERY_OBSERVATION_FAILED"
        print(json.dumps(redact_object({"ok": True, "work_item_id": wid, **result},
                                       settings(repo).get("redact_values", [])), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


def register_parser(sub) -> None:
    runtime = sub.add_parser("runtime", help="Inspect task execution facts without changing gates")
    commands = runtime.add_subparsers(dest="runtime_command", required=True)
    for name in ("status", "events", "runs", "show", "logs", "recover", "rebuild", "prune"):
        parser = commands.add_parser(name)
        parser.add_argument("--repo", default=".")
        parser.add_argument("--work")
        if name in {"events", "runs"}:
            parser.add_argument("--limit", type=int, default=50)
            parser.add_argument("--cursor")
        if name in {"show", "logs"}:
            parser.add_argument("--run", required=True)
        if name == "logs":
            parser.add_argument("--command", dest="command_id", required=True)
            parser.add_argument("--stream", choices=["stdout", "stderr"], default="stderr")
            parser.add_argument("--tail", type=int, default=100)
        if name == "recover":
            parser.add_argument("--check", required=True, action="store_true")
        if name == "prune":
            mode = parser.add_mutually_exclusive_group()
            mode.add_argument("--dry-run", action="store_true")
            mode.add_argument("--apply", action="store_true")
        parser.set_defaults(fn=cmd_runtime)
