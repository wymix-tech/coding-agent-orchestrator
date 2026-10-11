"""Pure requirement, acceptance, and completion-evidence bindings."""

import hashlib
import json
import time

SNAPSHOT_KEYS = ("requirement_sha256", "code_snapshot", "rules_snapshot",
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
        if not isinstance(gates, list) or any(not isinstance(gate, str) or gate != "tests" for gate in gates):
            raise ValueError("VERIFICATION_GATE_INVALID")
        names.add(name)
        result.append({"name": name, "command": command, "required": required,
                       "gates": sorted(set(gates))})
    return result


def tests_satisfied(verification: dict) -> bool:
    results = [result for result in verification.get("results", [])
               if result.get("required") and "tests" in result.get("gates", [])]
    return bool(results) and all(result.get("status") == "passed" for result in results)


def acceptance_hash(scripts: list[dict]) -> str:
    encoded = json.dumps(scripts, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def anchor_acceptance(work: dict, scripts: list[dict]) -> None:
    work["acceptance"] = {"version": 2, "config_sha256": acceptance_hash(scripts),
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
        previous = sdd.get("workflow") or {}
        sdd["workflow"] = {key: previous[key] for key in ("name", "entrypoint") if key in previous}
        if sdd["workflow"]:
            sdd["workflow"]["status"] = "selected"
        sdd.pop("plan_scope", None)
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


def verification_fresh(work: dict, current: dict) -> bool:
    verification = work.get("verification") or {}
    anchor = work.get("acceptance") or {}
    return bool(verification.get("version") == 2 and verification.get("source") == "config"
                and verification.get("status") == "passed" and anchor.get("version") == 2
                and anchor.get("config_sha256") == current["verification_config_sha256"]
                and all(verification.get(key) == current.get(key) for key in SNAPSHOT_KEYS))
