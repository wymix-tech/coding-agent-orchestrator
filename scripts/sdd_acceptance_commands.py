"""Phased CLI transactions for real commands and current native Skill reports."""
import json
import os
from copy import deepcopy

from scripts.completion_policy import verification_fresh
from scripts.sdd_acceptance_files import MAX_REPORT_BYTES, read_json, source_hashes, output_paths, report_hashes
from scripts.sdd_acceptance_policy import normalize_declaration, normalize_report
from scripts.sdd_acceptance_runtime import configure_record, start_run, finish_run, anchor_matches


def _emit(value: dict, code: int = 0) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return code


def _work(args, core):
    repo = core.repo_root(args.repo)
    core.ensure_initialized(repo)
    work = core.resolve_work(repo, args.work)
    if not work:
        raise ValueError("NO_WORK_ITEM")
    return repo, work


def _request(work: dict, run: dict) -> dict:
    return deepcopy({"run_id": run["id"], "input_sha256": run["input_sha256"],
                     "provider": work["sdd"]["provider"], "inputs": run["inputs"],
                     "declaration": work["sdd"]["acceptance"]["declaration"]})


def _start(args, core, mode):
    repo = core.repo_root(args.repo)
    with core.repo_transaction(repo):
        repo, work = _work(args, core)
        record = (work.get("sdd") or {}).get("acceptance") or {}
        if work.get("status") != "active" or not core.path_assessment_state(repo, work)["ready"]:
            raise ValueError("SDD_ACCEPTANCE_ACTIVE_ASSESSMENT_REQUIRED")
        if (work.get("path_assessment") or {}).get("profile") == "light":
            raise ValueError("SDD_ACCEPTANCE_NOT_APPLICABLE")
        if not record.get("declaration"):
            raise ValueError("SDD_ACCEPTANCE_NOT_CONFIGURED")
        if record["declaration"]["executor"]["mode"] != mode:
            raise ValueError("SDD_ACCEPTANCE_MODE_INVALID")
        cfg = core.config(repo)
        inputs = core.sdd_acceptance_inputs(repo, work, cfg)
        if not anchor_matches(record, inputs):
            raise ValueError("SDD_ACCEPTANCE_REANCHOR_REQUIRED")
        if not verification_fresh(work, core.verification_inputs(repo, work, cfg)):
            raise ValueError("SDD_ACCEPTANCE_TECHNICAL_VERIFICATION_REQUIRED")
        if mode == "skill" and not core.workflow_skill_identity(repo, work["sdd"]["provider"], record["declaration"]["executor"]["entrypoint"]):
            raise ValueError("SDD_ACCEPTANCE_NATIVE_SKILL_REQUIRED")
        core.sdd_acceptance_outputs(repo, work)
        run = start_run(work, inputs)
        core.queue_event(work, {"event": "sdd.acceptance.started", "source": "cao", "work_item_id": work["id"],
                               "run_id": run["id"], "mode": mode, "occurred_at": core.now()})
        request = _request(work, run)
        core.save_work(repo, work)
        return repo, work["id"], request


def _finish(repo, wid, request, report, execution, error, core):
    with core.repo_transaction(repo):
        work = core.load_work(repo, wid)
        if not work:
            raise ValueError("NO_WORK_ITEM")
        record = work["sdd"].get("acceptance") or {}
        current_run = record.get("run") or {}
        if current_run.get("id") != request["run_id"] or current_run.get("status") != "running":
            raise ValueError("SDD_ACCEPTANCE_RUN_CHANGED")
        hashes = {}
        try:
            results = normalize_report(report, record["declaration"], request["run_id"], request["input_sha256"], work["sdd"]["provider"])
            hashes = report_hashes(repo, results, record["declaration"]["outputs"])
        except (ValueError, OSError, KeyError, TypeError) as exc:
            error = error or str(exc)
            # A failed run may still own reports it actually wrote; record only
            # already registered, bounded Work-scoped output paths.
            try:
                hashes = report_hashes(repo, [], record["declaration"]["outputs"])
            except (ValueError, OSError):
                pass
        try:
            inputs = core.sdd_acceptance_inputs(repo, work, core.config(repo))
        except (ValueError, OSError, RuntimeError) as exc:
            inputs = {"input_error": str(exc)}
        # Save only bounded diagnostics. Full structured results are independently validated.
        saved_execution = {**execution, "stdout_tail": execution.get("stdout_tail", "")[-4000:],
                           "stderr_tail": execution.get("stderr_tail", "")[-4000:]}
        run = finish_run(work, request["run_id"], inputs, report, saved_execution, hashes, error)
        core.queue_event(work, {"event": "sdd.acceptance.finished", "source": "cao", "work_item_id": wid,
                               "run_id": run["id"], "status": run["status"], "occurred_at": core.now()})
        core.save_work(repo, work)
        codes = {"passed": 0, "failed": 1, "blocked": 2, "stale": 2, "error": 3}
        completion = core.completion_status(repo, work)
        return _emit({"ok": run["status"] == "passed", "run": run, "completion": completion,
                      "next": core.completion_next_instruction(repo, work, completion) if not completion["ready"] else "Run `cao done`."}, codes[run["status"]])


def dispatch(args, core) -> int:
    try:
        action = args.acceptance_action
        if action == "configure":
            with core.repo_transaction(core.repo_root(args.repo)):
                repo, work = _work(args, core)
                if work["status"] not in {"active", "suspended"}:
                    raise ValueError("SDD_ACCEPTANCE_WORK_NOT_CONFIGURABLE")
                declaration = normalize_declaration(read_json(core.repo_file(repo, args.declaration)))
                source_hashes(repo, declaration, core.provider_output_dirs(work["sdd"]["provider"]))
                previous = work["sdd"].get("acceptance") or {}
                output_paths(repo, declaration, core.provider_output_dirs(work["sdd"]["provider"]), work["id"], previous.get("owned_outputs") or {})
                if previous.get("declaration") and previous["declaration"] != declaration and not args.replace:
                    raise ValueError("SDD_ACCEPTANCE_REPLACE_REQUIRED")
                changed = configure_record(work, declaration)
                if changed:
                    core.save_work(repo, work)
                state = core.sdd_acceptance_state(repo, work)
                return _emit({"ok": True, "status": "configured" if changed else "unchanged",
                              "acceptance_review_required": not state["ready"], "next": state.get("next")})
        if action == "show":
            with core.repo_transaction(core.repo_root(args.repo)):
                repo, work = _work(args, core)
                return _emit({"ok": True, "acceptance": work["sdd"].get("acceptance"), "state": core.sdd_acceptance_state(repo, work)})
        if action in {"run", "begin"}:
            repo, wid, request = _start(args, core, "command" if action == "run" else "skill")
            if action == "begin":
                return _emit({"ok": True, "request": request,
                              "next": "Follow the exact installed Skill instructions, including independent reviewer requirements. Submit this run's complete item evidence; a renderer or self-written findings file does not prove acceptance."})
            executor = request["declaration"]["executor"]
            env = dict(os.environ)
            env["CAO_SDD_ACCEPTANCE_REQUEST"] = json.dumps(request, ensure_ascii=False)
            report, error = None, None
            try:
                execution = core.run_script(executor["entrypoint"], repo, timeout=executor["timeout_seconds"], max_bytes=MAX_REPORT_BYTES + 1, env=env)
                execution["source"] = "command_execution"
                if execution.get("stdout_bytes", len(execution["stdout_tail"].encode())) > MAX_REPORT_BYTES:
                    raise ValueError("SDD_ACCEPTANCE_REPORT_TOO_LARGE")
                report = json.loads(execution["stdout_tail"])
            except (ValueError, OSError, RuntimeError) as exc:
                error = str(exc)
                if "execution" not in locals():
                    execution = {"exit_code": 125, "source": "command_execution", "stderr_tail": str(exc)}
            return _finish(repo, wid, request, report, execution, error, core)
        if action == "submit":
            with core.repo_transaction(core.repo_root(args.repo)):
                repo, work = _work(args, core)
                record = work["sdd"].get("acceptance") or {}
                if (record.get("declaration") or {}).get("executor", {}).get("mode") != "skill":
                    raise ValueError("SDD_ACCEPTANCE_MODE_INVALID")
                run = record.get("run") or {}
                if run.get("id") != args.run or run.get("status") != "running":
                    raise ValueError("SDD_ACCEPTANCE_RUN_CHANGED")
                request = _request(work, run)
                error, report = None, None
                try:
                    report = read_json(core.repo_file(repo, args.report))
                except (ValueError, OSError) as exc:
                    error = str(exc)
                return _finish(repo, work["id"], request, report, {"exit_code": args.exit_code, "source": "agent_skill_submission"}, error, core)
        raise ValueError("SDD_ACCEPTANCE_ACTION_INVALID")
    except (ValueError, OSError, RuntimeError) as exc:
        return _emit({"ok": False, "error": str(exc), "next": "Inspect `cao sdd acceptance show` and follow its current scope/verification guidance."}, 2)
