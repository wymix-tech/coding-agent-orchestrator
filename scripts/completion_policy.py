"""Pure requirement, acceptance, and completion-evidence bindings."""

import hashlib
import json
import math
import time
from pathlib import Path

from scripts.snapshot_version import SNAPSHOT_FORMAT

SNAPSHOT_KEYS = ("snapshot_format", "requirement_sha256", "code_snapshot", "rules_snapshot",
                 "verification_config_sha256", "assessment_revision")


def normalize_scripts(raw: list) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("verification.scripts must be a list")
    result, names = [], set()
    for index, item in enumerate(raw, 1):
        if isinstance(item, str):
            item = {"command": item}
        if not isinstance(item, dict):
            raise ValueError("VERIFICATION_SCRIPT_INVALID")
        command = str(item.get("command") or "").strip()
        name = str(item.get("name") or f"script-{index}").strip()
        required = item.get("required", True)
        gates = item.get("gates", [])
        if not command or not name or name in names or not isinstance(required, bool):
            raise ValueError("VERIFICATION_SCRIPT_INVALID")
        if not isinstance(gates, list) or any(not isinstance(gate, str) or gate not in {"tests", "security_verification"} for gate in gates):
            raise ValueError("VERIFICATION_GATE_INVALID")
        names.add(name)
        result.append({"name": name, "command": command, "required": required,
                       "gates": sorted(set(gates))})
    return result


def required_entries(items: list[dict]) -> list[dict]:
    return [item for item in items if item.get("required") is True]


def required_verification_status(results: list[dict]) -> str:
    required = required_entries(results)
    if not required: return "blocked"
    statuses = {item.get("status") for item in required}
    for status in ("error", "blocked", "failed"):
        if status in statuses: return status
    return "passed" if statuses == {"passed"} else "blocked"


def tests_satisfied(verification: dict) -> bool:
    return capability_satisfied(verification, 'tests')


def capability_satisfied(verification: dict, capability: str) -> bool:
    results = [result for result in verification.get("results", [])
               if result.get("required") and capability in result.get("gates", [])]
    return bool(results) and all(result.get("status") == "passed" for result in results)


def normalize_timeout(value=300.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("VERIFICATION_LIMIT_INVALID")
    return float(value)


def acceptance_hash(scripts: list[dict], timeout_seconds=300.0) -> str:
    encoded = json.dumps({"scripts": scripts, "timeout_seconds": normalize_timeout(timeout_seconds)}, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def anchor_acceptance(work: dict, scripts: list[dict], timeout_seconds=300.0) -> None:
    work["acceptance"] = {"version": 2, "config_sha256": acceptance_hash(scripts, timeout_seconds),
                          "anchored_at": int(time.time())}


def reset_requirement(work: dict, requirement: dict) -> bool:
    old = work.get("requirement") or {}
    changed = any(old.get(key) != requirement.get(key) for key in ("path", "sha256"))
    work["requirement"] = requirement
    if changed:
        work["path_assessment"] = None
        work["gate_status"] = {}
        work["verification"] = None
        sdd = work.setdefault("sdd", {})
        sdd.pop("spec_binding", None)
        sdd.pop("acceptance", None)
        previous = sdd.get("workflow") or {}
        sdd["workflow"] = {key: previous[key] for key in ("name", "entrypoint") if key in previous}
        if sdd["workflow"]:
            sdd["workflow"]["status"] = "selected"
        sdd.pop("plan_scope", None)
        sdd.pop("spec_task_scope", None)
        sdd.pop("native_state", None)
        hint = sdd.pop("native_ref", None)
        if hint:
            work.setdefault("resume", {})["native_ref_hint"] = hint
        if sdd.get("artifacts"):
            work.setdefault("resume", {})["previous_sdd_artifacts"] = sdd["artifacts"]
            sdd["artifacts"] = []
    return changed


def evidence_matches(work: dict, evidence: dict) -> bool:
    assessment = work.get("path_assessment") or {}
    requirement_sha = (work.get("requirement") or {}).get("sha256")
    return bool(evidence and requirement_sha and evidence.get("requirement_sha256") == requirement_sha
                and evidence.get("assessment_revision") == assessment.get("revision")
                and assessment.get("revision") is not None)


def workflow_ready(work: dict, repo: Path) -> tuple[bool, str | None]:
    sdd = work.get("sdd") or {}
    workflow = sdd.get("workflow") or {}
    if workflow.get("status") != "completed":
        return False, "native SDD workflow is not recorded as completed"
    if (workflow.get("requirement_sha256") != (work.get("requirement") or {}).get("sha256")
            or workflow.get("provider") != sdd.get("provider")):
        return False, "native SDD workflow evidence is stale"
    execution = workflow.get("execution") or {}
    if not execution and not (workflow.get("unverified") or {}).get("allowed"):
        return False, "native SDD workflow has no recorded execution"
    exception = workflow.get("failure_exception") or {}
    if execution and execution.get("exit_code") != 0:
        if not str(exception.get("reason") or "").strip() or exception.get("exit_code") != execution.get("exit_code"):
            return False, "native SDD workflow execution failed"
    paths = workflow.get("artifacts") or []
    hashes = workflow.get("artifact_hashes") or {}
    if not paths:
        return False, "native SDD workflow has no recorded output artifacts"
    missing, changed = [], []
    for relative in paths:
        path = (repo / relative).resolve()
        if not path.is_relative_to(repo.resolve()) or not path.is_file():
            missing.append(relative)
        elif hashes.get(relative) != hashlib.sha256(path.read_bytes()).hexdigest():
            changed.append(relative)
    if missing:
        return False, "native SDD workflow output artifacts are missing: " + ", ".join(missing)
    if changed:
        return False, "native SDD workflow output artifacts changed: " + ", ".join(changed)
    return True, None


def verification_fresh(work: dict, current: dict) -> bool:
    verification = work.get("verification") or {}
    anchor = work.get("acceptance") or {}
    return bool(verification.get("snapshot_format") == SNAPSHOT_FORMAT and verification.get("version") == 2 and verification.get("source") == "config"
                and verification.get("status") == "passed"
                and required_verification_status(verification.get("results", [])) == "passed"
                and anchor.get("version") == 2
                and anchor.get("config_sha256") == current["verification_config_sha256"]
                and all(verification.get(key) == current.get(key) for key in SNAPSHOT_KEYS))
