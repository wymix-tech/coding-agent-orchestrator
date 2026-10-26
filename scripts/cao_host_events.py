"""Host event translation, session observation, and mutation gates."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from scripts.cao_completion import completion_next_instruction
from scripts.cao_defaults import CONFIG_REL
from scripts.cao_guidance import (
    current_work_action,
    render_context,
    runtime_action_context,
    work_intake_instruction,
    work_prerequisite_action,
)
from scripts.cao_instructions import cao_invocation
from scripts.cao_native_sync import (
    expose_approval_context,
    implementation_preflight,
    implementation_write_guard,
    native_route_state,
    observe_implementation_read,
    reconcile_native_sdd_state,
    route_preparation_action,
)
from scripts.cao_path import path_assessment_instruction, path_assessment_state
from scripts.cao_provider_workflows import provider_output_dirs
from scripts.cao_runtime import config, locked_command, now, repo_root
from scripts.cao_sessions import (
    create_handoff,
    end_session,
    load_session,
    register_session,
    stable_session_id,
    update_session_event,
)
from scripts.cao_snapshot import code_snapshot, verification_snapshot
from scripts.cao_tool_policy import (
    SHELL_TOOLS,
    _collect_values_for_keys,
    _event_key,
    _shell_segments,
    diagnostic_shell_write,
    native_intake_shell,
    protected_state_advice,
    dependency_setup_advice,
    tool_effect,
    tool_is_mutating,
    verification_baseline_state,
    verification_capability_state,
    verification_scaffold_write,
    write_targets_native_sdd_output,
    write_targets_protected_state,
)
from scripts.cao_work_store import empty_index, load_index, load_work, queue_event, save_work
from scripts.light_spec_policy import light_spec_state
from scripts.native_obligations import dispatch_patch_state
from scripts.native_route import (
    approve_baseline_response,
    approve_observed_response,
    bind_approval_question,
)
from scripts.provider_restart import observe_start, restart_state
from scripts.runtime_host import binding as runtime_binding, observe as observe_runtime
from scripts.route_inputs import writable_input
from scripts.startup_request import ORDER as STARTUP_ORDER, capture_request
from scripts.verification_setup import scaffold_instruction, setup_instruction, setup_state


@locked_command
def host_event(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo or os.getcwd())
    raw = json.load(sys.stdin)
    raw.setdefault("cwd", str(Path(args.repo or os.getcwd()).resolve()))
    event = str(raw.get("event") or raw.get("hook_event_name") or raw.get("event_name") or "").lower()
    mapping = {
        "sessionstart": "session_start", "session_start": "session_start",
        "sessionend": "session_end", "session_end": "session_end",
        "userpromptsubmit": "prompt_submit", "before_agent_start": "prompt_submit",
        "pretooluse": "pre_tool", "tool_call": "pre_tool",
        "posttooluse": "post_tool", "tool_result": "post_tool",
        "posttoolusefailure": "post_tool",
        "subagentstart": "subagent_start", "subagentstop": "subagent_stop",
        "agent_end": "turn_end",
    }
    if event == "posttoolusefailure":
        raw["is_error"] = True
    event = mapping.get(event, event)
    index = load_index(repo) if (repo / CONFIG_REL).exists() else empty_index()
    wid = index.get("active_work")
    work = load_work(repo, wid) if wid else None
    candidate_id = _event_key(raw, "subagent_id", "agent_id", "session_id", "sessionId", "thread_id")
    known_session = load_session(repo, candidate_id, host=args.host) if candidate_id else None
    kind = "subagent" if (event in {"subagent_start", "subagent_stop"} or raw.get("kind") == "subagent"
                           or (known_session or {}).get("kind") == "subagent") else "primary"
    if kind == 'subagent':
        parent_ref = _event_key(raw, 'parent_session_id', 'parentSessionId', 'parent_session')
        if not parent_ref and event == 'subagent_start':
            parent_ref = _event_key(raw, 'session_id', 'sessionId')
        parent_session = load_session(repo, parent_ref, host=args.host) if parent_ref else None
        bound_work = (known_session or parent_session or {}).get('work_item_id')
        if bound_work:
            work = load_work(repo, bound_work)
    if work and restart_state(repo, args.host)["ready"] and event in {"session_start", "subagent_start", "prompt_submit", "pre_tool", "stop", "turn_end", "before_settle"}:
        # Refresh before exposing context or writing a handoff, including edits
        # performed outside this host. Derived observations never reanchor input hashes.
        reconcile_native_sdd_state(repo, work)
    if kind == "subagent":
        host_session_id = _event_key(raw, "subagent_id", "agent_id")
        if not host_session_id:
            candidate = _event_key(raw, "session_id", "sessionId", "thread_id")
            known = load_session(repo, candidate, host=args.host) if candidate else None
            host_session_id = candidate if known and known.get("kind") == "subagent" else ""
        existing = load_session(repo, host_session_id, host=args.host) if host_session_id else None
        if existing and existing.get("kind") != "subagent":
            host_session_id = ""  # Never reinterpret a primary ID as a child shutdown.
    else:
        host_session_id = _event_key(raw, "session_id", "sessionId", "thread_id")
    parent_native = _event_key(raw, "parent_session_id", "parentSessionId", "parent_session")
    if kind == "subagent" and not parent_native:
        parent_native = _event_key(raw, "session_id", "sessionId")
    parent_id = stable_session_id(args.host, parent_native) if parent_native and parent_native != host_session_id else None

    # Bind post-tool side effects and observations to the task that authorized it.
    if event == "post_tool":
        try:
            route = runtime_binding(repo, raw, args.host, host_session_id)
        except (ValueError, OSError, RuntimeError):
            route = None
        if route:
            work = load_work(repo, route["bound_work_id"]) if route["bound_work_id"] != "_unassigned" else None
    def respond(result: dict[str, Any]) -> int:
        observed_work = work
        if kind == "subagent":
            parent_ref = parent_native or (known_session or {}).get("parent_session_id")
            parent = load_session(repo, parent_ref, host=args.host) if parent_ref else None
            bound = (parent or {}).get("work_item_id")
            observed_work = load_work(repo, bound) if bound else None
        diagnostic = observe_runtime(repo, observed_work, raw, args.host, host_session_id, event, result)
        if diagnostic:
            result["diagnostic"] = diagnostic
        if not host_session_id:
            result["diagnostic"] = "CAO_SESSION_ID_MISSING: no distinct native session ID; lifecycle tracking skipped"
            if args.host != "pi":
                print(result["diagnostic"], file=sys.stderr)
        print(json.dumps(native_hook_output(args.host, event, result), ensure_ascii=False))
        return 0

    if event == "session_start":
        observe_start(repo, args.host, host_session_id, primary=kind == "primary",
                      source=_event_key(raw, "reason", "source"))

    session = None
    if host_session_id:
        if event in {"session_start", "subagent_start"}:
            session = register_session(repo, work, host_session_id, args.host, event=event, kind=kind, parent_session_id=parent_id)
        else:
            session = update_session_event(repo, host_session_id, args.host, event, work, kind=kind, parent_session_id=parent_id)

    if session:
        observed_model = raw.get('model')
        if not isinstance(observed_model, str):
            observed_model = os.environ.get('PI_MODEL') if os.environ.get('PI_SESSION_ID') == host_session_id else None
        if observed_model:
            from scripts.review_binding import runtime_model_name
            from scripts.cao_sessions import save_session
            session['observed_model'] = runtime_model_name(observed_model, os.environ.get('PI_MODEL'), os.environ.get('PI_PROVIDER'))
            save_session(repo, session)
        if isinstance(raw.get('host_tools'), list) and all(isinstance(tool, str) for tool in raw['host_tools']):
            from scripts.cao_sessions import save_session
            session['observed_tools'] = raw['host_tools']
            save_session(repo, session)

    if session:
        from scripts.review_observation import observe as observe_review_task
        observe_review_task(repo, work, session, raw, event)

    if event == 'prompt_submit' and kind == 'primary' and (repo / CONFIG_REL).exists():
        from scripts.project_sources import observe_prompt
        observe_prompt(repo, raw.get('prompt'), args.host, host_session_id)

    if event == 'prompt_submit' and not work and kind == 'primary' and (repo / CONFIG_REL).exists():
        capture_request(repo, raw.get('prompt'), args.host, host_session_id)

    restart = restart_state(repo, args.host)
    if not restart["ready"] and event not in {"session_end", "subagent_stop"}:
        context = restart["reason"] + ": " + restart["next"]
        if event == "pre_tool" and tool_is_mutating(raw):
            return respond({"decision": "deny", "reason": context, "additional_context": context})
        # Allow settling: a restart reminder must never force another Agent turn.
        return respond({"decision": "allow", "additional_context": context})

    if event in {"pre_tool", "post_tool"} and kind == "primary":
        from scripts.project_sources import observe_question
        observe_question(repo, raw, event, args.host, host_session_id)

    if event in {"session_start", "subagent_start"}:
        if kind == "primary":
            expose_approval_context(repo, work, args.host, host_session_id)
        context = render_context(repo, work, session_id=host_session_id, host=args.host)
        result = {"decision": "allow", "additional_context": context}
        if host_session_id:
            result["session_id"] = host_session_id
        return respond(result)

    if event == "prompt_submit":
        if work and kind == "primary":
            review = None
            if work.get("verification_review"):
                try:
                    review = approve_baseline_response(work, raw, event, args.host, host_session_id,
                                                      verification_snapshot(config(repo), repo), now())
                except ValueError:
                    pass  # Invalid setup must remain recoverable through host context.
            if review:
                queue_event(work, {"event": "verification.baseline.approved", "work_item_id": work["id"],
                                   "review": review, "occurred_at": now()})
                save_work(repo, work)
            approval = approve_observed_response(repo, work, raw, event, args.host, host_session_id,
                provider_output_dirs(work["sdd"]["provider"]), now())
            if approval:
                queue_event(work, {"event": "sdd.spec.approved", "work_item_id": work["id"],
                                   "approval": approval, "occurred_at": now()})
                save_work(repo, work)
                reconcile_native_sdd_state(repo, work)
            expose_approval_context(repo, work, args.host, host_session_id)
        return respond({"decision": "allow", "additional_context": render_context(repo, work, session_id=host_session_id, host=args.host)})

    if event == "pre_tool":
        if work and kind == "primary":
            question_text = json.dumps(raw.get('tool_input') or raw.get('input') or raw.get('arguments') or {}, ensure_ascii=False).lower()
            baseline_question = '验证基线' in question_text or 'verification baseline' in question_text
            candidates = [work.get('verification_review') or {}] if baseline_question else [
                (work.get('sdd') or {}).get('spec_approval') or {}]
            for approval in candidates:
                if ((approval.get("status") == "pending" or approval.get("stop_after_approval"))
                        and bind_approval_question(approval, work["id"], raw, args.host, host_session_id)):
                    save_work(repo, work)
        # No-Work product writes and hand edits to managed state are denied.
        result: dict[str, Any] = {"decision": "allow"}
        # CAO's own state narrow enough that it can never deadlock intake:
        # `requirements/` stays writable (the Agent authors those), and CAO
        # itself writes state through commands, not through this hook.
        from scripts.project_setup import writable_input as writable_setup_input
        setup_input_write = writable_setup_input(repo, raw)
        protected = write_targets_protected_state(repo, raw, work)
        if protected and tool_is_mutating(raw) and not setup_input_write:
            result = {
                "decision": "deny",
                "reason": (
                    f"Do not edit CAO-managed state by hand ({', '.join(protected[:3])}). "
                    f"{protected_state_advice(protected)}"
                ),
            }
            return respond(result)
        analysis = tool_effect(raw, repo)
        route_input_write = (not analysis["diagnostics"] and bool(analysis["targets"])
                             and all(writable_input(repo, target, work) for target in analysis["targets"]))
        native_write = (route_input_write or write_targets_native_sdd_output(repo, raw)
                        or native_intake_shell(repo, raw) or diagnostic_shell_write(repo, raw))
        native_write = native_write or setup_input_write
        if tool_is_mutating(raw) and not native_write:
            if not work or work.get("status") != "active":
                advice_work = work
                if not advice_work and session and session.get("work_item_id"):
                    advice_work = load_work(repo, session["work_item_id"])
                startup = work_prerequisite_action(repo, advice_work)
                instruction = startup['instruction'] if startup else work_intake_instruction(advice_work)
                return respond({"decision": "deny", "reason": "CAO: no active Work; product writes are blocked. " + instruction})
            else:
                from scripts.project_verification import project_prerequisite
                project = project_prerequisite(repo, work)
                if project:
                    return respond({"decision": "deny", "reason": project["reason"] + ": " + project["instruction"]})
                baseline = verification_baseline_state(repo, work)
                if not baseline["ready"] and not verification_scaffold_write(repo, raw):
                    return respond({"decision": "deny", "reason": baseline["reason"] + ": " + baseline["next"]})
                if not verification_scaffold_write(repo, raw):
                    preflight = implementation_preflight(repo, work)
                    if not preflight["ready"]:
                        dependency = dependency_setup_advice(repo, raw)
                        if dependency:
                            return respond({"decision": "deny", "reason": dependency["reason"] + ": " + dependency["next"]})
                        return respond({"decision": "deny", "reason": str(preflight["reason"]) + ": " + preflight["next"] + " " + scaffold_instruction()})
                    capabilities = verification_capability_state(repo, work)
                    if not capabilities['ready']:
                        return respond({'decision': 'deny', 'reason': capabilities['reason'] + ': ' + capabilities['next']})
                    implementation = implementation_write_guard(repo, work, raw, host_session_id, kind)
                    if not implementation['ready']:
                        return respond({'decision': 'deny', 'reason': implementation['reason'] + ': ' + implementation['next']})
                review_patches = dispatch_patch_state(repo, work, code_snapshot)
                if not review_patches["ready"]:
                    return respond({"decision": "deny", "reason": review_patches["reason"] + ": classify every active native layer before patching; follow native loopback for intent/spec gaps."})
                path_state = path_assessment_state(repo, work)
                if not path_state["ready"]:
                    next_step = path_assessment_instruction(work)
                    if path_state["reason"] == "requirement changed since work started":
                        next_step = completion_next_instruction(repo, work, path_state) + " " + next_step
                    result["additional_context"] = f"CAO reminder: {path_state['reason']}. {next_step} Clarify open questions before reassessment."
                elif (work.get("path_assessment") or {}).get("profile") == "light":
                    state = light_spec_state(repo, work, provider_output_dirs((work.get("sdd") or {}).get("provider")))
                    if not state["ready"]:
                        result["additional_context"] = "CAO light reminder: " + (state.get("next") or state["reason"])
                else:
                    workflow = ((work.get("sdd") or {}).get("workflow") or {})
                    if not (workflow.get("execution") or {}).get("executed_at") and not (workflow.get("unverified") or {}).get("allowed"):
                        name = workflow.get("name") or "<native workflow>"
                        result["additional_context"] = (
                            f"CAO reminder: native SDD workflow `{name}` is selected but has NOT been executed. "
                            f"`cao done` requires native workflow evidence. Run it, then record it with "
                            f"`cao sdd workflow execute --work {work['id']} --command \"<command actually run>\"`."
                        )
        if tool_is_mutating(raw) and not native_write and not setup_state(config(repo))["ready"]:
            result["additional_context"] = (result.get("additional_context", "") + "\n" + setup_instruction(cao_invocation()["command"])).strip()
        commands = _collect_values_for_keys(raw, {"command", "cmd"})
        payload = raw.get("tool_input") or raw.get("input") or raw.get("arguments")
        if isinstance(payload, str) and _event_key(raw, "tool_name", "toolname", "tool", "name").lower() in SHELL_TOOLS:
            commands.append(payload)
        for command in commands:
            dynamic = any(char in command for char in "$`")
            try:
                segments = _shell_segments(command)
                dynamic = dynamic or any(tokens[0] in {"eval", "source", "."} or "-c" in tokens for tokens in segments)
            except ValueError:
                dynamic = True
            if dynamic:
                result["additional_context"] = (result.get("additional_context", "") + "\nCAO risk reminder: dynamic or unparseable Shell targets cannot be reliably checked; this guard is not a Shell sandbox.").strip()
                break
        return respond(result)

    if event in {"post_tool", "file_changed"}:
        if work:
            observe_implementation_read(repo, work, raw, host_session_id)
        if work and kind == "primary":
            review = None
            if work.get('verification_review'):
                try:
                    review = approve_baseline_response(work, raw, event, args.host, host_session_id,
                                                       verification_snapshot(config(repo), repo), now())
                except ValueError:
                    pass  # An invalid declaration must remain repairable through the host.
            if review:
                queue_event(work, {'event': 'verification.baseline.approved', 'work_item_id': work['id'],
                                   'review': review, 'occurred_at': now()})
                save_work(repo, work)
            approval = approve_observed_response(repo, work, raw, event, args.host, host_session_id,
                provider_output_dirs(work["sdd"]["provider"]), now())
            if approval:
                queue_event(work, {"event": "sdd.spec.approved", "work_item_id": work["id"],
                                   "approval": approval, "occurred_at": now()})
                save_work(repo, work)
        if work and tool_is_mutating(raw):
            if (work.get("verification") or {}).get("status") == "passed":
                work.setdefault("resume", {})["summary"] = "Code changed after the last verification; verification is now stale."
            save_work(repo, work)
        if work:
            reconcile_native_sdd_state(repo, work)
        if work:
            prerequisite = work_prerequisite_action(repo, work)
            route = native_route_state(repo, work)
            if prerequisite or not route["ready"]:
                current = prerequisite or route_preparation_action(repo, work, route)
                return respond({"decision": "allow", "additional_context": runtime_action_context(repo, work, current)})
        return respond({"decision": "allow"})

    if event in {"session_end", "subagent_stop"}:
        if session and session.get("status") != "ended":
            if work and session.get("kind") != "subagent":
                create_handoff(repo, work, host_session_id, args.host, reason=event)
            end_session(repo, host_session_id, args.host, reason=_event_key(raw, "reason") or event, event=event)
        return respond({"decision": "allow"})

    if event in {"stop", "turn_end", "before_settle"}:
        if event != "before_settle" and work and session and session.get("kind") != "subagent":
            create_handoff(repo, work, host_session_id, args.host, reason=event)
        result = {"decision": "allow"}
        if work and work.get("status") == "active":
            current = current_work_action(repo, work)
            result["additional_context"] = "CAO completion reminder: " + runtime_action_context(repo, work, current)
        return respond(result)

    return respond({"decision": "allow"})


def native_hook_output(host: str, event: str, result: dict[str, Any]) -> dict[str, Any]:
    if host == "pi":
        return result
    hook_name = {"session_start": "SessionStart", "session_end": "SessionEnd", "prompt_submit": "UserPromptSubmit", "pre_tool": "PreToolUse", "post_tool": "PostToolUse", "subagent_start": "SubagentStart", "subagent_stop": "SubagentStop", "stop": "Stop", "turn_end": "Stop"}.get(event, event)
    out: dict[str, Any] = {}
    context = result.get("additional_context")
    if context:
        out["hookSpecificOutput"] = {"hookEventName": hook_name, "additionalContext": context}
    if event == "pre_tool":
        specific = out.setdefault("hookSpecificOutput", {"hookEventName": hook_name})
        specific["permissionDecision"] = "deny" if result.get("decision") == "deny" else "allow"
        if result.get("decision") == "deny":
            specific["permissionDecisionReason"] = result.get("reason") or "Blocked by CAO"
        return out
    if result.get("decision") == "deny":
        out["decision"] = "block"; out["reason"] = result.get("reason") or "Blocked by CAO"
    return out
