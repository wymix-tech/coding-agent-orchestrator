#!/usr/bin/env python3
"""Verifiable evidence records: three independent dimensions, verifier-owned dependencies.

Three layers, kept strictly separate:

    inputs  = collect_verification_inputs(repo, record, state=state)   # IO only
    result  = verify(record, inputs=inputs, policy=policy)             # pure, deterministic
    stored  = store_evidence(repo, record, checked_at=now)             # write + audit

Dimensions never collapse into each other:

    kind               what produced the material  (agent_claim / mechanical_observation /
                                                    human_approval / native_result)
    validation_status  was it checked now          (verified / unverified / invalid)
    outcome            what it says                (passed / failed / approved / rejected /
                                                    unknown)

Immutability is about the *original claim*, not about its audit trail:
`.orchestrator/evidence/records/<id>.json` holds the raw claim and is written once; a second
write with different bytes for the same identity is refused. `.orchestrator/evidence/checks/`
holds the mutable validation results, and the index is rebuildable from both.

An evidence record is content addressed. Nothing in this module grants permission: `kind`,
`producer`, `verifier`, `source.type` and `strength` are recorded facts, not trust.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

EVIDENCE_KINDS = {"agent_claim", "mechanical_observation", "human_approval", "native_result"}
VALIDATION_STATUS = {"unverified", "verified", "invalid"}
OUTCOMES = {"passed", "failed", "approved", "rejected", "unknown", "error"}
SUCCESS_OUTCOMES = {"passed", "approved"}

EVIDENCE_DIR = ".orchestrator/evidence"
RECORDS_DIR = f"{EVIDENCE_DIR}/records"
CHECKS_DIR = f"{EVIDENCE_DIR}/checks"
INDEX_REL = f"{EVIDENCE_DIR}/index.json"

FIXTURE_SOURCE_TYPES = {"fixture", "synthetic", "mock"}
FIXTURE_PRODUCERS = {"fixture", "test-fixture", "synthetic"}

OBJECT_SCHEMES = {"path", "requirement", "native", "policy", "evidence", "state"}

# Documentation is a legal requirement source and a legal authority document, but it is never
# the object a mechanical claim runs against. Allowing it as `code_under_test` would let a
# claim keep its verdict while the real code moves.
DOCUMENT_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".adoc", ".org", ".pdf",
                     ".png", ".jpg", ".jpeg", ".svg", ".gif")

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

# Which readiness key may be satisfied by which kind of evidence, for which purpose, and by
# which outcomes. "acceptance criteria present" is a different conclusion from "acceptance
# satisfied"; verified without a success outcome is never enough.
READINESS_SOURCE_RULES: Dict[str, Dict[str, Any]] = {
    "behavior_change": {
        "kinds": {"mechanical_observation", "human_approval", "native_result"},
        "outcomes": SUCCESS_OUTCOMES,
    },
    "sdd_ready": {
        "kinds": {"native_result", "human_approval"},
        "claim_type": "readiness",
        "outcomes": SUCCESS_OUTCOMES,
    },
    "acceptance_criteria_present": {
        "kinds": {"native_result", "human_approval", "mechanical_observation"},
        "claim_type": "readiness",
        "outcomes": SUCCESS_OUTCOMES,
    },
    "implementation_tasks_complete": {
        "kinds": {"native_result", "mechanical_observation"},
        "claim_type": "readiness",
        "outcomes": SUCCESS_OUTCOMES,
    },
    "acceptance_satisfied": {
        "kinds": {"mechanical_observation", "human_approval"},
        "claim_type": "verification",
        "outcomes": SUCCESS_OUTCOMES,
    },
}

_REASONS = {
    "EVIDENCE_UNVERIFIED",
    "EVIDENCE_SCOPE_MISMATCH",
    "EVIDENCE_DEPENDENCY_MISSING",
    "EVIDENCE_DEPENDENCY_DRIFT",
    "EVIDENCE_DEPENDENCY_UNRESOLVED",
    "EVIDENCE_DEPENDENCY_UNPROVEN",
    "EVIDENCE_REPORT_CONTRADICTION",
    "EVIDENCE_REPORT_UNPARSABLE",
    "EVIDENCE_REPORT_DRIFT",
    "EVIDENCE_EXECUTION_UNBOUND",
    "EVIDENCE_APPROVAL_UNRESOLVED",
    "EVIDENCE_APPROVAL_NOT_FOUND",
    "EVIDENCE_APPROVAL_SUBJECT_MISMATCH",
    "EVIDENCE_APPROVAL_REVISION_MISMATCH",
    "EVIDENCE_APPROVAL_REVISION_UNBOUND",
    "EVIDENCE_APPROVAL_AUTHORITY_UNCONFIGURED",
    "EVIDENCE_APPROVER_NOT_AUTHORIZED",
    "EVIDENCE_OBJECT_MISSING",
    "EVIDENCE_OBJECT_NOT_CODE",
    "EVIDENCE_DEPENDENCY_OBJECT_CONFLICT",
    "EVIDENCE_RESULT_SOURCE_UNBOUND",
    "EVIDENCE_RESULT_DRIFT",
    "EVIDENCE_RESULT_TARGET_MISMATCH",
    "EVIDENCE_RESULT_SCOPE_MISMATCH",
    "EVIDENCE_RESULT_STALE",
    "EVIDENCE_NATIVE_UNRESOLVED",
    "EVIDENCE_NATIVE_REVISION_MISMATCH",
    "EVIDENCE_SNAPSHOT_STALE",
    "EVIDENCE_FIXTURE_IDENTITY_REJECTED",
    "EVIDENCE_SELF_REFERENTIAL",
    "EVIDENCE_INVALID_SHAPE",
    "EVIDENCE_KIND_NOT_ALLOWED",
}

# Fields that describe *this* audit run. They never take part in the record's identity.
AUDIT_FIELDS = {"validation_status", "reason_code", "checked_at", "missing_dependencies", "inputs"}


class EvidenceConflict(Exception):
    """The same evidence identity was claimed twice with different content."""


# --- project policy ------------------------------------------------------------------

POLICY_RELS = (".orchestrator/policies/evidence.yaml",
               ".orchestrator/policies/manifest.yaml",
               ".orchestrator/config.yaml")


def load_policy(repo: Path | None) -> Optional[Dict[str, Any]]:
    """Read the project's evidence policy, if it publishes one.

    A missing policy is never "no policy": consumers must pass what the project declares so
    that approval authorities and mandatory objects actually reach verification.
    """
    if repo is None:
        return None
    root = Path(repo).resolve()
    for rel in POLICY_RELS:
        doc = _read_doc(root, rel)
        if not isinstance(doc, dict):
            continue
        orch = doc.get("orchestrator") if isinstance(doc.get("orchestrator"), dict) else doc
        section = orch.get("evidence") if isinstance(orch, dict) else None
        if isinstance(section, dict):
            return {"evidence": section, "policy_ref": rel}
    return None


def required_objects(*, claim_type: str, policy: dict | None = None) -> List[Dict[str, str]]:
    """Concrete objects the project mandates for a conclusion type, in addition to the kinds.

    Kinds only say "some code under test"; a project may require the real one. Callers may
    add objects, never remove them.
    """
    section = ((policy or {}).get("evidence") or {}).get("required_objects")
    if not isinstance(section, dict):
        return []
    entries = section.get(claim_type)
    if not isinstance(entries, list):
        return []
    out: List[Dict[str, str]] = []
    for item in entries:
        if isinstance(item, dict) and item.get("object_kind") and item.get("object_id"):
            out.append({"object_kind": str(item["object_kind"]),
                        "object_id": str(item["object_id"])})
    return out


def _loaded(name: str):
    """Import a sibling module even when only *this* file was loaded by path."""
    try:
        return __import__(name)
    except ImportError:  # pragma: no cover - import shim only
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
        if spec is None or spec.loader is None:
            return None
        module = _ilu.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


# --- dependency resolution ---------------------------------------------------------------

def _is_path_like(value: str) -> bool:
    return ("/" in value) or value.endswith((".py", ".ts", ".js", ".java", ".go", ".md",
                                             ".json", ".yaml", ".yml", ".toml"))


def parse_pointer(object_kind: str, object_id: str) -> Tuple[str, str]:
    """Split a dependency object id into (scheme, value).

    A dependency must name something the verifier can look up on its own. An opaque label
    that only echoes the caller's own claim carries no information and can therefore never
    turn into freshness.
    """
    raw = str(object_id or "")
    if ":" in raw:
        head, _, tail = raw.partition(":")
        if head in OBJECT_SCHEMES and tail:
            if head in {"path", "policy", "evidence"} or \
                    (head == "requirement" and object_kind == "requirement_revision") or \
                    (head == "native" and object_kind == "native_source"):
                return head, tail
    if object_kind == "requirement_revision":
        # Requirement revisions always go through the requirement content resolver, even when
        # they name a path: runtime progress written into the same file is not a requirement
        # change, and only the requirement knows which part is which.
        return "requirement", raw
    if _is_path_like(raw) or object_kind in {"code_under_test", "policy"}:
        return "path", raw
    if object_kind == "native_source":
        return "native", raw
    if object_kind == "evidence":
        return "evidence", raw
    return "opaque", raw


def path_revision(repo: Path, rel: str) -> Optional[str]:
    """Current revision of a single file, or None when it cannot be read."""
    path = Path(rel)
    candidate = path if path.is_absolute() else Path(repo).resolve() / path
    try:
        if candidate.is_file():
            return hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        return None
    return None


def tree_revision(repo: Path, rel: str) -> Optional[str]:
    """Deterministic revision of a directory: membership itself is part of the revision."""
    root = Path(repo).resolve()
    path = Path(rel)
    base = path if path.is_absolute() else root / path
    try:
        if not base.is_dir():
            return None
    except OSError:
        return None
    members: List[str] = []
    digests: Dict[str, Optional[str]] = {}
    for member in sorted(p for p in base.rglob("*") if p.is_file()):
        try:
            relative = member.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if any(relative == skip or relative.startswith(f"{skip}/")
               for skip in (".git", "node_modules", "__pycache__", ".orchestrator")):
            continue
        members.append(relative)
        digests[relative] = path_revision(root, relative)
    material = "\n".join(f"{m}\n{digests[m] or 'unreadable'}" for m in members)
    return "tree-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def object_revision(repo: Path, rel: str) -> Optional[str]:
    return path_revision(repo, rel) or tree_revision(repo, rel)


def _read_json(path: Path) -> Optional[dict]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _read_doc(repo: Path, rel: str) -> Optional[dict]:
    """Read a JSON or YAML document inside the repository."""
    path = Path(rel)
    candidate = path if path.is_absolute() else Path(repo).resolve() / path
    doc = _read_json(candidate)
    if doc is not None:
        return doc
    try:
        import yaml  # type: ignore
    except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
        return None
    try:
        parsed = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _requirement_source_from_state(state: dict | None) -> Tuple[Optional[str], Optional[list]]:
    if not isinstance(state, dict):
        return None, None
    work_item = state.get("work_item") or {}
    members = work_item.get("members") if isinstance(work_item.get("members"), list) else None
    ref = (work_item.get("requirement_source_ref")
           or work_item.get("source_ref")
           or (state.get("requirement") or {}).get("ref"))
    return (str(ref) if ref else None), members


def _resolve_requirement(repo: Path, value: str, state: dict | None) -> Dict[str, Any]:
    """Requirement content revision, resolved from the real source instead of the claim."""
    identity = _loaded("requirement_identity")
    root = Path(repo).resolve()
    state_ref, state_members = _requirement_source_from_state(state)
    if _is_path_like(value) and not (Path(value).is_absolute() or root / value).exists():
        # A requirement source the claim itself names has to be readable. Falling back to
        # whatever the state declares would let any unresolvable path stand in for it.
        return {"resolved": False, "revision": None,
                "error": "EVIDENCE_DEPENDENCY_UNRESOLVED",
                "message": f"the declared requirement source {value!r} is not readable inside the project"}
    candidates: List[Tuple[Optional[str], Optional[list]]] = []
    if _is_path_like(value):
        candidates.append((value, None))
    candidates.append((state_ref, state_members))
    for ref, members in candidates:
        if not ref:
            continue
        path = Path(ref)
        probe = path if path.is_absolute() else root / path
        if not probe.exists():
            continue
        if identity is None:
            return {"resolved": False, "revision": None,
                    "error": "EVIDENCE_DEPENDENCY_UNRESOLVED",
                    "message": "requirement_identity is unavailable"}
        resolved = identity.requirement_content_revision(root, ref, members=members)
        revision = resolved.get("source_revision")
        if revision:
            return {"resolved": True, "revision": str(revision), "object_ref": str(ref)}
    return {"resolved": False, "revision": None,
            "error": "EVIDENCE_DEPENDENCY_UNRESOLVED",
            "message": f"no requirement source could be resolved for {value!r}"}


def _native_ref_from_state(state: dict | None) -> Optional[str]:
    if not isinstance(state, dict):
        return None
    authority = state.get("authority") or {}
    ref = authority.get("native_state_ref")
    if ref:
        return str(ref)
    last = authority.get("last_native_sync") or {}
    return str(last.get("source_ref")) if last.get("source_ref") else None


def _resolve_native(repo: Path, value: str, state: dict | None) -> Dict[str, Any]:
    """Native state revision, resolved by parsing the configured native source."""
    root = Path(repo).resolve()
    if state is not None:
        parser = _loaded("native_state_parser")
        if parser is not None:
            try:
                projection, diag = parser.resolve_projection(root, state)
            except Exception:  # pragma: no cover - defensive against a broken source
                projection, diag = None, {}
            if projection is not None:
                return {"resolved": True, "revision": projection.native_state_revision,
                        "object_ref": projection.source_ref, "work_item_id": projection.work_item_id}
            if diag and diag.get("error") not in {"NATIVE_SOURCE_UNCONFIGURED"}:
                return {"resolved": False, "revision": None,
                        "error": "EVIDENCE_NATIVE_UNRESOLVED",
                        "message": f"{diag.get('error')}: {diag.get('message')}"}
    for ref in [value if _is_path_like(value) else None, _native_ref_from_state(state)]:
        if not ref:
            continue
        revision = path_revision(root, ref)
        if revision:
            return {"resolved": True, "revision": revision, "object_ref": str(ref)}
    return {"resolved": False, "revision": None, "error": "EVIDENCE_NATIVE_UNRESOLVED",
            "message": "no native state source could be resolved"}


def _resolve_evidence(repo: Path, value: str) -> Dict[str, Any]:
    doc = load_record(repo, value)
    if doc is None:
        return {"resolved": False, "revision": None, "error": "EVIDENCE_DEPENDENCY_UNRESOLVED",
                "message": f"referenced evidence record {value!r} is not in the record store"}
    return {"resolved": True, "revision": evidence_id(doc), "object_ref": value}


def resolve_dependency(repo: Path, dep: Dict[str, Any], *, state: dict | None = None) -> Dict[str, Any]:
    """Current revision of one dependency. Never derived from the claim being validated."""
    kind = str(dep.get("object_kind") or "")
    raw = str(dep.get("object_id") or "")
    scheme, value = parse_pointer(kind, raw)
    if scheme == "path":
        revision = object_revision(repo, value)
        if revision is None:
            return {"resolved": False, "revision": None,
                    "error": "EVIDENCE_DEPENDENCY_UNRESOLVED",
                    "message": f"{value!r} is not readable inside the project"}
        return {"resolved": True, "revision": revision, "object_ref": value}
    if scheme == "requirement":
        return _resolve_requirement(repo, value, state)
    if scheme == "native":
        return _resolve_native(repo, value, state)
    if scheme == "policy":
        revision = object_revision(repo, value)
        if revision is None:
            return {"resolved": False, "revision": None,
                    "error": "EVIDENCE_DEPENDENCY_UNRESOLVED",
                    "message": f"policy source {value!r} is not readable"}
        return {"resolved": True, "revision": revision, "object_ref": value}
    if scheme == "evidence":
        return _resolve_evidence(repo, value)
    return {"resolved": False, "revision": None, "error": "EVIDENCE_DEPENDENCY_UNPROVEN",
            "message": f"{kind} object {raw!r} names no object the verifier can look up"}


def collect_verification_inputs(repo: Path | None, record: Dict[str, Any], *,
                                state: dict | None = None) -> Dict[str, Any]:
    """IO layer: resolve every declared dependency against the real project and read reports.

    Nothing here is copied from `record.depends_on[].revision`; each observed revision is
    computed from the object itself. Dependencies are keyed by (kind, object id) so two
    objects of the same kind cannot shadow each other.
    """
    inputs: Dict[str, Any] = {"current_revisions": {}, "report": None, "approval": None,
                              "native": None, "repo": str(repo) if repo else None}
    if repo is None:
        return inputs
    root = Path(repo).resolve()
    for dep in record.get("depends_on") or []:
        if not isinstance(dep, dict) or not dep.get("object_id"):
            continue
        key = f"{dep.get('object_kind')}::{dep.get('object_id')}"
        inputs["current_revisions"][key] = resolve_dependency(root, dep, state=state)
        # Also publish the plain kind key, so a consumer may bind by category only when the
        # record named exactly one object of that kind.
        inputs["current_revisions"].setdefault(str(dep.get("object_kind")),
                                               inputs["current_revisions"][key])
    report = record.get("report") or {}
    report_path = report.get("path")
    if report_path:
        inputs["report"] = _collect_report(root, report)
    if record.get("approval"):
        inputs["approval"] = _collect_approval(root, record, state=state)
    if str(record.get("kind")) == "native_result":
        inputs["native"] = _collect_native(root, record, state=state)
    return inputs


def _collect_report(repo: Path, report: Dict[str, Any]) -> Dict[str, Any]:
    """Read the report that was actually produced, including how it was produced."""
    rel = str(report.get("path") or "")
    path = Path(rel)
    candidate = path if path.is_absolute() else repo / path
    if not candidate.exists():
        return {"available": False, "error": "EVIDENCE_REPORT_UNPARSABLE"}
    doc = _read_json(candidate)
    if doc is None:
        return {"available": False, "error": "EVIDENCE_REPORT_UNPARSABLE"}
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    execution = [str(x) for x in (doc.get("command") or doc.get("argv") or []) if x]
    if isinstance(doc.get("command"), str):
        execution = [doc["command"]]
    observed = {
        "available": True,
        "status": doc.get("status"),
        "exit_code": doc.get("exit_code", report.get("exit_code")),
        "digest": digest,
        "execution": execution,
        "tests": doc.get("tests"),
        "empty_report": not bool(doc),
    }
    if report.get("digest") and report.get("digest") != digest:
        observed["digest_mismatch"] = True
    return observed


def _approval_entries(doc: Any) -> List[dict]:
    """Collect approval entries from one of the supported approval source shapes."""
    if isinstance(doc, list):
        return [e for e in doc if isinstance(e, dict)]
    if isinstance(doc, dict):
        for key in ("approvals", "records", "entries"):
            value = doc.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]
        return [doc]
    return []


def _collect_approval(repo: Path, record: Dict[str, Any], *, state: dict | None = None) -> Dict[str, Any]:
    """Independently read the approval that a claim points at. A claim never validates itself."""
    approval = record.get("approval") or {}
    source = str(approval.get("source") or "")
    if not source:
        return {"available": False, "error": "EVIDENCE_APPROVAL_UNRESOLVED",
                "message": "the approval names no source"}
    entries: List[dict] = []
    resolved_ref = None
    for ref in (source, *([f".orchestrator/{source}"] if not _is_path_like(source) else [])):
        doc = _read_doc(repo, ref)
        if doc is None:
            continue
        resolved_ref = ref
        entries = _approval_entries(doc)
        break
    if resolved_ref is None:
        return {"available": False, "error": "EVIDENCE_APPROVAL_UNRESOLVED",
                "message": f"approval source {source!r} could not be read inside the project"}
    wanted = str(approval.get("revision") or "")
    matched = None
    for entry in entries:
        if wanted and str(entry.get("id") or entry.get("approval_id") or "") == wanted:
            matched = entry
            break
    if matched is None and wanted:
        for entry in entries:
            if wanted in {str(entry.get("requirement_revision") or ""),
                          str(entry.get("revision") or ""),
                          str(entry.get("native_state_revision") or "")}:
                matched = entry
                break
    if matched is None:
        return {"available": False, "error": "EVIDENCE_APPROVAL_NOT_FOUND",
                "message": f"approval {wanted!r} is not present in {resolved_ref}",
                "source": resolved_ref}
    subject = str(approval.get("subject") or "")
    observed_subject = str(matched.get("subject") or matched.get("work_item_id")
                           or matched.get("requirement_id") or "")
    result = {
        "available": True,
        "source": resolved_ref,
        "subject": observed_subject,
        "decision": str(matched.get("decision") or matched.get("status") or ""),
        "approver": matched.get("approver") or matched.get("approved_by") or matched.get("reviewer"),
        "requirement_revision": matched.get("requirement_revision") or matched.get("revision"),
        "authority": matched.get("authority"),
    }
    if subject and observed_subject and observed_subject != subject:
        result["error"] = "EVIDENCE_APPROVAL_SUBJECT_MISMATCH"
        result["message"] = (f"approval {wanted!r} was issued for {observed_subject!r}, "
                             f"not for the claimed subject {subject!r}")
    return result


def _collect_native(repo: Path, record: Dict[str, Any], *, state: dict | None = None) -> Dict[str, Any]:
    """Native-result claims must match the native projection produced right now."""
    parser = _loaded("native_state_parser")
    approval = record.get("approval") or {}
    if parser is None:
        return {"available": False, "error": "EVIDENCE_NATIVE_UNRESOLVED",
                "message": "native_state_parser is unavailable"}
    projection, diag = parser.resolve_projection(repo, state)
    if projection is None:
        return {"available": False, "error": "EVIDENCE_NATIVE_UNRESOLVED",
                "message": f"{diag.get('error')}: {diag.get('message')}"}
    observed = {
        "available": True,
        "source": projection.source_ref,
        "revision": projection.native_state_revision,
        # Two different identities: the id the native source addresses this work item with,
        # and the local governance id it is projected onto. They are never interchangeable.
        "work_item_id": projection.work_item_id,
        "local_work_item_id": projection.local_work_item_id,
        "phase": projection.phase,
        "status_detail": projection.status_detail,
    }
    claimed = approval.get("revision") or record.get("native_state_revision")
    if claimed and str(claimed) != projection.native_state_revision:
        observed["error"] = "EVIDENCE_NATIVE_REVISION_MISMATCH"
        observed["message"] = ("the claim was issued against a different native revision "
                               f"(claimed={claimed}, observed={projection.native_state_revision})")
    return observed


# --- result bindings for gates, review and verification ------------------------------------

def _document_execution(doc: Dict[str, Any]) -> List[str]:
    """The command a result document says produced it, if it says so at all."""
    for key in ("command", "argv", "execution"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, list):
            parts = [str(x).strip() for x in value if str(x).strip()]
            if parts:
                return parts
    return []


def result_document_binding(repo: Path, ref: Optional[str], *, target: str,
                            work_item_id: Optional[str] = None,
                            requirement_revision: Optional[str] = None,
                            code_revision: Optional[str] = None) -> Dict[str, Any]:
    """Bind a result document inside the project to the conclusion it is meant to carry.

    A document that only says `{"status": "passed"}` is a label: it names no command that ran,
    no policy evaluation that can be re-read, and no object it was produced for. Three sources
    are legal, and each of them stays re-checkable after the result is recorded:

      * `command`/`argv` plus `exit_code` -- a command that really ran;
      * `policy_plan_ref` + `policy_evaluation_ref` -- a policy evaluation still in the project;
      * a verified evidence record, which the caller resolves before calling this.

    The binding also records which gate/action, work item and revisions it was issued for, so
    authorization can re-read the source instead of trusting the snapshot tag stored with it.
    """
    def fail(code: str, message: str) -> Dict[str, Any]:
        return {"available": False, "error": code, "message": message}

    if not ref:
        return fail("EVIDENCE_RESULT_SOURCE_UNBOUND",
                    "no result document was named; a passed result needs a source that can be re-read")
    root = Path(repo).resolve()
    candidate = Path(str(ref))
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        if not candidate.is_file():
            return fail("EVIDENCE_RESULT_SOURCE_UNBOUND",
                        f"result document {ref!r} is not a file inside the project")
        doc = json.loads(candidate.read_text(encoding="utf-8"))
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return fail("EVIDENCE_REPORT_UNPARSABLE", f"result document {ref!r} could not be read as JSON")
    if not isinstance(doc, dict):
        return fail("EVIDENCE_REPORT_UNPARSABLE", "a result document must be a JSON object")
    if str(doc.get("status") or "").lower() != "passed":
        return fail("EVIDENCE_RESULT_STALE", f"result document {ref!r} does not record a passed result")
    exit_code = doc.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
        return fail("EVIDENCE_RESULT_SOURCE_UNBOUND",
                    "a result document must record exit_code 0; a status without the exit code of "
                    "the command that produced it is an assertion, not a result")
    execution = _document_execution(doc)
    producer: Dict[str, Any]
    if execution:
        producer = {"kind": "command", "command": execution}
    elif doc.get("policy_evaluation_ref") or doc.get("policy_plan_ref"):
        refs = {key: str(doc[key]) for key in ("policy_plan_ref", "policy_evaluation_ref") if doc.get(key)}
        digests: Dict[str, Optional[str]] = {}
        for key, value in refs.items():
            source = Path(value)
            source = source if source.is_absolute() else root / value
            if not source.is_file():
                return fail("EVIDENCE_RESULT_SOURCE_UNBOUND",
                            f"{key} {value!r} is not readable inside the project")
            digests[key] = path_revision(root, value)
        producer = {"kind": "policy_evaluation", **refs, "digests": digests}
    else:
        return fail("EVIDENCE_RESULT_SOURCE_UNBOUND",
                    "the result document names neither a command that ran nor a policy evaluation "
                    "that can be re-read; a passed status on its own binds nothing")
    declared_target = doc.get("target") or doc.get("gate")
    if declared_target and str(declared_target) != str(target):
        return fail("EVIDENCE_RESULT_TARGET_MISMATCH",
                    f"result document {ref!r} was produced for {declared_target!r}, not for {target!r}")
    declared_work_item = doc.get("work_item_id")
    if declared_work_item and work_item_id and str(declared_work_item) != str(work_item_id):
        return fail("EVIDENCE_RESULT_SCOPE_MISMATCH",
                    f"result document {ref!r} was produced for work item {declared_work_item!r}, "
                    f"not for {work_item_id!r}")
    try:
        relative = candidate.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):  # pragma: no cover - defensive
        relative = str(ref)
    return {"available": True, "binding": {
        "kind": "result_document", "path": relative, "digest": digest, "status": "passed",
        "exit_code": 0, "target": str(target), "work_item_id": work_item_id,
        "requirement_revision": requirement_revision, "code_revision": code_revision,
        "producer": producer}}


def recheck_result_binding(repo: Path | None, binding: Any, *, target: Optional[str] = None,
                           work_item_id: Optional[str] = None,
                           requirement_revision: Optional[str] = None,
                           code_revision: Optional[str] = None,
                           state: dict | None = None,
                           policy: dict | None = None) -> Dict[str, Any]:
    """Re-read the source a passed result was bound to, at the moment the result is used.

    A stored status is history. The document, the command behind it and the object versions it
    was produced for are all re-read here, so a result whose source was deleted, edited, or
    issued for something else stops supporting the gate, review or verification that cites it.
    """
    def bad(code: str, message: str) -> Dict[str, Any]:
        return {"ok": False, "error": code, "message": message}

    if not isinstance(binding, dict) or not binding:
        return bad("EVIDENCE_RESULT_SOURCE_UNBOUND",
                   "this passed result has no source that can be re-read")
    if binding.get("evidence_id"):
        record = load_record(Path(repo).resolve(), str(binding["evidence_id"]))
        if record is None:
            return bad("EVIDENCE_RESULT_SOURCE_UNBOUND",
                       f"evidence record {binding['evidence_id']} is no longer in the record store")
        result = revalidate(repo, record, state=state, policy=policy, work_item_id=work_item_id,
                            requirement_revision=requirement_revision)
        if result.get("validation_status") != "verified" or str(result.get("outcome")) not in SUCCESS_OUTCOMES:
            return bad(str(result.get("reason_code") or "EVIDENCE_UNVERIFIED"),
                       f"the evidence behind this result is {result.get('validation_status')}"
                       f"/{result.get('outcome')} now")
        return {"ok": True, "error": None}
    path = binding.get("path")
    if not path:
        return bad("EVIDENCE_RESULT_SOURCE_UNBOUND", "this passed result names no result document")
    root = Path(repo).resolve()
    candidate = Path(str(path))
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        if not candidate.is_file():
            return bad("EVIDENCE_RESULT_SOURCE_UNBOUND",
                       f"the result document {path!r} this result was bound to is gone")
        doc = json.loads(candidate.read_text(encoding="utf-8"))
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return bad("EVIDENCE_RESULT_SOURCE_UNBOUND", f"the result document {path!r} can no longer be read")
    if not isinstance(doc, dict):
        return bad("EVIDENCE_REPORT_UNPARSABLE", f"the result document {path!r} is not a JSON object")
    if binding.get("digest") and binding["digest"] != digest:
        return bad("EVIDENCE_RESULT_DRIFT",
                   f"the result document {path!r} changed after the result was recorded")
    if str(doc.get("status") or "").lower() != "passed":
        return bad("EVIDENCE_RESULT_STALE",
                   f"the result document {path!r} no longer records a passed result")
    exit_code = doc.get("exit_code")
    if isinstance(exit_code, bool) or (exit_code is not None and exit_code != 0):
        return bad("EVIDENCE_RESULT_STALE",
                   f"the result document {path!r} no longer records exit_code 0")
    producer = binding.get("producer") or {}
    if producer.get("kind") == "command" and not _document_execution(doc):
        return bad("EVIDENCE_RESULT_SOURCE_UNBOUND",
                   f"the result document {path!r} no longer names the command that produced it")
    if producer.get("kind") == "policy_evaluation":
        for key in ("policy_plan_ref", "policy_evaluation_ref"):
            value = producer.get(key)
            if value and path_revision(root, str(value)) != (producer.get("digests") or {}).get(key):
                return bad("EVIDENCE_RESULT_DRIFT",
                           f"{key} changed after the result was recorded")
    if binding.get("target") and target and str(binding["target"]) != str(target):
        return bad("EVIDENCE_RESULT_TARGET_MISMATCH",
                   f"this result was recorded for {binding['target']!r}, not for {target!r}")
    if binding.get("work_item_id") and work_item_id and str(binding["work_item_id"]) != str(work_item_id):
        return bad("EVIDENCE_RESULT_SCOPE_MISMATCH",
                   f"this result was recorded for work item {binding['work_item_id']!r}")
    if binding.get("requirement_revision") and requirement_revision \
            and str(binding["requirement_revision"]) != str(requirement_revision):
        return bad("EVIDENCE_RESULT_SCOPE_MISMATCH",
                   "this result was recorded against a different requirement revision")
    # `code_revision` is recorded for audit but is not compared here: whether the result covers
    # the code in force is the snapshot comparison, and mixing the two would relabel a stale
    # result as an unverifiable one.
    return {"ok": True, "error": None}


# --- identity, persistence ---------------------------------------------------------------

def _canonical(record: Dict[str, Any]) -> str:
    body = {k: v for k, v in record.items()
            if k not in AUDIT_FIELDS and k not in {"evidence_id"}}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_id(record: Dict[str, Any]) -> str:
    """Identity of the *claim*. Two different outcomes are two different claims."""
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


# --- pure verification --------------------------------------------------------------------

def _dependency_observation(dep: Dict[str, Any], inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    current = (inputs or {}).get("current_revisions") or {}
    key = f"{dep.get('object_kind')}::{dep.get('object_id')}"
    return current.get(key) or current.get(str(dep.get("object_kind")))


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

    def unverified(code: str) -> Dict[str, Any]:
        result["validation_status"] = "unverified"
        result["reason_code"] = code
        return result

    kind = record.get("kind")
    claim_type = record.get("claim_type")
    if kind not in EVIDENCE_KINDS or not claim_type:
        return invalid("EVIDENCE_INVALID_SHAPE")
    if not record.get("work_item_id"):
        return invalid("EVIDENCE_INVALID_SHAPE")

    # Scope: the record must belong to the object being judged. Missing scope is never the
    # same as "no scope check needed": consumers must pass their real identifiers.
    if work_item_id is not None and record.get("work_item_id") != work_item_id:
        return invalid("EVIDENCE_SCOPE_MISMATCH")
    # A record that omits the revision is not "unscoped": the consumer's revision is the
    # scope, and an empty identity can never be equal to it.
    declared_revision = record.get("requirement_revision")
    if requirement_revision and str(declared_revision or "") != str(requirement_revision):
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

    # Freshness: compare every dependency against what the project says right now.
    unproven: List[str] = []
    for dep in depends_on:
        observed = _dependency_observation(dep, inputs)
        if not observed:
            if (inputs or {}).get("repo"):
                return invalid("EVIDENCE_DEPENDENCY_UNRESOLVED")
            continue
        if not observed.get("resolved"):
            # An unresolved dependency fails closed. The consumer's own revision says what the
            # requirement *should* be; it never makes an unreadable source readable.
            if observed.get("error") == "EVIDENCE_DEPENDENCY_UNPROVEN":
                unproven.append(f"{dep.get('object_kind')}::{dep.get('object_id')}")
                continue
            return invalid(observed.get("error") or "EVIDENCE_DEPENDENCY_UNRESOLVED")
        if str(observed.get("revision")) != str(dep.get("revision")):
            result["drifted_dependency"] = f"{dep.get('object_kind')}::{dep.get('object_id')}"
            return invalid("EVIDENCE_DEPENDENCY_DRIFT")

    # Mandatory concrete objects: a project may require the real code under test, not just
    # "some code". Kinds remain the floor; the policy may only add objects.
    for obj in required_objects(claim_type=claim_type, policy=policy):
        if not any(str(d.get("object_kind")) == obj["object_kind"]
                   and str(d.get("object_id")) == obj["object_id"] for d in depends_on):
            result["missing_object"] = obj
            return invalid("EVIDENCE_OBJECT_MISSING")

    # The object under test is not the requirement document. A claim may not satisfy both
    # dependencies with the same file: that would verify nothing.
    observed_refs: Dict[str, str] = {}
    for dep in depends_on:
        observed = _dependency_observation(dep, inputs) or {}
        if observed.get("object_ref"):
            observed_refs[str(dep.get("object_kind"))] = str(observed["object_ref"])
    code_ref = observed_refs.get("code_under_test")
    requirement_ref = observed_refs.get("requirement_revision")
    if code_ref and requirement_ref and code_ref == requirement_ref:
        result["conflicting_object"] = code_ref
        return invalid("EVIDENCE_DEPENDENCY_OBJECT_CONFLICT")
    # The object under test has to be code. A document can be a requirement source, but it can
    # never be the thing a gate, test or progress claim ran against: swapping the real source
    # for any readable prose file would otherwise keep an old verdict alive forever.
    if code_ref and code_ref.lower().endswith(DOCUMENT_SUFFIXES):
        result["non_code_object"] = code_ref
        return invalid("EVIDENCE_OBJECT_NOT_CODE")

    # Report binding: claimed outcome must match the report that was actually produced.
    report = record.get("report")
    if report:
        observed_report = (inputs or {}).get("report")
        if not observed_report or not observed_report.get("available"):
            return invalid("EVIDENCE_REPORT_UNPARSABLE")
        if observed_report.get("digest_mismatch"):
            return invalid("EVIDENCE_REPORT_DRIFT")
        reported_status = observed_report.get("status")
        claimed = result["outcome"]
        if isinstance(reported_status, str) and reported_status in {"failed", "passed", "error"}:
            # A real failure is valid evidence, not an invalid record.
            result["outcome"] = "failed" if reported_status == "error" else reported_status
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
        observed_report = (inputs or {}).get("report") or {}
        if observed_report.get("empty_report"):
            return unverified("EVIDENCE_EXECUTION_UNBOUND")
        if observed_report.get("status") not in OUTCOMES:
            return unverified("EVIDENCE_EXECUTION_UNBOUND")
        if observed_report.get("exit_code") is None:
            return unverified("EVIDENCE_EXECUTION_UNBOUND")
        if not observed_report.get("execution"):
            return unverified("EVIDENCE_EXECUTION_UNBOUND")
    elif kind == "human_approval":
        approval = record.get("approval") or {}
        if not (approval.get("source") and approval.get("subject") and approval.get("revision")):
            return result  # named approval fields alone never establish trust
        observed = (inputs or {}).get("approval") or {}
        if not observed.get("available"):
            return unverified(observed.get("error") or "EVIDENCE_APPROVAL_UNRESOLVED")
        if observed.get("error"):
            return unverified(observed["error"])
        # An approval without an approver is not an approval: no subject means no authority,
        # and a missing name must never default to valid.
        if not str(observed.get("approver") or "").strip():
            return unverified("EVIDENCE_APPROVER_NOT_AUTHORIZED")
        if str(observed.get("approver") or "") == str(record.get("producer") or ""):
            # Self-approval declares itself; it cannot be the authority it claims.
            return unverified("EVIDENCE_APPROVER_NOT_AUTHORIZED")
        authorities = ((policy or {}).get("evidence") or {}).get("approval_authorities")
        if isinstance(authorities, list) and authorities:
            approver = str(observed.get("approver") or "")
            if approver not in {str(a) for a in authorities}:
                return unverified("EVIDENCE_APPROVER_NOT_AUTHORIZED")
        else:
            # A name that is merely different from the producer is not an authority. Without a
            # published trust configuration the approval stays unverified rather than being
            # promoted to trust by the absence of a policy.
            return unverified("EVIDENCE_APPROVAL_AUTHORITY_UNCONFIGURED")
        # The approval must be about *this* work item, not merely about whatever subject the
        # claim named. A file that exists is not yet an approval for the object being judged.
        if work_item_id and str(observed.get("subject") or "") \
                and str(observed.get("subject")) != str(work_item_id):
            return unverified("EVIDENCE_APPROVAL_SUBJECT_MISMATCH")
        # And it must have been issued for the revision that is active now. The approval has to
        # bind that revision itself: a revision copied in by the outer record says what the
        # claim wants, not what the approver actually approved.
        approval_revision = observed.get("requirement_revision")
        if requirement_revision:
            if not str(approval_revision or "").strip():
                result["approval_revision"] = approval_revision
                return unverified("EVIDENCE_APPROVAL_REVISION_UNBOUND")
            if str(approval_revision) != str(requirement_revision):
                result["approval_revision"] = approval_revision
                return unverified("EVIDENCE_APPROVAL_REVISION_MISMATCH")
        if str(observed.get("decision") or "").lower() not in {"approved", "accepted", "passed"} \
                and result["outcome"] in SUCCESS_OUTCOMES:
            return unverified("EVIDENCE_APPROVAL_SUBJECT_MISMATCH")
    elif kind == "native_result":
        approval = record.get("approval") or {}
        if not (approval.get("source") and approval.get("subject") and approval.get("revision")):
            return result
        observed = (inputs or {}).get("native") or {}
        if not observed.get("available"):
            return unverified(observed.get("error") or "EVIDENCE_NATIVE_UNRESOLVED")
        if observed.get("error"):
            return unverified(observed["error"])
        # Local and native identities are different categories. The projection must be the one
        # produced for this local work item; its native id is what the native source knows.
        local_id = observed.get("local_work_item_id")
        if work_item_id and local_id and str(local_id) != str(work_item_id):
            return unverified("EVIDENCE_SCOPE_MISMATCH")
        if work_item_id and not local_id and observed.get("work_item_id") not in {None, work_item_id}:
            return unverified("EVIDENCE_SCOPE_MISMATCH")

    if unproven:
        result["unproven_dependencies"] = unproven
        return unverified("EVIDENCE_DEPENDENCY_UNPROVEN")
    result["validation_status"] = "verified"
    result["reason_code"] = None
    return result


def revalidate(repo: Path | None, record: Dict[str, Any], *, policy: dict | None = None,
               work_item_id: str | None = None, state: dict | None = None,
               requirement_revision: str | None = None) -> Dict[str, Any]:
    """Convenience: collect inputs (IO) then verify (pure).

    The project's published policy is read when the caller does not pass one: who may approve
    is a project decision, and a caller that forgets it must not silently turn a configured
    approval into an unconfigured one.
    """
    if policy is None:
        policy = load_policy(repo)
    return verify(record,
                  inputs=collect_verification_inputs(repo, record, state=state),
                  policy=policy, work_item_id=work_item_id,
                  requirement_revision=requirement_revision)


# --- storage: immutable originals plus mutable checks --------------------------------------

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


def checks_dir(repo: Path) -> Path:
    return Path(repo).resolve() / CHECKS_DIR


def raw_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """The immutable claim: everything the verifier needs to re-check it independently."""
    payload = {k: v for k, v in record.items() if k not in AUDIT_FIELDS and k != "evidence_id"}
    payload["evidence_id"] = evidence_id(record)
    return payload


def persist_raw_record(repo: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    """Write the original claim once. Same bytes are idempotent; different bytes conflict."""
    payload = raw_payload(record)
    path = records_dir(repo) / f"{payload['evidence_id']}.json"
    existing = _read_json(path)
    if existing is not None:
        same = json.dumps(existing, ensure_ascii=False, sort_keys=True) == \
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if not same:
            raise EvidenceConflict(
                f"an evidence record with identity {payload['evidence_id']} already exists with "
                f"different content; a changed claim needs a new identity")
        return payload
    _atomic_write(path, payload)
    return payload


def _check_files(repo: Path, ident: str) -> List[Tuple[str, int, str, Dict[str, Any]]]:
    """All checks of one record, ordered by (checked_at, sequence, name).

    Ordering by a hash of the timestamp would be ordering by noise: two checks one second
    apart can sort in any direction. The sequence is what makes the order real.
    """
    directory = checks_dir(repo)
    if not directory.exists():
        return []
    entries: List[Tuple[str, int, str, Dict[str, Any]]] = []
    for path in directory.glob(f"{ident}.*.json"):
        parts = path.stem.split(".")
        seq = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else -1
        doc = _read_json(path)
        if doc is None:
            continue
        entries.append((str(doc.get("checked_at") or ""), seq, path.name, doc))
    entries.sort(key=lambda item: (item[0], item[1], item[2]))
    return entries


def persist_check(repo: Path, result: Dict[str, Any], *, checked_at: str) -> Path:
    """Write this run's validation outcome. Checks are append-only, never overwriting.

    Two checks taken at the same instant are two events: each gets its own sequence, so a
    later result can never overwrite an earlier one.
    """
    check = {k: v for k, v in result.items() if k != "checked_at"}
    check["checked_at"] = checked_at
    ident = str(check.get("evidence_id") or "unknown")
    existing = _check_files(repo, ident)
    seq = (existing[-1][1] + 1) if existing else 1
    stamp = hashlib.sha256(
        f"{checked_at}\n{seq}\n{json.dumps(check, ensure_ascii=False, sort_keys=True)}"
        .encode("utf-8")).hexdigest()[:8]
    path = checks_dir(repo) / f"{ident}.{seq:08d}.{stamp}.json"
    _atomic_write(path, check)
    return path


def load_record(repo: Path, evidence_id_: str) -> Optional[Dict[str, Any]]:
    return _read_json(records_dir(repo) / f"{evidence_id_}.json")


def load_records(repo: Path) -> Dict[str, Dict[str, Any]]:
    directory = records_dir(repo)
    found: Dict[str, Dict[str, Any]] = {}
    if not directory.exists():
        return found
    for path in sorted(directory.glob("*.json")):
        doc = _read_json(path)
        if doc and doc.get("evidence_id"):
            found[str(doc["evidence_id"])] = doc
    return found


def latest_check(repo: Path, evidence_id_: str) -> Optional[Dict[str, Any]]:
    entries = _check_files(repo, evidence_id_)
    return entries[-1][3] if entries else None


def latest_check_path(repo: Path, evidence_id_: str) -> Optional[str]:
    """Where the newest check of this record lives, relative to the project root."""
    entries = _check_files(repo, evidence_id_)
    if not entries:
        return None
    root = Path(repo).resolve()
    try:
        return (checks_dir(root) / entries[-1][2]).relative_to(root).as_posix()
    except (OSError, ValueError):  # pragma: no cover - defensive
        return f"{CHECKS_DIR}/{entries[-1][2]}"


def store_evidence(repo: Path, record: Dict[str, Any], *, checked_at: str,
                   policy: dict | None = None, state: dict | None = None,
                   work_item_id: str | None = None,
                   requirement_revision: str | None = None) -> Dict[str, Any]:
    """Store the claim and this run's verdict, then refresh the index."""
    payload = persist_raw_record(repo, record)
    result = revalidate(repo, record, policy=policy, state=state, work_item_id=work_item_id,
                        requirement_revision=requirement_revision)
    check_path = persist_check(repo, result, checked_at=checked_at)
    _refresh_index_entry(repo, payload, result, checked_at, check_path=check_path)
    return {"record": payload, "check": result}


def persist_verification(repo: Path, record: Dict[str, Any], *, checked_at: str,
                         result: Dict[str, Any] | None = None, **kwargs: Any) -> Dict[str, Any]:
    """Backwards-compatible entry point: keep the raw claim, record this check."""
    stored = store_evidence(repo, record, checked_at=checked_at, **(kwargs or {}))
    return stored["record"] if result is None else {**stored["record"], **result}


def _refresh_index_entry(repo: Path, record: Dict[str, Any], result: Dict[str, Any],
                         checked_at: str, *, check_path: Path | None = None) -> None:
    index_path = Path(repo).resolve() / INDEX_REL
    index = _read_json(index_path) or {"schema_version": 2, "records": {}}
    index.setdefault("schema_version", 2)
    index.setdefault("records", {})
    ident = str(record["evidence_id"])
    root = Path(repo).resolve()
    relative = _relative(root, check_path)
    index["records"][ident] = {
        "work_item_id": record.get("work_item_id"),
        "claim_type": record.get("claim_type"),
        "kind": record.get("kind"),
        "validation_status": result.get("validation_status"),
        "outcome": result.get("outcome"),
        "reason_code": result.get("reason_code"),
        "checked_at": checked_at,
        "path": f"{RECORDS_DIR}/{ident}.json",
        "check_path": relative or latest_check_path(root, ident),
    }
    _atomic_write(index_path, index)


def _relative(root: Path, path: Path | None) -> Optional[str]:
    if path is None:
        return None
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):  # pragma: no cover - defensive
        return f"{CHECKS_DIR}/{Path(path).name}"


def rebuild_index(repo: Path) -> Dict[str, Any]:
    """Rebuild the index from immutable records plus their checks. Orphans are legal input."""
    root = Path(repo).resolve()
    records: Dict[str, Any] = {}
    for ident, doc in load_records(root).items():
        records[ident] = {
            "work_item_id": doc.get("work_item_id"),
            "claim_type": doc.get("claim_type"),
            "kind": doc.get("kind"),
            "validation_status": "unverified",
            "outcome": doc.get("outcome"),
            "reason_code": None,
            "checked_at": None,
            "path": f"{RECORDS_DIR}/{ident}.json",
            "check_path": None,
        }
        check = latest_check(root, ident)
        if check:
            records[ident].update({
                "validation_status": check.get("validation_status"),
                "outcome": check.get("outcome"),
                "reason_code": check.get("reason_code"),
                "checked_at": check.get("checked_at"),
                "check_path": latest_check_path(root, ident),
            })
    index = {"schema_version": 2, "records": records}
    _atomic_write(root / INDEX_REL, index)
    return index


def load_index(repo: Path) -> Dict[str, Any]:
    return _read_json(Path(repo).resolve() / INDEX_REL) or {"schema_version": 2, "records": {}}


# --- consumer-facing rules ----------------------------------------------------------------

def readiness_rule(key: str) -> Dict[str, Any]:
    return READINESS_SOURCE_RULES.get(key, {"kinds": set(EVIDENCE_KINDS)})


def readiness_kind_allowed(key: str, kind: str) -> bool:
    rule = READINESS_SOURCE_RULES.get(key)
    if not rule:
        return kind in EVIDENCE_KINDS
    return kind in rule.get("kinds", set())


def readiness_decision(key: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Does this verified result actually support this readiness key?

    Checks kind (source rule), purpose (claim_type) and conclusion (outcome). `verified`
    alone never means "passed".
    """
    rule = READINESS_SOURCE_RULES.get(key)
    if not rule:
        return {"allowed": False, "error": "EVIDENCE_KIND_NOT_ALLOWED",
                "message": f"no source rule is defined for readiness key {key!r}"}
    kind = str(result.get("kind") or "")
    if kind not in rule.get("kinds", set()):
        return {"allowed": False, "error": "EVIDENCE_KIND_NOT_ALLOWED",
                "message": f"readiness key {key!r} cannot be satisfied by a {kind!r} claim"}
    claim_type = rule.get("claim_type")
    if claim_type and str(result.get("claim_type") or "") != claim_type:
        return {"allowed": False, "error": "EVIDENCE_CLAIM_TYPE_MISMATCH",
                "message": f"readiness key {key!r} requires a {claim_type!r} claim, "
                           f"got {result.get('claim_type')!r}"}
    if result.get("validation_status") != "verified":
        return {"allowed": False, "error": "EVIDENCE_UNVERIFIED",
                "message": f"the evidence supporting {key!r} is {result.get('validation_status')}"}
    outcomes = rule.get("outcomes") or SUCCESS_OUTCOMES
    if result.get("outcome") not in outcomes:
        return {"allowed": False, "error": "EVIDENCE_OUTCOME_NOT_SUCCESS",
                "message": f"{key!r} requires one of {sorted(outcomes)}, got {result.get('outcome')!r}"}
    return {"allowed": True}
