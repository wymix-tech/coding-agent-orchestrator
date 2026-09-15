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
import time
import uuid

import action_guard
import repository_snapshot
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

SCHEMA_VERSION = 1
PHASES = list(action_guard.PHASES)
STATUSES = action_guard.STATUSES
FLOW_RANK = {"TRIVIAL": 0, "FAST": 1, "STANDARD": 2, "DEEP": 3}
ACCEPTED_GATE_STATUS = {"pending", "passed", "failed", "skipped", "not_required"}
ACCEPTED_REVIEW_STATUS = {"pending", "passed", "failed", "not_required"}
ACCEPTED_VERIFICATION_STATUS = {"pending", "passed", "failed"}
ACCEPTED_OBLIGATION_STATUS = {"active", "superseded", "not_applicable", "waived"}
AUTHORITY_MODES = {"native", "hybrid", "orchestrator"}
PROVIDERS = {"bmad", "openspec", "generic", "other"}


class StateError(ValueError):
    pass


class RevisionConflict(StateError):
    pass


class TransitionDenied(StateError):
    def __init__(self, reasons: Iterable[str], authorization: Optional[Dict[str, Any]] = None):
        self.authorization = authorization
        self.reasons = list(reasons)
        super().__init__("transition denied: " + "; ".join(self.reasons))


class NativeAuthorityRequired(StateError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _serialize(data: Any, path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        if yaml is None:
            raise StateError("PyYAML is required for YAML state files")
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Atomically replace one file without reusing a shared temporary filename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _dump(data: Any, path: pathlib.Path) -> None:
    _atomic_write_text(path, _serialize(data, path))


def _load_raw(path: pathlib.Path) -> Dict[str, Any]:
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


def _load(path: pathlib.Path) -> Dict[str, Any]:
    # Any interrupted state/history commit is completed before callers observe state.
    _recover_transaction(path)
    return _load_raw(path)


def _history_path(state_path: pathlib.Path) -> pathlib.Path:
    stem = state_path.stem
    return state_path.with_name(stem.replace("execution-state", "execution-history") + ".jsonl")


def _history_event_ids(state_path: pathlib.Path) -> set[str]:
    hp = _history_path(state_path)
    if not hp.exists():
        return set()
    out: set[str] = set()
    for line in hp.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict) and item.get("event_id"):
            out.add(str(item["event_id"]))
    return out


def _append_history(state_path: pathlib.Path, event: Dict[str, Any]) -> None:
    """Append an event exactly once and fsync it before reporting success."""
    if str(event.get("event_id")) in _history_event_ids(state_path):
        return
    hp = _history_path(state_path)
    hp.parent.mkdir(parents=True, exist_ok=True)
    with hp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


LOCK_TIMEOUT_SECONDS = float(os.environ.get("ORCHESTRATOR_STATE_LOCK_TIMEOUT", "5"))


def _lock_path(state_path: pathlib.Path) -> pathlib.Path:
    return state_path.with_name(f".{state_path.name}.lock")


def _journal_path(state_path: pathlib.Path) -> pathlib.Path:
    return state_path.with_name(f".{state_path.name}.transaction.json")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


class _StateFileLock:
    """Portable cross-process lock based on atomic directory creation.

    The lock contains owner metadata. If the owner process no longer exists, the
    stale lock is reclaimed. This intentionally protects only the short durable
    commit/recovery boundary, never CBM/test execution.
    """
    def __init__(self, state_path: pathlib.Path, timeout: float = LOCK_TIMEOUT_SECONDS):
        self.path = _lock_path(state_path)
        self.timeout = timeout
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.mkdir(parents=False)
                owner = {"pid": os.getpid(), "acquired_at": utc_now()}
                _atomic_write_text(self.path / "owner.json", json.dumps(owner, sort_keys=True) + "\n")
                self.acquired = True
                return self
            except FileExistsError:
                owner_path = self.path / "owner.json"
                stale = False
                try:
                    owner = json.loads(owner_path.read_text(encoding="utf-8"))
                    stale = not _pid_alive(int(owner.get("pid", -1)))
                except Exception:
                    # A just-created lock can briefly exist before owner metadata. Give it
                    # a small grace period; old malformed locks are reclaimable.
                    try:
                        stale = (time.time() - self.path.stat().st_mtime) > 1.0
                    except OSError:
                        stale = False
                if stale:
                    try:
                        for child in self.path.iterdir():
                            child.unlink(missing_ok=True)
                        self.path.rmdir()
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise StateError(f"state lock timeout: {self.path}")
                time.sleep(0.03)

    def __exit__(self, exc_type, exc, tb):
        if self.acquired:
            try:
                for child in self.path.iterdir():
                    child.unlink(missing_ok=True)
                self.path.rmdir()
            except FileNotFoundError:
                pass
            self.acquired = False


def _fault(point: str) -> None:
    if os.environ.get("ORCHESTRATOR_TXN_CRASH_AT") == point:
        raise RuntimeError(f"injected transaction crash at {point}")


def _write_journal(state_path: pathlib.Path, journal: Dict[str, Any]) -> None:
    _atomic_write_text(_journal_path(state_path), json.dumps(journal, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _recover_transaction_locked(state_path: pathlib.Path) -> None:
    jp = _journal_path(state_path)
    if not jp.exists():
        return
    try:
        txn = json.loads(jp.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StateError(f"cannot recover transaction journal {jp}: {exc}") from exc
    new_state = txn.get("state")
    event = txn.get("event")
    if not isinstance(new_state, dict) or not isinstance(event, dict):
        raise StateError(f"invalid transaction journal: {jp}")
    validate_state(new_state)
    target_rev = int(event.get("revision_after", new_state.get("revision", -1)))
    current_rev = -1
    if state_path.exists():
        current = _load_raw(state_path)
        current_rev = int(current.get("revision", -1))
        if current_rev > target_rev:
            raise StateError(f"transaction journal revision {target_rev} is behind current state {current_rev}")
        if current_rev == target_rev and current != new_state:
            raise StateError("transaction journal conflicts with current state at same revision")
    if current_rev < target_rev:
        _dump(new_state, state_path)
    _append_history(state_path, event)
    jp.unlink(missing_ok=True)


def _recover_transaction(state_path: pathlib.Path) -> None:
    jp = _journal_path(state_path)
    if not jp.exists():
        return
    with _StateFileLock(state_path):
        _recover_transaction_locked(state_path)


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
    requirement_id: Optional[str] = None,
    requirement_revision: Optional[str] = None,
    requirement_source_ref: Optional[str] = None,
    native_work_item_id: Optional[str] = None,
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
        "enforcement",
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
            "requirement_id": requirement_id,
            "requirement_revision": requirement_revision,
            "requirement_source_ref": requirement_source_ref,
            "native_work_item_id": native_work_item_id,
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
        "verification_obligations": {},
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
        "enforcement": {
            "enabled": False,
            "dirty": False,
            "semantic_fresh": None,
            "context_fresh": None,
            "policy_fresh": None,
            "last_mutation_snapshot_id": None,
            "last_mutation_at": None,
            "last_mutation_paths": [],
            "last_host": None,
            "last_event": None,
        },
        "analysis": {
            "semantic_impact_ref": None,
            "work_facts_ref": None,
            "decision_ref": None,
            "verification_plan_ref": None,
            "policy_plan_ref": None,
            "policy_evaluation_ref": None,
            "policy_context_ref": None,
            "policy_snapshot_id": None,
            "context_manifest_ref": None,
            "context_pack_ref": None,
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
    enforcement = state.get("enforcement")
    if enforcement is not None:
        if not isinstance(enforcement, dict):
            raise StateError("enforcement must be an object")
        if not isinstance(enforcement.get("enabled", False), bool) or not isinstance(enforcement.get("dirty", False), bool):
            raise StateError("enforcement enabled/dirty must be boolean")
    ver = state.get("verification") or {}
    if ver.get("status") not in ACCEPTED_VERIFICATION_STATUS:
        raise StateError("verification.status is invalid")
    for name, gate in (state.get("quality_gates") or {}).items():
        if gate.get("status") not in ACCEPTED_GATE_STATUS:
            raise StateError(f"quality gate {name!r} has invalid status")
        if not isinstance(gate.get("required"), bool):
            raise StateError(f"quality gate {name!r} required must be boolean")
    obligations = state.get("verification_obligations") or {}
    if not isinstance(obligations, dict):
        raise StateError("verification_obligations must be an object")
    for oid, obligation in obligations.items():
        if not isinstance(obligation, dict) or obligation.get("status") not in ACCEPTED_OBLIGATION_STATUS:
            raise StateError(f"verification obligation {oid!r} has invalid status")
        if not isinstance(obligation.get("required", False), bool):
            raise StateError(f"verification obligation {oid!r} required must be boolean")


def _require_revision(state: Dict[str, Any], expected_revision: Optional[int]) -> None:
    if expected_revision is not None and state["revision"] != expected_revision:
        raise RevisionConflict(
            f"stale revision: expected {expected_revision}, current {state['revision']}"
        )


def _durable_transaction(state_path: pathlib.Path, new_state: Dict[str, Any], event: Dict[str, Any]) -> None:
    txn = {
        "transaction_id": uuid.uuid4().hex,
        "prepared_at": utc_now(),
        "state_path": str(state_path),
        "history_path": str(_history_path(state_path)),
        "state": copy.deepcopy(new_state),
        "event": copy.deepcopy(event),
    }
    _write_journal(state_path, txn)
    _fault("after_journal")
    _dump(new_state, state_path)
    _fault("after_state")
    _append_history(state_path, event)
    _fault("after_history")
    _journal_path(state_path).unlink(missing_ok=True)


def _commit(
    state_path: pathlib.Path,
    state: Dict[str, Any],
    event: Dict[str, Any],
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    with _StateFileLock(state_path):
        _recover_transaction_locked(state_path)
        current = _load_raw(state_path)
        _require_revision(current, expected_revision)
        if current["revision"] != event["revision_before"]:
            raise RevisionConflict(
                f"event was built from revision {event['revision_before']}, current is {current['revision']}"
            )
        state["revision"] = current["revision"] + 1
        state["updated_at"] = utc_now()
        event["revision_after"] = state["revision"]
        event.setdefault("commit_id", uuid.uuid4().hex)
        validate_state(state)
        _durable_transaction(state_path, state, event)
        return state


def initialize(state_path: pathlib.Path, state: Dict[str, Any], actor: str = "orchestrator") -> Dict[str, Any]:
    with _StateFileLock(state_path):
        _recover_transaction_locked(state_path)
        if state_path.exists():
            raise StateError(f"state already exists: {state_path}")
        validate_state(state)
        event = _new_event(state, "STATE_INITIALIZED", actor, initial_state=copy.deepcopy(state))
        event["revision_after"] = state["revision"]
        event.setdefault("commit_id", uuid.uuid4().hex)
        _durable_transaction(state_path, state, event)
        return state


def required_gate_failures(state: Dict[str, Any], repo: Optional[pathlib.Path] = None) -> List[str]:
    evidence = action_guard.collect_evidence(repo, state) if repo is not None else {}
    gates = state.get("quality_gates") or {}
    return [f"required gate {name} is {(gates.get(name) or {}).get('status', 'missing')}"
            for name in action_guard.required_gates(state, evidence)
            if (gates.get(name) or {}).get("status") != "passed"]


def transition_guard(state: Dict[str, Any], to_phase: str, to_status: str = "in_progress",
                     repo: Optional[pathlib.Path] = None, native_confirmed: bool = False) -> List[str]:
    decision = action_guard.authorize(repo, state, "close" if to_phase == "closed" else "advance",
                                      target_phase=to_phase, target_status=to_status, native_confirmed=native_confirmed)
    return [r["message"] for r in decision["reasons"]]


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
    state = _load(state_path) if state_path.exists() else None
    if state is not None:
        _require_revision(state, expected_revision)
    authorization = action_guard.authorize(
        repository_snapshot.repo_from_state(state_path), state,
        "close" if to_phase == "closed" else "advance", target_phase=to_phase,
        target_status=to_status, native_confirmed=native_confirmed,
    )
    if "NATIVE_AUTHORITY_REQUIRED" in authorization["reason_codes"]:
        error = NativeAuthorityRequired("native authority owns phase/status; use the native adapter")
        error.authorization = authorization
        raise error
    if not authorization["allowed"]:
        raise TransitionDenied([r["message"] for r in authorization["reasons"]], authorization)
    before = {"phase": state["phase"], "status": state["status"]}
    new_state = copy.deepcopy(state)
    new_state["phase"] = to_phase
    new_state["status"] = to_status
    if to_phase == "closed" and to_status == "completed":
        wi = new_state.get("work_item") or {}
        analysis = new_state.get("analysis") or {}
        new_state["completion_record"] = {
            "status": "completed",
            "closed_at": utc_now(),
            "closed_by": actor,
            "work_item_id": wi.get("id"),
            "requirement_id": wi.get("requirement_id"),
            "requirement_revision": wi.get("requirement_revision"),
            "execution_snapshot_id": new_state.get("execution_snapshot_id"),
            "analysis_snapshot_id": analysis.get("analysis_snapshot_id"),
            "repository_snapshot_id": analysis.get("repository_snapshot_id"),
            "authorization": {
                "decision": authorization.get("decision"),
                "reason_codes": authorization.get("reason_codes") or [],
                "state_revision": authorization.get("state_revision"),
            },
        }
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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


def revise_work_item(
    state_path: pathlib.Path,
    requirement_id: str,
    requirement_revision: str,
    actor: str,
    evidence_ref: str,
    expected_revision: Optional[int] = None,
    requirement_source_ref: Optional[str] = None,
    native_work_item_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply an explicit revision of the active requirement and invalidate derived evidence."""
    state = _load(state_path)
    _require_revision(state, expected_revision)
    current = state.get("work_item") or {}
    if current.get("requirement_id") not in {None, requirement_id}:
        raise StateError("cannot revise active work item to a different requirement identity")
    if current.get("requirement_revision") == requirement_revision:
        return state
    new_state = copy.deepcopy(state)
    wi = new_state["work_item"]
    old_revision = wi.get("requirement_revision")
    wi["requirement_id"] = requirement_id
    wi["requirement_revision"] = requirement_revision
    wi["requirement_source_ref"] = requirement_source_ref
    wi["native_work_item_id"] = native_work_item_id
    wi["progress"] = {"completed": 0, "total": 0}
    new_state["readiness"].update({
        "sdd_ready": False,
        "acceptance_criteria_present": False,
        "implementation_tasks_complete": False,
        "acceptance_satisfied": False,
    })
    new_state["quality_gates"] = {}
    new_state["verification_obligations"] = {}
    new_state["review"] = _default_review(new_state["flow_profile"])
    new_state["verification"] = {
        "required": True, "status": "pending", "snapshot_id": None, "fresh": False,
        "evidence": [], "verified_at": None,
    }
    analysis = new_state.get("analysis") or {}
    for key in list(analysis):
        if key in {"provider"}:
            continue
        if isinstance(analysis.get(key), bool):
            analysis[key] = False
        elif isinstance(analysis.get(key), dict):
            analysis[key] = {}
        else:
            analysis[key] = None
    new_state["analysis"] = analysis
    enf = new_state.get("enforcement") or {}
    enf.update({"dirty": True, "semantic_fresh": False, "context_fresh": False, "policy_fresh": False, "last_event": "requirement_revision"})
    new_state["enforcement"] = enf
    if new_state.get("authority", {}).get("mode") != "native":
        new_state["phase"] = "discovery"
        new_state["status"] = "in_progress"
    new_state["cursor"] = {"current_task_id": None, "current_task_title": None, "next_action": "run_semantic_intake"}
    event = _new_event(
        state, "REQUIREMENT_REVISED", actor, requirement_id=requirement_id,
        old_requirement_revision=old_revision, requirement_revision=requirement_revision,
        evidence_ref=evidence_ref,
    )
    return _commit(state_path, new_state, event, expected_revision)


def _evidence_module():
    """Load the evidence verifier; the sibling module may not be on sys.path."""
    try:
        import evidence_provenance
        return evidence_provenance
    except ImportError:  # pragma: no cover - import shim only
        import importlib.util as _ilu
        import pathlib as _pl
        _spec = _ilu.spec_from_file_location(
            "evidence_provenance", _pl.Path(__file__).resolve().parent / "evidence_provenance.py")
        module = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(module)
        return module


def _verify_evidence_record(state_path: pathlib.Path, record: dict, *,
                            work_item_id: str | None = None) -> Dict[str, Any]:
    """Verify a bound evidence record; invalid evidence never reaches the state."""
    ep = _evidence_module()
    repo = repository_snapshot.repo_from_state(state_path)
    result = ep.revalidate(repo, record, work_item_id=work_item_id)
    if result.get("validation_status") == "invalid":
        raise StateError(
            f"evidence rejected: {result.get('reason_code')} (evidence_id={result.get('evidence_id')})")
    return result


def _bind_report(state_path: pathlib.Path, status: str, report_path: Optional[str],
                 exit_code: Optional[int], argv: Optional[list]) -> Optional[Dict[str, Any]]:
    """Bind the report that was actually produced. A real failure is recorded as a failure."""
    if not report_path and exit_code is None:
        return None
    repo = repository_snapshot.repo_from_state(state_path)
    reported_status = None
    digest = None
    if report_path:
        path = pathlib.Path(report_path)
        path = path if path.is_absolute() else repo / path
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StateError(f"EVIDENCE_REPORT_UNPARSABLE: {exc}")
        if not isinstance(doc, dict):
            raise StateError("EVIDENCE_REPORT_UNPARSABLE: report must be a JSON object")
        reported_status = doc.get("status")
        if exit_code is None:
            exit_code = doc.get("exit_code")
        try:
            digest = repository_snapshot.digest_path(path)
        except OSError:
            digest = None
    if status == "passed" and (reported_status not in (None, "passed") or (exit_code not in (None, 0))):
        raise StateError(
            f"EVIDENCE_REPORT_CONTRADICTION: report status={reported_status!r} exit_code={exit_code!r} "
            "cannot record a passed result")
    return {
        "path": report_path,
        "status": reported_status,
        "exit_code": exit_code,
        "digest": digest,
        "argv": list(argv or []),
    }


def set_readiness(
    state_path: pathlib.Path,
    key: str,
    value: bool,
    actor: str,
    evidence_ref: str,
    expected_revision: Optional[int] = None,
    evidence_record: Optional[Dict[str, Any]] = None,
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
    ep = _evidence_module()
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    if evidence_record is not None:
        result = _verify_evidence_record(state_path, evidence_record,
                                         work_item_id=state.get("work_item_id"))
        if not ep.readiness_kind_allowed(key, result.get("kind") or ""):
            raise StateError(
                f"readiness key {key} cannot be satisfied by a {result.get('kind')!r} claim; "
                "each key has its own source rule")
        records = new_state.setdefault("evidence", {}).setdefault("records", [])
        stored = dict(evidence_record, evidence_id=result["evidence_id"])
        if stored not in records:
            records.append(stored)
        new_state.setdefault("readiness_evidence", {})[key] = {
            "evidence_id": result["evidence_id"],
            "kind": result.get("kind"),
            "validation_status": result.get("validation_status"),
            "outcome": result.get("outcome"),
            "recorded_at": utc_now(),
        }
    new_state["readiness"][key] = value
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
    event = _new_event(
        state,
        "UNBLOCKED" if not new_state["blocked"] else "BLOCKER_RESOLVED",
        actor,
        blocker_id=blocker_id,
        resolution=resolution,
        evidence_ref=evidence_ref,
    )
    return _commit(state_path, new_state, event, expected_revision)


def reconcile_verification_obligations(
    state_path: pathlib.Path,
    plan: Dict[str, Any],
    actor: str,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Add newly required obligations without silently deleting older active ones.

    Plan shrinkage never erases an obligation. Removal requires dispose_verification_obligation
    with an explicit audited disposition.
    """
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    obligations = new_state.setdefault("verification_obligations", {})
    wi = new_state.get("work_item") or {}
    desired: list[Dict[str, Any]] = []
    for item in plan.get("items", []):
        if item.get("required_by_impact"):
            desired.append({
                "id": item.get("obligation_id") or f"obl-impact-{hashlib.sha256(str(item.get('gate_name')).encode()).hexdigest()[:12]}",
                "source": item.get("source") or "semantic_impact",
                "gate_name": item.get("gate_name") or "impact:" + str(item.get("kind")),
                "required": True,
                "reason": item.get("reason"),
                "source_ref": item.get("evidence_ref"),
            })
    for item in plan.get("policy_gates", []):
        if item.get("required_by_policy"):
            gate_name = item.get("gate_name") or ("policy:" + str(item.get("rule_id")) + ":" + str(item.get("gate") or item.get("engine") or "policy"))
            desired.append({
                "id": item.get("obligation_id") or f"obl-policy-{hashlib.sha256(str(gate_name).encode()).hexdigest()[:12]}",
                "source": item.get("source") or "engineering_policy",
                "gate_name": gate_name,
                "required": True,
                "reason": f"project policy {item.get('rule_id')}",
                "source_ref": item.get("rule_id"),
            })
    changed = False
    for d in desired:
        oid = str(d["id"])
        existing = obligations.get(oid)
        if existing and existing.get("status") == "active":
            # Refresh descriptive/source fields but never downgrade established requiredness.
            existing.update({k: v for k, v in d.items() if k != "id" and v is not None})
            existing["required"] = bool(existing.get("required") or d.get("required"))
            existing["last_seen_plan_id"] = plan.get("plan_id")
            changed = True
            continue
        obligations[oid] = {
            **d,
            "status": "active",
            "work_item_id": wi.get("id"),
            "requirement_id": wi.get("requirement_id"),
            "requirement_revision": wi.get("requirement_revision"),
            "created_at": utc_now(),
            "created_by": actor,
            "last_seen_plan_id": plan.get("plan_id"),
            "disposition": None,
        }
        changed = True
    if not changed:
        return state
    event = _new_event(state, "VERIFICATION_OBLIGATIONS_RECONCILED", actor,
                       plan_id=plan.get("plan_id"), active_ids=[d["id"] for d in desired])
    return _commit(state_path, new_state, event, expected_revision)


def dispose_verification_obligation(
    state_path: pathlib.Path,
    obligation_id: str,
    disposition: str,
    reason: str,
    authority: str,
    evidence_ref: str,
    actor: str,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    if disposition not in {"superseded", "not_applicable", "waived"}:
        raise StateError("obligation disposition must be superseded, not_applicable, or waived")
    if not reason.strip() or not authority.strip() or not evidence_ref.strip():
        raise StateError("obligation disposition requires reason, authority, and evidence_ref")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    current = (state.get("verification_obligations") or {}).get(obligation_id)
    if not current or current.get("status") != "active":
        raise StateError(f"active verification obligation not found: {obligation_id}")
    new_state = copy.deepcopy(state)
    target = new_state["verification_obligations"][obligation_id]
    target["status"] = disposition
    target["disposition"] = {
        "reason": reason,
        "authority": authority,
        "evidence_ref": evidence_ref,
        "at": utc_now(),
        "by": actor,
    }
    gate_name = target.get("gate_name")
    if gate_name and gate_name in new_state.get("quality_gates", {}):
        gate = new_state["quality_gates"][gate_name]
        gate["required"] = False
        gate["status"] = "not_required"
        gate["disposition"] = copy.deepcopy(target["disposition"])
        gate["updated_at"] = utc_now()
        gate["updated_by"] = actor
    event = _new_event(state, "VERIFICATION_OBLIGATION_DISPOSED", actor,
                       obligation_id=obligation_id, disposition=disposition,
                       reason=reason, authority=authority, evidence_ref=evidence_ref)
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
    report_path: Optional[str] = None,
    exit_code: Optional[int] = None,
    argv: Optional[list] = None,
) -> Dict[str, Any]:
    if status not in ACCEPTED_GATE_STATUS:
        raise StateError(f"invalid gate status: {status}")
    if required and status in {"skipped", "not_required"}:
        raise StateError("a required gate cannot be skipped or marked not_required")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    report = _bind_report(state_path, status, report_path, exit_code, argv)
    new_state = copy.deepcopy(state)
    if (state.get("quality_gates", {}).get(name) or {}).get("required") and not required:
        raise StateError("an established required gate cannot be downgraded by recording a result")
    new_state["quality_gates"][name] = {
        "required": required,
        "status": status,
        "stage": stage,
        "snapshot_id": state.get("execution_snapshot_id"),
        "evidence_snapshot_id": (state.get("analysis") or {}).get("evidence_snapshot_id"),
        "evidence_ref": evidence_ref,
        "command": command,
        "report": report,
        "exit_code": (report or {}).get("exit_code"),
        "updated_at": utc_now(),
        "updated_by": actor,
    }
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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
    new_state["review"]["snapshot_id"] = state.get("execution_snapshot_id")
    new_state["review"]["evidence_snapshot_id"] = (state.get("analysis") or {}).get("evidence_snapshot_id")
    new_state["review"]["status"] = status
    new_state["review"]["blocking_findings"] = blocking_findings
    if evidence_ref:
        new_state["review"]["evidence"].append(evidence_ref)
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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
    policy_plan_ref: Optional[str] = None,
    policy_evaluation_ref: Optional[str] = None,
    policy_context_ref: Optional[str] = None,
    policy_snapshot_id: Optional[str] = None,
    context_manifest_ref: Optional[str] = None,
    context_pack_ref: Optional[str] = None,
    requirement_ref: Optional[str] = None,
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
        "policy_plan_ref": policy_plan_ref,
        "policy_evaluation_ref": policy_evaluation_ref,
        "policy_context_ref": policy_context_ref,
        "policy_snapshot_id": policy_snapshot_id,
        "context_manifest_ref": context_manifest_ref,
        "context_pack_ref": context_pack_ref,
        "analysis_snapshot_id": analysis_snapshot_id,
        "requirement_ref": requirement_ref,
        "provider": provider,
        "updated_at": utc_now(),
    }
    # V6 analysis snapshot binds current code/evidence. Attaching a different snapshot
    # therefore advances the execution snapshot and invalidates stale final verification.
    if old_execution_snapshot != analysis_snapshot_id:
        new_state["execution_snapshot_id"] = analysis_snapshot_id
        if new_state["verification"].get("snapshot_id") != analysis_snapshot_id:
            new_state["verification"]["fresh"] = False
    binding = action_guard.bind_analysis(repository_snapshot.repo_from_state(state_path), new_state["analysis"])
    new_state["analysis"].update(binding)
    if old.get("evidence_snapshot_id") != binding["evidence_snapshot_id"]:
        new_state["verification"]["fresh"] = False
    enforcement = new_state.setdefault("enforcement", {})
    if enforcement.get("enabled"):
        ready = binding["decision_status"] == "CLASSIFIED" and binding["semantic_complete"]
        enforcement["semantic_fresh"] = bool(ready)
        enforcement["policy_fresh"] = True if policy_snapshot_id else enforcement.get("policy_fresh")
        enforcement["context_fresh"] = None
        enforcement["dirty"] = not ready
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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
        policy_plan_ref=policy_plan_ref,
        policy_evaluation_ref=policy_evaluation_ref,
        policy_context_ref=policy_context_ref,
        policy_snapshot_id=policy_snapshot_id,
        context_manifest_ref=context_manifest_ref,
        context_pack_ref=context_pack_ref,
        provider=provider,
    )
    return _commit(state_path, new_state, event, expected_revision)

def set_enforcement_enabled(
    state_path: pathlib.Path, enabled: bool, actor: str, expected_revision: Optional[int] = None
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    enf = new_state.setdefault("enforcement", {})
    enf["enabled"] = bool(enabled)
    event = _new_event(state, "ENFORCEMENT_MODE_UPDATED", actor, enabled=bool(enabled))
    return _commit(state_path, new_state, event, expected_revision)


def mark_enforcement_dirty(
    state_path: pathlib.Path, change_kind: str, paths: List[str], actor: str, snapshot_id: str, reason: str,
    expected_revision: Optional[int] = None
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    enf = new_state.setdefault("enforcement", {})
    enf.update({
        "enabled": True, "dirty": True, "semantic_fresh": False, "context_fresh": False,
        "policy_fresh": False if change_kind in {"policy_change", "external_change"} else enf.get("policy_fresh"),
        "last_mutation_snapshot_id": snapshot_id, "last_mutation_at": utc_now(),
        "last_mutation_paths": list(paths), "last_host": actor, "last_event": change_kind,
    })
    new_state["execution_snapshot_id"] = snapshot_id
    if new_state["verification"].get("snapshot_id") != snapshot_id:
        new_state["verification"]["fresh"] = False
    event = _new_event(state, "ENFORCEMENT_DIRTY", actor, change_kind=change_kind, paths=list(paths), snapshot_id=snapshot_id, reason=reason)
    return _commit(state_path, new_state, event, expected_revision)


def attach_context_snapshot(
    state_path: pathlib.Path, context_snapshot_id: str, context_manifest_ref: str, context_pack_ref: str,
    actor: str, expected_revision: Optional[int] = None
) -> Dict[str, Any]:
    state = _load(state_path)
    _require_revision(state, expected_revision)
    new_state = copy.deepcopy(state)
    analysis = new_state.setdefault("analysis", {})
    analysis["context_manifest_ref"] = context_manifest_ref
    analysis["context_pack_ref"] = context_pack_ref
    analysis["context_snapshot_id"] = context_snapshot_id
    analysis["updated_at"] = utc_now()
    enf = new_state.setdefault("enforcement", {})
    if enf.get("enabled"):
        enf["context_fresh"] = True
        if enf.get("semantic_fresh") is not False and enf.get("policy_fresh") is not False:
            enf["dirty"] = False
    event = _new_event(state, "CONTEXT_SNAPSHOT_ATTACHED", actor, context_snapshot_id=context_snapshot_id, context_manifest_ref=context_manifest_ref, context_pack_ref=context_pack_ref)
    return _commit(state_path, new_state, event, expected_revision)



def record_verification(
    state_path: pathlib.Path,
    status: str,
    actor: str,
    snapshot_id: str,
    evidence_ref: str,
    expected_revision: Optional[int] = None,
    report_path: Optional[str] = None,
    exit_code: Optional[int] = None,
    argv: Optional[list] = None,
) -> Dict[str, Any]:
    if status not in ACCEPTED_VERIFICATION_STATUS:
        raise StateError(f"invalid verification status: {status}")
    state = _load(state_path)
    _require_revision(state, expected_revision)
    report = _bind_report(state_path, status, report_path, exit_code, argv)
    new_state = copy.deepcopy(state)
    current = new_state.get("execution_snapshot_id")
    fresh = status == "passed" and current is not None and current == snapshot_id
    new_state["verification"].update(
        {
            "status": status,
            "snapshot_id": snapshot_id,
            "evidence_snapshot_id": (state.get("analysis") or {}).get("evidence_snapshot_id"),
            "fresh": fresh,
            "verified_at": utc_now(),
            "report": report,
            "exit_code": (report or {}).get("exit_code"),
        }
    )
    new_state["verification"]["evidence"].append(evidence_ref)
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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
    new_state["cursor"]["next_action"] = compute_next_action(new_state, repository_snapshot.repo_from_state(state_path))
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


def compute_next_action(state: Dict[str, Any], repo: Optional[pathlib.Path] = None) -> str:
    facts = action_guard.collect_evidence(repo, state) if repo is not None else None
    return action_guard.next_action(state, facts)


def completion_status(state: Optional[Dict[str, Any]], repo: Optional[pathlib.Path] = None,
                      evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Separate immutable historical completion from current close readiness.

    Once a work item has legally transitioned to closed/completed, later repository changes
    must not resurrect it. Current close readiness still evaluates live evidence and may become
    false after later edits; callers deciding whether a *new* close is allowed must use
    governance_close_ready/authorization, not done.
    """
    facts = evidence if evidence is not None else action_guard.collect_evidence(repo, state)
    decision = action_guard.evaluate(state, "close", evidence=facts)
    canonical_closed = (state or {}).get("phase") == "closed" and (state or {}).get("status") == "completed"
    record = (state or {}).get("completion_record") or {}
    record_matches = bool(
        canonical_closed
        and record.get("status") == "completed"
        and record.get("work_item_id") == (((state or {}).get("work_item") or {}).get("id"))
    )
    # Legacy orchestrator-owned closed/completed states predate completion_record. Preserve
    # those historical facts, but never treat a native authority merely reporting
    # closed/completed as Governance Done without a recorded governance close.
    authority_mode = (((state or {}).get("authority") or {}).get("mode"))
    historical_complete = bool(record_matches or (canonical_closed and authority_mode != "native" and not record))
    return {
        "native_or_canonical_closed": canonical_closed,
        "historically_completed": historical_complete,
        "completion_recorded": record_matches,
        "completion_record": record or None,
        "governance_close_ready": decision["allowed"],
        "done": historical_complete,
        "close_guard_failures": [r["message"] for r in decision["reasons"]],
        "authorization": decision,
    }


def resume_summary(state: Dict[str, Any], repo: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    facts = action_guard.collect_evidence(repo, state)
    return {
        "work_item": state["work_item"], "iteration": state["iteration"],
        "authority": state["authority"], "flow_profile": state["flow_profile"],
        "phase": state["phase"], "status": state["status"], "blocked": bool(active_blockers := action_guard.active_blockers(state)) or state["blocked"],
        "active_blockers": active_blockers, "assignments": state["assignments"],
        "cursor": state["cursor"], "next_action": action_guard.next_action(state, facts),
        "readiness": state["readiness"], "quality_gates": state["quality_gates"],
        "verification_obligations": state.get("verification_obligations", {}),
        "review": state["review"], "verification": state["verification"],
        "execution_snapshot_id": state["execution_snapshot_id"],
        "analysis": state.get("analysis", {}), "enforcement": state.get("enforcement", {}),
        "completion": completion_status(state, repo, facts), "revision": state["revision"],
        "authorization": action_guard.evaluate(state, "mutate_code", evidence=facts),
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
    x.add_argument("--command", dest="gate_command")
    x.add_argument("--expected-revision", type=int)

    x = sub.add_parser("dispose-obligation")
    x.add_argument("--id", required=True)
    x.add_argument("--disposition", required=True, choices=["superseded", "not_applicable", "waived"])
    x.add_argument("--reason", required=True)
    x.add_argument("--authority", required=True)
    x.add_argument("--evidence-ref", required=True)
    x.add_argument("--actor", required=True)
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
    x.add_argument("--policy-plan-ref")
    x.add_argument("--policy-evaluation-ref")
    x.add_argument("--policy-context-ref")
    x.add_argument("--policy-snapshot-id")
    x.add_argument("--context-manifest-ref")
    x.add_argument("--context-pack-ref")
    x.add_argument("--requirement-ref")
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
            result = resume_summary(_load(path), repository_snapshot.repo_from_state(path))
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
            result = record_gate(path, args.name, args.required, args.status, args.actor, args.stage, args.evidence_ref, args.gate_command, args.expected_revision)
        elif args.command == "dispose-obligation":
            result = dispose_verification_obligation(path, args.id, args.disposition, args.reason, args.authority, args.evidence_ref, args.actor, args.expected_revision)
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
                policy_plan_ref=args.policy_plan_ref,
                policy_evaluation_ref=args.policy_evaluation_ref,
                policy_context_ref=args.policy_context_ref,
                policy_snapshot_id=args.policy_snapshot_id,
                context_manifest_ref=args.context_manifest_ref,
                context_pack_ref=args.context_pack_ref,
                requirement_ref=args.requirement_ref,
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
        print(json.dumps({"status": "ERROR", "error": type(exc).__name__, "message": str(exc), "authorization": getattr(exc, "authorization", None)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
