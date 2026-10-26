"""Phased CLI transactions for real commands and current native Skill reports."""


import json
import os
from copy import deepcopy

from scripts.cao_completion import (
    completion_next_instruction,
    completion_status,
    sdd_acceptance_inputs,
    sdd_acceptance_outputs,
    sdd_acceptance_state,
    verification_inputs,
)
from scripts.cao_path import path_assessment_state
from scripts.cao_provider_workflows import provider_output_dirs, workflow_skill_identity
from scripts.cao_runtime import config, ensure_initialized, now, repo_root
from scripts.cao_work_store import load_work, queue_event, resolve_work, save_work
from scripts.completion_policy import verification_fresh
from scripts.path_guard import repo_file
from scripts.sdd_acceptance_files import (
    MAX_REPORT_BYTES,
    output_paths,
    read_json,
    report_hashes,
    source_hashes,
)
from scripts.sdd_acceptance_policy import normalize_declaration, normalize_report
from scripts.sdd_acceptance_runtime import anchor_matches, configure_record, finish_run, start_run
from scripts.state_store import repo_transaction
from scripts.verification_runner import run_script
from scripts.runtime_execution import execute, finish_execution, owner
from scripts.runtime_store import start_run as start_runtime_run, project_result
from scripts.runtime_logs import redact


def _emit(value: dict, code: int = 0) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return code


def _work(args):
    repo = repo_root(args.repo)
    ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        raise ValueError("NO_WORK_ITEM")
    return repo, work


def _request(work: dict, run: dict) -> dict:
    return deepcopy({"run_id": run["id"], "input_sha256": run["input_sha256"],
                     "provider": work["sdd"]["provider"], "inputs": run["inputs"],
                     "declaration": work["sdd"]["acceptance"]["declaration"]})


def _start(args, mode):
    repo = repo_root(args.repo)
    with repo_transaction(repo):
        repo, work = _work(args)
        record = (work.get("sdd") or {}).get("acceptance") or {}
        if work.get("status") != "active" or not path_assessment_state(repo, work)["ready"]:
            raise ValueError("SDD_ACCEPTANCE_ACTIVE_ASSESSMENT_REQUIRED")
        if (work.get("path_assessment") or {}).get("profile") == "light" and not work.get("project_binding"):
            raise ValueError("SDD_ACCEPTANCE_NOT_APPLICABLE")
        if not record.get("declaration"):
            raise ValueError("SDD_ACCEPTANCE_NOT_CONFIGURED")
        if record["declaration"]["executor"]["mode"] != mode:
            raise ValueError("SDD_ACCEPTANCE_MODE_INVALID")
        if work.get("project_binding"):
            from scripts.acceptance_trace import coverage
            trace = coverage(repo, work)
            if trace["missing"] or trace["unknown"] or not trace["required"]:
                raise ValueError("TASK_ACCEPTANCE_COVERAGE_INCOMPLETE: run cao acceptance criteria and map every source item using covers")
        cfg = config(repo)
        inputs = sdd_acceptance_inputs(repo, work, cfg)
        if not anchor_matches(record, inputs):
            raise ValueError("SDD_ACCEPTANCE_REANCHOR_REQUIRED")
        if not verification_fresh(work, verification_inputs(repo, work, cfg)):
            raise ValueError("SDD_ACCEPTANCE_TECHNICAL_VERIFICATION_REQUIRED")
        if mode == "skill" and not workflow_skill_identity(repo, work["sdd"]["provider"], record["declaration"]["executor"]["entrypoint"]):
            raise ValueError("SDD_ACCEPTANCE_NATIVE_SKILL_REQUIRED")
        sdd_acceptance_outputs(repo, work)
        run = start_run(work, inputs)
        queue_event(work, {"event": "sdd.acceptance.started", "source": "cao", "work_item_id": work["id"],
                               "run_id": run["id"], "mode": mode, "occurred_at": now()})
        request = _request(work, run)
        start_runtime_run(repo, work["id"], run["id"], "sdd_acceptance", inputs,
                          source="cao_executor" if mode == "command" else "agent_declaration")
        save_work(repo, work)
        return repo, work["id"], request


def _finish(repo, wid, request, report, execution, error):
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if not work:
            raise ValueError("NO_WORK_ITEM")
        record = work["sdd"].get("acceptance") or {}
        current_run = record.get("run") or {}
        if current_run.get("id") != request["run_id"] or current_run.get("status") != "running":
            raise ValueError("SDD_ACCEPTANCE_RUN_CHANGED")
        discovery = execution.get("test_discovery") or {}
        if execution.get("exit_code") == 0 and discovery.get("observed") and discovery.get("maximum_count") == 0:
            error = error or "SDD_ACCEPTANCE_ZERO_TESTS_DISCOVERED"
        hashes, diagnostic = {}, None
        try:
            results = normalize_report(report, record["declaration"], request["run_id"], request["input_sha256"], work["sdd"]["provider"])
            hashes = report_hashes(repo, results, record["declaration"]["outputs"])
        except (ValueError, OSError, KeyError, TypeError) as exc:
            error = error or str(exc)
            diagnostic = getattr(exc, "diagnostic", None)
            # A failed run may still own reports it actually wrote; record only
            # already registered, bounded Work-scoped output paths.
            try:
                hashes = report_hashes(repo, [], record["declaration"]["outputs"])
            except (ValueError, OSError):
                pass
        try:
            inputs = sdd_acceptance_inputs(repo, work, config(repo))
        except (ValueError, OSError, RuntimeError) as exc:
            inputs = {"input_error": str(exc)}
        # Save only bounded diagnostics. Full structured results are independently validated.
        tails = execution.get("diagnostic_tails") or {
            "stdout_tail": redact(execution.get("stdout_tail", "")),
            "stderr_tail": redact(execution.get("stderr_tail", ""))}
        saved_execution = {**{key: value for key, value in execution.items() if key != "diagnostic_tails"},
                           **{key: value[-4000:] for key, value in tails.items()}}
        run = finish_run(work, request["run_id"], inputs, report, saved_execution, hashes, error)
        if diagnostic:
            run["diagnostic"] = diagnostic
        queue_event(work, {"event": "sdd.acceptance.finished", "source": "cao", "work_item_id": wid,
                               "run_id": run["id"], "status": run["status"],
                               "evidence_hashes": run.get("evidence_hashes"), "output_hashes": run.get("output_hashes"),
                               "occurred_at": now()})
        save_work(repo, work)
        projection = project_result(repo, wid, run["id"], run)
        codes = {"passed": 0, "failed": 1, "blocked": 2, "stale": 2, "error": 3}
        completion = completion_status(repo, work)
        current_error = run.get('reason') or error or (None if run['status'] == 'passed' else 'SDD_ACCEPTANCE_' + run['status'].upper())
        recovery = ('Inspect the current run error/diagnostic; correct its executor or per-criterion mapping, then rerun acceptance. '
                    'Do not repeat native code review unless its inputs actually changed.')
        return _emit({"ok": run["status"] == "passed", "error": current_error,
                      "diagnostic": run.get('diagnostic'), "run": run, "completion": completion,
                      "runtime_diagnostic": None if projection["ok"] else projection,
                      "next": recovery if current_error else completion_next_instruction(repo, work, completion) if not completion["ready"] else "Run `cao done`."}, codes[run["status"]])


def dispatch(args) -> int:
    try:
        action = args.acceptance_action
        if action == "protocol":
            from scripts.sdd_acceptance_protocol import protocol
            return _emit({"ok": True, **protocol()})
        if action == "configure":
            with repo_transaction(repo_root(args.repo)):
                repo, work = _work(args)
                if work["status"] not in {"active", "suspended"}:
                    raise ValueError("SDD_ACCEPTANCE_WORK_NOT_CONFIGURABLE")
                if getattr(args, 'declaration', None):
                    declaration = normalize_declaration(read_json(repo_file(repo, args.declaration)))
                else:
                    from scripts.acceptance_trace import criteria
                    from scripts.path_guard import provider_artifact
                    binding = work['sdd'].get('spec_binding') or {}
                    spec = (getattr(args, 'spec', None) or (binding.get('spec') or {}).get('path')
                            or (work['sdd'].get('acceptance') or {}).get('declaration', {}).get('spec')
                            or (work['sdd'].get('route_decision') or {}).get('primary_artifact'))
                    if not spec:
                        raise ValueError('TASK_ACCEPTANCE_NATIVE_SOURCE_REQUIRED: bind the native SPEC/Intent or pass --spec')
                    derived = criteria(repo, work)
                    if not derived:
                        raise ValueError('TASK_ACCEPTANCE_SOURCE_REQUIRED')
                    mode = 'command' if getattr(args, 'executor_command', None) else 'skill'
                    entries = []
                    dirs = provider_output_dirs(work['sdd']['provider'], repo)
                    for item in derived:
                        try:
                            provider_artifact(repo, item['source'], dirs)
                            source = item['source']
                        except ValueError:
                            source = spec
                        entries.append({**item, 'source': source, 'covers': [item['id']]})
                    declaration = normalize_declaration({'spec': spec, 'criteria': entries,
                        'evidence_policy': 'criterion-checks-v1',
                        'executor': {'mode': mode, 'entrypoint': getattr(args, 'executor_command', None) or args.executor_skill},
                        'outputs': getattr(args, 'output', None) or []})

                previous_declaration = (work['sdd'].get('acceptance') or {}).get('declaration')
                if declaration != previous_declaration:
                    declaration = normalize_declaration({**declaration, 'evidence_policy': 'criterion-checks-v1'})
                route = work['sdd'].get('route_decision') or {}
                if work['sdd']['provider'] == 'bmad' and route.get('build_route') == 'dispatch' and declaration['spec'] == route.get('primary_artifact'):
                    declaration = normalize_declaration({**declaration, 'lifecycle_metadata': 'bmad-dispatch-status-v1'})
                elif declaration.get('lifecycle_metadata'):
                    raise ValueError('SDD_ACCEPTANCE_METADATA_POLICY_NOT_APPLICABLE')
                source_hashes(repo, declaration, provider_output_dirs(work["sdd"]["provider"], repo))
                previous = work["sdd"].get("acceptance") or {}
                output_paths(repo, declaration, provider_output_dirs(work["sdd"]["provider"], repo), work["id"], previous.get("owned_outputs") or {})
                if previous.get("declaration") and previous["declaration"] != declaration and not args.replace:
                    raise ValueError("SDD_ACCEPTANCE_REPLACE_REQUIRED")
                changed = configure_record(work, declaration)
                declaration_file = getattr(args, 'declaration', None)
                if declaration_file:
                    declaration_file = repo_file(repo, declaration_file).relative_to(repo).as_posix()
                    record = work['sdd']['acceptance']
                    if record.get('declaration_file') != declaration_file:
                        record['declaration_file'] = declaration_file
                        changed = True
                if changed:
                    save_work(repo, work)
                state = sdd_acceptance_state(repo, work)
                from scripts.acceptance_trace import coverage
                return _emit({"ok": True, "status": "configured" if changed else "unchanged",
                              "acceptance_review_required": not state["ready"], "next": state.get("next"),
                              "coverage": coverage(repo, work)})
        if action == "show":
            with repo_transaction(repo_root(args.repo)):
                repo, work = _work(args)
                return _emit({"ok": True, "acceptance": work["sdd"].get("acceptance"), "state": sdd_acceptance_state(repo, work)})
        if action == "reuse":
            # A real configured technical command may also produce criterion results.
            # Its exact command must match the accepted task executor, and its original
            # output, freshness and result identity remain recorded.
            with repo_transaction(repo_root(args.repo)):
                repo, work = _work(args)
                verification = work.get("verification") or {}
                if not verification_fresh(work, verification_inputs(repo, work, config(repo))):
                    raise ValueError("SDD_ACCEPTANCE_TECHNICAL_VERIFICATION_REQUIRED")
                declaration = (work["sdd"].get("acceptance") or {}).get("declaration") or {}
                matches = [item for item in verification.get("results", []) if item.get("name") == args.script]
                executor = declaration.get("executor") or {}
                if len(matches) != 1 or matches[0].get("command") != executor.get("entrypoint") or matches[0].get("status") != "passed":
                    raise ValueError("ACCEPTANCE_SHARED_RUN_MISMATCH")
                original = matches[0]
                raw = json.loads(original.get("stdout_tail", ""))
                if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
                    raise ValueError("ACCEPTANCE_SHARED_RUN_ITEMS_REQUIRED")
                if original.get("stdout_bytes", len(original.get("stdout_tail", "").encode())) > MAX_REPORT_BYTES:
                    raise ValueError("SDD_ACCEPTANCE_REPORT_TOO_LARGE")
                repo, wid, request = _start(args, "command")
                for item in raw['items']:
                    if isinstance(item.get('check'), dict):
                        item['check']['source_run_id'] = item['check'].get('run_id')
                        item['check']['run_id'] = request['run_id']
                report = {"run_id": request["run_id"], "input_sha256": request["input_sha256"],
                          "provider": request["provider"], "items": raw["items"]}
                return _finish(repo, wid, request, report,
                               {"exit_code": original["exit_code"], "source": "verification_execution",
                                "verification_run_id": verification["run_id"], "script": args.script}, None)
        if action in {"run", "begin"}:
            repo, wid, request = _start(args, "command" if action == "run" else "skill")
            if action == "begin":
                return _emit({"ok": True, "request": request,
                              "next": "Follow the exact installed Skill instructions, including independent reviewer requirements. Submit this run's complete item evidence; a renderer or self-written findings file does not prove acceptance."})
            with owner(repo, wid, request["run_id"]):
                executor = request["declaration"]["executor"]
                env = dict(os.environ)
                env["CAO_SDD_ACCEPTANCE_REQUEST"] = json.dumps(request, ensure_ascii=False)
                report, error = None, None
                try:
                    execution = execute(repo, wid, request["run_id"], "command-0001",
                                        executor["entrypoint"], executor["timeout_seconds"],
                                        max_bytes=MAX_REPORT_BYTES + 1, env=env, runner=run_script)
                    finish_execution(repo, wid, request["run_id"], [execution])
                    execution["source"] = "command_execution"
                    if execution.get("stdout_bytes", len(execution["stdout_tail"].encode())) > MAX_REPORT_BYTES:
                        raise ValueError("SDD_ACCEPTANCE_REPORT_TOO_LARGE")
                    report = json.loads(execution["stdout_tail"])
                except (ValueError, OSError, RuntimeError) as exc:
                    error = str(exc)
                    if "execution" not in locals():
                        execution = {"exit_code": 125, "source": "command_execution", "stderr_tail": str(exc)}
                return _finish(repo, wid, request, report, execution, error)
        if action == "submit":
            with repo_transaction(repo_root(args.repo)):
                repo, work = _work(args)
                record = work["sdd"].get("acceptance") or {}
                if (record.get("declaration") or {}).get("executor", {}).get("mode") != "skill":
                    raise ValueError("SDD_ACCEPTANCE_MODE_INVALID")
                run = record.get("run") or {}
                if run.get("id") != args.run or run.get("status") != "running":
                    raise ValueError("SDD_ACCEPTANCE_RUN_CHANGED")
                request = _request(work, run)
                error, report = None, None
                try:
                    report = read_json(repo_file(repo, args.report))
                except (ValueError, OSError) as exc:
                    error = str(exc)
                return _finish(repo, work["id"], request, report, {"exit_code": args.exit_code, "source": "agent_skill_submission"}, error)
        raise ValueError("SDD_ACCEPTANCE_ACTION_INVALID")
    except (ValueError, OSError, RuntimeError) as exc:
        return _emit({"ok": False, "error": str(exc),
                      **({"diagnostic": exc.diagnostic} if hasattr(exc, "diagnostic") else {}),
                      "next": "Read `cao sdd acceptance protocol` for the JSON request/report contract; inspect `cao sdd acceptance show` for current scope."}, 2)
