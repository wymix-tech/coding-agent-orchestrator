#!/usr/bin/env python3
"""V6.4 Session Bootstrap & Handoff Context.

Canonical session artifacts are JSON. YAML config controls projection. Markdown is the
prompt-facing view injected into coding-agent hosts. Session artifacts are projections
of authoritative SDD/Decision/State/Impact/Policy/Evidence and never become authority.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

import context_plane
import execution_state_manager as sm

SCHEMA_VERSION = 1
ROLES = {"planner", "implementer", "reviewer", "verifier", "debugger", "resume"}
SESSION_TYPES = {"fresh_project", "session_resume", "agent_handoff"}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "enabled": True,
    "canonical_format": "json",
    "prompt_format": "markdown",
    "bootstrap": {
        "max_chars": 7000,
        "recent_events": 5,
        "include_context_pack_summary": True,
        "include_latest_handoff": True,
        "include_policy_ids": True,
        "include_gate_summary": True,
    },
    "handoff": {
        "max_chars": 9000,
        "auto_collect_changed_files": True,
        "require_summary": True,
        "require_snapshot_binding": True,
    },
    "injection": {
        "session_start": True,
        "session_resume": True,
        "subagent_start": True,
        "prompt_submit": "delta_only",
    },
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha_obj(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is required for session-context YAML config")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def repo_root(cwd: str | Path) -> Path:
    cwd = Path(cwd).resolve()
    try:
        root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
        return Path(root).resolve()
    except Exception:
        return cwd


def load_config(repo: Path) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    p = repo / ".orchestrator" / "session-context.yaml"
    if not p.exists():
        return cfg
    raw = _load_yaml(p) or {}
    if not isinstance(raw, dict):
        raise ValueError(".orchestrator/session-context.yaml must be an object")
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def state_path(repo: Path) -> Path:
    return repo / ".orchestrator" / "execution-state.yaml"


def session_dir(repo: Path) -> Path:
    return repo / ".orchestrator" / "session"


def _rel(repo: Path, path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except Exception:
        return str(path.resolve())


def _resolve(repo: Path, ref: Optional[str]) -> Optional[Path]:
    if not ref:
        return None
    p = Path(ref)
    return p if p.is_absolute() else (repo / p)


def _manifest(repo: Path, state: Optional[dict[str, Any]]) -> tuple[Optional[Path], Optional[dict[str, Any]]]:
    candidates: list[Path] = []
    if state:
        ref = (state.get("analysis") or {}).get("context_manifest_ref")
        p = _resolve(repo, ref)
        if p:
            candidates.append(p)
    candidates.append(repo / ".orchestrator" / "intake" / "context-manifest.json")
    for p in candidates:
        if p.exists():
            try:
                return p, _load_json(p)
            except Exception:
                continue
    return None, None


def _pack(repo: Path, state: Optional[dict[str, Any]], role: str) -> tuple[Optional[Path], Optional[dict[str, Any]]]:
    candidates: list[Path] = []
    if state:
        ref = (state.get("analysis") or {}).get("context_pack_ref")
        p = _resolve(repo, ref)
        if p:
            candidates.append(p)
    candidates.extend(sorted((repo / ".orchestrator" / "intake").glob(f"context-pack.{role}.*.json"), reverse=True))
    for p in candidates:
        if p.exists():
            try:
                doc = _load_json(p)
                if doc.get("role") == role or not doc.get("role"):
                    return p, doc
            except Exception:
                continue
    return None, None


def _history(repo: Path, limit: int) -> list[dict[str, Any]]:
    p = repo / ".orchestrator" / "execution-history.jsonl"
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                rows.append(d)
        except Exception:
            continue
    compact: list[dict[str, Any]] = []
    for d in rows[-max(limit, 0):]:
        compact.append({
            "at": d.get("timestamp"),
            "event": d.get("event"),
            "actor": d.get("actor"),
            "revision_after": d.get("revision_after"),
            "phase": d.get("to_phase") or d.get("phase"),
            "status": d.get("to_status") or d.get("status"),
            "reason": d.get("reason") or d.get("resolution"),
        })
    return compact


def _changed_files(repo: Path) -> list[str]:
    try:
        out = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=repo, text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    result: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        p = line[3:]
        if " -> " in p:
            p = p.split(" -> ", 1)[1]
        p = p.replace("\\", "/")
        if p.startswith(".orchestrator/"):
            continue
        result.append(p)
    return sorted(set(result))


def _latest_handoff(repo: Path) -> tuple[Optional[Path], Optional[dict[str, Any]]]:
    p = session_dir(repo) / "latest-handoff.json"
    if not p.exists():
        return None, None
    try:
        return p, _load_json(p)
    except Exception:
        return p, None


def _gate_summary(state: dict[str, Any]) -> dict[str, Any]:
    gates = state.get("quality_gates") or {}
    required_pending = []
    failed = []
    passed = []
    for name, gate in sorted(gates.items()):
        status = gate.get("status")
        if gate.get("required") and status != "passed":
            required_pending.append(name)
        if status == "failed":
            failed.append(name)
        if status == "passed":
            passed.append(name)
    return {
        "required_not_passed": required_pending,
        "failed": failed,
        "passed": passed,
        "review": state.get("review") or {},
        "verification": state.get("verification") or {},
    }


def _policy_ids(repo: Path, state: dict[str, Any]) -> list[str]:
    ref = (state.get("analysis") or {}).get("policy_plan_ref")
    p = _resolve(repo, ref)
    if not p or not p.exists():
        return []
    try:
        doc = _load_json(p)
    except Exception:
        return []
    result = []
    for rule in doc.get("applicable_rules") or []:
        if not isinstance(rule, dict):
            continue
        if str(rule.get("level") or "").upper() == "MUST" or rule.get("blocking") is True:
            if rule.get("id"):
                result.append(str(rule["id"]))
    return result[:20]



def _authority_fingerprint(repo: Path, manifest: Optional[dict[str, Any]]) -> Optional[str]:
    if not manifest:
        return None
    excluded = {"execution_state", "execution_history"}
    items: list[dict[str, Any]] = []
    for src in manifest.get("sources") or []:
        sid = src.get("id")
        if sid in excluded:
            continue
        ref = src.get("ref")
        p = _resolve(repo, ref)
        current_sha = None
        if p and p.exists():
            try:
                current_sha = context_plane._path_digest(p)[0]
            except Exception:
                current_sha = "unreadable"
        else:
            current_sha = "missing"
        items.append({
            "id": sid, "ref": ref, "authority": src.get("authority"),
            "snapshot_id": src.get("snapshot_id"), "content_sha256": current_sha,
        })
    return _sha_obj(sorted(items, key=lambda x: str(x.get("id"))))[:24]

def validate_handoff(repo: Path, handoff: dict[str, Any], state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    state = state or (sm._load(state_path(repo)) if state_path(repo).exists() else None)
    stale_reasons: list[str] = []
    valid_for = handoff.get("valid_for") or {}
    _, manifest = _manifest(repo, state)
    if state is None:
        stale_reasons.append("canonical execution state is missing")
    else:
        if valid_for.get("work_item_id") and valid_for.get("work_item_id") != (state.get("work_item") or {}).get("id"):
            stale_reasons.append("work item changed")
        # Handoffs are allowed to survive state revision advancement, but not an execution snapshot change.
        if valid_for.get("execution_snapshot_id") and valid_for.get("execution_snapshot_id") != state.get("execution_snapshot_id"):
            stale_reasons.append("execution snapshot changed")
        if valid_for.get("analysis_snapshot_id") and valid_for.get("analysis_snapshot_id") != (state.get("analysis") or {}).get("analysis_snapshot_id"):
            stale_reasons.append("analysis snapshot changed")
    expected_authority = valid_for.get("authority_fingerprint")
    current_authority = _authority_fingerprint(repo, manifest)
    if expected_authority and current_authority and expected_authority != current_authority:
        stale_reasons.append("requirement/decision/policy/evidence authority changed")
    return {"status": "STALE" if stale_reasons else "FRESH", "reasons": stale_reasons, "authority_fingerprint": current_authority}


def infer_session_type(state: Optional[dict[str, Any]], handoff: Optional[dict[str, Any]], handoff_fresh: bool) -> str:
    if state is None:
        return "fresh_project"
    if handoff is not None and handoff_fresh:
        return "agent_handoff"
    return "session_resume"


def build_bootstrap(repo: Path, *, role: str = "implementer", session_type: str = "auto", host: Optional[str] = None,
                    session_id: Optional[str] = None, max_chars: Optional[int] = None) -> dict[str, Any]:
    repo = repo.resolve()
    cfg = load_config(repo)
    sp = state_path(repo)
    state = sm._load(sp) if sp.exists() else None
    manifest_path, manifest = _manifest(repo, state)
    pack_path, pack = _pack(repo, state, role)
    handoff_path, handoff = _latest_handoff(repo)
    handoff_status = validate_handoff(repo, handoff, state) if handoff else {"status": "MISSING", "reasons": []}
    handoff_applicable = bool(handoff and (handoff.get("to_role") == role or role == "resume"))
    stype = infer_session_type(state, handoff if handoff_applicable else None, handoff_applicable and handoff_status.get("status") == "FRESH") if session_type == "auto" else session_type
    if stype not in SESSION_TYPES:
        raise ValueError(f"invalid session_type: {stype}")

    if state is None:
        project_initialized = (repo / ".orchestrator" / "config.yaml").exists()
        if project_initialized:
            bootstrap_status = "READY_FOR_INTAKE"
            blockers = []
            next_action = "run_intake_for_first_work_item"
        else:
            bootstrap_status = "UNBOOTSTRAPPED"
            blockers = [{"code": "PROJECT_NOT_BOOTSTRAPPED", "detail": "Orchestrator project configuration is missing"}]
            next_action = "run_safe_auto_init"
        doc: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "kind": "session_bootstrap",
            "generated_at": utc_now(),
            "session_type": stype,
            "host": host,
            "session_id": session_id,
            "role": role,
            "status": bootstrap_status,
            "project": {"repo": str(repo), "name": repo.name},
            "current": None,
            "authorities": {},
            "blockers": blockers,
            "next_action": next_action,
            "latest_handoff": None,
            "source_of_truth": "This bootstrap is a prompt projection only; project configuration and later SDD/Decision/Execution State/Policy/Evidence remain authoritative.",
        }
    else:
        resume = sm.resume_summary(state, repo)
        manifest_fresh = context_plane.validate_manifest(repo, manifest) if manifest else {"status": "MISSING", "changes": []}
        pack_valid = None
        if pack:
            vf = pack.get("valid_for") or {}
            pack_valid = (
                vf.get("state_revision") == state.get("revision")
                and vf.get("execution_snapshot_id") == state.get("execution_snapshot_id")
                and (manifest is None or pack.get("context_snapshot_id") == manifest.get("context_snapshot_id"))
            )
        blockers = list((manifest or {}).get("blockers") or [])
        if manifest_fresh.get("status") != "FRESH":
            blockers.append({"code": "CONTEXT_MANIFEST_STALE", "detail": ", ".join(manifest_fresh.get("changes") or manifest_fresh.get("reasons") or []) or manifest_fresh.get("status")})
        if pack is not None and not pack_valid:
            blockers.append({"code": "CONTEXT_PACK_STALE", "detail": "role/stage pack no longer matches current state/context snapshot"})

        current = {
            "work_item": state.get("work_item"),
            "flow_profile": state.get("flow_profile"),
            "phase": state.get("phase"),
            "status": state.get("status"),
            "blocked": state.get("blocked"),
            "blockers": state.get("blockers") or [],
            "current_task": {
                "id": (state.get("cursor") or {}).get("current_task_id"),
                "title": (state.get("cursor") or {}).get("current_task_title"),
            },
            "next_action": (state.get("cursor") or {}).get("next_action"),
            "progress": (state.get("work_item") or {}).get("progress"),
            "revision": state.get("revision"),
        }
        authorities = (manifest or {}).get("authorities") or {}
        snapshots = (manifest or {}).get("snapshots") or {
            "analysis_snapshot_id": (state.get("analysis") or {}).get("analysis_snapshot_id"),
            "execution_snapshot_id": state.get("execution_snapshot_id"),
            "verification_snapshot_id": (state.get("verification") or {}).get("snapshot_id"),
            "verification_fresh": (state.get("verification") or {}).get("fresh"),
        }
        pack_summary = None
        if cfg["bootstrap"].get("include_context_pack_summary", True) and pack:
            pack_summary = {
                "ref": _rel(repo, pack_path),
                "role": pack.get("role"),
                "stage": pack.get("stage"),
                "pack_snapshot_id": pack.get("pack_snapshot_id"),
                "valid": pack_valid,
                "selected": [{"id": x.get("id"), "ref": x.get("ref"), "mandatory": x.get("mandatory"), "summary": x.get("summary")} for x in (pack.get("selected") or [])[:12]],
            }
        latest_handoff = None
        if cfg["bootstrap"].get("include_latest_handoff", True) and handoff and handoff_applicable:
            latest_handoff = {
                "ref": _rel(repo, handoff_path),
                "handoff_id": handoff.get("handoff_id"),
                "from_role": handoff.get("from_role"),
                "to_role": handoff.get("to_role"),
                "summary": handoff.get("summary"),
                "completed_task": handoff.get("completed_task"),
                "next_action": handoff.get("next_action"),
                "known_risks": handoff.get("known_risks") or [],
                "assumptions": handoff.get("assumptions") or [],
                "freshness": handoff_status,
            }
        doc = {
            "schema_version": SCHEMA_VERSION,
            "kind": "session_bootstrap",
            "generated_at": utc_now(),
            "session_type": stype,
            "host": host,
            "session_id": session_id,
            "role": role,
            "status": "READY" if not blockers else "ATTENTION_REQUIRED",
            "project": {"repo": str(repo), "name": repo.name},
            "current": current,
            "authorities": authorities,
            "snapshots": snapshots,
            "context": {
                "manifest_ref": _rel(repo, manifest_path),
                "manifest_status": manifest_fresh.get("status"),
                "context_snapshot_id": (manifest or {}).get("context_snapshot_id"),
                "pack": pack_summary,
            },
            "required_policy_ids": _policy_ids(repo, state) if cfg["bootstrap"].get("include_policy_ids", True) else [],
            "evidence": _gate_summary(state) if cfg["bootstrap"].get("include_gate_summary", True) else None,
            "recent_events": _history(repo, int(cfg["bootstrap"].get("recent_events", 5))),
            "latest_handoff": latest_handoff,
            "blockers": blockers,
            "next_action": resume.get("next_action"),
            "source_of_truth": "This bootstrap is a prompt projection only; SDD/Decision/Execution State/Policy/Evidence remain authoritative.",
        }
    doc["bootstrap_snapshot_id"] = _sha_obj({k: v for k, v in doc.items() if k not in {"generated_at", "bootstrap_snapshot_id"}})[:24]
    if max_chars is not None:
        doc["render_budget_chars"] = max_chars
    return doc


def render_bootstrap(doc: dict[str, Any], *, max_chars: Optional[int] = None) -> str:
    if doc.get("status") in {"UNBOOTSTRAPPED", "READY_FOR_INTAKE", "UNINITIALIZED"}:
        status = doc.get("status")
        detail = (
            "Project bootstrap has not run yet; Safe Auto initialization is required before normal work."
            if status == "UNBOOTSTRAPPED"
            else "Project bootstrap is complete and no active work item exists yet."
        )
        text = "\n".join([
            "# Orchestrator Session Bootstrap",
            "",
            f"- Status: **{status}**",
            f"- {detail}",
            "- Do not mutate production code before intake/classification.",
            f"- Next action: `{doc.get('next_action')}`",
            "",
            "> This bootstrap is a projection, not a source of truth.",
        ]) + "\n"
        return text[:max_chars] if max_chars else text
    c = doc.get("current") or {}
    wi = c.get("work_item") or {}
    ct = c.get("current_task") or {}
    lines = [
        "# Orchestrator Session Bootstrap",
        "",
        f"- Session: `{doc.get('session_type')}` | Role: `{doc.get('role')}` | Status: `{doc.get('status')}`",
        f"- Work item: `{wi.get('id')}` {wi.get('title') or ''}",
        f"- Flow: `{c.get('flow_profile')}` | Phase/status: `{c.get('phase')}` / `{c.get('status')}` | Revision: `{c.get('revision')}`",
        f"- Current task: `{ct.get('id')}` {ct.get('title') or ''}",
        f"- Progress: `{(c.get('progress') or {}).get('completed')}/{(c.get('progress') or {}).get('total')}`",
        f"- Next action: `{doc.get('next_action')}`",
    ]
    blockers = doc.get("blockers") or []
    if blockers:
        lines += ["", "## Attention / blockers"]
        for b in blockers[:10]:
            lines.append(f"- **{b.get('code')}**: {b.get('detail')}")
    handoff = doc.get("latest_handoff")
    if handoff and (handoff.get("freshness") or {}).get("status") == "FRESH":
        lines += ["", "## Previous handoff"]
        if handoff.get("completed_task"):
            t = handoff.get("completed_task") or {}
            lines.append(f"- Completed: `{t.get('id')}` {t.get('title') or ''}")
        for s in handoff.get("summary") or []:
            lines.append(f"- {s}")
        if handoff.get("known_risks"):
            lines.append("- Known risks: " + "; ".join(str(x) for x in handoff.get("known_risks")[:6]))
        if handoff.get("next_action"):
            lines.append(f"- Handoff next action: `{handoff.get('next_action')}`")
    policy_ids = doc.get("required_policy_ids") or []
    if policy_ids:
        lines += ["", "## Applicable MUST policy IDs", "- " + ", ".join(f"`{x}`" for x in policy_ids[:20])]
    ev = doc.get("evidence") or {}
    if ev:
        req = ev.get("required_not_passed") or []
        ver = ev.get("verification") or {}
        review = ev.get("review") or {}
        lines += ["", "## Evidence status"]
        lines.append(f"- Review: `{review.get('status')}` | blocking findings: `{review.get('blocking_findings')}`")
        lines.append(f"- Verification: `{ver.get('status')}` | fresh: `{ver.get('fresh')}` | snapshot: `{ver.get('snapshot_id')}`")
        lines.append(f"- Required gates not passed: `{', '.join(req) if req else 'none'}`")
    ctx = doc.get("context") or {}
    pack = ctx.get("pack") or {}
    lines += ["", "## Context pointers"]
    lines.append(f"- Manifest: `{ctx.get('manifest_ref')}` ({ctx.get('manifest_status')})")
    if pack:
        lines.append(f"- Role pack: `{pack.get('ref')}` | valid: `{pack.get('valid')}`")
        selected = [x.get("id") for x in pack.get("selected") or [] if x.get("id")]
        if selected:
            lines.append("- Selected sources: " + ", ".join(f"`{x}`" for x in selected[:12]))
    lines += [
        "",
        "## Context discipline",
        "- Treat this as a compact projection only. Follow the referenced authorities for exact truth.",
        "- Do not trust stale Context/Impact/Verification snapshots after material changes.",
        "- Lazy-retrieve full graph/history/rule corpora only when the current step needs them.",
    ]
    text = "\n".join(lines) + "\n"
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[bootstrap truncated by configured prompt budget]\n"
    return text


def build_handoff(repo: Path, *, from_role: str, to_role: str, summary: list[str], completed_task_id: Optional[str] = None,
                  completed_task_title: Optional[str] = None, next_action: Optional[str] = None, assumptions: Optional[list[str]] = None,
                  known_risks: Optional[list[str]] = None, changed_files: Optional[list[str]] = None, evidence_refs: Optional[list[str]] = None,
                  host: Optional[str] = None, session_id: Optional[str] = None) -> dict[str, Any]:
    repo = repo.resolve()
    cfg = load_config(repo)
    if cfg["handoff"].get("require_summary", True) and not [x for x in summary if str(x).strip()]:
        raise ValueError("handoff requires at least one summary item")
    sp = state_path(repo)
    if not sp.exists():
        raise ValueError("Canonical Execution State is required for handoff")
    state = sm._load(sp)
    manifest_path, manifest = _manifest(repo, state)
    changed = list(changed_files or [])
    if cfg["handoff"].get("auto_collect_changed_files", True):
        changed = sorted(set(changed + _changed_files(repo)))
    completed_task = None
    if completed_task_id or completed_task_title:
        completed_task = {"id": completed_task_id, "title": completed_task_title}
    if completed_task is None:
        cursor = state.get("cursor") or {}
        if cursor.get("current_task_id") or cursor.get("current_task_title"):
            completed_task = {"id": cursor.get("current_task_id"), "title": cursor.get("current_task_title")}
    valid_for = {
        "work_item_id": (state.get("work_item") or {}).get("id"),
        "execution_snapshot_id": state.get("execution_snapshot_id"),
        "analysis_snapshot_id": (state.get("analysis") or {}).get("analysis_snapshot_id"),
        "context_snapshot_id": (manifest or {}).get("context_snapshot_id"),
        "authority_fingerprint": _authority_fingerprint(repo, manifest),
        "state_revision_at_handoff": state.get("revision"),
    }
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "task_handoff",
        "generated_at": utc_now(),
        "from_role": from_role,
        "to_role": to_role,
        "host": host,
        "session_id": session_id,
        "work_item": state.get("work_item"),
        "phase": state.get("phase"),
        "flow_profile": state.get("flow_profile"),
        "completed_task": completed_task,
        "summary": [str(x).strip() for x in summary if str(x).strip()],
        "changed_files": changed,
        "assumptions": [str(x).strip() for x in (assumptions or []) if str(x).strip()],
        "known_risks": [str(x).strip() for x in (known_risks or []) if str(x).strip()],
        "pending": {
            "next_action": next_action or (state.get("cursor") or {}).get("next_action"),
            "required_gates_not_passed": _gate_summary(state).get("required_not_passed") or [],
            "review": (state.get("review") or {}).get("status"),
            "verification": {
                "status": (state.get("verification") or {}).get("status"),
                "fresh": (state.get("verification") or {}).get("fresh"),
            },
        },
        "next_action": next_action or (state.get("cursor") or {}).get("next_action"),
        "evidence_refs": evidence_refs or [],
        "valid_for": valid_for,
        "authority_refs": (manifest or {}).get("authorities") or {},
        "source_of_truth": "Handoff is a resumability projection. Execution State/SDD/Evidence remain authoritative.",
    }
    doc["handoff_id"] = _sha_obj({k: v for k, v in doc.items() if k not in {"generated_at", "handoff_id"}})[:24]
    return doc


def render_handoff(doc: dict[str, Any], *, max_chars: Optional[int] = None) -> str:
    wi = doc.get("work_item") or {}
    task = doc.get("completed_task") or {}
    pending = doc.get("pending") or {}
    lines = [
        "# Orchestrator Task Handoff",
        "",
        f"- Handoff: `{doc.get('handoff_id')}`",
        f"- From/To: `{doc.get('from_role')}` → `{doc.get('to_role')}`",
        f"- Work item: `{wi.get('id')}` {wi.get('title') or ''}",
        f"- Flow/phase: `{doc.get('flow_profile')}` / `{doc.get('phase')}`",
        f"- Completed task: `{task.get('id')}` {task.get('title') or ''}",
        "",
        "## What changed",
    ]
    for x in doc.get("summary") or []:
        lines.append(f"- {x}")
    if doc.get("changed_files"):
        lines += ["", "## Changed files"] + [f"- `{x}`" for x in doc.get("changed_files")[:40]]
    if doc.get("assumptions"):
        lines += ["", "## Assumptions"] + [f"- {x}" for x in doc.get("assumptions")[:20]]
    if doc.get("known_risks"):
        lines += ["", "## Known risks"] + [f"- {x}" for x in doc.get("known_risks")[:20]]
    lines += ["", "## Pending / next"]
    lines.append(f"- Next action: `{doc.get('next_action')}`")
    lines.append(f"- Required gates not passed: `{', '.join(pending.get('required_gates_not_passed') or []) or 'none'}`")
    lines.append(f"- Review: `{pending.get('review')}`")
    ver = pending.get("verification") or {}
    lines.append(f"- Verification: `{ver.get('status')}` | fresh: `{ver.get('fresh')}`")
    lines += ["", "## Handoff discipline", "- Revalidate this handoff against current Execution/Analysis snapshots before relying on it.", "- This handoff does not override SDD, State, Policy, or Evidence authority."]
    text = "\n".join(lines) + "\n"
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[handoff truncated by configured prompt budget]\n"
    return text


def persist_bootstrap(repo: Path, doc: dict[str, Any]) -> tuple[Path, Path]:
    d = session_dir(repo)
    jp = d / "session-bootstrap.json"
    mp = d / "session-bootstrap.md"
    cfg = load_config(repo)
    _write_json(jp, doc)
    _write_text(mp, render_bootstrap(doc, max_chars=int(cfg["bootstrap"].get("max_chars", 7000))))
    return jp, mp


def persist_handoff(repo: Path, doc: dict[str, Any]) -> tuple[Path, Path]:
    d = session_dir(repo)
    hid = doc["handoff_id"]
    jp = d / f"handoff-{hid}.json"
    mp = d / f"handoff-{hid}.md"
    cfg = load_config(repo)
    _write_json(jp, doc)
    _write_text(mp, render_handoff(doc, max_chars=int(cfg["handoff"].get("max_chars", 9000))))
    # Stable aliases are copies, not symlinks, for cross-platform behavior.
    _write_json(d / "latest-handoff.json", doc)
    _write_text(d / "latest-handoff.md", render_handoff(doc, max_chars=int(cfg["handoff"].get("max_chars", 9000))))
    return jp, mp


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="V6.4 Session Bootstrap & Handoff Context")
    p.add_argument("--repo", type=Path, default=Path("."))
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("bootstrap")
    b.add_argument("--role", default="implementer", choices=sorted(ROLES))
    b.add_argument("--session-type", default="auto", choices=["auto"] + sorted(SESSION_TYPES))
    b.add_argument("--host")
    b.add_argument("--session-id")
    b.add_argument("--stdout", choices=["json", "markdown"])

    h = sub.add_parser("handoff")
    h.add_argument("--from-role", required=True)
    h.add_argument("--to-role", required=True)
    h.add_argument("--summary", action="append", required=True)
    h.add_argument("--completed-task-id")
    h.add_argument("--completed-task-title")
    h.add_argument("--next-action")
    h.add_argument("--assumption", action="append", default=[])
    h.add_argument("--risk", action="append", default=[])
    h.add_argument("--changed-file", action="append", default=[])
    h.add_argument("--evidence-ref", action="append", default=[])
    h.add_argument("--host")
    h.add_argument("--session-id")
    h.add_argument("--stdout", choices=["json", "markdown"])

    v = sub.add_parser("validate-handoff")
    v.add_argument("--file", type=Path)

    args = p.parse_args(argv)
    repo = repo_root(args.repo)
    if args.command == "bootstrap":
        doc = build_bootstrap(repo, role=args.role, session_type=args.session_type, host=args.host, session_id=args.session_id)
        jp, mp = persist_bootstrap(repo, doc)
        if args.stdout == "json":
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        elif args.stdout == "markdown":
            print(mp.read_text(encoding="utf-8"), end="")
        else:
            print(json.dumps({"json": _rel(repo, jp), "markdown": _rel(repo, mp), "bootstrap_snapshot_id": doc["bootstrap_snapshot_id"]}, ensure_ascii=False))
        return 0
    if args.command == "handoff":
        doc = build_handoff(repo, from_role=args.from_role, to_role=args.to_role, summary=args.summary,
                            completed_task_id=args.completed_task_id, completed_task_title=args.completed_task_title,
                            next_action=args.next_action, assumptions=args.assumption, known_risks=args.risk,
                            changed_files=args.changed_file, evidence_refs=args.evidence_ref, host=args.host, session_id=args.session_id)
        jp, mp = persist_handoff(repo, doc)
        if args.stdout == "json":
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        elif args.stdout == "markdown":
            print(mp.read_text(encoding="utf-8"), end="")
        else:
            print(json.dumps({"json": _rel(repo, jp), "markdown": _rel(repo, mp), "handoff_id": doc["handoff_id"]}, ensure_ascii=False))
        return 0
    hp = args.file or (session_dir(repo) / "latest-handoff.json")
    if not hp.is_absolute():
        hp = repo / hp
    if not hp.exists():
        print(json.dumps({"status": "MISSING", "file": _rel(repo, hp)}))
        return 2
    result = validate_handoff(repo, _load_json(hp))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "FRESH" else 3


if __name__ == "__main__":
    raise SystemExit(main())
