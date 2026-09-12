#!/usr/bin/env python3
"""Canonical Execution State Manager for Adaptive SDD Coding Orchestrator v6.

The manager owns canonical execution metadata, transition guards, optimistic locking,
quality/review/verification state, blockers, assignments, and append-only history.
Native SDD state remains authoritative when authority.mode == 'native'.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

SCHEMA_VERSION = 1
PHASES = [
    "discovery",
    "specification",
    "design",
    "planning",
    "implementation",
    "review",
    "verification",
    "release",
    "closed",
]
STATUSES = {"pending", "ready", "in_progress", "completed", "failed", "cancelled"}
FLOW_RANK = {"TRIVIAL": 0, "FAST": 1, "STANDARD": 2, "DEEP": 3}
ACCEPTED_GATE_STATUS = {"pending", "passed", "failed", "skipped", "not_required"}
ACCEPTED_REVIEW_STATUS = {"pending", "passed", "failed", "not_required"}
ACCEPTED_VERIFICATION_STATUS = {"pending", "passed", "failed"}
AUTHORITY_MODES = {"native", "hybrid", "orchestrator"}
PROVIDERS = {"bmad", "openspec", "generic", "other"}


class StateError(ValueError):
    pass


class RevisionConflict(StateError):
    pass


class TransitionDenied(StateError):
    def __init__(self, reasons: Iterable[str]):
        self.reasons = list(reasons)
        super().__init__("transition denied: " + "; ".join(self.reasons))


class NativeAuthorityRequired(StateError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dump(data: Any, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise StateError("PyYAML is required for YAML state files")
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    else:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        raise StateError(f"state file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise StateError("PyYAML is required for YAML state files")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise StateError("state document must be an object")
    validate_state(data)
    return data


def _history_path(state_path: pathlib.Path) -> pathlib.Path:
    stem = state_path.stem
    return state_path.with_name(stem.replace("execution-state", "execution-history") + ".jsonl")


def _append_history(state_path: pathlib.Path, event: Dict[str, Any]) -> None:
    hp = _history_path(state_path)
    hp.parent.mkdir(parents=True, exist_ok=True)
    with hp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _event_id(event: Dict[str, Any]) -> str:
    raw = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _new_event(state: Dict[str, Any], event_type: str, actor: str, **payload: Any) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "timestamp": utc_now(),
        "event": event_type,
        "actor": actor,
        "work_item_id": state["work_item"]["id"],
        "revision_before": state["revision"],
    }
    event.update(payload)
    event["event_id"] = _event_id(event)
    return event


def _default_review(flow: str) -> Dict[str, Any]:
    required = FLOW_RANK[flow] >= FLOW_RANK["STANDARD"]
    return {
        "required": required,
        "status": "pending" if required else "not_required",
        "blocking_findings": 0,
        "evidence": [],
    }


def create_state(
    work_id: str,
    title: str,
    flow_profile: str,
    provider: str = "generic",
    authority_mode: str = "orchestrator",
    work_type: str = "change",
    native_state_ref: Optional[str] = None,
    iteration_type: str = "continuous",
    iteration_id: Optional[str] = None,
) -> Dict[str, Any]:
    flow = flow_profile.upper()
    provider = provider.lower()
    authority_mode = authority_mode.lower()
    if flow not in FLOW_RANK:
        raise StateError(f"invalid flow_profile: {flow_profile}")
    if provider not in PROVIDERS:
        raise StateError(f"invalid provider: {provider}")
    if authority_mode not in AUTHORITY_MODES:
        raise StateError(f"invalid authority mode: {authority_mode}")
    if provider == "bmad" and authority_mode == "orchestrator":
        raise StateError("BMAD should use native or hybrid authority when native sprint state exists")

    now = utc_now()
    native_fields = ["phase", "status", "work_item.progress"] if authority_mode == "native" else []
    owned_fields = [
        "blocked",
        "blockers",
        "assignments",
        "quality_gates",
        "review",
        "verification",
        "cursor",
        "readiness",
        "execution_snapshot_id",
        "analysis",
    ]
    if authority_mode != "native":
        owned_fields.extend(["phase", "status", "work_item.progress"])

    state = {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "created_at": now,
        "updated_at": now,
        "authority": {
            "provider": provider,
            "mode": authority_mode,
            "native_state_ref": native_state_ref,
            "native_owned_fields": native_fields,
            "orchestrator_owned_fields": owned_fields,
            "last_native_sync": None,
        },
        "work_item": {
            "id": work_id,
            "type": work_type,
            "title": title,
            "progress": {"completed": 0, "total": 0},
        },
        "iteration": {
            "type": iteration_type,
            "id": iteration_id,
        },
        "flow_profile": flow,
        "phase": "discovery",
        "status": "in_progress",
        "blocked": False,
        "blockers": [],
        "assignments": {},
        "cursor": {
            "current_task_id": None,
            "current_task_title": None,
            "next_action": "discover_and_classify",
        },
        "readiness": {
            "behavior_change": None,
            "sdd_ready": False,
            "acceptance_criteria_present": False,
            "implementation_tasks_complete": False,
            "acceptance_satisfied": False,
        },
        "quality_gates": {},
        "review": _default_review(flow),
        "verification": {
            "required": True,
            "status": "pending",
            "snapshot_id": None,
            "fresh": False,
            "evidence": [],
            "verified_at": None,
        },
        "execution_snapshot_id": None,
        "analysis": {
            "semantic_impact_ref": None,
            "work_facts_ref": None,
            "decision_ref": None,
            "verification_plan_ref": None,
            "analysis_snapshot_id": None,
            "provider": None,
            "updated_at": None,
        },
    }
    validate_state(state)
    return state


def validate_state(state: Dict[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StateError(f"unsupported schema_version: {state.get('schema_version')}")
    if not isinstance(state.get("revision"), int) or state["revision"] < 0:
        raise StateError("revision must be a non-negative integer")
    auth = state.get("authority") or {}
    if auth.get("mode") not in AUTHORITY_MODES:
        raise StateError("authority.mode is invalid")
    if auth.get("provider") not in PROVIDERS:
        raise StateError("authority.provider is invalid")
    if state.get("flow_profile") not in FLOW_RANK:
        raise StateError("flow_profile is invalid")
    if state.get("phase") not in PHASES:
        raise StateError("phase is invalid")
    if state.get("status") not in STATUSES:
        raise StateError("status is invalid")
    if not isinstance(state.get("blocked"), bool):
        raise StateError("blocked must be boolean")
    if not isinstance(state.get("blockers"), list):
        raise StateError("blockers must be a list")
    review = state.get("review") or {}
    if review.get("status") not in ACCEPTED_REVIEW_STATUS:
        raise StateError("review.status is invalid")
    analysis = state.get("analysis")
    if analysis is not None and not isinstance(analysis, dict):
        raise StateError("analysis must be an object")
    ver = state.get("verification") or {}
    if ver.get("status") not in ACCEPTED_VERIFICATION_STATUS:
        raise StateError("verification.status is invalid")
    for name, gate in (state.get("quality_gates") or {}).items():
        if gate.get("status") not in ACCEPTED_GATE_STATUS:
            raise StateError(f"quality gate {name!r} has invalid status")
        if not isinstance(gate.get("required"), bool):
            raise StateError(f"quality gate {name!r} required must be boolean")


def _require_revision(state: Dict[str, Any], expected_revision: Optional[int]) -> None:
    if expected_revision is not None and state["revision"] != expected_revision:
        raise RevisionConflict(
            f"stale revision: expected {expected_revision}, current {state['revision']}"
        )


def _commit(
    state_path: pathlib.Path,
    state: Dict[str, Any],
    event: Dict[str, Any],
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    current = _load(state_path)
    _require_revision(current, expected_revision)
    if current["revision"] != event["revision_before"]:
        raise RevisionConflict(
            f"event was built from revision {event['revision_before']}, current is {current['revision']}"
        )
    state["revision"] = current["revision"] + 1
    state["updated_at"] = utc_now()
    event["revision_after"] = state["revision"]
    validate_state(state)
    _dump(state, state_path)
    _append_history(state_path, event)
    return state


def initialize(state_path: pathlib.Path, state: Dict[str, Any], actor: str = "orchestrator") -> Dict[str, Any]:
    if state_path.exists():
        raise StateError(f"state already exists: {state_path}")
    _dump(state, state_path)
    event = _new_event(state, "STATE_INITIALIZED", actor, initial_state=copy.deepcopy(state))
    event["revision_after"] = state["revision"]
    _append_history(state_path, event)
    return state


def required_gate_failures(state: Dict[str, Any]) -> List[str]:
    failures = []
    for name, gate in state.get("quality_gates", {}).items():
        if gate.get("required") and gate.get("status") != "passed":
            failures.append(f"required gate {name} is {gate.get('status')}")
    return failures


def transition_guard(state: Dict[str, Any], to_phase: str, to_status: str = "in_progress") -> List[str]:
    reasons: List[str] = []
    if to_phase not in PHASES:
        return [f"invalid target phase: {to_phase}"]
    if to_status not in STATUSES:
        return [f"invalid target status: {to_status}"]
    if state.get("blocked") and to_phase not in {state["phase"], "closed"}:
        reasons.append("active blocker prevents phase advance")

    readiness = state.get("readiness", {})
    if to_phase == "implementation":
        if not readiness.get("sdd_ready"):
            reasons.append("SDD/native planning state is not implementation-ready")
        if readiness.get("behavior_change") is True and not readiness.get("acceptance_criteria_present"):
            reasons.append("behavior change requires acceptance criteria")

    if to_phase in {"review", "verification", "release", "closed"}:
        if not readiness.get("implementation_tasks_complete"):
            reasons.append("implementation tasks are not complete")

    if to_phase in {"verification", "release", "closed"} and state["review"].get("required"):
        if state["review"].get("status") != "passed":
            reasons.append("required review has not passed")
        if int(state["review"].get("blocking_findings", 0)) > 0:
            reasons.append("blocking review findings remain")

    if to_phase == "closed":
        if state.get("blocked"):
            reasons.append("active blocker prevents close")
        if not readiness.get("acceptance_satisfied"):
            reasons.append("acceptance criteria are not recorded as satisfied")
        reasons.extend(required_gate_failures(state))
        ver = state.get("verification", {})
        if ver.get("status") != "passed":
            reasons.append("final verification has not passed")
        if not ver.get("fresh"):
            reasons.append("final verification evidence is not fresh")
        current_snapshot = state.get("execution_snapshot_id")
        if current_snapshot and ver.get("snapshot_id") != current_snapshot:
            reasons.append("verification snapshot does not match current execution snapshot")
    return reasons


def transition(
    state_path: pathlib.Path,
    to_phase: str,
    to_status: str,
    actor: str,
    reason: str,
    expected_revision: Optional[int] = None,
    native_confirmed: bool = False,
    evidence_ref: Optional[str] = None,
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    if state["authority"]["mode"] == "native" and not native_confirmed:
        raise NativeAuthorityRequired(
            "native authority owns phase/status; update via the native adapter, then retry with native_confirmed"
        )
    reasons = transition_guard(state, to_phase, to_status)
    if reasons:
        raise TransitionDenied(reasons)
    before = {"phase": state["phase"], "status": state["status"]}
    new_state = copy.deepcopy(state)
    new_state["phase"] = to_phase
    new_state["status"] = to_status
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "TRANSITION",
        actor,
        from_state=before,
        to_state={"phase": to_phase, "status": to_status},
        reason=reason,
        evidence_ref=evidence_ref,
        native_confirmed=native_confirmed,
    )
    return _commit(state_path, new_state, event, expected_revision)


def set_readiness(
    state_path: pathlib.Path,
    key: str,
    value: bool,
    actor: str,
    evidence_ref: str,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    allowed = {
        "behavior_change",
        "sdd_ready",
        "acceptance_criteria_present",
        "implementation_tasks_complete",
        "acceptance_satisfied",
    }
    if key not in allowed:
        raise StateError(f"unsupported readiness key: {key}")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    new_state["readiness"][key] = value
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "READINESS_UPDATED",
        actor,
        key=key,
        value=value,
        evidence_ref=evidence_ref,
    )
    return _commit(state_path, new_state, event, expected_revision)


def set_progress(
    state_path: pathlib.Path,
    completed: int,
    total: int,
    actor: str,
    evidence_ref: str,
    expected_revision: Optional[int] = None,
    native_confirmed: bool = False,
) -> Dict[str, Any]:
    if completed < 0 or total < 0 or completed > total:
        raise StateError("progress must satisfy 0 <= completed <= total")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    if state["authority"]["mode"] == "native" and not native_confirmed:
        raise NativeAuthorityRequired("native authority owns work_item.progress")
    new_state = copy.deepcopy(state)
    new_state["work_item"]["progress"] = {"completed": completed, "total": total}
    event = _new_event(
        state,
        "PROGRESS_UPDATED",
        actor,
        completed=completed,
        total=total,
        evidence_ref=evidence_ref,
        native_confirmed=native_confirmed,
    )
    return _commit(state_path, new_state, event, expected_revision)


def add_blocker(
    state_path: pathlib.Path,
    blocker_type: str,
    reason: str,
    actor: str,
    evidence_ref: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    blocker = {
        "id": f"blk-{uuid.uuid4().hex[:10]}",
        "type": blocker_type,
        "reason": reason,
        "created_at": utc_now(),
        "created_by": actor,
        "evidence_ref": evidence_ref,
        "resolved_at": None,
        "resolved_by": None,
        "resolution": None,
    }
    new_state["blockers"].append(blocker)
    new_state["blocked"] = True
    new_state["cursor"]["next_action"] = f"resolve_blocker:{blocker['id']}"
    event = _new_event(state, "BLOCKED", actor, blocker=copy.deepcopy(blocker))
    return _commit(state_path, new_state, event, expected_revision)


def resolve_blocker(
    state_path: pathlib.Path,
    blocker_id: str,
    resolution: str,
    actor: str,
    evidence_ref: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    target = None
    for blocker in new_state["blockers"]:
        if blocker["id"] == blocker_id and blocker.get("resolved_at") is None:
            target = blocker
            break
    if target is None:
        raise StateError(f"active blocker not found: {blocker_id}")
    target["resolved_at"] = utc_now()
    target["resolved_by"] = actor
    target["resolution"] = resolution
    target["resolution_evidence_ref"] = evidence_ref
    new_state["blocked"] = any(b.get("resolved_at") is None for b in new_state["blockers"])
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "UNBLOCKED" if not new_state["blocked"] else "BLOCKER_RESOLVED",
        actor,
        blocker_id=blocker_id,
        resolution=resolution,
        evidence_ref=evidence_ref,
    )
    return _commit(state_path, new_state, event, expected_revision)


def record_gate(
    state_path: pathlib.Path,
    name: str,
    required: bool,
    status: str,
    actor: str,
    stage: str = "verification",
    evidence_ref: Optional[str] = None,
    command: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    if status not in ACCEPTED_GATE_STATUS:
        raise StateError(f"invalid gate status: {status}")
    if required and status in {"skipped", "not_required"}:
        raise StateError("a required gate cannot be skipped or marked not_required")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    new_state["quality_gates"][name] = {
        "required": required,
        "status": status,
        "stage": stage,
        "evidence_ref": evidence_ref,
        "command": command,
        "updated_at": utc_now(),
        "updated_by": actor,
    }
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "QUALITY_GATE_RECORDED",
        actor,
        gate=name,
        required=required,
        status=status,
        stage=stage,
        evidence_ref=evidence_ref,
    )
    return _commit(state_path, new_state, event, expected_revision)


def record_review(
    state_path: pathlib.Path,
    status: str,
    actor: str,
    blocking_findings: int = 0,
    evidence_ref: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    if status not in ACCEPTED_REVIEW_STATUS:
        raise StateError(f"invalid review status: {status}")
    if blocking_findings < 0:
        raise StateError("blocking_findings cannot be negative")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    if new_state["review"]["required"] and status == "not_required":
        raise StateError("review is required for this flow")
    new_state["review"]["status"] = status
    new_state["review"]["blocking_findings"] = blocking_findings
    if evidence_ref:
        new_state["review"]["evidence"].append(evidence_ref)
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "REVIEW_RECORDED",
        actor,
        status=status,
        blocking_findings=blocking_findings,
        evidence_ref=evidence_ref,
    )
    return _commit(state_path, new_state, event, expected_revision)


def set_execution_snapshot(
    state_path: pathlib.Path,
    snapshot_id: str,
    actor: str,
    reason: str,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    old = new_state.get("execution_snapshot_id")
    new_state["execution_snapshot_id"] = snapshot_id
    if new_state["verification"].get("snapshot_id") != snapshot_id:
        new_state["verification"]["fresh"] = False
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "EXECUTION_SNAPSHOT_UPDATED",
        actor,
        old_snapshot_id=old,
        snapshot_id=snapshot_id,
        reason=reason,
    )
    return _commit(state_path, new_state, event, expected_revision)




def set_flow_profile(
    state_path: pathlib.Path,
    flow_profile: str,
    actor: str,
    evidence_ref: str,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Reconcile a newly computed Decision Engine flow into durable execution state."""
    flow = flow_profile.upper()
    if flow not in FLOW_RANK:
        raise StateError(f"invalid flow_profile: {flow_profile}")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    old = state["flow_profile"]
    if old == flow:
        return state
    new_state = copy.deepcopy(state)
    new_state["flow_profile"] = flow
    # Raising rigor to STANDARD/DEEP activates review. Lowering flow does not silently
    # erase a review obligation already established by prior evidence/policy.
    if FLOW_RANK[flow] >= FLOW_RANK["STANDARD"]:
        if not new_state["review"].get("required"):
            new_state["review"]["required"] = True
            if new_state["review"].get("status") == "not_required":
                new_state["review"]["status"] = "pending"
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "FLOW_PROFILE_UPDATED",
        actor,
        old_flow_profile=old,
        flow_profile=flow,
        evidence_ref=evidence_ref,
    )
    return _commit(state_path, new_state, event, expected_revision)

def attach_analysis(
    state_path: pathlib.Path,
    actor: str,
    analysis_snapshot_id: str,
    semantic_impact_ref: str,
    work_facts_ref: str,
    decision_ref: str,
    verification_plan_ref: str,
    provider: str = "codebase-memory-mcp",
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Attach V6 analysis artifacts without claiming their conclusions as native SDD state."""
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    old = copy.deepcopy(new_state.get("analysis") or {})
    old_execution_snapshot = new_state.get("execution_snapshot_id")
    new_state["analysis"] = {
        "semantic_impact_ref": semantic_impact_ref,
        "work_facts_ref": work_facts_ref,
        "decision_ref": decision_ref,
        "verification_plan_ref": verification_plan_ref,
        "analysis_snapshot_id": analysis_snapshot_id,
        "provider": provider,
        "updated_at": utc_now(),
    }
    # V6 analysis snapshot binds current code/evidence. Attaching a different snapshot
    # therefore advances the execution snapshot and invalidates stale final verification.
    if old_execution_snapshot != analysis_snapshot_id:
        new_state["execution_snapshot_id"] = analysis_snapshot_id
        if new_state["verification"].get("snapshot_id") != analysis_snapshot_id:
            new_state["verification"]["fresh"] = False
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "ANALYSIS_ATTACHED",
        actor,
        old_analysis_snapshot_id=old.get("analysis_snapshot_id"),
        old_execution_snapshot_id=old_execution_snapshot,
        analysis_snapshot_id=analysis_snapshot_id,
        semantic_impact_ref=semantic_impact_ref,
        work_facts_ref=work_facts_ref,
        decision_ref=decision_ref,
        verification_plan_ref=verification_plan_ref,
        provider=provider,
    )
    return _commit(state_path, new_state, event, expected_revision)

def record_verification(
    state_path: pathlib.Path,
    status: str,
    actor: str,
    snapshot_id: str,
    evidence_ref: str,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    if status not in ACCEPTED_VERIFICATION_STATUS:
        raise StateError(f"invalid verification status: {status}")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    current = new_state.get("execution_snapshot_id")
    fresh = status == "passed" and current is not None and current == snapshot_id
    new_state["verification"].update(
        {
            "status": status,
            "snapshot_id": snapshot_id,
            "fresh": fresh,
            "verified_at": utc_now(),
        }
    )
    new_state["verification"]["evidence"].append(evidence_ref)
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "VERIFICATION_RECORDED",
        actor,
        status=status,
        snapshot_id=snapshot_id,
        fresh=fresh,
        evidence_ref=evidence_ref,
    )
    return _commit(state_path, new_state, event, expected_revision)


def assign_role(
    state_path: pathlib.Path,
    role: str,
    assignee: str,
    actor: str,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    new_state["assignments"][role] = {
        "assignee": assignee,
        "assigned_at": utc_now(),
        "assigned_by": actor,
    }
    event = _new_event(state, "ROLE_ASSIGNED", actor, role=role, assignee=assignee)
    return _commit(state_path, new_state, event, expected_revision)


def set_cursor(
    state_path: pathlib.Path,
    actor: str,
    task_id: Optional[str] = None,
    task_title: Optional[str] = None,
    next_action: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    if task_id is not None:
        new_state["cursor"]["current_task_id"] = task_id
    if task_title is not None:
        new_state["cursor"]["current_task_title"] = task_title
    if next_action is not None:
        new_state["cursor"]["next_action"] = next_action
    event = _new_event(state, "CURSOR_UPDATED", actor, cursor=copy.deepcopy(new_state["cursor"]))
    return _commit(state_path, new_state, event, expected_revision)


def sync_native(
    state_path: pathlib.Path,
    phase: str,
    status: str,
    actor: str,
    native_state_ref: str,
    native_revision: Optional[str] = None,
    completed: Optional[int] = None,
    total: Optional[int] = None,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    if state["authority"]["mode"] not in {"native", "hybrid"}:
        raise StateError("sync_native is only valid for native/hybrid authority")
    if phase not in PHASES or status not in STATUSES:
        raise StateError("invalid native phase/status projection")
    new_state = copy.deepcopy(state)
    new_state["phase"] = phase
    new_state["status"] = status
    if completed is not None or total is not None:
        if completed is None or total is None or completed < 0 or total < 0 or completed > total:
            raise StateError("native progress requires valid completed and total")
        new_state["work_item"]["progress"] = {"completed": completed, "total": total}
    new_state["authority"]["native_state_ref"] = native_state_ref
    new_state["authority"]["last_native_sync"] = {
        "at": utc_now(),
        "native_revision": native_revision,
        "native_state_ref": native_state_ref,
    }
    new_state["cursor"]["next_action"] = compute_next_action(new_state)
    event = _new_event(
        state,
        "NATIVE_STATE_SYNCED",
        actor,
        phase=phase,
        status=status,
        native_state_ref=native_state_ref,
        native_revision=native_revision,
        progress=new_state["work_item"]["progress"],
    )
    return _commit(state_path, new_state, event, expected_revision)


def compute_next_action(state: Dict[str, Any]) -> str:
    active = [b for b in state.get("blockers", []) if b.get("resolved_at") is None]
    if active:
        return f"resolve_blocker:{active[0]['id']}"
    r = state.get("readiness", {})
    phase = state.get("phase")
    if phase in {"discovery", "specification", "design", "planning"}:
        if not r.get("sdd_ready"):
            return "advance_native_sdd_to_ready"
        if r.get("behavior_change") is True and not r.get("acceptance_criteria_present"):
            return "define_acceptance_criteria"
        return "transition_to_implementation"
    if phase == "implementation":
        if not r.get("implementation_tasks_complete"):
            task = state.get("cursor", {}).get("current_task_id")
            return f"implement_task:{task}" if task else "implement_next_task"
        if state.get("review", {}).get("required"):
            return "transition_to_review"
        return "transition_to_verification"
    if phase == "review":
        review = state.get("review", {})
        if review.get("status") != "passed" or review.get("blocking_findings", 0):
            return "complete_required_review"
        return "transition_to_verification"
    if phase == "verification":
        for name, gate in state.get("quality_gates", {}).items():
            if gate.get("required") and gate.get("status") != "passed":
                return f"run_required_gate:{name}"
        ver = state.get("verification", {})
        if ver.get("status") != "passed" or not ver.get("fresh"):
            return "run_fresh_final_verification"
        if not r.get("acceptance_satisfied"):
            return "verify_acceptance_criteria"
        return "transition_to_closed"
    if phase == "release":
        return "complete_release_or_transition_to_closed"
    if phase == "closed":
        return "none"
    return "inspect_state"



def completion_status(state: Dict[str, Any]) -> Dict[str, Any]:
    failures = transition_guard(state, "closed", "completed")
    native_complete = state.get("phase") == "closed" and state.get("status") == "completed"
    governance_complete = not failures
    return {
        "native_or_canonical_closed": native_complete,
        "governance_close_ready": governance_complete,
        "done": native_complete and governance_complete,
        "close_guard_failures": failures,
    }

def resume_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "work_item": state["work_item"],
        "iteration": state["iteration"],
        "authority": state["authority"],
        "flow_profile": state["flow_profile"],
        "phase": state["phase"],
        "status": state["status"],
        "blocked": state["blocked"],
        "active_blockers": [b for b in state["blockers"] if b.get("resolved_at") is None],
        "assignments": state["assignments"],
        "cursor": state["cursor"],
        "next_action": compute_next_action(state),
        "readiness": state["readiness"],
        "quality_gates": state["quality_gates"],
        "review": state["review"],
        "verification": state["verification"],
        "execution_snapshot_id": state["execution_snapshot_id"],
        "analysis": state.get("analysis", {}),
        "completion": completion_status(state),
        "revision": state["revision"],
    }


def _bool_arg(value: str) -> bool:
    v = value.strip().lower()
    if v in {"true", "1", "yes", "y"}:
        return True
    if v in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", default=".orchestrator/execution-state.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init")
    x.add_argument("--work-id", required=True)
    x.add_argument("--title", required=True)
    x.add_argument("--flow", required=True)
    x.add_argument("--provider", default="generic")
    x.add_argument("--authority-mode", default="orchestrator")
    x.add_argument("--work-type", default="change")
    x.add_argument("--native-state-ref")
    x.add_argument("--iteration-type", default="continuous")
    x.add_argument("--iteration-id")
    x.add_argument("--actor", default="orchestrator")

    sub.add_parser("show")
    sub.add_parser("resume")
    sub.add_parser("validate")

    x = sub.add_parser("transition")
    x.add_argument("--phase", required=True)
    x.add_argument("--status", default="in_progress")
    x.add_argument("--actor", required=True)
    x.add_argument("--reason", required=True)
    x.add_argument("--expected-revision", type=int)
    x.add_argument("--native-confirmed", action="store_true")
    x.add_argument("--evidence-ref")

    x = sub.add_parser("readiness")
    x.add_argument("--key", required=True)
    x.add_argument("--value", type=_bool_arg, required=True)
    x.add_argument("--actor", required=True)
    x.add_argument("--evidence-ref", required=True)
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("progress")
    x.add_argument("--completed", type=int, required=True)
    x.add_argument("--total", type=int, required=True)
    x.add_argument("--actor", required=True)
    x.add_argument("--evidence-ref", required=True)
    x.add_argument("--expected-revision", type=int)
    x.add_argument("--native-confirmed", action="store_true")

    x = sub.add_parser("block")
    x.add_argument("--type", required=True)
    x.add_argument("--reason", required=True)
    x.add_argument("--actor", required=True)
    x.add_argument("--evidence-ref")
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("unblock")
    x.add_argument("--blocker-id", required=True)
    x.add_argument("--resolution", required=True)
    x.add_argument("--actor", required=True)
    x.add_argument("--evidence-ref")
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("gate")
    x.add_argument("--name", required=True)
    x.add_argument("--required", type=_bool_arg, required=True)
    x.add_argument("--status", required=True)
    x.add_argument("--stage", default="verification")
    x.add_argument("--actor", required=True)
    x.add_argument("--evidence-ref")
    x.add_argument("--command")
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("review")
    x.add_argument("--status", required=True)
    x.add_argument("--blocking-findings", type=int, default=0)
    x.add_argument("--actor", required=True)
    x.add_argument("--evidence-ref")
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("snapshot")
    x.add_argument("--id", required=True)
    x.add_argument("--actor", required=True)
    x.add_argument("--reason", required=True)
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("verify")
    x.add_argument("--status", required=True)
    x.add_argument("--snapshot-id", required=True)
    x.add_argument("--evidence-ref", required=True)
    x.add_argument("--actor", required=True)
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("flow")
    x.add_argument("--flow", required=True)
    x.add_argument("--actor", required=True)
    x.add_argument("--evidence-ref", required=True)
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("analysis")
    x.add_argument("--analysis-snapshot-id", required=True)
    x.add_argument("--semantic-impact-ref", required=True)
    x.add_argument("--work-facts-ref", required=True)
    x.add_argument("--decision-ref", required=True)
    x.add_argument("--verification-plan-ref", required=True)
    x.add_argument("--provider", default="codebase-memory-mcp")
    x.add_argument("--actor", required=True)
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("assign")
    x.add_argument("--role", required=True)
    x.add_argument("--assignee", required=True)
    x.add_argument("--actor", required=True)
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("cursor")
    x.add_argument("--task-id")
    x.add_argument("--task-title")
    x.add_argument("--next-action")
    x.add_argument("--actor", required=True)
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("sync-native")
    x.add_argument("--phase", required=True)
    x.add_argument("--status", required=True)
    x.add_argument("--native-state-ref", required=True)
    x.add_argument("--native-revision")
    x.add_argument("--completed", type=int)
    x.add_argument("--total", type=int)
    x.add_argument("--actor", required=True)
    x.add_argument("--expected-revision", type=int)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    path = pathlib.Path(args.state)
    try:
        if args.command == "init":
            state = create_state(
                args.work_id,
                args.title,
                args.flow,
                provider=args.provider,
                authority_mode=args.authority_mode,
                work_type=args.work_type,
                native_state_ref=args.native_state_ref,
                iteration_type=args.iteration_type,
                iteration_id=args.iteration_id,
            )
            result = initialize(path, state, args.actor)
        elif args.command == "show":
            result = _load(path)
        elif args.command == "resume":
            result = resume_summary(_load(path))
        elif args.command == "validate":
            result = {"status": "VALID", "revision": _load(path)["revision"]}
        elif args.command == "transition":
            result = transition(path, args.phase, args.status, args.actor, args.reason, args.expected_revision, args.native_confirmed, args.evidence_ref)
        elif args.command == "readiness":
            result = set_readiness(path, args.key, args.value, args.actor, args.evidence_ref, args.expected_revision)
        elif args.command == "progress":
            result = set_progress(path, args.completed, args.total, args.actor, args.evidence_ref, args.expected_revision, args.native_confirmed)
        elif args.command == "block":
            result = add_blocker(path, args.type, args.reason, args.actor, args.evidence_ref, args.expected_revision)
        elif args.command == "unblock":
            result = resolve_blocker(path, args.blocker_id, args.resolution, args.actor, args.evidence_ref, args.expected_revision)
        elif args.command == "gate":
            result = record_gate(path, args.name, args.required, args.status, args.actor, args.stage, args.evidence_ref, args.command, args.expected_revision)
        elif args.command == "review":
            result = record_review(path, args.status, args.actor, args.blocking_findings, args.evidence_ref, args.expected_revision)
        elif args.command == "snapshot":
            result = set_execution_snapshot(path, args.id, args.actor, args.reason, args.expected_revision)
        elif args.command == "verify":
            result = record_verification(path, args.status, args.actor, args.snapshot_id, args.evidence_ref, args.expected_revision)
        elif args.command == "flow":
            result = set_flow_profile(path, args.flow, args.actor, args.evidence_ref, args.expected_revision)
        elif args.command == "analysis":
            result = attach_analysis(
                path, args.actor, args.analysis_snapshot_id, args.semantic_impact_ref,
                args.work_facts_ref, args.decision_ref, args.verification_plan_ref,
                args.provider, args.expected_revision,
            )
        elif args.command == "assign":
            result = assign_role(path, args.role, args.assignee, args.actor, args.expected_revision)
        elif args.command == "cursor":
            result = set_cursor(path, args.actor, args.task_id, args.task_title, args.next_action, args.expected_revision)
        elif args.command == "sync-native":
            result = sync_native(path, args.phase, args.status, args.actor, args.native_state_ref, args.native_revision, args.completed, args.total, args.expected_revision)
        else:  # pragma: no cover
            raise StateError("unknown command")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (StateError, RevisionConflict, TransitionDenied, NativeAuthorityRequired) as exc:
        print(json.dumps({"status": "ERROR", "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
