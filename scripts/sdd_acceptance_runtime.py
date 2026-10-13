"""Pure Work-scoped independent acceptance runs and explicit source anchoring."""
from copy import deepcopy
import uuid

from scripts.sdd_acceptance_policy import (digest, normalize_declaration, normalize_report,
                                           result_state, is_fresh)


def configure_record(work: dict, declaration: dict) -> bool:
    declaration = normalize_declaration(declaration)
    sdd = work.setdefault("sdd", {})
    previous = sdd.get("acceptance") or {}
    if previous.get("declaration") == declaration:
        return False
    sdd["acceptance"] = {"version": 1, "declaration": declaration,
                         "declaration_sha256": digest(declaration), "anchor_sha256": None,
                         "anchor_sources": {}, "run": None,
                         "owned_outputs": deepcopy(previous.get("owned_outputs") or {})}
    return True


def reanchor_record(work: dict, sources: dict[str, str]) -> None:
    record = work["sdd"]["acceptance"]
    record["anchor_sources"] = dict(sources)
    record["anchor_sha256"] = digest({"declaration": record["declaration_sha256"], "sources": sources})
    record["run"] = None


def anchor_matches(record: dict, inputs: dict) -> bool:
    return bool(record.get("anchor_sha256") == digest({"declaration": inputs.get("declaration_sha256"),
                                                       "sources": inputs.get("source_hashes")}))


def start_run(work: dict, inputs: dict) -> dict:
    record = work["sdd"]["acceptance"]
    if not anchor_matches(record, inputs):
        raise ValueError("SDD_ACCEPTANCE_REANCHOR_REQUIRED")
    run = {"id": "sdda-" + uuid.uuid4().hex, "status": "running", "inputs": deepcopy(inputs),
           "input_sha256": digest(inputs), "results": [], "evidence_hashes": {}, "output_hashes": {}, "reason": None}
    record["run"] = run
    return run


def finish_run(work: dict, run_id: str, inputs: dict, report: dict | None,
               execution: dict, hashes: dict[str, str], error: str | None = None) -> dict:
    record = work["sdd"]["acceptance"]
    run = record.get("run") or {}
    if run.get("id") != run_id or run.get("status") != "running":
        raise ValueError("SDD_ACCEPTANCE_RUN_CHANGED")
    run["execution"] = deepcopy(execution)
    outputs = set(record["declaration"]["outputs"])
    run["output_hashes"] = {key: value for key, value in hashes.items() if key in outputs}
    run["evidence_hashes"] = {key: value for key, value in hashes.items() if key not in outputs}
    record.setdefault("owned_outputs", {}).update(run["output_hashes"])
    if run["inputs"] != inputs:
        run["status"], run["reason"] = "stale", "SDD acceptance inputs changed during execution"
        return run
    try:
        if error:
            raise ValueError(error)
        run["results"] = normalize_report(report, record["declaration"], run_id, run["input_sha256"], work["sdd"]["provider"])
        run["status"] = result_state(run["results"], execution["exit_code"])
        run["reason"] = None if run["status"] == "passed" else "SDD acceptance did not pass"
    except (ValueError, KeyError, TypeError) as exc:
        code = execution.get("exit_code", 0)
        run["status"] = "error" if isinstance(code, int) and (code < 0 or code >= 3) else "failed" if code == 1 else "blocked"
        run["reason"] = str(exc)
    return run


def acceptance_status(work: dict, inputs: dict, hashes: dict[str, str]) -> dict:
    record = (work.get("sdd") or {}).get("acceptance") or {}
    if not record.get("declaration"):
        return {"ready": False, "state": "unconfigured", "reason": "SDD acceptance is not configured"}
    if not anchor_matches(record, inputs):
        return {"ready": False, "state": "stale", "reason": "SDD acceptance sources/standard need explicit reanchor"}
    run = record.get("run")
    if not run:
        return {"ready": False, "state": "pending", "reason": "SDD acceptance has not run for the current implementation"}
    if run.get("status") != "passed":
        return {"ready": False, "state": run.get("status", "blocked"), "reason": run.get("reason") or "SDD acceptance has not passed"}
    fresh = is_fresh(run, inputs, hashes)
    return {"ready": fresh, "state": "passed" if fresh else "stale",
            "reason": None if fresh else "SDD acceptance evidence is stale"}
