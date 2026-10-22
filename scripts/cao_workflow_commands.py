"""Native workflow selection, execution, spec binding, and review commands."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import uuid
from pathlib import Path
from typing import Any

from scripts.cao_artifacts import artifact_reference
from scripts.cao_instructions import cao_invocation
from scripts.cao_native_sync import native_sync_state, reconcile_native_sdd_state
from scripts.cao_plans import bind_plan_scope
from scripts.cao_provider_workflows import (
    provider_native_skills,
    provider_output_dirs,
    skill_invocation_guidance,
    validate_workflow_artifacts,
    validate_workflow_entrypoint,
    workflow_command_identities,
    workflow_entrypoint_tokens,
    workflow_skill_identity,
)
from scripts.cao_providers import host_session_identity
from scripts.cao_runtime import (
    action,
    ensure_initialized,
    locked_command,
    new_id,
    now,
    repo_root,
    sha256_file,
)
from scripts.cao_sessions import list_sessions, load_session
from scripts.cao_snapshot import code_snapshot
from scripts.cao_work_store import queue_event, resolve_work, save_work, work_summary
from scripts.light_spec_policy import binding_digest, make_binding
from scripts.native_obligations import (
    active_layers,
    classify_review,
    digest as native_digest,
    dispatch as dispatch_review,
    dispatch_review_next,
    load_findings,
    planning_state,
    resolve_review,
    review_inputs,
    submit_review,
)
from scripts.path_guard import inside, provider_artifact, repo_file
from scripts.review_binding import attach_reviewer, runtime_model_name


def record_workflow_unverified(repo: Path, work: dict[str, Any], provider: str, name: str, reason: str, operation: str = "select") -> None:
    """Persist an auditable trace for a documented native-workflow exception."""
    identity = {"work_item_id": work["id"], "event": "workflow.unverified_exception", "workflow": name,
                "provider": provider, "requirement_sha256": work["requirement"]["sha256"], "reason": reason}
    key = uuid.uuid4().hex
    queue_event(work, {
        "event_id": "evt-" + key[:20],
        "idempotency_key": key,
        "event": "workflow.unverified_exception",
        "work_item_id": work["id"],
        "source": "cao",
        "provider": provider,
        "requirement_sha256": identity["requirement_sha256"],
        "workflow": name,
        "reason": reason,
        "evidence_source": "agent_declaration",
        "operation": operation,
        "occurred_at": now(),
    })


@locked_command
def cmd_sdd_workflow(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    sdd = work.setdefault("sdd", {})
    provider = str(sdd.get("provider") or "none")
    unverified_reason = str(getattr(args, "reason", "") or "").strip()
    allow_unverified = bool(getattr(args, "allow_unverified", False))
    if allow_unverified and not unverified_reason:
        print(json.dumps({"ok": False, "error": "UNVERIFIED_REASON_REQUIRED", "reason": "--allow-unverified requires --reason explaining why the native workflow cannot be verified."}, ensure_ascii=False, indent=2)); return 2

    if args.workflow_action == "select":
        name = str(args.name or "").strip()
        entrypoint = str(args.entrypoint or "").strip()
        if not name or not entrypoint:
            print(json.dumps({"ok": False, "error": "WORKFLOW_NAME_AND_ENTRYPOINT_REQUIRED"}, ensure_ascii=False, indent=2)); return 2
        skills = provider_native_skills(repo, provider)
        selected = workflow_skill_identity(repo, provider, entrypoint)
        explicit_skill = workflow_entrypoint_tokens(entrypoint)[:2] == ["invoke", "skill"]
        if not allow_unverified and not selected and (explicit_skill or (skills and provider == "bmad")):
            next_step = (f'Select an installed Skill: `cao sdd workflow select --name {skills[0]["name"]} --entrypoint "invoke skill {skills[0]["name"]}"`.'
                         if skills else "No matching native Skill is installed. Inspect the provider installation and its documented CLI/Skill entrypoint.")
            print(json.dumps({"ok": False, "error": "NATIVE_SKILL_REQUIRED", "skills": skills,
                              "next": next_step})); return 2
        if allow_unverified:
            check = {"ok": True, "reason": None}
        else:
            check = validate_workflow_entrypoint(provider, name, entrypoint)
        if not check["ok"]:
            print(json.dumps({
                "ok": False,
                "error": "NATIVE_WORKFLOW_ENTRYPOINT_INVALID",
                "reason": check["reason"],
                "provider": provider,
                "next": f"Record the provider's real native workflow entrypoint, or pass `--allow-unverified --reason \"<why>\"` to record a documented exception.",
            }, ensure_ascii=False, indent=2)); return 2
        sdd["workflow"] = {
            "name": name,
            "entrypoint": entrypoint,
            "status": "selected",
            "selected_at": now(),
            "requirement_sha256": work["requirement"]["sha256"],
            "provider": provider,
            "artifacts": [],
            "execution": None,
            "unverified": {"allowed": True, "reason": unverified_reason, "at": now(), "by": str(getattr(args, "assessed_by", "agent") or "agent")} if allow_unverified else None,
        }
        if selected == "bmad-build":
            sdd["native_route_required"] = True
        if allow_unverified:
            record_workflow_unverified(repo, work, provider, name, unverified_reason)
        work.setdefault("execution", {})["step"] = "native-sdd-workflow-selected"
        guidance = skill_invocation_guidance(repo, provider, entrypoint)
        if selected == "bmad-build":
            guidance += (" CAO quality profile and BMAD route are separate. After native planning creates a SPEC, "
                         "bind it immediately with `cao sdd artifact bind --artifact <spec>`; submit installed routing rules, "
                         "their hashes/quotes, required artifacts and omitted-planning replacements with "
                         "`cao sdd route propose --declaration <route.json>` before product implementation. "
                         "Direct Build does not lower standard gates. For dispatch, present the native SPEC summary, "
                         "run `cao sdd approval request`, and wait for explicit user approval; technical option answers "
                         "are not SPEC approval. Run `cao sdd sync` at native stage transitions.")
        work["execution"]["next_action"] = action("agent", f"Execute the selected native SDD workflow `{name}` via `{entrypoint}` now. Do not skip its native prerequisites, and do not implement the change yourself instead of running the workflow - `cao done` refuses completion until it has actually run and its artifacts are recorded.{guidance} Then record the execution with `cao sdd workflow execute --command \"<command actually run>\" --exit-code <code> --work {work['id']}`, and record the real output files with `cao sdd workflow complete --work {work['id']} --artifact <repo-relative-file>`.")
    elif args.workflow_action == "execute":
        workflow = sdd.get("workflow") or {}
        if not workflow.get("name") or not workflow.get("entrypoint"):
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_NOT_SELECTED", "next": f"Run `cao sdd workflow select --name \"<workflow name>\" --entrypoint \"<native workflow command or skill>\" --work {work['id']}` first."}, ensure_ascii=False, indent=2)); return 2
        command = str(getattr(args, "command", "") or "").strip()
        if not command:
            print(json.dumps({"ok": False, "error": "WORKFLOW_COMMAND_REQUIRED", "reason": "Record the command that was actually executed."}, ensure_ascii=False, indent=2)); return 2
        if re.search(r'\s->\s', command) or any(Path(identity).name == 'render_skill.py' for identity in workflow_command_identities(command)):
            print(json.dumps({'ok': False, 'error': 'RENDERER_IS_NOT_WORKFLOW_EXECUTION',
                              'next': 'A renderer only loads instructions. Follow the native workflow; record its actual session invocation (invoke skill <installed skill>) without explanatory pseudo-shell suffixes. Native review and artifacts are checked separately.'})); return 2
        selected_skill = workflow_skill_identity(repo, provider, str(workflow["entrypoint"]))
        executed_skill = workflow_skill_identity(repo, provider, command)
        if executed_skill and selected_skill != executed_skill:
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_IDENTITY_CHANGED",
                              "next": "Reselect the actual workflow with `cao sdd workflow select` before recording its execution."})); return 2
        if not allow_unverified:
            check = validate_workflow_entrypoint(provider, str(workflow.get("name") or ""), command)
            if not check["ok"]:
                print(json.dumps({
                    "ok": False,
                    "error": "NATIVE_WORKFLOW_EXECUTION_INVALID",
                    "reason": check["reason"],
                    "provider": provider,
                    "next": f"Record the command that actually ran the `{workflow.get('name')}` workflow, or pass `--allow-unverified --reason \"<why>\"`.",
                }, ensure_ascii=False, indent=2)); return 2
        # A new run requires a new completion and a separately accepted failure exception.
        workflow["status"] = "executed"
        workflow["requirement_sha256"] = work["requirement"]["sha256"]
        workflow["provider"] = provider
        workflow["artifacts"] = []
        for key in ("completed_at", "artifact_hashes", "failure_exception"):
            workflow.pop(key, None)
        workflow["execution"] = {
            "command": command,
            "exit_code": int(getattr(args, "exit_code", 0) or 0),
            "executed_at": now(),
            "output_tail": str(getattr(args, "output", "") or "").strip()[-2000:],
        }
        workflow["unverified"] = {"allowed": True, "reason": unverified_reason, "at": now(), "by": "agent"} if allow_unverified else workflow.get("unverified")
        if allow_unverified:
            record_workflow_unverified(repo, work, provider, workflow["name"], unverified_reason, "execute")
        work.setdefault("execution", {})["step"] = "native-sdd-workflow-executed"
        work["execution"]["next_action"] = action("agent", f"Record the real output files produced by `{workflow.get('name')}` with `cao sdd workflow complete --work {work['id']} --artifact <repo-relative-file>` (repeat --artifact for each file).")
    elif args.workflow_action == "complete":
        planning = planning_state(repo, work)
        if not planning['ready']:
            print(json.dumps({'ok': False, 'error': planning['reason'].split(':', 1)[0], **planning})); return 2
        workflow = sdd.get("workflow") or {}
        if not workflow.get("name") or not workflow.get("entrypoint"):
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_NOT_SELECTED", "next": f"Run `cao sdd workflow select --name \"<workflow name>\" --entrypoint \"<native workflow command or skill>\" --work {work['id']}` first."}, ensure_ascii=False, indent=2)); return 2
        if not workflow.get("execution") and not allow_unverified:
            print(json.dumps({
                "ok": False,
                "error": "NATIVE_WORKFLOW_EXECUTION_REQUIRED",
                "reason": f"`{workflow.get('name')}` has no recorded execution. Run the workflow, then record it with `cao sdd workflow execute --command \"<command actually run>\" --exit-code <code> --work {work['id']}`.",
                "workflow": workflow,
            }, ensure_ascii=False, indent=2)); return 2
        execution = workflow.get("execution") or {}
        if execution and (workflow.get("requirement_sha256") != work["requirement"]["sha256"]
                          or workflow.get("provider") != provider):
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_EVIDENCE_STALE",
                              "next": f"Select and execute the workflow for this requirement with `cao sdd workflow select` and `cao sdd workflow execute --work {work['id']}`."}, ensure_ascii=False)); return 2
        if execution and execution.get("exit_code") != 0 and not allow_unverified:
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_EXECUTION_FAILED",
                              "exit_code": execution.get("exit_code"),
                              "next": "Rerun the native workflow successfully; accepting its failed run requires complete --allow-unverified --reason \"<why>\"."}, ensure_ascii=False)); return 2
        paths = list(args.artifact or [])
        if not paths:
            print(json.dumps({"ok": False, "error": "NATIVE_WORKFLOW_ARTIFACT_REQUIRED", "reason": "At least one actual output file must be recorded."}, ensure_ascii=False, indent=2)); return 2
        dir_check = validate_workflow_artifacts(repo, provider, paths)
        if not dir_check["ok"]:
            print(json.dumps({
                "ok": False,
                "error": "NATIVE_WORKFLOW_ARTIFACT_OUTSIDE_PROVIDER_DIRS",
                "reason": dir_check["reason"],
                "provider": provider,
                "offending": dir_check["offending"],
                "next": f"Record only files the native workflow produced under {', '.join(provider_output_dirs(provider)) or 'the provider directory'}; product source files are not SDD artifacts.",
            }, ensure_ascii=False, indent=2)); return 2
        refs = []
        try:
            for value in paths:
                ref = artifact_reference(repo, value, role="native-sdd-output", native_type=workflow["name"], native_ref=workflow["entrypoint"])
                refs.append(ref)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": "ARTIFACT_INVALID", "reason": str(exc)}, ensure_ascii=False, indent=2)); return 2
        artifacts = sdd.setdefault("artifacts", [])
        for ref in refs:
            artifacts[:] = [item for item in artifacts if item.get("path") != ref["path"]]
            artifacts.append(ref)
        workflow["artifacts"] = [ref["path"] for ref in refs]
        workflow["artifact_hashes"] = {ref["path"]: ref["sha256"] for ref in refs}
        workflow["requirement_sha256"] = work["requirement"]["sha256"]
        workflow["provider"] = provider
        workflow.pop("failure_exception", None)
        if execution and execution.get("exit_code") != 0:
            workflow["failure_exception"] = {"reason": unverified_reason, "at": now(),
                                              "exit_code": execution["exit_code"]}
            queue_event(work, {"event": "workflow.execution_failure_exception", "work_item_id": work["id"],
                               "source": "cao", "provider": provider, "workflow": workflow["name"],
                               "requirement_sha256": work["requirement"]["sha256"],
                               "reason": unverified_reason, "exit_code": execution["exit_code"], "occurred_at": now()})
        selections = {}
        try:
            for value in getattr(args, "plan_item", None) or []:
                path, item = value.rsplit("#", 1)
                if not path or not item:
                    raise ValueError("PLAN_ITEM_INVALID")
                selections.setdefault(path, []).append(item)
            scope = bind_plan_scope(repo, work, getattr(args, "plan", None) or [],
                                    selections, bool(getattr(args, "no_plan", False)))
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc).split(":", 1)[0], "reason": str(exc),
                              "next": "Supply associated --plan <path> and shared --plan-item <path>#<story-id>; --no-plan cannot remove an existing valid plan scope."}, ensure_ascii=False)); return 2
        sdd["plan_scope"] = scope
        if scope["mode"] == "none":
            queue_event(work, {"event": "workflow.no_plan_declared", "work_item_id": work["id"],
                               "source": "cao", "provider": provider, "workflow": workflow["name"],
                               "requirement_sha256": work["requirement"]["sha256"], "occurred_at": now()})
        workflow["status"] = "completed"
        workflow["completed_at"] = now()
        if allow_unverified and not (execution and execution.get("exit_code") != 0):
            workflow["unverified"] = {"allowed": True, "reason": unverified_reason, "at": now(), "by": "agent"}
            record_workflow_unverified(repo, work, provider, str(workflow.get("name") or ""), unverified_reason, "complete")
        work["execution"]["step"] = "native-sdd-workflow-completed"
        work["execution"]["next_action"] = action("agent", "Continue implementation/review/verification according to the selected path; record manual reviews with `cao gate satisfy`, record additional native workflow outputs if produced, then run configured `cao verify` for derived gates and `cao done`.")
    from scripts.runtime_store import queue_record
    names = {"select": "selected", "execute": "execution_recorded", "complete": "completed"}
    queue_record(repo, work, "workflow." + names[args.workflow_action],
                 {"provider": provider, "workflow": sdd.get("workflow")},
                 source="agent_declaration" if args.workflow_action == "execute" else "cao_command")
    save_work(repo, work)
    print(json.dumps({"ok": True, "work_item": work_summary(work), "workflow": sdd.get("workflow")}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_sdd_spec(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    try:
        binding = make_binding(repo, work, args.spec, args.criteria, args.criteria_section,
                               provider_output_dirs(work["sdd"]["provider"]))
        previous = work["sdd"].get("spec_binding")
        if previous == binding:
            reconcile_native_sdd_state(repo, work)
            print(json.dumps({"ok": True, "status": "unchanged", "binding": binding, "native_sync": native_sync_state(repo, work)}, ensure_ascii=False)); return 0
        if previous and not args.replace:
            raise ValueError("LIGHT_SPEC_REPLACE_REQUIRED")
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); return 2
    work["sdd"]["spec_binding"] = binding
    for ref, role in ((binding["spec"], "spec"), (binding["criteria"], "acceptance-criteria")):
        artifacts = work["sdd"].setdefault("artifacts", [])
        if not any(item.get("path") == ref["path"] for item in artifacts):
            artifacts.append({**ref, "role": role})
    queue_event(work, {"event": "spec.bound", "work_item_id": work["id"], "source": "cao",
                       "provider": binding["provider"], "requirement_sha256": binding["requirement_sha256"],
                       "binding_sha256": binding_digest(binding), "occurred_at": now()})
    save_work(repo, work)
    reconcile_native_sdd_state(repo, work)
    print(json.dumps({"ok": True, "status": "bound", "binding": binding,
                      "native_sync": native_sync_state(repo, work),
                      "next": "Follow the native implementation/review workflow. After reviewing any SPEC changes, rebind if needed, run configured `cao verify`, then `cao done`."}, ensure_ascii=False)); return 0


@locked_command
def cmd_sdd_review(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = resolve_work(repo, args.work)
    if not work or work.get("status") != "active":
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"})); return 2
    try:
        records = work.setdefault("sdd", {}).setdefault("native_reviews", {})
        identity = host_session_identity(None)
        runtime_model = os.environ.get('PI_MODEL') if identity and identity[0] == 'pi' else None
        if args.review_action in {'begin', 'attach'}:
            args.model = runtime_model_name(args.model, runtime_model, os.environ.get('PI_PROVIDER'))
            if runtime_model and runtime_model != args.model:
                print(json.dumps({'ok': False, 'error': 'NATIVE_REVIEW_MODEL_MISMATCH',
                                  'expected_model': runtime_model, 'actual_model': args.model,
                                  'next': 'Use the current model in a new independent reviewer session; a different model is not required.'}))
                return 2
        if args.review_action == "begin":
            if not args.model.strip():
                raise ValueError("NATIVE_REVIEW_MODEL_REQUIRED")
            path = repo_file(repo, args.instructions)
            expected_name = "step-04-review.md" if dispatch_review(work) else "step-oneshot.md"
            if path.name != expected_name or not inside(path, repo / "_bmad/render") or (repo / "_bmad/render").resolve() != repo / "_bmad/render" or not path.is_file():
                raise ValueError("NATIVE_REVIEW_INSTRUCTIONS_INVALID")
            content = path.read_text()
            layers = sorted(set(re.findall(r"(?m)^####[^\n]*\(`?([a-z][a-z0-9-]*)`?\)", content)))
            if not layers or args.layer not in layers:
                raise ValueError("NATIVE_REVIEW_LAYER_NOT_ACTIVE")
            if dispatch_review(work) and layers != active_layers(repo, work):
                raise ValueError("NATIVE_REVIEW_CONTRACT_MISMATCH")
            records["_contract"] = {"sources": [path.relative_to(repo).as_posix()], "layers": layers}
            if any(record.get('model') != args.model for key, record in records.items() if key != '_contract'):
                # A model change starts a new review round; never mix layers
                # from two capability levels while retaining their old passes.
                for key in list(records):
                    if key != '_contract':
                        records.pop(key)
            inputs = review_inputs(repo, work, code_snapshot)
            primary_ids = [session.get("host_session_id") for session in list_sessions(repo)
                           if session.get("work_item_id") == work["id"] and session.get("kind") == "primary"]
            existing = records.get(args.layer) or {}
            matching_inputs = (existing.get('inputs') == inputs or (existing.get('status') == 'classified'
                               and all(existing.get('inputs', {}).get(k) == v for k, v in inputs.items() if k != 'code_snapshot')))
            reused = (dispatch_review(work) and not args.restart and existing.get('status') in {'running', 'classified'}
                      and matching_inputs and existing.get('model') == args.model)
            run = existing if reused else {"id": new_id("review"), "status": "running", "inputs": inputs,
                   "input_sha256": native_digest(inputs), "model": args.model,
                   "existing_session_ids": [session['id'] for session in list_sessions(repo)],
                   "primary_session_ids": primary_ids}
            records[args.layer] = run
            output = {"ok": True, "review": run, "active_layers": layers, 'reused': reused}
            output['reviewer_start'] = (f'{cao_invocation()["command"]} sdd review attach --work {work["id"]} '
                f'--layer {args.layer} --run {run["id"]} --input-sha256 {run["input_sha256"]} --model {shlex.quote(args.model)}')
            output['next'] = ('Begin ALL active layers before launching reviewers. Each new independent host session must first run reviewer_start '
                'to acknowledge this ticket and frozen input, then inspect that exact code without product writes. '
                + ('Preserve unmodified native Markdown/JSON findings for each layer; classify ALL layers before patches, then resolve ALL layers. '
                   'Use `cao sdd review classify --layer <layer> --report <triage.json>`, then `cao sdd review resolve --layer <layer> --report <resolved.json>`.'
                   if dispatch_review(work) else
                   'Preserve raw Markdown findings and submit triage with `cao sdd review submit --layer <layer> --report <report.json>`.')
                + ' Before launch, inspect the installed agent definitions: the reviewer needs shell access to execute reviewer_start and the CAO host adapter. '
                'Use an available agent name; do not assume task/general-purpose exist. Preserve native prompts/output formats, including Markdown. '
                'If no capable subagent is available, use a new independent external host session with its CAO adapter. Never reuse results from before this ticket.')
            if reused:
                output['next'] = dispatch_review_next(records, layers, inputs)
        elif args.review_action == 'attach':
            run = records.get(args.layer) or {}
            identity = host_session_identity(None)
            session = load_session(repo, identity[1], host=identity[0]) if identity else None
            if session and any(key != args.layer and (record.get('execution_binding') or {}).get('session_id') == session['id']
                               for key, record in records.items() if key != '_contract'):
                raise ValueError('NATIVE_REVIEW_SESSION_REUSED')
            attached = attach_reviewer(run, review_inputs(repo, work, code_snapshot), identity, session,
                                       args.model, args.run, args.input_sha256)
            output = {'ok': True, 'review': attached,
                      'next': 'Read the frozen review input and native instructions. Return actual findings; do not patch product code.'}
        elif args.review_action == 'findings':
            source = provider_artifact(repo, args.source, ['_bmad-output'])
            output = {'ok': True, 'source': source.relative_to(repo).as_posix(),
                      'source_sha256': sha256_file(source), 'findings': load_findings(source)}
            print(json.dumps(output, ensure_ascii=False)); return 0
        elif args.review_action == "classify":
            output = {"ok": True, "review": classify_review(repo, work, args.layer, args.report, code_snapshot),
                      "next": dispatch_review_next(records, active_layers(repo, work), review_inputs(repo, work, code_snapshot))}
        elif args.review_action == "resolve":
            output = {"ok": True, "review": resolve_review(repo, work, args.layer, args.report, code_snapshot)}
        else:
            output = {"ok": True, "review": submit_review(repo, work, args.layer, args.report, code_snapshot)}
        queue_event(work, {"event": "sdd.native_review." + args.review_action,
                           "work_item_id": work["id"], "layer": args.layer, "occurred_at": now()})
        save_work(repo, work)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); return 2
    print(json.dumps(output, ensure_ascii=False)); return 0
