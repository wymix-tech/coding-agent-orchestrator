"""Technical verification configuration, execution, and result commits."""
from __future__ import annotations

import argparse
import json
import subprocess
import uuid
from typing import Any

from scripts.cao_completion import (
    completion_next_instruction,
    completion_status,
    verification_inputs,
)
from scripts.cao_defaults import CONFIG_REL, DEFAULT_CONFIG
from scripts.cao_native_sync import ensure_verification_review, native_route_state
from scripts.cao_plans import observe_plan_scope
from scripts.cao_provider_workflows import provider_native_skills
from scripts.cao_runtime import (
    action,
    config,
    deep_merge,
    ensure_initialized,
    locked_command,
    now,
    repo_root,
    save_json,
    verification_scripts,
)
from scripts.cao_snapshot import (
    anchor_verification,
    code_snapshot,
    verification_snapshot,
    verification_timeout,
)
from scripts.cao_work_store import _read_work, load_index, load_work, queue_event, save_work
from scripts.completion_policy import normalize_scripts, required_verification_status
from scripts.native_obligations import planning_state, review_state as native_review_state
from scripts.smoke_runner import run_smoke
from scripts.state_store import repo_transaction
from scripts.verification_runner import run_script
from scripts.runtime_store import start_run as start_runtime_run, queue_record, project_result, record_denial
from scripts.runtime_execution import execute, finish_execution, owner
from scripts.verification_setup import declared_scripts, prepare_config, setup_state
from scripts.snapshot_scope import paths, validate_scope


def cmd_verification_smoke(args: argparse.Namespace) -> int:
    try:
        result = run_smoke(repo_root(args.repo), args.server_command, args.path, args.method,
                           args.body, args.expect_status, args.timeout)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result)); return 0 if result["ok"] else 2


def cmd_sdd_acceptance(args: argparse.Namespace) -> int:
    from scripts.sdd_acceptance_commands import dispatch
    return dispatch(args)


@locked_command
def cmd_verification_configure(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    path = repo / CONFIG_REL
    from scripts.project_verification import verification_binding
    from scripts.project_context import load_context
    binding = verification_binding(repo, getattr(args, "scope", None)) if load_context(repo) else None
    if getattr(args, "plan_only", False):
        if not binding:
            raise ValueError("PROJECT_CONTEXT_REQUIRED")
        raw = json.loads(path.read_text(encoding="utf-8"))
        plan = {"version": 1, "scripts": declared_scripts(args.verify_script or [], args.test_script or [], getattr(args, "security_script", None) or []), "context_binding": binding}
        from scripts.project_verification import validate_entries
        validate_entries(repo, plan["scripts"], getattr(args, "scope", None))
        previous = (raw.get("verification") or {}).get("plan")
        if previous and previous != plan and not args.replace:
            raise ValueError("VERIFICATION_CONFIG_REPLACE_REQUIRED")
        if (raw.get("verification") or {}).get("scripts"):
            raise ValueError("VERIFICATION_ACTUAL_ENTRIES_EXIST: preserve actual entries; configure them instead")
        raw.setdefault("verification", {}).update(plan=plan, context_binding=binding)
        save_json(path, raw)
        print(json.dumps({"ok": True, "status": "planned", "plan": plan, "executable": False}))
        return 0
    scripts = []
    raw = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        outputs = getattr(args, "output", None)
        clear_outputs = getattr(args, "clear_outputs", False)
        excludes = getattr(args, "exclude_path", None)
        clear_excludes = getattr(args, "clear_exclude_paths", False)
        declarations = (("verification", "outputs", outputs, clear_outputs),
                        ("snapshot", "exclude_paths", excludes, clear_excludes))
        for section, key, requested, clear in declarations:
            if requested is not None and clear:
                raise ValueError("SNAPSHOT_SCOPE_INVALID: conflicting set/clear options for " + section + "." + key)
        environment = getattr(args, "environment_variable", None)
        maximum_age = getattr(args, "maximum_evidence_age_seconds", None)
        has_metadata = environment is not None or maximum_age is not None
        has_scripts = bool(args.verify_script or args.test_script or getattr(args, "security_script", None))
        if has_scripts:
            scripts = declared_scripts(args.verify_script or [], args.test_script or [], getattr(args, 'security_script', None) or [])
        elif outputs is not None or clear_outputs or excludes is not None or clear_excludes or has_metadata:
            scripts = verification_scripts(raw)
        else:
            raise ValueError("VERIFICATION_SCRIPT_REQUIRED")
        from scripts.project_verification import validate_entries
        validate_entries(repo, scripts, getattr(args, "scope", None))
        candidate, changed = prepare_config(raw, scripts, args.replace, now())
        for section, key, requested, clear in declarations:
            if requested is None and not clear:
                continue
            normalized = paths(requested or [], section + "." + key)
            previous = (raw.get(section) or {}).get(key, [])
            if normalized != previous:
                if not args.replace:
                    raise ValueError("VERIFICATION_CONFIG_REPLACE_REQUIRED")
                candidate = json.loads(json.dumps(candidate))
                candidate.setdefault(section, {})[key] = normalized
                candidate.setdefault("verification", {})["configured_at"] = now()
                changed = True
        if has_metadata:
            candidate = json.loads(json.dumps(candidate))
            metadata = {}
            if environment is not None:
                import re
                if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) for key in environment):
                    raise ValueError('VERIFICATION_ENVIRONMENT_INVALID')
                metadata['environment_variables'] = sorted(set(environment))
            if maximum_age is not None:
                from scripts.completion_policy import normalize_timeout
                metadata['maximum_evidence_age_seconds'] = normalize_timeout(maximum_age)
            if any((raw.get('verification') or {}).get(key) != value for key, value in metadata.items()):
                if (raw.get('verification') or {}).get('scripts') and not args.replace:
                    raise ValueError('VERIFICATION_CONFIG_REPLACE_REQUIRED')
                candidate.setdefault('verification', {}).update(metadata)
                changed = True
        if binding:
            if candidate is raw:
                candidate = json.loads(json.dumps(candidate))
            changed = changed or (raw.get("verification") or {}).get("context_binding") != binding
            candidate.setdefault("verification", {})["context_binding"] = binding
        # Establish known Maven outputs with the first actual baseline, not after
        # test execution has already invalidated review evidence. Explicit scope wins.
        if (not (raw.get('verification') or {}).get('scripts') and has_scripts
                and outputs is None and excludes is None and not clear_outputs and not clear_excludes):
            from scripts.project_context import load_context
            context = load_context(repo) or {}
            build = ((context.get('scopes', {}).get('root') or {}).get('technology') or {}).get('build_tool') or {}
            maven = build.get('status') == 'accepted' and str(build.get('value', '')).casefold() == 'maven'
            if maven:
                candidate.setdefault('snapshot', {})['exclude_paths'] = sorted(set(
                    candidate.get('snapshot', {}).get('exclude_paths', []) + ['target/']))
                changed = True
        effective = json.loads(json.dumps(DEFAULT_CONFIG))
        deep_merge(effective, candidate)
        wid = getattr(args, "work", None) or load_index(repo).get("active_work")
        # An accepted standard cannot be silently reanchored. Only the first
        # valid baseline of a Work started with empty setup is established here.
        work = _read_work(repo, wid) if wid else None
        validate_scope(repo, effective, protected={work["requirement"]["path"]} if work else ())
        initial_baseline = bool(work and work.get("status") == "active" and
            not setup_state(config(repo))["ready"] and
            (work.get("acceptance") or {}).get("config_sha256") == verification_snapshot(config(repo), repo) and
            not work.get("verification") and not work.get("gate_status"))
        review = bool(work and work.get("status") == "active" and
                      not initial_baseline and
                      (work.get("acceptance") or {}).get("config_sha256") != verification_snapshot(effective, repo))
    except ValueError as exc:
        error = "VERIFICATION_CONFIG_INVALID" if isinstance(exc, json.JSONDecodeError) else str(exc).split(":", 1)[0]
        out = {"ok": False, "error": error, "reason": str(exc)}
        if error == "VERIFICATION_CONFIG_REPLACE_REQUIRED":
            out.update(current=setup_state(raw), requested=scripts,
                       next=action("human", "Review the existing and requested verification declarations; use --replace only to explicitly replace them."))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2
    if changed:
        save_json(path, candidate)
    if initial_baseline and setup_state(effective)["ready"]:
        anchor_verification(repo, work, effective)
        queue_event(work, {"event": "verification.baseline.established", "work_item_id": work["id"],
                           "source": "cao", "acceptance": work["acceptance"], "occurred_at": now()})
        save_work(repo, work)
    out = {"ok": True, "status": "configured" if changed else "unchanged", "config": str(path),
           "verification_setup": setup_state(effective), "acceptance_review_required": review,
           "initial_baseline_established": bool(initial_baseline and setup_state(effective)["ready"])}
    if review:
        out["next"] = action("human", f'Review changed acceptance and explicitly run `cao reopen --work {wid} --reanchor --reason "Reviewed acceptance changes"`.')
        if work["sdd"].get("native_route_required") or any(s["name"] == "bmad-build" for s in provider_native_skills(repo, work["sdd"]["provider"])):
            ensure_verification_review(repo, work, effective)
            out["next"] = action("human", f'Present the changed verification scripts and wait for explicit user `批准验证基线` (approve verification baseline) through the host. Then `cao reopen --work {wid} --reanchor --reason "Reviewed acceptance changes"`. Agent reason text is not human approval.')
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def verification_status_for_exit_code(exit_code: int) -> str:
    if exit_code == 0:
        return "passed"
    if exit_code == 1:
        return "failed"
    if exit_code == 2:
        return "blocked"
    return "error"


def verification_reason(stdout: str, stderr: str, status: str) -> str:
    for stream in ((stdout, stderr) if status == "passed" else (stderr, stdout)):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1][-1000:]
    defaults = {
        "passed": "Verification script passed.",
        "failed": "Verification script failed.",
        "blocked": "Verification could not be completed.",
        "error": "Verification script execution failed.",
    }
    return defaults[status]


def overall_verification_status(results: list[dict[str, Any]]) -> str:
    return required_verification_status(results)


@locked_command
def _prepare_verification(args: argparse.Namespace):
    from scripts.cao_tool_policy import verification_baseline_state

    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps(record_denial(repo, work, "verify", {"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}), ensure_ascii=False, indent=2)); return 2

    if work.get("status") != "active":
        recovery = (f'cao reopen --work {wid} --reason "<why>"' if work.get("status") == "completed"
                    else f'cao resume --work {wid} --reactivate')
        print(json.dumps(record_denial(repo, work, "verify", {"ok": False, "error": "VERIFICATION_WORK_NOT_ACTIVE", "status": work.get("status"), "next": recovery}))); return 2

    from scripts.project_verification import project_prerequisite
    project = project_prerequisite(repo, work)
    if project:
        print(json.dumps({"ok": False, "error": project["reason"], "next": project}))
        return 2
    route = native_route_state(repo, work)
    if not route["ready"]:
        print(json.dumps(record_denial(repo, work, "verify", {"ok": False, "error": route["reason"], "next": route["next"]}))); return 2

    plan_state = observe_plan_scope(repo, work)
    if not plan_state["ready"]:
        print(json.dumps(record_denial(repo, work, "verify", {"ok": False, "error": str(plan_state["reason"]).split(":", 1)[0],
                          "verification_plan": plan_state, "next": plan_state["next"]}), ensure_ascii=False, indent=2)); return 2
    review = native_review_state(repo, work, code_snapshot)
    if not review["ready"]:
        print(json.dumps(record_denial(repo, work, "verify", {"ok": False, "error": review["reason"], "native_review": review, "next": review.get("next")}), ensure_ascii=False)); return 2
    planning = planning_state(repo, work)
    if not planning['ready']:
        print(json.dumps(record_denial(repo, work, "verify", {'ok': False, 'error': planning['reason'].split(':', 1)[0], **planning}))); return 2

    cfg = config(repo)
    cfg_scripts = verification_scripts(cfg)
    cli_values = args.script or args.command or []
    cli_override = normalize_scripts([
        {"name": f"script-{i}", "command": str(command).strip(), "required": True}
        for i, command in enumerate(cli_values, 1) if str(command).strip()
    ])
    source = "cli" if cli_override else "config"
    scripts = cli_override or cfg_scripts
    if not scripts:
        print(json.dumps(record_denial(repo, work, "verify", {
            "ok": False,
            "error": "VERIFICATION_SCRIPT_REQUIRED",
            "next": "Configure required verification with `cao verification configure --test-script \"<real test command>\" --replace`, then explicitly re-anchor the Work Item."
        }), ensure_ascii=False, indent=2)); return 2

    if source == "config":
        baseline = verification_baseline_state(repo, work)
        if not baseline["ready"]:
            print(json.dumps(record_denial(repo, work, "verify", {"ok": False, "error": baseline["reason"], "next": baseline["next"]}))); return 2
        required_gates = ((work.get("path_assessment") or {}).get("required_gates")) or []
        if "security_verification" in required_gates and not any(item['required'] and 'security_verification' in item['gates'] for item in scripts):
            print(json.dumps(record_denial(repo, work, "verify", {'ok': False, 'error': 'VERIFICATION_GATE_UNBOUND',
                              'reason': 'security_verification needs a required script declaring that capability.',
                              'next': 'Use verification configure --security-script <actual security checks>; the same test wrapper may also declare --test-script. Review and reanchor changed standards.'}))); return 2
        if "tests" in required_gates and not any(item["required"] and "tests" in item["gates"] for item in scripts):
            print(json.dumps(record_denial(repo, work, "verify", {
                "ok": False, "error": "VERIFICATION_GATE_UNBOUND",
                "reason": "The tests gate needs a required verification script explicitly declaring gates: [tests].",
                "next": "Use `cao verification configure --test-script \"<real test command>\" --replace`, then `cao reopen --reanchor --reason \"<why>\"` and rerun `cao verify`.",
            }), ensure_ascii=False, indent=2)); return 2

    req = repo / work["requirement"]["path"]
    if not req.exists():
        print(json.dumps(record_denial(repo, work, "verify", {"ok": False, "error": "REQUIREMENT_FILE_MISSING"}), ensure_ascii=False, indent=2)); return 2
    inputs = verification_inputs(repo, work, cfg)
    run = {"run_id": "verify-" + uuid.uuid4().hex,
           "sequence": int(work.get("verification_sequence", 0)) + 1,
           "generation": int(work.get("execution_generation", 0)), "status": "running", "at": now(),
           "inputs": inputs, "source": source}
    previous = work.get("verification") or {}
    if previous:
        work.setdefault("verification_history", []).append(previous)
    work["verification_sequence"] = run["sequence"]
    work["verification"] = run
    start_runtime_run(repo, wid, run["run_id"], "verification", inputs,
                      retry_of=previous.get("run_id"), generation=run["generation"], sequence=run["sequence"])
    queue_record(repo, work, "verification.started", {"run_id": run["run_id"],
                 "sequence": run["sequence"], "generation": run["generation"], "inputs": inputs})
    save_work(repo, work)
    return repo, work["id"], scripts, source, inputs, verification_timeout(cfg), run


def commit_verification(repo, wid, inputs, scripts, results, source, started, run=None):
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if work is None:
            raise RuntimeError("WORK_ITEM_NOT_FOUND")
        identity = work.get("verification") or {}
        eligible = (run is not None and work.get("status") == "active"
                    and identity.get("run_id") == run["run_id"] and identity.get("status") == "running"
                    and int(work.get("execution_generation", 0)) == run["generation"])
        if not eligible:
            rejected = {"status": "superseded", "run_status": overall_verification_status(results),
                        "run_id": (run or {}).get("run_id"), "results": results,
                        "reason": "VERIFICATION_RUN_CHANGED", "source": source, "at": started}
            # Completed history remains immutable. Do not change its progress/official result.
            if work.get("status") != "completed":
                work.setdefault("verification_history", []).append(rejected)
                queue_record(repo, work, "verification.finished", rejected)
                save_work(repo, work)
            return rejected
        try:
            current = verification_inputs(repo, work, config(repo))
        except (ValueError, OSError, RuntimeError) as exc:
            current = None
            snapshot_error = str(exc)
        else:
            snapshot_error = None
        changed = current != inputs
        plan_state = observe_plan_scope(repo, work)
        if plan_state.get("scope"):
            work.setdefault("sdd", {})["plan_scope"] = plan_state["scope"]
        status = overall_verification_status(results)
        verification = {"requirement_sha256": work["requirement"]["sha256"],
                        "assessment_revision": (work.get("path_assessment") or {}).get("revision"),
                        "version": 2, "run_id": run["run_id"], "sequence": run["sequence"], "generation": run["generation"], "source": source, "at": started, **inputs,
                        "scripts": scripts, "results": results,
                        "status": "stale" if changed else status, "run_status": status}
        if changed:
            changed_keys = [key for key in inputs if current is None or current.get(key) != inputs[key]]
            reason = ("VERIFICATION_SNAPSHOT_FAILED" if current is None else
                      "VERIFICATION_CONFIG_CHANGED" if "verification_config_sha256" in changed_keys else
                      "VERIFICATION_SOURCE_CHANGED" if "code_snapshot" in changed_keys else "VERIFICATION_INPUTS_CHANGED")
            verification.update(reason_code=reason, changed_inputs=changed_keys,
                                reason=snapshot_error or "Verification inputs changed during execution.",
                                next="Restore readable inputs and review scope/config changes; explicitly reanchor changed standards, then rerun cao verify. Do not accept the post-run snapshot as evidence.")
            work["execution"]["next_action"] = action("agent", verification["next"], reason=reason)
        work["verification"] = verification
        if not changed:
            work["execution"]["step"] = "verification"
            work["execution"]["phase"] = "verification"
            completion = completion_status(repo, work) if status == "passed" else None
            instruction = ("Run `cao done`." if completion and completion["ready"] else
                           (completion.get("next") or completion_next_instruction(repo, work, completion)) if completion else
                           "Fix verification and rerun `cao verify`.")
            work["execution"]["next_action"] = action("agent", instruction)
            work["resume"]["summary"] = "Verification passed." if status == "passed" else f"Verification {status}. See verification.results for script status and reasons."
        queue_record(repo, work, "verification.finished", verification)
        save_work(repo, work)
        return verification


def cmd_verify(args: argparse.Namespace) -> int:
    prepared = _prepare_verification(args)
    if isinstance(prepared, int):
        return prepared
    repo, wid, scripts, source, inputs, timeout, run = prepared
    started = now()
    results: list[dict[str, Any]] = []
    with owner(repo, wid, run["run_id"]):
        for index, item in enumerate(scripts, 1):
            command = item["command"]
            command_started = now()
            result = execute(repo, wid, run["run_id"], f"command-{index:04d}", command,
                             timeout, runner=run_script)
            exit_code = result["exit_code"]
            stdout, stderr = result["diagnostic_tails"]["stdout_tail"], result["diagnostic_tails"]["stderr_tail"]
            status_for_script = verification_status_for_exit_code(exit_code)
            discovery = result.get('test_discovery') or {}
            test_reason = None
            if exit_code == 0 and 'tests' in item['gates']:
                from scripts.cao_tool_policy import _shell_segments
                try:
                    segments = _shell_segments(command)
                    noop = bool(segments) and all(tokens and tokens[0] in {'true', ':', 'echo', 'printf', 'exit'} for tokens in segments)
                except ValueError:
                    noop = False
                if discovery.get('observed') and discovery.get('maximum_count') == 0:
                    status_for_script, test_reason = 'blocked', 'ZERO_TESTS_DISCOVERED'
                elif noop:
                    status_for_script, test_reason = 'blocked', 'TEST_EXECUTION_NOT_PROVEN'

            results.append({
                "name": item["name"], "command": command, "required": item["required"],
                "gates": list(item["gates"]), "exit_code": exit_code, "status": status_for_script,
                "timed_out": result["timed_out"],
                "reason": test_reason or verification_reason(stdout, stderr, status_for_script),
                "test_discovery": discovery,
                "started_at": command_started, "process_status": result.get("process_status", "exited"), "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:], "runtime": result.get("runtime"),
            })
        finish_execution(repo, wid, run["run_id"], results)
        verification = commit_verification(repo, wid, inputs, scripts, results, source, started, run)
        projection = project_result(repo, wid, run["run_id"], verification)
        if not projection["ok"]:
            verification = {**verification, "runtime_diagnostic": projection}
    status = verification["status"]
    with repo_transaction(repo):
        work = load_work(repo, wid)
        next_action = work["execution"]["next_action"]
    cao_exit = {"passed": 0, "failed": 1, "blocked": 2, "error": 3, "stale": 2, "superseded": 2}[status]
    print(json.dumps({"ok": status == "passed", "verification": verification, "next": next_action}, ensure_ascii=False, indent=2)); return cao_exit
