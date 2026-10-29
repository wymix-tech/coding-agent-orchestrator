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
from scripts.runtime_store import identifier, observation, settings, validate_settings
from scripts.state_store import repo_transaction


def _query(repo, work, args):
    wid = work["id"]
    action = args.runtime_command
    if action == 'effects':
        from scripts.host_effects import effects
        return page(effects(repo, wid, args.pending), getattr(args, 'limit', 50), getattr(args, 'cursor', None))
    if action == "events":
        return page(events(repo, wid), args.limit, args.cursor)
    if action == "runs":
        return page(runs(repo, work), args.limit, args.cursor)
    if action == "abandon":
        from scripts.runtime_execution import owner_status
        from scripts.path_guard import repo_file
        from scripts.cao_runtime import sha256_file
        from scripts.cao_work_store import save_work
        from scripts.runtime_store import queue_record
        from scripts.state_store import repo_transaction
        with repo_transaction(repo):
            current = inspect_run(repo, work, args.run)
            if owner_status(repo, work['id'], args.run) == 'running':
                raise ValueError('RUNTIME_RUN_ACTIVE')
            from scripts.cao_work_store import load_work
            work = load_work(repo, work['id'])
            kind = current['kind']
            if kind == 'verification':
                run = work.get('verification') or {}
            elif kind == 'native_completion':
                rows = (work.get('sdd') or {}).get('completion_executions') or []
                run = rows[-1] if rows else {}
            else:
                raise ValueError('RUNTIME_ABANDON_KIND_UNSUPPORTED')
            if run.get('run_id') != args.run or run.get('status') != 'running':
                raise ValueError('RUNTIME_CURRENT_UNRESOLVED_RUN_REQUIRED')
            path = repo_file(repo, args.evidence)
            if not path.is_file() or not path.read_bytes().strip():
                raise ValueError('RUN_INSPECTION_EVIDENCE_REQUIRED')
            if not args.reason.strip():
                raise ValueError('RUN_INSPECTION_REASON_REQUIRED')
            run.update(status='abandoned' if kind == 'native_completion' else 'blocked', reason_code='RUN_OUTCOME_UNKNOWN_ABANDONED', outcome_unknown=True,
                       resolution={'source': 'agent_declaration', 'path': path.relative_to(repo).as_posix(), 'sha256': sha256_file(path), 'reason': args.reason})
            work['execution_generation'] = work.get('execution_generation', 0) + 1
            queue_record(repo, work, 'sdd.completion.executed' if kind == 'native_completion' else 'verification.finished', run)
            save_work(repo, work)
            return {'ok': True, 'status': 'abandoned', 'outcome_unknown': True, 'note': 'No success or process termination is certified; a later attempt uses a new generation.'}
    if action == "cancel":
        from scripts.runtime_store import run_path, read_record, write_record
        from scripts.runtime_execution import owner_status
        current = inspect_run(repo, work, args.run)
        if current['start']['source'] != 'cao_executor' or owner_status(repo, work['id'], args.run) != 'running':
            raise ValueError('CONTROLLED_LIVE_EXECUTOR_REQUIRED')
        if not current['kind'].startswith('provider_') and current['start'].get('generation') != work.get('execution_generation', 0):
            raise ValueError('RUNTIME_CANCEL_GENERATION_MISMATCH')
        path = run_path(repo, work['id'], args.run, 'cancel.json')
        if not read_record(path):
            write_record(path, {'schema_version': 1, 'work_id': work['id'], 'run_id': args.run, 'reason': args.reason})
        return {'ok': True, 'status': 'cancellation_requested', 'note': 'Wait for the executor receipt; no PID is killed by this query process.'}
    if action == "settle-effect":
        from scripts.effect_resolution import settle
        from scripts.state_store import repo_transaction
        with repo_transaction(repo):
            return settle(repo, work, args)
    if action == "finalize":
        from scripts.runtime_finalize import finalize
        return finalize(repo, work, args.run)
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
    from scripts.host_effects import effects
    host_effects = effects(repo, wid, True)
    return {"counts": counts, "needs_attention": pending, "unresolved_effects": host_effects[:20],
            "unresolved_effect_count": len(host_effects), "check_only": True,
            "human_summary": "Runtime facts never grant gates. Recovery checks do not execute commands or change Work state."}


def cmd_runtime(args) -> int:
    try:
        repo = repo_root(args.repo)
        ensure_initialized(repo)
        action = args.runtime_command
        with repo_transaction(repo):
            if action == 'configure':
                from scripts.cao_runtime import config, CONFIG_REL, save_json
                cfg = config(repo)
                runtime = cfg.setdefault('runtime', {})
                for field in ('mode', 'segment_bytes', 'max_bytes_per_stream'):
                    value = getattr(args, field, None)
                    if value is not None:
                        runtime.setdefault('logs', {})[field] = value
                for field in ('max_bytes_per_work', 'closed_work_days'):
                    value = getattr(args, field, None)
                    if value is not None:
                        runtime.setdefault('retention', {})[field] = value
                # Validate the complete candidate through the existing settings contract.
                validate_settings(cfg)
                save_json(repo / CONFIG_REL, cfg)
                print(json.dumps({'ok': True, 'runtime': runtime}))
                return 0
            selected = args.work or load_index(repo).get("active_work")
            if not selected:
                raise ValueError("NO_WORK_ITEM: choose --work <id> or --work _unassigned for events")
            wid = identifier(selected)
            work = load_work(repo, wid) if wid != "_unassigned" else None
            if wid == "_unassigned":
                if action not in {"events", "runs", "show", "logs", "status", "cancel"}:
                    raise ValueError("RUNTIME_UNASSIGNED_QUERY_OR_CANCEL_ONLY")
                work = {"id": wid}
            if wid != "_unassigned" and not work:
                raise ValueError("WORK_ITEM_NOT_FOUND")
            if action == "rebuild":
                result = rebuild(repo, work)
            elif action == "prune":
                result = prune(repo, work, args.apply)
        if action not in {"rebuild", "prune"}:
            # Read immutable facts against a Work snapshot without holding the
            # repository writer lock while scanning history or reading log bodies.
            result = page(events(repo, wid), args.limit, args.cursor) if wid == "_unassigned" and action == "events" else _query(repo, work, args)
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
        return 0 if result.get("ok", True) else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


def register_parser(sub) -> None:
    runtime = sub.add_parser("runtime", help="Inspect task execution facts without changing gates")
    commands = runtime.add_subparsers(dest="runtime_command", required=True)
    for name in ("status", "effects", "events", "runs", "show", "logs", "recover", "rebuild", "prune", "configure", "finalize", "settle-effect", "cancel", "abandon"):
        parser = commands.add_parser(name)
        parser.add_argument("--repo", default=".")
        parser.add_argument("--work")
        if name == 'effects':
            parser.add_argument('--pending', action='store_true')
        if name == 'abandon':
            parser.add_argument('--evidence', required=True)
            parser.add_argument('--reason', required=True)
        if name == 'cancel':
            parser.add_argument('--reason', required=True)
        if name == 'settle-effect':
            parser.add_argument('--host', required=True)
            parser.add_argument('--session', required=True)
            parser.add_argument('--call', required=True)
            parser.add_argument('--evidence', required=True)
            parser.add_argument('--reason', required=True)
            parser.add_argument('--resolution', choices=['observed-complete', 'observed-stopped'], required=True)
        if name == 'configure':
            parser.add_argument('--max-bytes-per-work', type=int)
            parser.add_argument('--closed-work-days', type=int)
            parser.add_argument('--mode', choices=['off', 'bounded'])
            parser.add_argument('--segment-bytes', type=int)
            parser.add_argument('--max-bytes-per-stream', type=int)
        if name in {"effects", "events", "runs"}:
            parser.add_argument("--limit", type=int, default=50)
            parser.add_argument("--cursor")
        if name in {"show", "logs", "finalize", "cancel", "abandon"}:
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
