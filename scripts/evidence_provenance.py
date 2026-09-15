#!/usr/bin/env python3
"""Verifiable evidence records: three independent dimensions, verifier-owned dependencies.

Three layers, kept strictly separate:

    inputs = collect_verification_inputs(repo, record)      # IO only
    result = verify(record, inputs=inputs, policy=policy)   # pure, deterministic
    persist_verification(repo, result, checked_at=now)      # write + audit

An evidence record is immutable and content addressed. The index is rebuildable and is
never a second authority. Nothing in this module grants permission: `kind`, `producer`,
`verifier`, `source.type` and `strength` are recorded facts, not trust.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

EVIDENCE_KINDS = {"agent_claim", "mechanical_observation", "human_approval", "native_result"}
VALIDATION_STATUS = {"unverified", "verified", "invalid"}
OUTCOMES = {"passed", "failed", "approved", "rejected", "unknown"}

EVIDENCE_DIR = ".orchestrator/evidence"
RECORDS_DIR = f"{EVIDENCE_DIR}/records"
INDEX_REL = f"{EVIDENCE_DIR}/index.json"

FIXTURE_SOURCE_TYPES = {"fixture", "synthetic", "mock"}
FIXTURE_PRODUCERS = {"fixture", "test-fixture", "synthetic"}

# Minimum dependencies derived by the verifier. Callers may add, never remove.
REQUIRED_DEPENDENCIES: Dict[str, frozenset] = {
    "fact_resolution": frozenset({"requirement_revision"}),
    "readiness": frozenset({"requirement_revision"}),
    "gate_result": frozenset({"code_under_test", "requirement_revision"}),
    "test_result": frozenset({"code_under_test", "requirement_revision"}),
    "verification": frozenset({"code_under_test", "requirement_revision"}),
    "approval": frozenset({"requirement_revision"}),
    "native_state": frozenset({"requirement_revision", "native_source"}),
}

# Which readiness key may be satisfied by which kind of evidence.
# "acceptance criteria present" is a different conclusion from "acceptance satisfied".
READINESS_SOURCE_RULES: Dict[str, Dict[str, Any]] = {
    "behavior_change": {"kinds": {"mechanical_observation", "human_approval", "native_result"}},
    "sdd_ready": {"kinds": {"native_result", "human_approval"}},
    "acceptance_criteria_present": {"kinds": {"native_result", "human_approval", "mechanical_observation"},
                                    "claim_type": "readiness"},
    "implementation_tasks_complete": {"kinds": {"native_result", "mechanical_observation"}},
    "acceptance_satisfied": {"kinds": {"mechanical_observation", "human_approval"},
                             "claim_type": "verification"},
}

_REASONS = {
    "EVIDENCE_UNVERIFIED",
    "EVIDENCE_SCOPE_MISMATCH",
    "EVIDENCE_DEPENDENCY_MISSING",
    "EVIDENCE_DEPENDENCY_DRIFT",
    "EVIDENCE_REPORT_CONTRADICTION",
    "EVIDENCE_REPORT_UNPARSABLE",
    "EVIDENCE_SNAPSHOT_STALE",
    "EVIDENCE_FIXTURE_IDENTITY_REJECTED",
    "EVIDENCE_SELF_REFERENTIAL",
    "EVIDENCE_INVALID_SHAPE",
    "EVIDENCE_KIND_NOT_ALLOWED",
}


def required_dependencies(*, kind: str, claim_type: str, policy: dict | None = None) -> frozenset:
    """Minimum dependencies for a conclusion type, derived by the verifier (not the caller)."""
    deps = set(REQUIRED_DEPENDENCIES.get(claim_type, frozenset({"requirement_revision"})))
    if kind == "mechanical_observation":
        deps.add("code_under_test")
    if kind == "native_result":
        deps.add("native_source")
    extra = ((policy or {}).get("evidence_required_dependencies") or {}).get(claim_type)
    if isinstance(extra, Iterable) and not isinstance(extra, str):
        deps.update(str(x) for x in extra)
    return frozenset(deps)


def _canonical(record: Dict[str, Any]) -> str:
    body = {k: v for k, v in record.items()
            if k not in {"evidence_id", "checked_at", "validation_status", "outcome", "reason_code"}}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_id(record: Dict[str, Any]) -> str:
    return "ev-" + hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()[:24]


def build_record(*, kind: str, claim_type: str, work_item_id: str,
                 requirement_revision: str | None = None, outcome: str = "unknown",
                 producer: str = "unspecified", verifier: str | None = None,
                 source: dict | None = None, depends_on: List[dict] | None = None,
                 report: dict | None = None, approval: dict | None = None,
                 extra: dict | None = None) -> Dict[str, Any]:
    """Build an unsigned evidence record. `kind` and friends never imply trust by themselves."""
    record: Dict[str, Any] = {
        "kind": kind,
        "claim_type": claim_type,
        "work_item_id": work_item_id,
        "requirement_revision": requirement_revision,
        "outcome": outcome,
        "producer": producer,
        "verifier": verifier,
        "source": source or {},
        "depends_on": list(depends_on or []),
    }
    if report is not None:
        record["report"] = report
    if approval is not None:
        record["approval"] = approval
    if extra:
        record["extra"] = extra
    return record


def _read_json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _digest(path: Path) -> str | None:
    try:
        import repository_snapshot
        return repository_snapshot.digest_path(path)
    except (OSError, ValueError, ImportError):
        return None


def collect_verification_inputs(repo: Path | None, record: Dict[str, Any]) -> Dict[str, Any]:
    """IO layer: resolve current revisions of declared dependencies and read any report."""
    inputs: Dict[str, Any] = {"current_revisions": {}, "report": None}
    if repo is None:
        return inputs
    repo = Path(repo).resolve()
    for dep in record.get("depends_on") or []:
        object_kind = dep.get("object_kind")
        object_id = dep.get("object_id")
        if not object_kind or not object_id:
            continue
        # Snapshot-style dependencies carry their own revision; the caller's own claim is
        # compared against the snapshot recorded for that object kind.
        inputs["current_revisions"][object_kind] = {
            "object_id": object_id,
            "revision": dep.get("revision"),
        }
    report = record.get("report") or {}
    report_path = report.get("path")
    if report_path:
        path = Path(report_path)
        path = path if path.is_absolute() else repo / path
        doc = _read_json(path)
        if doc is None:
            inputs["report"] = {"available": False}
        else:
            inputs["report"] = {
                "available": True,
                "status": doc.get("status"),
                "exit_code": doc.get("exit_code", report.get("exit_code")),
                "digest": _digest(path),
            }
    return inputs


def verify(record: Dict[str, Any], *, inputs: Dict[str, Any], policy: dict | None = None,
           now: str | None = None, work_item_id: str | None = None,
           requirement_revision: str | None = None) -> Dict[str, Any]:
    """Pure verification. Same inputs must always yield the same result.

    `now` is only echoed for auditing; it never participates in the decision.
    """
    result: Dict[str, Any] = {
        "evidence_id": evidence_id(record),
        "kind": record.get("kind"),
        "claim_type": record.get("claim_type"),
        "validation_status": "unverified",
        "outcome": record.get("outcome") or "unknown",
        "reason_code": "EVIDENCE_UNVERIFIED",
        "depends_on": list(record.get("depends_on") or []),
        "checked_at": now,
    }

    def invalid(code: str) -> Dict[str, Any]:
        result["validation_status"] = "invalid"
        result["reason_code"] = code
        return result

    kind = record.get("kind")
    claim_type = record.get("claim_type")
    if kind not in EVIDENCE_KINDS or not claim_type:
        return invalid("EVIDENCE_INVALID_SHAPE")
    if not record.get("work_item_id"):
        return invalid("EVIDENCE_INVALID_SHAPE")

    # Scope: the record must belong to the object being judged.
    if work_item_id is not None and record.get("work_item_id") != work_item_id:
        return invalid("EVIDENCE_SCOPE_MISMATCH")
    declared_revision = record.get("requirement_revision")
    if requirement_revision is not None and declared_revision and declared_revision != requirement_revision:
        return invalid("EVIDENCE_SCOPE_MISMATCH")

    # Fixture identities never enter a production judgement.
    source = record.get("source") or {}
    if (str(source.get("type") or "").lower() in FIXTURE_SOURCE_TYPES
            or str(record.get("producer") or "").lower() in FIXTURE_PRODUCERS):
        return invalid("EVIDENCE_FIXTURE_IDENTITY_REJECTED")

    # Verifier-owned minimum dependencies; callers may add but never remove.
    depends_on = record.get("depends_on") or []
    declared = {d.get("object_kind") for d in depends_on if isinstance(d, dict)}
    required = required_dependencies(kind=kind, claim_type=claim_type, policy=policy)
    missing = sorted(required - declared)
    if missing:
        result["missing_dependencies"] = missing
        return invalid("EVIDENCE_DEPENDENCY_MISSING")

    # A dependency must name a concrete object and revision, never only a category.
    eid = result["evidence_id"]
    for dep in depends_on:
        if not isinstance(dep, dict) or not dep.get("object_kind") or not dep.get("object_id"):
            return invalid("EVIDENCE_DEPENDENCY_MISSING")
        if dep.get("revision") in (None, ""):
            return invalid("EVIDENCE_DEPENDENCY_MISSING")
        # An evidence record may never be part of the digest that validates itself.
        if (dep.get("object_kind") == "evidence" or dep.get("object_id") == eid
                or str(dep.get("revision")) == eid):
            return invalid("EVIDENCE_SELF_REFERENTIAL")

    current = (inputs or {}).get("current_revisions") or {}
    for object_kind, observed in current.items():
        expected = next((d for d in depends_on if d.get("object_kind") == object_kind), None)
        if expected is None:
            continue
        if expected.get("object_id") != observed.get("object_id"):
            return invalid("EVIDENCE_DEPENDENCY_DRIFT")
        if observed.get("revision") is not None and expected.get("revision") != observed.get("revision"):
            return invalid("EVIDENCE_DEPENDENCY_DRIFT")

    # Report binding: claimed outcome must match the report that was actually produced.
    report = record.get("report")
    if report:
        observed_report = (inputs or {}).get("report")
        if not observed_report or not observed_report.get("available"):
            return invalid("EVIDENCE_REPORT_UNPARSABLE")
        reported_status = observed_report.get("status")
        claimed = result["outcome"]
        if reported_status in {"failed", "passed", "error"}:
            # A real failure is valid evidence, not an invalid record.
            result["outcome"] = reported_status if reported_status != "error" else "failed"
            if claimed == "passed" and reported_status != "passed":
                result["validation_status"] = "verified"
                result["reason_code"] = "EVIDENCE_REPORT_CONTRADICTION"
                return result
        if observed_report.get("exit_code") not in (None, 0) and claimed == "passed":
            result["outcome"] = "failed"
            result["validation_status"] = "verified"
            result["reason_code"] = "EVIDENCE_REPORT_CONTRADICTION"
            return result

    # Trust can only come from a mechanically checked source or a checked approval.
    if kind == "agent_claim":
        return result  # stays unverified; needs support material or a real approval
    if kind == "mechanical_observation":
        if not report:
            return result
    elif kind in {"human_approval", "native_result"}:
        approval = record.get("approval") or {}
        if not (approval.get("source") and approval.get("subject") and approval.get("revision")):
            return result  # named approval fields alone never establish trust
    result["validation_status"] = "verified"
    result["reason_code"] = None
    return result


def revalidate(repo: Path | None, record: Dict[str, Any], *, policy: dict | None = None,
               work_item_id: str | None = None,
               requirement_revision: str | None = None) -> Dict[str, Any]:
    """Convenience: collect inputs (IO) then verify (pure)."""
    return verify(record, inputs=collect_verification_inputs(repo, record), policy=policy,
                  work_item_id=work_item_id, requirement_revision=requirement_revision)


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def records_dir(repo: Path) -> Path:
    return Path(repo).resolve() / RECORDS_DIR


def persist_verification(repo: Path, result: Dict[str, Any], *, checked_at: str) -> Dict[str, Any]:
    """Write an immutable record and refresh the rebuildable index."""
    repo = Path(repo).resolve()
    record = {k: v for k, v in result.items() if k not in {"checked_at"}}
    record["checked_at"] = checked_at
    path = records_dir(repo) / f"{record['evidence_id']}.json"
    _atomic_write(path, record)
    index_path = repo / INDEX_REL
    index = _read_json(index_path) or {"schema_version": 1, "records": {}}
    index.setdefault("schema_version", 1)
    index.setdefault("records", {})
    index["records"][record["evidence_id"]] = {
        "work_item_id": record.get("work_item_id"),
        "claim_type": record.get("claim_type"),
        "kind": record.get("kind"),
        "validation_status": record.get("validation_status"),
        "outcome": record.get("outcome"),
        "checked_at": checked_at,
        "path": f"{RECORDS_DIR}/{record['evidence_id']}.json",
    }
    _atomic_write(index_path, index)
    return record


def rebuild_index(repo: Path) -> Dict[str, Any]:
    """Rebuild the index from immutable records. Orphan records are legal input."""
    repo = Path(repo).resolve()
    directory = records_dir(repo)
    records: Dict[str, Any] = {}
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            doc = _read_json(path)
            if not doc or not doc.get("evidence_id"):
                continue
            records[doc["evidence_id"]] = {
                "work_item_id": doc.get("work_item_id"),
                "claim_type": doc.get("claim_type"),
                "kind": doc.get("kind"),
                "validation_status": doc.get("validation_status"),
                "outcome": doc.get("outcome"),
                "checked_at": doc.get("checked_at"),
                "path": f"{RECORDS_DIR}/{path.name}",
            }
    index = {"schema_version": 1, "records": records}
    _atomic_write(repo / INDEX_REL, index)
    return index


def load_index(repo: Path) -> Dict[str, Any]:
    return _read_json(Path(repo).resolve() / INDEX_REL) or {"schema_version": 1, "records": {}}


def readiness_rule(key: str) -> Dict[str, Any]:
    return READINESS_SOURCE_RULES.get(key, {"kinds": set(EVIDENCE_KINDS)})


def readiness_kind_allowed(key: str, kind: str) -> bool:
    rule = READINESS_SOURCE_RULES.get(key)
    if not rule:
        return kind in EVIDENCE_KINDS
    return kind in rule.get("kinds", set())
