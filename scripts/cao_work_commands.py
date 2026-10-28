"""Work lifecycle commands: start, resume, checkpoint, block, reopen, and done."""
from __future__ import annotations

import base64

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.cao_completion import (
    completion_next_instruction,
    completion_status,
    sdd_transition,
    work_sdd_ready,
)
from scripts.cao_defaults import SDD_CATALOG, WORK_DIR
from scripts.cao_guidance import agent_guidance, current_work_action, render_context, resume_payload
from scripts.cao_instructions import cao_invocation
from scripts.cao_language import apply_provider_language
from scripts.cao_native_sync import ensure_verification_review, reconcile_native_sdd_state
from scripts.cao_path import path_assessment_instruction
from scripts.cao_plans import observe_plan_scope
from scripts.cao_provider_workflows import provider_native_skills, provider_output_dirs
from scripts.cao_providers import (
    detect_current_host,
    detect_sdd,
    host_session_identity,
    infer_sdd_provider,
    run_sdd_command,
    sdd_options,
    sdd_status,
    select_sdd_provider,
)
from scripts.cao_requirements import requirement_evidence, requirement_files, resolve_requirement
from scripts.cao_runtime import (
    action,
    config,
    ensure_initialized,
    load_json,
    locked_command,
    new_id,
    now,
    rel,
    repo_root,
)
from scripts.cao_sessions import (
    choose_resume_work,
    create_handoff,
    end_session,
    find_handoff,
    list_handoff_records,
    load_session,
    register_session,
    touch_session,
)
from scripts.cao_snapshot import anchor_verification, verification_snapshot
from scripts.cao_work_store import (
    _activation_denied,
    execution_phase,
    load_index,
    load_work,
    other_active_work,
    queue_event,
    save_index,
    save_work,
    stable_work_id,
    work_summary,
)
from scripts.completion_policy import reset_requirement
from scripts.openspec_archive import relocate_acceptance_sources
from scripts.provider_restart import restart_state
from scripts.sdd_acceptance_files import source_hashes
from scripts.sdd_acceptance_runtime import reanchor_record
from scripts.startup_request import consume_request
from scripts.state_store import repo_transaction
from scripts.verification_setup import setup_instruction, setup_state


@locked_command
def _prepare_start(args: argparse.Namespace):
    repo = repo_root(args.repo); ensure_initialized(repo)
    if args.sdd_provider:
        detected = detect_sdd(repo, args.sdd_provider)
        if not detected["installed"]:
            print(json.dumps({
                "ok": False,
                "error": "SDD_NOT_INSTALLED",
                "provider": args.sdd_provider,
                "status": detected,
                "next": f"cao sdd install --provider {args.sdd_provider} --yes",
            }, ensure_ascii=False, indent=2)); return 2
        select_sdd_provider(repo, args.sdd_provider)
    sdd_state = sdd_status(repo)
    installed = sdd_state["installed"]
    if not installed:
        print(json.dumps({"ok": False, "error": "SDD_SELECTION_REQUIRED", "options": sdd_options(sdd_state), "next": "cao bootstrap"}, ensure_ascii=False, indent=2)); return 2
    selected = str((config(repo).get("sdd") or {}).get("provider") or "auto")
    if selected == "auto" and len(installed) > 1:
        print(json.dumps({"ok": False, "error": "SDD_SELECTION_REQUIRED", "options": sdd_options(sdd_state), "next": "cao sdd select --provider <provider>"}, ensure_ascii=False, indent=2)); return 2
    if selected == "auto" and len(installed) == 1:
        select_sdd_provider(repo, installed[0]["provider"])
        selected = installed[0]["provider"]
    if selected in SDD_CATALOG:
        try:
            apply_provider_language(repo, selected)
        except (OSError, UnicodeError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": "SDD_LANGUAGE_CONFIGURATION_FAILED", "provider": selected, "reason": str(exc)}, ensure_ascii=False, indent=2))
            return 1
    from scripts.project_verification import project_prerequisite
    from scripts.project_context import make_binding
    p = resolve_requirement(repo, args.requirement)
    if p is None:
        if args.requirement:
            # An explicit path that does not exist is a different failure from
            # "which requirement do you mean?": the Agent must write the file
            # first (native SDD output dirs are writable for exactly that).
            print(json.dumps({
                "ok": False,
                "error": "REQUIREMENT_FILE_MISSING",
                "requirement": args.requirement,
                "reason": "The requirement file does not exist yet. Create it first (inside the provider's native output directory), then rerun `cao start`.",
            }, ensure_ascii=False, indent=2)); return 2
        candidates = [rel(x, repo) for x in requirement_files(repo, config(repo))]
        print(json.dumps({"ok": False, "error": "REQUIREMENT_SELECTION_REQUIRED", "candidates": candidates}, ensure_ascii=False, indent=2)); return 2
    index = load_index(repo)
    wid = stable_work_id(repo, p)
    from scripts.requirement_content import template_diagnostics
    diagnostics = template_diagnostics(p)
    if diagnostics:
        print(json.dumps({"ok": False, "error": "REQUIREMENT_CONTENT_REQUIRED", "diagnostics": diagnostics,
                          "next": f"Fill the actual user request in `{rel(p, repo)}`, then rerun the same cao start command. Keep the existing file and current Work; do not allocate another requirement."}, ensure_ascii=False))
        return 2
    existing = load_work(repo, wid)
    project = project_prerequisite(repo, existing, scopes=getattr(args, "scope", None))
    if project:
        print(json.dumps({"ok": False, "error": project["reason"], "next": project}))
        return 2
    is_new_work = existing is None
    if existing and existing.get("status") == "completed" and not args.reopen:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_COMPLETED", "work_item": work_summary(existing)}, ensure_ascii=False, indent=2))
        return 2
    active_id = index.get("active_work")
    active_to_suspend = None
    if active_id:
        active = load_work(repo, active_id)
        if active and active.get("status") == "active":
            if not args.replace:
                print(json.dumps({"ok": False, "error": "WORK_ITEM_ALREADY_ACTIVE", "work_item": work_summary(active)}, ensure_ascii=False, indent=2)); return 2
            if active_id != wid:
                active_to_suspend = active
    configured_provider = str((config(repo).get("sdd") or {}).get("provider") or "auto")
    provider = configured_provider if configured_provider != "auto" else infer_sdd_provider(repo, p, config(repo))
    if is_new_work and config(repo).get("lifecycle_contract", 2) not in {1, 2}:
        raise ValueError("LIFECYCLE_CONTRACT_UNSUPPORTED")
    work = existing or {
        "version": 1,
        "lifecycle_contract": config(repo).get("lifecycle_contract", 2),
        "id": wid,
        "title": args.title or p.stem,
        "status": "todo",
        "requirement": requirement_evidence(repo, p),
        "requirement_original_base64": base64.b64encode(p.read_bytes()).decode(),
        "sdd": {"provider": provider, "native_ref": args.sdd_ref, "artifacts": []},
        "execution": {"step": "path-assessment", "next_action": action("agent", path_assessment_instruction())},
        "path_assessment": None,
        "resume": {"summary": "Work item created. Requirement is ready for intake.", "completed": [], "pending": []},
        "decisions": [],
        "blockers": [],
        "verification": None,
        "created_at": now(),
    }
    if active_to_suspend is None and _activation_denied(repo, wid):
        return 2
    work["status"] = "active"
    work["started_at"] = work.get("started_at") or now()
    changed = reset_requirement(work, requirement_evidence(repo, p))
    if (work.get("sdd") or {}).get("provider") != provider:
        work["path_assessment"] = None
        work["gate_status"] = {}
        work["verification"] = None
        for field in ("workflow", "plan_scope", "native_state", "spec_binding", "acceptance",
                      "native_route_required", "route_decision", "spec_approval", "implementation_state", "spec_task_scope"):
            work["sdd"].pop(field, None)
        hint = work["sdd"].pop("native_ref", None)
        if hint:
            work.setdefault("resume", {})["native_ref_hint"] = hint
        if work["sdd"].get("artifacts"):
            work.setdefault("resume", {})["previous_sdd_artifacts"] = work["sdd"]["artifacts"]
            work["sdd"]["artifacts"] = []
    if is_new_work or not work.get("project_binding"):
        work["project_binding"] = make_binding(repo, getattr(args, "scope", None))
    if is_new_work or changed:
        cfg = config(repo)
        anchor_verification(repo, work, cfg)
    if not isinstance(work.get("path_assessment"), dict):
        work.setdefault("execution", {})["step"] = "path-assessment"
        work.setdefault("execution", {})["next_action"] = action("agent", path_assessment_instruction(work))
    if args.title:
        work["title"] = args.title
    work["sdd"]["provider"] = provider
    if provider == "bmad" and any(s["name"] == "bmad-build" for s in provider_native_skills(repo, provider)):
        work["sdd"]["native_route_required"] = True
    if args.sdd_ref:
        work["sdd"]["native_ref"] = args.sdd_ref
    if is_new_work:
        from scripts.review_changes import capture_baseline
        capture_baseline(repo, work)
        queue_event(work, {"event": "task.created", "work_item_id": wid, "source": "cao",
                           "native_ref": work.get("sdd", {}).get("native_ref"),
                           "occurred_at": work.get("created_at") or now()})
    # Prepare and validate all target evidence before mutating the current Work.
    # A rejection must not leave it suspended or create a false handoff.
    if active_to_suspend:
        active_to_suspend["status"] = "suspended"
        active_to_suspend.setdefault("resume", {})["pending"] = list(dict.fromkeys(
            (active_to_suspend.get("resume", {}).get("pending") or [])
            + [f"Execution re-anchored to a new work item: {Path(p).stem}"]))
        save_work(repo, active_to_suspend)
        create_handoff(repo, active_to_suspend, None, None, reason="execution re-anchored to another work item")
    save_work(repo, work)
    # Associate the new work and Session in the same transaction as activation.
    identity = host_session_identity(getattr(args, "host", None))
    session = register_session(repo, work, identity[1], identity[0], event="start") if identity else None
    return repo, wid, rel(p, repo), session


def cmd_start(args: argparse.Namespace) -> int:
    if getattr(args, 'defer', False):
        from scripts.deferred_work import defer
        work = defer(args)
        print(json.dumps({'ok': True, 'status': 'queued', 'work_item': work_summary(work)}))
        return 0
    same_provider_change = False
    if args.replace and args.requirement:
        from scripts.cao_work_store import stable_work_id
        from scripts.cao_requirements import resolve_requirement
        candidate = resolve_requirement(repo_root(args.repo), args.requirement)
        existing = load_work(repo_root(args.repo), stable_work_id(repo_root(args.repo), candidate)) if candidate else None
        chosen = args.sdd_provider or (config(repo_root(args.repo)).get('sdd') or {}).get('provider')
        same_provider_change = bool(existing and existing.get('status') == 'active' and chosen != existing['sdd']['provider'] and chosen != 'auto')
    if same_provider_change:
        from scripts.work_operations import preview
        preflight = preview(repo_root(args.repo), existing["id"])
        if not preflight["ready"]:
            print(json.dumps({"ok": False, "error": preflight["blockers"][0]}))
            return 2
    if args.replace and not same_provider_change:
        from scripts.deferred_work import defer
        from scripts.work_operations import switch
        work = defer(args)
        repo = repo_root(args.repo)
        identity = host_session_identity(getattr(args, 'host', None))
        result = switch(repo, work['id'], 'Execution re-anchored to another work item: explicit start --replace', identity[1] if identity else None, identity[0] if identity else None)
        if result['ok']:
            result['work_item'] = work_summary(load_work(repo, work['id']))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 2
    prepared = _prepare_start(args)
    if isinstance(prepared, int):
        return prepared
    repo, wid, requirement, session = prepared
    from scripts.project_knowledge import refresh_nonblocking
    refresh_nonblocking(repo)
    native = run_sdd_command(repo, "inspect", [requirement])
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if work is None:
            raise RuntimeError("WORK_ITEM_NOT_FOUND")
        current = current_work_action(repo, work)
        consume_request(repo, work['id'])
        summary = work_summary(work)
        summary["next_action"] = current
        out = {"ok": True, "action": "VERIFICATION_SETUP" if current.get("stage") == "verification_setup" else "ASSESS_PATH", "work_item": summary, "next_action": current,
               "guidance": agent_guidance(repo, work, next_action=current), "sdd": native}
        if session:
            out["session"] = load_session(repo, session["id"]) or session
        out["verification_setup"] = setup_state(config(repo))
        if not out["verification_setup"]["ready"]:
            out["warning"] = setup_instruction(cao_invocation()["command"])
        print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_list(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    items: list[dict[str, Any]] = []
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        work = load_work(repo, p.stem)
        if work:
            if work.get("status") in {"active", "suspended"} and reconcile_native_sdd_state(repo, work):
                work = load_work(repo, work.get("id")) or work
            items.append(work_summary(work))
    index["work_items"] = items
    save_index(repo, index)
    if getattr(args, "status", None):
        items = [w for w in items if w["status"] == args.status]
    print(json.dumps({"ok": True, "active_work": index.get("active_work"), "work_items": items}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_status(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo)
    wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    try:
        if work:
            # Validate scope before reconciliation can persist observations.
            verification_snapshot(config(repo), repo)
        if work and reconcile_native_sdd_state(repo, work):
            work = load_work(repo, wid) or work
        out = {"ok": True, "work": resume_payload(repo, work) if work else None, "work_items": []}
    except (ValueError, RuntimeError) as exc:
        if not str(exc).startswith('SNAPSHOT_'):
            raise
        # Inspection must not depend on successful evidence refresh. Show only
        # durable state and explicitly withhold any freshness/completion claim.
        work = load_work(repo, wid) if wid else None
        out = {'ok': True, 'inspection_only': True, 'diagnostic': str(exc),
               'work': {'work_item': work_summary(work), 'execution': work.get('execution'),
                        'completion': {'ready': False, 'reason': 'SNAPSHOT_UNAVAILABLE'}} if work else None,
               'work_items': [],
               'next': 'Inspect the named path and declared outputs; repair snapshot scope before advancing. Continue the existing SDD step after repair.'}
    for p in sorted((repo / WORK_DIR).glob("*.json")):
        w = load_json(p, None)
        if w:
            out["work_items"].append(work_summary(w))
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_next(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    current = current_work_action(repo, work)
    if work and current.get('owner') != 'project' and restart_state(repo, detect_current_host())["ready"] and reconcile_native_sdd_state(repo, work):
        work = load_work(repo, work['id']) or work
        current = current_work_action(repo, work)
    out = {"ok": True, "action": current, "work_item": work_summary(work) if work else None,
           "guidance": agent_guidance(repo, work, next_action=current)}
    if current.get('owner') == 'project' or current.get('stage') == 'verification_setup':
        from scripts.project_verification import preparation_context
        out['context'] = preparation_context(repo, work, purpose='project_baseline_preparation' if current.get('owner') == 'project' else 'work_baseline_adoption')
        out['pending_work'] = out['context']['pending_work']
        out['work_item'] = None
    if out["work_item"]:
        out["work_item"]["next_action"] = current
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_checkpoint(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work or work.get("status") not in {"active", "suspended"}:
        print(json.dumps({"ok": False, "error": "NO_ACTIVE_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    previous = json.loads(json.dumps({"execution": work.get("execution"), "resume": work.get("resume")}))
    from scripts.coordination_items import update as update_items
    update_items(work, args)
    if args.step:
        execution = work.setdefault("execution", {})
        phase = execution_phase({"step": args.step}) or execution_phase(execution)
        if phase:
            execution["phase"] = phase
        execution["step"] = args.step
    if args.next:
        work.setdefault("execution", {})["next_action"] = action(args.next_type, args.next, command=args.command) if args.next_type != "none" else action("none", args.next)
    if args.summary:
        work.setdefault("resume", {})["summary"] = args.summary
    if args.completed:
        work.setdefault("resume", {}).setdefault("completed", []).append(args.completed)
    if args.pending:
        work.setdefault("resume", {}).setdefault("pending", []).append(args.pending)
    from scripts.runtime_store import queue_record
    current = {"execution": work.get("execution"), "resume": work.get("resume")}
    if previous != current:
        queue_record(repo, work, "checkpoint.recorded", {
            "execution": work.get("execution"), "summary": args.summary,
            "completed": args.completed, "pending": args.pending, "session_id": args.session})
    work.setdefault("resume", {})["last_checkpoint_at"] = now()
    save_work(repo, work)
    if args.session:
        touch_session(repo, args.session, "checkpoint", work, host=args.host)
    print(json.dumps({"ok": True, "work_item": work_summary(work), "next": work.get("execution", {}).get("next_action")}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_decision(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work"); work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM", "scope": args.scope, "next": "Use `cao project context set` for project technology; Work decision requires an active Work."}, ensure_ascii=False, indent=2)); return 2
    d = {"id": new_id("decision"), "decision": args.decision, "reason": args.reason, "scope": args.scope, "created_at": now(), "supersedes": args.supersedes}
    work.setdefault("decisions", []).append(d)
    from scripts.runtime_store import queue_record
    queue_record(repo, work, "decision.recorded", d)
    save_work(repo, work)
    print(json.dumps({"ok": True, "decision": d}, ensure_ascii=False, indent=2)); return 0


def cmd_decision_list(args: argparse.Namespace) -> int:
    """Read back recorded decisions.

    Recording existed, but only reaching them was missing: an Agent could write
    decisions with `cao decision` yet had no way to look at the ones already
    made, so the recorded reasoning went unread and un superseded.
    """
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    decisions = work.get("decisions") or []
    print(json.dumps({
        "ok": True,
        "work_item": work["id"],
        "count": len(decisions),
        "decisions": decisions[-int(args.limit or 50):],
    }, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_block(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    repair = bool(args.work and args.suspend)
    # An explicit suspension must remain usable even with a historical conflict.
    if repair:
        wid = args.work
    else:
        index = load_index(repo)
        wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    b = {"id": new_id("blocker"), "reason": args.reason, "resolution": args.resolution, "created_at": now()}
    work.setdefault("blockers", []).append(b)
    work["status"] = "suspended" if args.suspend else work.get("status", "active")
    from scripts.runtime_store import queue_record
    queue_record(repo, work, "blocker.added", {**b, "status": work["status"]})
    try:
        save_work(repo, work)
    except RuntimeError as exc:
        if not repair or not str(exc).startswith("MULTIPLE_ACTIVE_WORK_ITEMS:"):
            raise
        # Authority and its conflict projection committed; more explicit fixes remain.
    if args.suspend:
        try:
            create_handoff(repo, work, None, None, reason=args.reason)
        except RuntimeError as exc:
            if not repair or not str(exc).startswith("MULTIPLE_ACTIVE_WORK_ITEMS:"):
                raise
    out = {"ok": True, "blocker": b, "status": work["status"]}
    remaining = other_active_work(repo, work["id"])
    if len(remaining) > 1:
        out["remaining_active_work_items"] = remaining
        out["next"] = "Continue resolving the conflict with `cao block --work <id> --suspend --reason <reason>`."
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_unblock(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = load_work(repo, args.work)
    if not work:
        print(json.dumps({"ok": False, "error": "WORK_ITEM_NOT_FOUND"}, ensure_ascii=False, indent=2)); return 2
    previous_blockers = list(work.get("blockers") or [])
    before = len(previous_blockers)
    if args.blocker:
        work["blockers"] = [b for b in work.get("blockers", []) if b.get("id") != args.blocker]
    else:
        work["blockers"] = []
    from scripts.runtime_store import queue_record
    removed = [b["id"] for b in previous_blockers if b not in work["blockers"]]
    if removed:
        queue_record(repo, work, "blocker.resolved", {"blocker_ids": removed, "status": work["status"]})
    save_work(repo, work)
    if work.get("status") == "suspended" and not work["blockers"] and not other_active_work(repo, work["id"]):
        from scripts.work_operations import switch
        result = switch(repo, work["id"], "blockers resolved")
        if not result["ok"]:
            print(json.dumps(result))
            return 2
        work = load_work(repo, work["id"])
    out = {"ok": True, "removed": before - len(work.get("blockers") or []), "work_item": work_summary(work)}
    if work.get("status") == "suspended" and not work["blockers"]:
        out["next"] = f"Explicitly switch with `cao start --requirement {work['requirement']['path']} --replace`."
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


def cmd_handoff_list(args: argparse.Namespace, repo: Path) -> int:
    if args.limit < 1:
        print(json.dumps({"ok": False, "error": "INVALID_LIMIT", "reason": "--limit must be >= 1"}, ensure_ascii=False, indent=2))
        return 2
    records = list_handoff_records(repo, args.work)[:args.limit]
    summaries = [{
        "id": record.get("id"),
        "sequence": record.get("sequence"),
        "work_item": record.get("work_item"),
        "host": record.get("host"),
        "session_id": record.get("session_id"),
        "reason": record.get("reason"),
        "created_at": record.get("created_at"),
        "step": record.get("step"),
        "summary": record.get("summary"),
    } for record in records]
    print(json.dumps({"ok": True, "count": len(summaries), "handoffs": summaries}, ensure_ascii=False, indent=2))
    return 0


def cmd_handoff_show(args: argparse.Namespace, repo: Path) -> int:
    if not args.handoff_id:
        print(json.dumps({"ok": False, "error": "HANDOFF_ID_REQUIRED", "next": "cao handoff show --id <handoff-id>"}, ensure_ascii=False, indent=2))
        return 2
    record = find_handoff(repo, args.handoff_id)
    if not record:
        print(json.dumps({"ok": False, "error": "HANDOFF_NOT_FOUND", "id": args.handoff_id}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": True, "handoff": record}, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_handoff(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    if args.handoff_action == "accept":
        from scripts.recovery_plan import accept, read_work
        from scripts.state_store import repo_transaction
        with repo_transaction(repo):
            result = accept(repo, read_work(repo, args.work), args.handoff_id, args.host, args.session)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    if args.handoff_action == "list":
        return cmd_handoff_list(args, repo)
    if args.handoff_action == "show":
        return cmd_handoff_show(args, repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    if args.summary:
        work.setdefault("resume", {})["summary"] = args.summary
    save_work(repo, work)
    handoff = create_handoff(repo, work, args.session, args.host, args.reason)
    if args.end_session and args.session:
        end_session(repo, args.session, args.host, args.reason, event="stop")
    print(json.dumps({"ok": True, "handoff": handoff}, ensure_ascii=False, indent=2)); return 0


@locked_command
def cmd_resume(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    work = choose_resume_work(repo, args.work)
    if not work:
        candidates = []
        for p in sorted((repo / WORK_DIR).glob("*.json")):
            w = load_json(p, None)
            if w and w.get("status") in {"active", "suspended"}:
                candidates.append(work_summary(w))
        if candidates:
            print(json.dumps({"ok": False, "error": "WORK_ITEM_SELECTION_REQUIRED", "candidates": candidates}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"ok": False, "error": "NO_RESUMABLE_WORK"}, ensure_ascii=False, indent=2))
        return 2
    ready, detected = work_sdd_ready(repo, work)
    if not ready:
        provider = ((work.get('sdd') or {}).get('provider') or 'auto')
        print(json.dumps({'ok': False, 'error': 'SDD_NOT_INSTALLED', 'provider': provider,
                          'status': detected, 'next': f'cao sdd install --provider {provider} --yes'}))
        return 2
    restart = restart_state(repo, args.host or detect_current_host())
    if not restart['ready']:
        print(json.dumps({'ok': False, 'error': restart['reason'], 'next': restart['next']}))
        return 2
    from scripts.project_verification import project_prerequisite, preparation_context
    project = project_prerequisite(repo, work)
    if project:
        print(json.dumps({'ok': False, 'error': project['reason'], 'next': project,
                          'context': preparation_context(repo, work)}, ensure_ascii=False, indent=2))
        return 2
    from scripts.recovery_plan import plan as recovery_plan
    recovery = recovery_plan(repo, work)
    if recovery.get('unresolved_runs'):
        print(json.dumps({'ok': False, 'error': 'RECOVERY_EXECUTION_UNRESOLVED', 'recovery': recovery}, ensure_ascii=False))
        return 2
    if reconcile_native_sdd_state(repo, work):
        work = load_work(repo, work['id']) or work
    if work.get("status") in {"todo", "suspended"} and args.reactivate:
        if _activation_denied(repo, work["id"]):
            return 2
        from scripts.work_operations import switch
        result = switch(repo, work["id"], "resume reactivation", args.session, args.host)
        if not result["ok"]:
            print(json.dumps(result))
            return 2
        work = load_work(repo, work["id"])
        work["status"] = "active"
        save_work(repo, work)
    elif work.get("status") == "suspended" and work.get("blockers"):
        # Resume can inspect suspended work without silently changing its status.
        pass
    if work.get("status") == "completed":
        print(render_context(repo, work, host=args.host))
        return 0
    identity = host_session_identity(args.host)
    session_id = args.session or (identity[1] if identity else None)
    session = register_session(repo, work, session_id, args.host or "unknown", event="resume") if session_id else None
    print(render_context(repo, work, session_id=session_id, host=args.host))
    from scripts.recovery_plan import summary as recovery_summary
    print("\n## Recovery inspection\n" + json.dumps(recovery_summary(recovery), ensure_ascii=False))
    print("\n## Session\n" + json.dumps(session, ensure_ascii=False, indent=2))
    return 0


@locked_command
def cmd_reopen(args: argparse.Namespace) -> int:
    """Return a completed Work Item to active.

    demo11 closed a requirement that was never implemented: the Agent satisfied
    every gate with self-description, defined "compiles" as the verification
    standard, and `cao done` accepted it. `done` is append-only, so reopening is
    the honest correction path: previously satisfied gates and verification are
    cleared because they were earned by the same evidence that was wrong.
    """
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); wid = args.work or index.get("active_work")
    work = load_work(repo, wid) if wid else None
    if not work:
        print(json.dumps({"ok": False, "error": "NO_WORK_ITEM"}, ensure_ascii=False, indent=2)); return 2
    reanchored = bool(getattr(args, "reanchor", False))
    status = str(work.get("status") or "")
    # Explicit standard acceptance also applies to active work, not only completed work.
    allowed = {"active", "completed"} if reanchored else {"completed"}
    if status not in allowed:
        print(json.dumps({
            "ok": False,
            "error": "WORK_ITEM_NOT_COMPLETED" if not reanchored else "WORK_ITEM_NOT_REANCHORABLE",
            "status": status,
            "reason": (
                f"Only a completed Work Item can be reopened; this one is `{status}`. Active work continues as-is."
                if not reanchored else
                f"Only an active or completed Work Item can be re-anchored; this one is `{status}`."
            ),
        }, ensure_ascii=False, indent=2)); return 2

    if _activation_denied(repo, work["id"]):
        return 2
    previous_execution = json.loads(json.dumps(work.get("execution") or {}))
    relocate_acceptance_sources(work.get("sdd") or {})
    if reanchored:
        review = work.get("verification_review") or {}
        cfg = config(repo)
        current_hash = verification_snapshot(cfg, repo)
        if ((work.get("acceptance") or {}).get("config_sha256") != current_hash
                and (review or work["sdd"].get("native_route_required"))):
            review = ensure_verification_review(repo, work, cfg)
            if review.get("status") != "approved":
                print(json.dumps({"ok": False, "error": "VERIFICATION_REVIEW_REQUIRED",
                                  "next": "Present changed scripts and wait for explicit user 批准验证基线 through the host."}))
                return 2
        p = repo / work["requirement"]["path"]
        if not p.is_file():
            print(json.dumps({"ok": False, "error": "REQUIREMENT_FILE_MISSING"}, ensure_ascii=False)); return 2
        reset_requirement(work, requirement_evidence(repo, p))
        work["requirement_original_base64"] = base64.b64encode(p.read_bytes()).decode()
        cfg = config(repo)
        anchor_verification(repo, work, cfg)
        work["reanchored_at"] = now()
        record = work["sdd"].get("acceptance") or {}
        if record.get("declaration") and ((work.get("path_assessment") or {}).get("profile") != "light" or work.get("project_binding")):
            sources = source_hashes(repo, record["declaration"], provider_output_dirs(work["sdd"]["provider"], repo))
            reanchor_record(work, sources)
    reopening_message = (
        "The active Work Item's verification standard was re-anchored after explicit review."
        if status == "active" else
        "The Work Item was reopened: it was marked completed but the requirement is not actually satisfied."
    )
    work["execution_generation"] = int(work.get("execution_generation", 0)) + 1
    work["status"] = "active"
    work["completed_at"] = None
    work["execution"]["step"] = "reopened"
    work["execution"]["next_action"] = action(
        "agent",
        reopening_message
        + (f" Reopen reason: {args.reason}." if args.reason else "")
        + (" The current verification configuration was explicitly accepted as the new standard."
           if reanchored else "")
        + " Continue the requirement from where it truly stands. Gates and verification were cleared and must be re-earned with real evidence.",
    )
    work["resume"]["summary"] = args.reason or (
        "Re-anchored: the current verification standard was accepted; gates and verification must be re-earned."
        if reanchored else "Reopened after being completed without the requirement actually being satisfied."
    )
    work["gate_status"] = {}
    work["verification"] = {}
    if (work.get("sdd") or {}).get("acceptance"):
        work["sdd"]["acceptance"]["run"] = None
    queue_event(work, {
        "event": "verification.baseline.reanchored" if status == "active" and reanchored else "task.reopened",
        "work_item_id": work["id"],
        "source": "cao",
        "to_state": "active",
        "occurred_at": now(),
        "reason": args.reason or "not specified",
        "reanchored": reanchored,
    })
    if status == "active" and reanchored:
        work["execution"] = previous_execution
        # Keep the business cursor and reconstruct native prerequisites below.
    save_work(repo, work)
    reconcile_native_sdd_state(repo, work)
    print(json.dumps({
        "ok": True,
        "status": "active",
        "reanchored": reanchored,
        "work_item": work_summary(work),
    }, ensure_ascii=False, indent=2)); return 0


def _completion_blocked(repo: Path, work: dict[str, Any] | None, c: dict[str, Any]) -> int:
    blocked: dict[str, Any] = {"ok": False, "error": "COMPLETION_BLOCKED", "reason": c["reason"]}
    # Report concrete items, not just an unfinished category.
    for key in ("pending_plan_items", "missing_artifacts", "unmet_gates", "pending_gates", "gates", "workflow", "technical_acceptance", "task_acceptance", "lifecycle", "blockers"):
        if c.get(key):
            blocked[key] = c[key]
    blocked["next"] = c.get("next") or completion_next_instruction(repo, work, c)
    from scripts.runtime_store import record_denial
    print(json.dumps(record_denial(repo, work, "done", blocked), ensure_ascii=False, indent=2)); return 2


def cmd_done(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    with repo_transaction(repo):
        index = load_index(repo)
        wid = args.work or index.get("active_work")
        work = load_work(repo, wid) if wid else None
        if work and work.get("status") == "active":
            observe_plan_scope(repo, work)
        c = completion_status(repo, work)
        if not c["ready"]:
            return _completion_blocked(repo, work, c)
    transition = sdd_transition(repo, "done", wid)
    if transition is not None and transition["exit_code"] != 0:
        print(json.dumps({"ok": False, "error": "SDD_TRANSITION_FAILED", "transition": transition}, ensure_ascii=False, indent=2)); return 1
    # Native transitions can change both state and freshness. Judge again before closing.
    with repo_transaction(repo):
        work = load_work(repo, wid)
        if work and work.get("status") == "active":
            observe_plan_scope(repo, work)
        c = completion_status(repo, work)
        if not c["ready"]:
            return _completion_blocked(repo, work, c)
        from scripts.project_commands import finish_change
        finish_change(repo, work)
        work["completion_result"] = c
        work["status"] = "completed"; work["completed_at"] = now(); work["execution"]["step"] = "done"; work["execution"]["next_action"] = action("none", "Work item is completed.")
        work["resume"]["summary"] = "Work item completed and verification was fresh."
        queue_event(work, {"event": "task.closed", "work_item_id": work.get("id"),
                           "source": "cao", "to_state": "closed", "occurred_at": work.get("completed_at")})
        from scripts.project_knowledge import collect_nonblocking
        work['knowledge_collection'] = collect_nonblocking(repo, work)
        save_work(repo, work)
        print(json.dumps({"ok": True, "knowledge_collection": work['knowledge_collection'], "status": "completed", "work_item": work_summary(work), "sdd_transition": transition}, ensure_ascii=False, indent=2)); return 0


def cmd_context(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo); ensure_initialized(repo)
    index = load_index(repo); work = load_work(repo, args.work or index.get("active_work")) if (args.work or index.get("active_work")) else None
    print(render_context(repo, work, session_id=args.session, host=args.host)); return 0
