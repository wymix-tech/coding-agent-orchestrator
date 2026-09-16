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
import shlex
import subprocess
import uuid
from datetime import datetime, timezone
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
# Receipts of commands that really ran, and the one store human approvals are recorded into.
EXECUTIONS_DIR = f"{EVIDENCE_DIR}/executions"
APPROVALS_REL = f"{EVIDENCE_DIR}/approvals.json"
DEFAULT_APPROVAL_SOURCES = (APPROVALS_REL,)
# A channel says where a human decision came from. A name inside a file is not a channel.
TRUSTED_APPROVAL_CHANNELS = ("host_approval_event", "external_adapter")

FIXTURE_SOURCE_TYPES = {"fixture", "synthetic", "mock"}
FIXTURE_PRODUCERS = {"fixture", "test-fixture", "synthetic"}

OBJECT_SCHEMES = {"path", "requirement", "native", "policy", "evidence", "state"}

# Documentation is a legal requirement source and a legal authority document, but it is never
# the object a mechanical claim runs against. Allowing it as `code_under_test` would let a
# claim keep its verdict while the real code moves.
DOCUMENT_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".adoc", ".org", ".pdf",
                     ".png", ".jpg", ".jpeg", ".svg", ".gif")

# A conclusion about code is bound to the code that was in force when it was produced. Where a
# project publishes no finer mapping, the verifier derives that object itself instead of
# believing whatever single file the caller named: the caller may add objects, never replace
# the one the consumer requires.
DERIVED_CODE_DEPENDENCY = "code_snapshot"
# The object that dependency names: the project tree. A caller may add finer objects, never
# stand a different tree in for it.
DERIVED_CODE_OBJECT_IDS = (".", "./", "")

# Minimum dependencies derived by the verifier. Callers may add, never remove.
REQUIRED_DEPENDENCIES: Dict[str, frozenset] = {
    "fact_resolution": frozenset({"requirement_revision"}),
    "readiness": frozenset({"requirement_revision"}),
    "gate_result": frozenset({"code_under_test", "requirement_revision", DERIVED_CODE_DEPENDENCY}),
    "test_result": frozenset({"code_under_test", "requirement_revision", DERIVED_CODE_DEPENDENCY}),
    "verification": frozenset({"code_under_test", "requirement_revision", DERIVED_CODE_DEPENDENCY}),
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
    "EVIDENCE_EXECUTION_NOT_RECORDED",
    "EVIDENCE_EXECUTION_IDENTITY_MISMATCH",
    "EVIDENCE_EXECUTION_SCOPE_MISSING",
    "EVIDENCE_EXECUTION_REVISION_MISSING",
    "EVIDENCE_EXECUTION_CODE_MISMATCH",
    "EVIDENCE_PURPOSE_MISMATCH",
    "EVIDENCE_PURPOSE_STATUS_NOT_VERIFIED",
    "EVIDENCE_PURPOSE_OUTCOME_UNSUCCESSFUL",
    "EVIDENCE_APPROVAL_SOURCE_UNTRUSTED",
    "EVIDENCE_APPROVAL_CHANNEL_UNVERIFIED",
    "EVIDENCE_OBSERVER_UNTRUSTED",
    "EVIDENCE_OBJECT_EMPTY",
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
    if object_kind == DERIVED_CODE_DEPENDENCY:
        # The conservative object a result about code is bound to: the project tree itself.
        return "path", raw or "."
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


SKIP_TREE_PARTS = (".git", "node_modules", "__pycache__", ".orchestrator")
# Documentation is not code. A requirement, a note or a readme is not what a result about code
# ran against; those are covered by their own dependency, so editing one does not invalidate a
# result about the code.
MATERIAL_DOC_SUFFIXES = (".md", ".markdown", ".rst", ".adoc", ".asciidoc", ".txt", ".pdf")


def material_files(repo: Path, rel: str) -> List[str]:
    """Material files of a directory: generated content is not part of the code."""
    root = Path(repo).resolve()
    path = Path(rel)
    base = path if path.is_absolute() else root / path
    try:
        if not base.is_dir():
            return []
    except OSError:
        return []
    members: List[str] = []
    snapshots = _loaded("repository_snapshot")
    state = _read_doc(root, ".orchestrator/execution-state.yaml")
    native_ref = _native_ref_from_state(state)
    if not native_ref and ((state or {}).get("authority") or {}).get("mode") == "native":
        native_ref = "sprint-status.yaml"
    native_path = (root / native_ref).resolve() if native_ref else None
    for member in sorted(p for p in base.rglob("*") if p.is_file()):
        try:
            relative = member.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if native_path is not None and member.resolve() == native_path:
            continue
        if any(relative == skip or relative.startswith(f"{skip}/") for skip in SKIP_TREE_PARTS):
            continue
        if snapshots and (any(part in snapshots.IGNORED_DIRS for part in Path(relative).parts)
                          or relative.startswith(snapshots.IGNORED_PREFIXES)):
            continue
        if relative.lower().endswith(MATERIAL_DOC_SUFFIXES):
            continue
        members.append(relative)
    return members


def tree_revision(repo: Path, rel: str) -> Optional[str]:
    """Deterministic revision of a directory: membership itself is part of the revision.

    A directory that exists and holds no material file still has a revision: an empty tree is
    a state the code was in, and it is not the same state as the one after a file appears.
    Only a path that is not a directory has no revision to record.
    """
    root = Path(repo).resolve()
    path = Path(rel)
    base = path if path.is_absolute() else root / path
    if not base.is_dir():
        return None
    members = material_files(repo, rel)
    digests: Dict[str, Optional[str]] = {m: path_revision(root, m) for m in members}
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
    if _is_path_like(value):
        declared = Path(value)
        # An absolute requirement path is resolved as it stands; a relative one against the
        # project root. Both are then probed once, in the same way.
        probe = declared if declared.is_absolute() else root / declared
        if not probe.exists():
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
            candidate = Path(value)
            probe = candidate if candidate.is_absolute() else Path(repo).resolve() / candidate
            if kind in {DERIVED_CODE_DEPENDENCY, "code_under_test"} and probe.is_dir():
                # An object with nothing in it can be named without pointing at anything. It
                # is not the code a result ran against.
                return {"resolved": False, "revision": None,
                        "error": "EVIDENCE_OBJECT_EMPTY",
                        "message": f"{value!r} contains no material file; an empty object "
                                   f"cannot be the code a result was produced for"}
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


def code_revision_now(repo: Path) -> Optional[str]:
    """Revision of the code in force: one digest over the project tree.

    Receipts, reports and approvals live under `.orchestrator/`, which is generated content,
    so recording them never changes the revision of the code a result is about.
    """
    return tree_revision(Path(repo).resolve(), ".")


# --- execution receipts: what a command that actually ran produced -------------------------

TARGET_CLAIM_TYPES = {"gate": "gate_result", "review": "review_result",
                      "verification": "verification"}


def claim_type_for_target(target: str) -> Optional[str]:
    """The conclusion a result target is about.

    `gate:unit` is a gate result. It is not a review, and not a final verification, even
    though all three are "verified evidence" once they have been checked.
    """
    prefix = str(target or "").split(":", 1)[0].strip().lower()
    if prefix == "readiness":
        return (READINESS_SOURCE_RULES.get(str(target).partition(":")[2]) or {}).get("claim_type", "readiness")
    return {**TARGET_CLAIM_TYPES, "fact": "fact_observation", "task_progress": "task_progress"}.get(prefix)


def execution_scope(receipt, *, current_code, work_item_id=None, requirement_revision=None,
                    target=None, claim_type=None):
    def bad(code):
        return {"ok": False, "error": code, "message": code}
    if not isinstance(receipt, dict):
        return bad("EVIDENCE_EXECUTION_NOT_RECORDED")
    if not receipt.get("target") or not receipt.get("work_item_id"):
        return bad("EVIDENCE_EXECUTION_SCOPE_MISSING")
    if not receipt.get("requirement_revision"):
        return bad("EVIDENCE_EXECUTION_REVISION_MISSING")
    if work_item_id and receipt["work_item_id"] != work_item_id:
        return bad("EVIDENCE_RESULT_SCOPE_MISMATCH")
    if requirement_revision and receipt["requirement_revision"] != requirement_revision:
        return bad("EVIDENCE_RESULT_SCOPE_MISMATCH")
    if target and receipt["target"] != target:
        return bad("EVIDENCE_RESULT_TARGET_MISMATCH")
    actual_type = claim_type_for_target(receipt["target"])
    aliases = {"test_result": "gate_result", "code_review": "review_result",
               "verification_result": "verification"}
    if actual_type is None or (claim_type and aliases.get(claim_type, claim_type) != actual_type):
        return bad("EVIDENCE_PURPOSE_MISMATCH")
    before, after = receipt.get("code_revision"), receipt.get("code_revision_after")
    if not before or not after or not current_code:
        return bad("EVIDENCE_EXECUTION_REVISION_MISSING")
    if before != after or after != current_code:
        return bad("EVIDENCE_EXECUTION_CODE_MISMATCH")
    return {"ok": True, "error": None}


def observer_binding(repo, fact_path, argv, *, state=None):
    """Policy selects the observer and its complete inputs, never --fact-path alone."""
    rule = (((load_policy(repo) or {}).get("evidence") or {}).get("observers") or {}).get(fact_path)
    if not isinstance(rule, dict) or list(argv) != rule.get("argv"):
        return None
    files, refs = rule.get("files"), rule.get("inputs")
    if not isinstance(files, dict) or not files or not isinstance(refs, list) or not refs:
        return None
    if any(path_revision(repo, ref) != digest for ref, digest in files.items()):
        return None
    required_ref, members = _requirement_source_from_state(state)
    required_refs = members or ([required_ref] if required_ref else [])
    normalized = {str((Path(repo) / ref).resolve()) for ref in refs}
    if any(str((Path(repo) / ref).resolve()) not in normalized for ref in required_refs):
        return None
    inputs = {ref: path_revision(repo, ref) for ref in refs}
    if any(value is None for value in inputs.values()):
        return None
    return {"rule": rule, "inputs": inputs}


def execution_context(repo, work_item_id, requirement_revision):
    state = _read_doc(repo, ".orchestrator/execution-state.yaml")
    wi = (state or {}).get("work_item") or {}
    if wi and wi.get("id") != work_item_id:
        return None, state
    ref, members = _requirement_source_from_state(state)
    revision = wi.get("requirement_revision")
    if ref:
        current = _loaded("requirement_identity").requirement_content_revision(repo, ref, members=members)
        if not current.get("source_revision") or (revision and revision != current["source_revision"]):
            return None, state
        revision = current["source_revision"]
    if revision and requirement_revision and revision != requirement_revision:
        return None, state
    return revision or requirement_revision, state


def build_execution_receipt(*, argv: Iterable[Any], exit_code: int, target: str,
                            work_item_id: str, requirement_revision: Optional[str] = None,
                            code_revision: Optional[str] = None,
                            code_revision_after: Optional[str] = None,
                            fact_path: Optional[str] = None, observed_value: Any = None,
                            status: Optional[str] = None, cwd: Optional[str] = None,
                            started_at: Optional[str] = None, ended_at: Optional[str] = None,
                            producer: str = "orchestrator.execution", output: dict | None = None,
                            observer: dict | None = None) -> Dict[str, Any]:
    """The facts a command that ran leaves behind, in the shape the verifier re-reads."""
    parts = [str(part) for part in argv]
    receipt: Dict[str, Any] = {
        "kind": "execution_receipt",
        "producer": producer,
        "argv": parts,
        "command": " ".join(shlex.quote(part) for part in parts),
        "exit_code": int(exit_code),
        "status": status or ("passed" if int(exit_code) == 0 else "failed"),
        "target": str(target),
        "claim_type": claim_type_for_target(target),
        "work_item_id": str(work_item_id) if work_item_id else None,
        "requirement_revision": requirement_revision,
        "code_revision": code_revision,
        "code_revision_after": code_revision_after if code_revision_after else code_revision,
        "fact_path": fact_path,
        "observed_value": observed_value,
        "cwd": cwd,
        "started_at": started_at,
        "ended_at": ended_at,
        "output": output,
        "observer": observer,
    }
    return receipt


def execution_id(receipt: Dict[str, Any]) -> str:
    """Identity of a receipt: its content, never a name the caller picked."""
    body = {k: v for k, v in receipt.items() if k != "execution_id"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "exec-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def executions_dir(repo: Path) -> Path:
    return Path(repo).resolve() / EXECUTIONS_DIR


def persist_execution(repo: Path, receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Record a receipt once. Same content is idempotent; different content conflicts."""
    root = Path(repo).resolve()
    ident = execution_id(receipt)
    stored = {**receipt, "execution_id": ident}
    path = executions_dir(root) / f"{ident}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_json(path)
        if existing is None:
            return {"available": False, "error": "EVIDENCE_EXECUTION_NOT_RECORDED",
                    "id": ident, "path": EXECUTIONS_DIR + f"/{ident}.json",
                    "message": f"execution receipt {ident} could not be read back"}
        previous = {k: v for k, v in existing.items() if k != "execution_id"}
        if json.dumps(previous, ensure_ascii=False, sort_keys=True) != \
                json.dumps({k: v for k, v in stored.items() if k != "execution_id"},
                           ensure_ascii=False, sort_keys=True):
            return {"available": False, "error": "EVIDENCE_EXECUTION_IDENTITY_MISMATCH",
                    "id": ident, "path": EXECUTIONS_DIR + f"/{ident}.json",
                    "message": f"an execution receipt with identity {ident} already exists with "
                               f"different content"}
        return {"available": True, "id": ident, "path": EXECUTIONS_DIR + f"/{ident}.json",
                "receipt": existing}
    _atomic_write(path, stored)
    return {"available": True, "id": ident, "path": EXECUTIONS_DIR + f"/{ident}.json",
            "receipt": stored}


def load_execution(repo: Path, ident: str) -> Optional[Dict[str, Any]]:
    """Read a receipt back from the store. A file that is not its own content is not a receipt."""
    if not ident:
        return None
    path = executions_dir(repo) / f"{str(ident)}.json"
    doc = _read_json(path)
    if not isinstance(doc, dict) or str(doc.get("kind") or "") != "execution_receipt":
        return None
    if execution_id({k: v for k, v in doc.items() if k != "execution_id"}) != str(ident):
        return None
    return doc


def resolve_execution_document(root: Path, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Trust a result document only when the execution behind it is on record.

    A document may name a receipt, or be one. Either way the values that matter come from the
    recorded receipt, never from the copy the caller handed in: a hand-written file that says
    `status: passed` says nothing about what ran.
    """
    ident = (doc.get("execution_id") or doc.get("execution_ref") or doc.get("receipt")
             or (doc.get("execution") if isinstance(doc.get("execution"), str) else None))
    if not ident:
        return None
    stored = load_execution(root, str(ident))
    if stored is None:
        return None
    for key in ("status", "exit_code", "target", "work_item_id", "requirement_revision",
                "command", "code_revision", "code_revision_after", "fact_path", "observed_value", "output", "observer"):
        if key in doc and doc[key] != stored.get(key):
            return None
    output = stored.get("output") or {}
    for key in ("tests", "tasks", "progress"):
        if key in doc and doc[key] != output.get(key):
            return None
    return stored


def run_execution(repo: Path, argv: Iterable[Any], *, target: str, work_item_id: str,
                  requirement_revision: Optional[str] = None, cwd: Optional[str] = None,
                  fact_path: Optional[str] = None, timeout: Optional[float] = None,
                  producer: str = "orchestrator.execution") -> Dict[str, Any]:
    """Run a command, and record what it actually did.

    This is the entry point a real result comes from: the argv, the return code, the target
    and work item it was produced for, and the requirement/code revisions in force before and
    after the run are all observed here. Nothing in it is filled in from the consumer's
    current state later.
    """
    root = Path(repo).resolve()
    parts = [str(part) for part in argv]
    if not parts:
        return {"available": False, "error": "EVIDENCE_EXECUTION_NOT_RECORDED",
                "message": "no command was given"}
    requirement_revision, state = execution_context(root, work_item_id, requirement_revision)
    if not requirement_revision:
        return {"available": False, "error": "EVIDENCE_EXECUTION_REVISION_MISSING",
                "message": "Resolve the selected work item and current requirement revision before running evidence."}
    observer = observer_binding(root, fact_path, parts, state=state) if fact_path else None
    if fact_path and (target != f"fact:{fact_path}" or observer is None or (cwd and Path(cwd).resolve() != root)):
        return {"available": False, "error": "EVIDENCE_OBSERVER_UNTRUSTED",
                "message": "Configure this predicate's observer argv, program digests and inputs in orchestrator.evidence.observers."}
    started = datetime.now(timezone.utc).isoformat()
    before = code_revision_now(root)
    try:
        done = subprocess.run(parts, cwd=str(cwd or root), capture_output=True, text=True,
                              timeout=timeout)
        exit_code, stdout = int(done.returncode), done.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        exit_code, stdout = 127, ""
        receipt = build_execution_receipt(
            argv=parts, exit_code=exit_code, target=target, work_item_id=work_item_id,
            requirement_revision=requirement_revision, code_revision=before,
            code_revision_after=code_revision_now(root), cwd=str(cwd or root),
            started_at=started, ended_at=datetime.now(timezone.utc).isoformat(),
            producer=producer, status="error")
        stored = persist_execution(root, receipt)
        return {**stored, "exit_code": exit_code, "status": "error",
                "message": f"the command could not be run: {exc}"}
    ended = datetime.now(timezone.utc).isoformat()
    after = code_revision_now(root)
    try:
        output = json.loads(stdout)
    except ValueError:
        output = None
    if not isinstance(output, dict):
        output = None
    observed_value: Any = None
    if fact_path:
        # An observer reports the value of one predicate. It does so by printing it, so the
        # value is what the observer said, not what the caller wants it to be.
        observed_value = _observed_fact_value(stdout, fact_path)
    receipt = build_execution_receipt(
        argv=parts, exit_code=exit_code, target=target, work_item_id=work_item_id,
        requirement_revision=requirement_revision, code_revision=before,
        code_revision_after=after, fact_path=fact_path, observed_value=observed_value,
        cwd=str(cwd or root), started_at=started, ended_at=ended, producer=producer,
        output=output, observer=observer)
    stored = persist_execution(root, receipt)
    return {**stored, "exit_code": exit_code, "status": receipt["status"], "stdout": stdout}


def _observed_fact_value(stdout: str, fact_path: str) -> Any:
    """Read the observed value of one predicate from an observer's own output."""
    text = str(stdout or "").strip()
    if not text:
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    if doc.get("fact_path") != fact_path:
        return None
    return doc.get("value")


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
    inputs["code_revision"] = code_revision_now(root)
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
        receipt = (inputs["report"] or {}).get("execution_receipt") or {}
        if receipt.get("fact_path"):
            binding = observer_binding(root, receipt["fact_path"], receipt.get("argv") or [], state=state)
            inputs["observer_valid"] = binding is not None and binding == receipt.get("observer")
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
    # The execution behind this report. A report that is not a receipt, and does not name one,
    # is a document the caller wrote: it says nothing about a command that ran.
    receipt = resolve_execution_document(repo, doc)
    observed = {
        "available": True,
        "status": doc.get("status"),
        "exit_code": doc.get("exit_code", report.get("exit_code")),
        "digest": digest,
        "execution": execution,
        "execution_receipt": receipt,
        "tests": doc.get("tests"),
        "empty_report": not bool(doc),
    }
    # What the observer reported, for which predicate, comes from the receipt of the run. A
    # field the caller attached to its own report does not say what the observer observed.
    if receipt and receipt.get("fact_path"):
        observed["fact_path"] = str(receipt.get("fact_path"))
        observed["observed_value"] = receipt.get("observed_value")
    if report.get("digest") and report.get("digest") != digest:
        observed["digest_mismatch"] = True
    return observed


def _observed_fact(record: Dict[str, Any], inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The one predicate a trusted source observed, and the value it reported.

    A mechanical failure is a real failure, but it is not a statement that some semantic fact
    is false. Only an observer that reports the value of that predicate, or an approval that
    was issued for it, says something about the predicate itself.
    """
    # A checked approval authenticates its signed value, not an attached execution report.
    # Likewise a mechanical observation cannot borrow a separate approval's meaning.
    report = (inputs or {}).get("report") if record.get("kind") == "mechanical_observation" else None
    if isinstance(report, dict) and report.get("fact_path") and "observed_value" in report:
        return {"path": str(report["fact_path"]), "value": report.get("observed_value"),
                "source": "execution", "kind": record.get("kind"),
                "outcome": str(report.get("status") or ""),
                "exit_code": report.get("exit_code")}
    approval = (inputs or {}).get("approval") if record.get("kind") == "human_approval" else None
    if isinstance(approval, dict) and approval.get("fact_path") and "value" in approval:
        return {"path": str(approval["fact_path"]), "value": approval.get("value"),
                "source": "approval", "kind": record.get("kind"),
                "decision": str(approval.get("decision") or "")}
    return None


def fact_observation(result: Dict[str, Any], path: str, value: Any) -> Dict[str, Any]:
    """Does this result observe *value* for the predicate *path*?

    The observation has to come from a source that was produced for this predicate and that
    reported this value while it was in force. A failed run, a search that found nothing and
    a field the caller attached to its own claim are all different things, and none of them
    is an observation of the predicate.
    """
    observed = (result or {}).get("observed_fact")
    if not isinstance(observed, dict):
        return {"observed": False, "error": "EVIDENCE_FACT_UNOBSERVED",
                "message": f"no trusted source observes {path!r}"}
    if str(observed.get("path")) != str(path):
        return {"observed": False, "error": "EVIDENCE_FACT_SCOPE_MISMATCH",
                "message": f"the observation is about {observed.get('path')!r}, not {path!r}"}
    if type(observed.get("value")) is not type(value) or observed.get("value") != value:
        return {"observed": False, "error": "EVIDENCE_FACT_VALUE_MISMATCH",
                "message": f"the observation reports {observed.get('value')!r} for {path!r}"}
    if str(result.get("validation_status")) != "verified":
        return {"observed": False, "error": "EVIDENCE_UNVERIFIED",
                "message": f"the observation is {result.get('validation_status')} now"}
    if str(observed.get("source")) == "execution":
        # An observer that did not complete reported nothing about the predicate.
        if str(observed.get("outcome")) != "passed" or observed.get("exit_code") not in (0, None):
            return {"observed": False, "error": "EVIDENCE_EXECUTION_UNBOUND",
                    "message": f"the observer of {path!r} did not complete successfully"}
    elif str(observed.get("source")) == "approval":
        if str(observed.get("decision") or "").lower() not in {"approved", "accepted", "passed"}:
            return {"observed": False, "error": "EVIDENCE_APPROVAL_SUBJECT_MISMATCH",
                    "message": f"the approval behind {path!r} is {observed.get('decision')!r}"}
    return {"observed": True, "error": None}


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


def approval_sources(policy: dict | None = None) -> List[str]:
    """Where a human approval may be recorded for this project to treat it as a source.

    A file anywhere else is a file the caller wrote. Copying a valid approval into it does
    not move the approval, and does not make the copy a source.
    """
    section = ((policy or {}).get("evidence") or {}).get("approval_sources")
    if isinstance(section, list) and section:
        return [str(x) for x in section]
    return list(DEFAULT_APPROVAL_SOURCES)


def trusted_approval_channels(policy: dict | None = None) -> Tuple[str, ...]:
    """Channels this project accepts as the origin of a human decision."""
    section = ((policy or {}).get("evidence") or {}).get("approval_channels")
    if isinstance(section, list) and section:
        return tuple(str(x) for x in section)
    return TRUSTED_APPROVAL_CHANNELS


def _approval_body(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in entry.items() if k not in {"receipt", "prev", "seq"}}


def _approval_receipt(entry: Dict[str, Any], prev: str) -> str:
    body = {"prev": prev, **_approval_body(entry)}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _approval_channel(channel: Any, channel_ref: Any = None) -> Dict[str, Any]:
    if isinstance(channel, dict):
        ctype = str(channel.get("type") or "")
        cref = channel.get("ref") or channel.get("path") or channel_ref
        extra = {k: v for k, v in channel.items() if k not in {"type", "ref", "path"}}
        return {"type": ctype, "ref": str(cref) if cref else None, **extra}
    return {"type": str(channel or ""), "ref": str(channel_ref) if channel_ref else None}


def record_approval(repo: Path, *, approver: str, subject: str | None = None,
                    work_item_id: str | None = None, decision: str = "approved",
                    requirement_revision: str | None = None, fact_path: str | None = None,
                    value: Any = None, channel: Any = None, channel_ref: str | None = None,
                    decided_at: str | None = None, approval_id: str | None = None,
                    note: str | None = None) -> Dict[str, Any]:
    """Record a human approval through the entry point that is the approval's source.

    The hash chain records local ordering and detects accidental edits. Authority comes from
    the external event's signature and scope, checked before append and again on consumption;
    this function never creates the identity or decision of the approver.
    """
    root = Path(repo).resolve()
    channel_doc = _approval_channel(channel, channel_ref)
    if not channel_doc.get("type") or not channel_doc.get("ref"):
        return {"available": False, "error": "EVIDENCE_APPROVAL_CHANNEL_UNVERIFIED",
                "message": "an approval must name the channel it was decided through, and the "
                           "artifact of that channel that can be re-read"}
    ident = approval_id or f"appr-{uuid.uuid4().hex[:16]}"
    entry: Dict[str, Any] = {
        "id": ident,
        "subject": str(subject or work_item_id or ""),
        "work_item_id": str(work_item_id or subject or ""),
        "decision": str(decision),
        "approver": str(approver),
        "requirement_revision": requirement_revision,
        "fact_path": fact_path,
        "value": value,
        "channel": channel_doc,
        "decided_at": decided_at or datetime.now(timezone.utc).isoformat(),
        "note": note,
    }
    verified = verify_approval_channel(root, entry, policy=load_policy(root))
    if not verified.get("verified"):
        return {"available": False, "error": verified.get("error"),
                "message": "Import a signed, scope-bound event from a configured approval issuer; unsigned local event files are not approvals."}
    entry["channel"]["event_id"] = verified["event_id"]
    path = root / APPROVALS_REL
    existing = _read_json(path)
    entries: List[dict] = []
    if isinstance(existing, dict) and isinstance(existing.get("approvals"), list):
        entries = [e for e in existing["approvals"] if isinstance(e, dict)]
    elif isinstance(existing, list):
        entries = [e for e in existing if isinstance(e, dict)]
    prev = str(entries[-1].get("receipt")) if entries and entries[-1].get("receipt") else "genesis"
    stored = {**entry, "seq": len(entries), "prev": prev}
    stored["receipt"] = _approval_receipt(stored, prev)
    entries.append(stored)
    _atomic_write(path, {"schema_version": 1, "approvals": entries})
    return {"available": True, "id": ident, "path": APPROVALS_REL, "entry": stored}


def _approval_chain_ok(entries: List[dict], ident: str) -> bool:
    """Is this entry part of the chain the approval store actually is?

    This is an audit integrity check, not authentication: a writer could recompute hashes.
    The signed channel event is checked independently before any approval is trusted.
    """
    prev = "genesis"
    for entry in entries:
        if str(entry.get("id") or entry.get("approval_id") or "") == str(ident):
            return str(entry.get("prev") or "") == prev and \
                str(entry.get("receipt") or "") == _approval_receipt(entry, prev)
        if str(entry.get("prev") or "") != prev or \
                str(entry.get("receipt") or "") != _approval_receipt(entry, prev):
            return False
        prev = str(entry.get("receipt") or "")
    return False


def _approval_channel_events(doc: Any) -> List[dict]:
    for key in ("events", "approvals", "records", "entries"):
        if isinstance(doc, dict) and isinstance(doc.get(key), list):
            return [e for e in doc[key] if isinstance(e, dict)]
    if isinstance(doc, list):
        return [e for e in doc if isinstance(e, dict)]
    return [doc] if isinstance(doc, dict) else []


def verify_approval_channel(root: Path, entry: Dict[str, Any], *,
                            policy: dict | None = None) -> Dict[str, Any]:
    """Re-read the artifact of the channel this decision was made through."""
    channel = entry.get("channel") if isinstance(entry.get("channel"), dict) else {}
    ctype = str(channel.get("type") or "")
    cref = channel.get("ref")
    if not ctype or not cref:
        return {"verified": False, "error": "EVIDENCE_APPROVAL_CHANNEL_UNVERIFIED",
                "message": "the approval names no channel it was decided through"}
    if ctype not in trusted_approval_channels(policy):
        return {"verified": False, "error": "EVIDENCE_APPROVAL_CHANNEL_UNVERIFIED",
                "message": f"approval channel {ctype!r} is not a channel this project trusts"}
    doc = _read_doc(root, str(cref))
    if doc is None:
        return {"verified": False, "error": "EVIDENCE_APPROVAL_CHANNEL_UNVERIFIED",
                "message": f"the artifact of approval channel {ctype!r} ({cref}) cannot be read"}
    from approval_auth import authenticate
    config = ((policy or load_policy(root) or {}).get("evidence") or {})
    for event in _approval_channel_events(doc):
        if not authenticate(event, keys=config.get("approval_keys"),
                            audience=config.get("approval_audience", str(root.resolve())), channel=ctype):
            continue
        if channel.get("event_id") and event["id"] != channel["event_id"]:
            continue
        if any(event.get(key) != entry.get(key) for key in
               ("approver", "subject", "work_item_id", "requirement_revision", "decision", "fact_path", "value")):
            continue
        return {"verified": True, "error": None, "channel": ctype, "ref": str(cref), "event_id": event["id"]}
    return {"verified": False, "error": "EVIDENCE_APPROVAL_CHANNEL_UNVERIFIED",
            "message": f"no {ctype} records that {entry.get('approver')!r} decided "
                       f"{entry.get('decision')!r} for {entry.get('subject')!r}"}


def _collect_approval(repo: Path, record: Dict[str, Any], *, state: dict | None = None) -> Dict[str, Any]:
    """Independently read the approval that a claim points at. A claim never validates itself."""
    approval = record.get("approval") or {}
    source = str(approval.get("source") or "")
    if not source:
        return {"available": False, "error": "EVIDENCE_APPROVAL_UNRESOLVED",
                "message": "the approval names no source"}
    policy = load_policy(repo)
    if source not in approval_sources(policy):
        # A name in a file is not a person. An approval only counts as a source when it was
        # recorded into the store the project declares; anywhere else it is a copy.
        return {"available": False, "error": "EVIDENCE_APPROVAL_SOURCE_UNTRUSTED",
                "message": f"{source!r} is not an approval source this project declares; "
                           f"approved decisions are recorded in "
                           f"{', '.join(approval_sources(policy))}"}
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
    store = _read_doc(repo, resolved_ref)
    chain_ok = _approval_chain_ok(_approval_entries(store) if store is not None else [],
                                  str(matched.get("id") or matched.get("approval_id") or ""))
    channel = verify_approval_channel(repo, matched, policy=policy)
    result = {
        "available": True,
        "source": resolved_ref,
        "subject": observed_subject,
        "work_item_id": matched.get("work_item_id"),
        "decision": str(matched.get("decision") or matched.get("status") or ""),
        "approver": matched.get("approver") or matched.get("approved_by") or matched.get("reviewer"),
        "requirement_revision": matched.get("requirement_revision") or matched.get("revision"),
        "authority": matched.get("authority"),
        "fact_path": matched.get("fact_path"),
        "value": matched.get("value"),
        "chain_ok": chain_ok,
        "channel": channel.get("channel"),
        "channel_verified": bool(channel.get("verified")),
    }
    if subject and observed_subject and observed_subject != subject:
        result["error"] = "EVIDENCE_APPROVAL_SUBJECT_MISMATCH"
        result["message"] = (f"approval {wanted!r} was issued for {observed_subject!r}, "
                             f"not for the claimed subject {subject!r}")
    elif not chain_ok:
        result["error"] = "EVIDENCE_APPROVAL_SOURCE_UNTRUSTED"
        result["message"] = (f"approval {wanted!r} is not part of the chain in {resolved_ref}: "
                             f"it was not recorded by the approval entry point, so it does not "
                             f"say who approved anything")
    elif not channel.get("verified"):
        result["error"] = channel.get("error")
        result["message"] = channel.get("message")
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


# What a verified evidence record may be used for. "Verified" only says the material was
# checked; it does not say which conclusion it is. An approval that a requirement may be
# implemented is not a test result, not a code review, and not a final verification.
CONSUMER_PURPOSE_RULES: Dict[str, Dict[str, Any]] = {
    "gate": {"claim_types": frozenset({"gate_result", "test_result"}),
             "outcomes": SUCCESS_OUTCOMES},
    "review": {"claim_types": frozenset({"review_result", "code_review"}),
               "outcomes": SUCCESS_OUTCOMES},
    "verification": {"claim_types": frozenset({"verification", "verification_result"}),
                     "outcomes": SUCCESS_OUTCOMES},
}


def use_for_target(target: Optional[str]) -> Optional[str]:
    """Which purpose a result target is used for: `gate:unit` is a gate, `review` a review."""
    prefix = str(target or "").split(":", 1)[0].strip().lower()
    return prefix if prefix in CONSUMER_PURPOSE_RULES else None


def purpose_decision(use: Optional[str], result: Dict[str, Any], *,
                     target: Optional[str] = None) -> Dict[str, Any]:
    """May this conclusion be spent on this purpose?

    The consumer decides what it needs; the evidence does not get to relabel itself. A
    readiness approval, a gate result and a final verification are three different
    conclusions even when all three are "verified", and only the one that says what the
    consumer is asking about may be used for it.
    """
    rule = CONSUMER_PURPOSE_RULES.get(str(use or ""))
    if rule is None:
        return {"ok": True, "error": None}
    claim_type = str(result.get("claim_type") or "")
    if claim_type not in rule["claim_types"]:
        return {"ok": False, "error": "EVIDENCE_PURPOSE_MISMATCH",
                "message": f"a {claim_type or 'unlabelled'} claim is not a {use} result; "
                           f"{use} requires one of {sorted(rule['claim_types'])}"}
    if str(result.get("validation_status")) != "verified":
        return {"ok": False, "error": "EVIDENCE_PURPOSE_STATUS_NOT_VERIFIED",
                "message": f"the {claim_type} claim is {result.get('validation_status')} now"}
    if result.get("outcome") not in (rule.get("outcomes") or SUCCESS_OUTCOMES):
        return {"ok": False, "error": "EVIDENCE_PURPOSE_OUTCOME_UNSUCCESSFUL",
                "message": f"the {claim_type} claim says {result.get('outcome')!r}, "
                           f"which is not a successful {use} result"}
    claimed_target = result.get("target")
    if target and str(claimed_target or "") != str(target):
        return {"ok": False, "error": "EVIDENCE_PURPOSE_MISMATCH",
                "message": f"the {claim_type} claim was produced for {claimed_target!r}, "
                           f"not for {target!r}"}
    return {"ok": True, "error": None}


def _document_execution_ref(doc: Dict[str, Any]) -> Optional[str]:
    for key in ("execution_id", "execution_ref", "receipt"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = doc.get("execution")
    if isinstance(nested, str) and nested.strip():
        return nested.strip()
    return None


def result_document_binding(repo: Path, ref: Optional[str], *, target: str,
                            work_item_id: Optional[str] = None,
                            requirement_revision: Optional[str] = None,
                            code_revision: Optional[str] = None) -> Dict[str, Any]:
    """Bind a result document inside the project to the conclusion it is meant to carry.

    A document that says `{"status": "passed", "exit_code": 0, "command": "..."}` is an
    assertion: it names no execution that produced it, and anyone can write one. A result is
    bound to the receipt the execution entry point wrote when the command actually ran. That
    receipt carries the argv, the return code, the target and work item it was produced for,
    and the requirement and code revisions that were in force. All of them are re-read here
    from the receipt, and none of them is filled in from the consumer's current state.

    A document may be the receipt itself, or name one. Either way the stored receipt wins: a
    copy that edits a field is not the execution it points at.
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
    ident = _document_execution_ref(doc)
    if not ident:
        return fail("EVIDENCE_EXECUTION_NOT_RECORDED",
                    "the result document names no execution receipt; a passed status with a "
                    "command string says nothing about whether that command ran. Produce the "
                    "result through the execution entry point, which records what ran")
    stored = load_execution(root, ident)
    if stored is None:
        return fail("EVIDENCE_EXECUTION_NOT_RECORDED",
                    f"execution receipt {ident!r} is not in the execution store; only a command "
                    f"that was run through the execution entry point leaves a receipt there")
    conflicting = [key for key in ("status", "exit_code", "target", "work_item_id",
                                   "requirement_revision", "command", "code_revision_after")
                   if key in doc and doc[key] != stored.get(key)]
    if conflicting:
        return fail("EVIDENCE_EXECUTION_IDENTITY_MISMATCH",
                    f"the result document contradicts the recorded execution {ident} on "
                    f"{', '.join(sorted(conflicting))}; the receipt is what ran, the document "
                    f"is only what the caller wrote")
    if resolve_execution_document(root, doc) is None:
        return fail("EVIDENCE_EXECUTION_IDENTITY_MISMATCH", "Report fields differ from the recorded execution.")
    scope = execution_scope(stored, current_code=code_revision_now(root), work_item_id=work_item_id,
                            requirement_revision=requirement_revision, target=target)
    if not scope["ok"]:
        return fail(scope["error"], scope["message"])
    receipt_work_item = stored["work_item_id"]
    receipt_requirement = stored["requirement_revision"]
    receipt_code = stored["code_revision_after"]
    try:
        relative = candidate.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):  # pragma: no cover - defensive
        relative = str(ref)
    return {"available": True, "binding": {
        "kind": "result_document", "path": relative, "digest": digest, "status": "passed",
        "exit_code": 0, "claim_type": stored.get("claim_type"),
        "execution_id": str(ident), "execution_path": f"{EXECUTIONS_DIR}/{ident}.json",
        "target": str(target), "work_item_id": str(receipt_work_item),
        "requirement_revision": str(receipt_requirement),
        "code_revision": str(receipt_code), "snapshot_code_revision": code_revision,
        "producer": {"kind": "command", "command": [str(stored.get("command") or "")],
                     "execution_id": str(ident)}}}


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
        purpose = purpose_decision(use_for_target(target), result, target=target)
        if not purpose["ok"]:
            return bad(str(purpose["error"]), str(purpose["message"]))
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
    # The execution that produced the result is still on record, and still says the same thing
    # as the document that cites it. A document that stops agreeing with its receipt -- or
    # that never named one -- is no longer a result.
    stored = resolve_execution_document(root, doc)
    if stored is None:
        if _document_execution_ref(doc):
            return bad("EVIDENCE_RESULT_DRIFT",
                       f"the result document {path!r} no longer agrees with the execution "
                       f"receipt it names; the receipt is what ran")
        return bad("EVIDENCE_EXECUTION_NOT_RECORDED",
                   f"the result document {path!r} names no execution receipt; a passed status "
                   f"is an assertion, not a result")
    if not str(stored.get("command") or "").strip():
        return bad("EVIDENCE_RESULT_SOURCE_UNBOUND",
                   f"execution {stored.get('execution_id')} no longer records the command that ran")
    scope = execution_scope(stored, current_code=code_revision_now(root), work_item_id=work_item_id,
                            requirement_revision=requirement_revision, target=target)
    if not scope["ok"]:
        return bad(scope["error"], scope["message"])
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
    # The code revision was compared above, against the receipt of the run itself. A result is
    # about the code it ran on: when that code is not the code in force, it is stale, however
    # the verdict it recorded reads.
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
    drifted: List[str] = []
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
            # Every object that moved is reported, not only the first: which one invalidated
            # the evidence is a diagnosis the human has to be able to read.
            drifted.append(f"{dep.get('object_kind')}::{dep.get('object_id')}")
    if drifted:
        result["drifted_dependencies"] = list(drifted)
        result["drifted_dependency"] = drifted[0]
        return invalid("EVIDENCE_DEPENDENCY_DRIFT")

    # Mandatory concrete objects: a project may require the real code under test, not just
    # "some code". Kinds remain the floor; the policy may only add objects.
    for obj in required_objects(claim_type=claim_type, policy=policy):
        if not any(str(d.get("object_kind")) == obj["object_kind"]
                   and str(d.get("object_id")) == obj["object_id"] for d in depends_on):
            result["missing_object"] = obj
            return invalid("EVIDENCE_OBJECT_MISSING")
    # The conservative code object is derived here, not picked by the caller: a claim may add
    # finer objects, never stand a different tree in for the one the consumer requires.
    if DERIVED_CODE_DEPENDENCY in required:
        for dep in depends_on:
            if str(dep.get("object_kind")) != DERIVED_CODE_DEPENDENCY:
                continue
            if str(dep.get("object_id") or ".") not in DERIVED_CODE_OBJECT_IDS:
                result["missing_object"] = {"object_kind": DERIVED_CODE_DEPENDENCY,
                                            "object_id": "."}
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

    if kind == "mechanical_observation":
        observed_report = (inputs or {}).get("report") or {}
        if record.get("report") and not observed_report.get("available"):
            return invalid("EVIDENCE_REPORT_UNPARSABLE")
        if observed_report.get("digest_mismatch"):
            return invalid("EVIDENCE_REPORT_DRIFT")
        if (observed_report.get("empty_report") or observed_report.get("exit_code") is None
                or not observed_report.get("execution")):
            return unverified("EVIDENCE_EXECUTION_UNBOUND")
        receipt = observed_report.get("execution_receipt")
        scope = execution_scope(receipt, current_code=(inputs or {}).get("code_revision"),
                                work_item_id=work_item_id or record.get("work_item_id"),
                                requirement_revision=requirement_revision or record.get("requirement_revision"),
                                claim_type=claim_type)
        if not scope["ok"]:
            return unverified(scope["error"])
        result["target"] = receipt["target"]
        result["observed_output"] = receipt.get("output")
        if receipt.get("fact_path") and not (inputs or {}).get("observer_valid"):
            return unverified("EVIDENCE_OBSERVER_UNTRUSTED")

    # Report binding: claimed outcome must match the report that was actually produced.
    report = record.get("report")
    if report and kind == "mechanical_observation":
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

    # What a trusted source actually observed about one predicate. It is read from the source
    # that produced it, never from a field the caller attached to its own claim.
    observed_fact = _observed_fact(record, inputs)
    if observed_fact:
        result["observed_fact"] = observed_fact

    # Trust can only come from a mechanically checked source or a checked approval.
    if kind == "agent_claim":
        return result  # stays unverified; needs support material or a real approval
    if kind == "mechanical_observation":
        observed_report = (inputs or {}).get("report") or {}
        if observed_report.get("status") not in OUTCOMES:
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
